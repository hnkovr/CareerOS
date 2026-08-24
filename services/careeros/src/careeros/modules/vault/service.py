"""Vault facade: load → validate → preview diff → apply (write + git commit).

The only write path into canonical data (ADR-001, ADR-010). Synchronous on purpose — file and git
operations are fast and callers wrap it in ``asyncio.to_thread``.
"""

from __future__ import annotations

import difflib
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from careeros.core.logging import get_logger
from careeros.modules.vault import schema as s
from careeros.modules.vault.git import CommitInfo, GitError, GitRepo
from careeros.modules.vault.layout import COLLECTIONS, EDITABLE_COLLECTIONS, Collection
from careeros.modules.vault.loader import LoadResult, VaultIssue, load_vault
from careeros.modules.vault.validator import validate_vault
from careeros.modules.vault.yamlio import dump_yaml, load_yaml, merge_into, to_plain

log = get_logger(__name__)


class VaultError(Exception):
    pass


class VaultInvalid(VaultError):
    def __init__(self, issues: list[VaultIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(str(i) for i in issues[:5]))


class VaultReadOnly(VaultError):
    """Write attempted against a vault opened read-only (the bundled demo vault)."""


class VaultConflict(VaultError):
    pass


class IssueOut(BaseModel):
    level: str
    file: str
    location: str
    message: str

    @classmethod
    def from_issue(cls, i: VaultIssue) -> IssueOut:
        return cls(level=i.level, file=i.file, location=i.location, message=i.message)


class VaultStatus(BaseModel):
    path: str
    exists: bool
    read_only: bool = False  # true when reads fall back to the bundled demo vault
    is_repo: bool
    head_sha: str | None
    dirty: bool
    valid: bool
    counts: dict[str, int]
    errors: int
    warnings: int
    owner: str | None = None
    default_positioning: str | None = None
    default_cv_variant: str | None = None


class ChangeRequest(BaseModel):
    collection: str
    item_id: str | None = Field(default=None, description="omit for singletons or when creating")
    op: Literal["upsert", "delete"] = "upsert"
    data: dict[str, Any] | None = Field(default=None, description="full item for upsert")
    message: str | None = Field(default=None, description="commit message; generated when omitted")
    base_sha: str | None = Field(
        default=None, description="HEAD the edit was based on (conflict check)"
    )


class ChangePreview(BaseModel):
    collection: str
    item_id: str
    file: str
    diff: str
    message: str
    ok: bool
    issues: list[IssueOut]
    base_sha: str | None


class ChangeResult(ChangePreview):
    commit_sha: str


class CommitOut(BaseModel):
    sha: str
    date: str
    message: str


class Vault:
    def __init__(
        self,
        root: Path,
        *,
        git_user_name: str = "CareerOS",
        git_user_email: str = "careeros@localhost",
        auto_push: bool = False,
        read_only: bool = False,
    ) -> None:
        self.root = Path(root)
        self.git = GitRepo(self.root, user_name=git_user_name, user_email=git_user_email)
        self.auto_push = auto_push
        self.read_only = read_only  # set for the bundled demo vault: reads yes, commits no
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ read
    def load(self) -> LoadResult:
        result = load_vault(self.root)
        if result.data is not None:
            result.issues.extend(validate_vault(result.data))
        return result

    def require(self) -> s.VaultData:
        result = self.load()
        if result.data is None or result.errors:
            raise VaultInvalid(result.errors or result.issues)
        return result.data

    def head_sha(self) -> str | None:
        return self.git.head_sha()

    def status(self) -> VaultStatus:
        result = self.load() if self.root.exists() else LoadResult(None, [])
        data = result.data
        counts: dict[str, int] = {}
        if data is not None:
            for name in EDITABLE_COLLECTIONS:
                value = getattr(data, name, None)
                counts[name] = len(value) if isinstance(value, list) else int(value is not None)
        return VaultStatus(
            path=str(self.root),
            exists=self.root.exists(),
            read_only=self.read_only,
            is_repo=self.git.is_repo(),
            head_sha=self.git.head_sha(),
            dirty=self.git.is_dirty() if self.git.is_repo() else False,
            valid=result.ok,
            counts=counts,
            errors=len(result.errors),
            warnings=len([i for i in result.issues if i.level == "warning"]),
            owner=data.meta.owner if data else None,
            default_positioning=data.meta.default_positioning if data else None,
            default_cv_variant=data.meta.default_cv_variant if data else None,
        )

    def history(self, path: str | None = None, n: int = 20) -> list[CommitInfo]:
        return self.git.log(n=n, path=path) if self.git.is_repo() else []

    # ------------------------------------------------------------------ write
    def preview_change(self, req: ChangeRequest) -> ChangePreview:
        with self._lock:
            return self._prepare(req)[0]

    def apply_change(self, req: ChangeRequest) -> ChangeResult:
        if self.read_only:
            raise VaultReadOnly(
                f"{self.root} is the bundled demo vault and cannot be written to — create your "
                "own with `just vault-init <path>` and point CAREEROS_VAULT_PATH at it"
            )
        with self._lock:
            preview, rel_path, new_text = self._prepare(req)
            if not preview.ok:
                raise VaultInvalid(
                    [VaultIssue(i.level, i.file, i.location, i.message) for i in preview.issues]
                )
            head = self.git.head_sha()
            if req.base_sha and head and req.base_sha != head:
                raise VaultConflict(f"vault moved on: base {req.base_sha[:8]} != HEAD {head[:8]}")
            if not self.git.is_repo():
                self.git.init()
            target = self.root / rel_path
            if req.op == "delete" and new_text is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(new_text or "", encoding="utf-8")
            try:
                sha = self.git.commit_paths([rel_path], preview.message)
            except GitError:
                log.exception("vault.commit_failed", file=rel_path)
                raise
            if self.auto_push:
                try:
                    self.git.push()
                except GitError:
                    log.warning("vault.push_failed", file=rel_path)
            log.info("vault.change_applied", file=rel_path, commit=sha[:8], message=preview.message)
            return ChangeResult(**preview.model_dump(), commit_sha=sha)

    # ------------------------------------------------------------------ internals
    def _prepare(self, req: ChangeRequest) -> tuple[ChangePreview, str, str | None]:
        if req.collection not in EDITABLE_COLLECTIONS:
            raise VaultError(f"collection '{req.collection}' is not editable")
        collection = COLLECTIONS[req.collection]
        issues: list[VaultIssue] = []

        item_id, rel_path, new_text = self._render_change(collection, req, issues)
        current_text = (
            (self.root / rel_path).read_text(encoding="utf-8")
            if (self.root / rel_path).exists()
            else ""
        )
        diff = "".join(
            difflib.unified_diff(
                current_text.splitlines(keepends=True),
                (new_text or "").splitlines(keepends=True),
                fromfile=f"a/{rel_path}",
                tofile=f"b/{rel_path}" if new_text is not None else "/dev/null",
            )
        )

        if not issues:
            issues.extend(self._validate_with_change(rel_path, new_text))

        verb = {"upsert": "update", "delete": "remove"}[req.op]
        if req.op == "upsert" and not current_text:
            verb = "add"
        message = req.message or f"career({req.collection}): {verb} {item_id}"
        preview = ChangePreview(
            collection=req.collection,
            item_id=item_id,
            file=rel_path,
            diff=diff,
            message=message,
            ok=not any(i.level == "error" for i in issues),
            issues=[IssueOut.from_issue(i) for i in issues],
            base_sha=self.git.head_sha(),
        )
        return preview, rel_path, new_text

    def _render_change(
        self, collection: Collection, req: ChangeRequest, issues: list[VaultIssue]
    ) -> tuple[str, str, str | None]:
        """Return (item_id, relative file path, new file text or None when the file is deleted)."""
        model = collection.item_model or collection.file_model

        if req.op == "upsert":
            if req.data is None:
                raise VaultError("data is required for upsert")
            data = dict(req.data)
            if collection.singleton:
                data.setdefault("id", req.item_id or collection.name)
            elif req.item_id and "id" not in data:
                data["id"] = req.item_id
            try:
                item = model.model_validate(data)
            except ValidationError as exc:
                for err in exc.errors():
                    loc = ".".join(str(p) for p in err["loc"]) or "<root>"
                    issues.append(VaultIssue("error", collection.path, loc, err["msg"]))
                return (
                    str(data.get("id", req.item_id or "?")),
                    self._path_for(collection, str(data.get("id", ""))),
                    None,
                )
            item_id = getattr(item, "id", req.item_id or collection.name)
            if req.item_id and req.item_id != item_id:
                raise VaultError(
                    "item_id in path and data differ (ids are immutable; retire + add instead)"
                )
            fresh = item.model_dump(mode="python", exclude_unset=True)
            fresh.setdefault("id", item_id)
        else:
            if not req.item_id and not collection.singleton:
                raise VaultError("item_id is required for delete")
            item_id = req.item_id or collection.name
            fresh = None

        rel_path = self._path_for(collection, item_id)
        target = self.root / rel_path
        raw: Any = load_yaml(target) if target.exists() else None

        if collection.singleton or collection.per_file:
            if fresh is None:
                return item_id, rel_path, None
            if collection.per_file:
                fresh = {k: v for k, v in fresh.items() if k != "id"} | {"id": item_id}
            if isinstance(raw, CommentedMap):
                merge_into(raw, fresh)
                return item_id, rel_path, dump_yaml(raw)
            return item_id, rel_path, dump_yaml(to_plain(fresh))

        # list collection: items: [...]
        container: CommentedMap = raw if isinstance(raw, CommentedMap) else CommentedMap()
        items = container.get("items")
        if not isinstance(items, CommentedSeq):
            items = CommentedSeq()
            container["items"] = items
        index = next(
            (i for i, it in enumerate(items) if isinstance(it, dict) and it.get("id") == item_id),
            None,
        )
        if fresh is None:
            if index is None:
                issues.append(VaultIssue("error", rel_path, item_id, "item not found"))
            else:
                del items[index]
        elif index is None:
            items.append(to_plain(fresh))
        else:
            merge_into(items[index], fresh)
        return item_id, rel_path, dump_yaml(container)

    @staticmethod
    def _path_for(collection: Collection, item_id: str) -> str:
        if collection.per_file:
            return f"{collection.path}{item_id}.yaml"
        return collection.path

    def _validate_with_change(self, rel_path: str, new_text: str | None) -> list[VaultIssue]:
        """Validate the whole vault as it would be after the change, on a shadow copy."""
        with tempfile.TemporaryDirectory(prefix="careeros-vault-") as tmp:
            shadow = Path(tmp) / "vault"
            shutil.copytree(self.root, shadow, ignore=shutil.ignore_patterns(".git"))
            target = shadow / rel_path
            if new_text is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(new_text, encoding="utf-8")
            result = load_vault(shadow)
            if result.data is not None:
                result.issues.extend(validate_vault(result.data))
            return result.issues

    # ------------------------------------------------------------------ bootstrap
    def init_from_template(self, template_dir: Path, *, owner: str) -> None:
        if self.root.exists() and any(
            p for p in self.root.iterdir() if p.name not in {"README.md", ".git"}
        ):
            raise VaultError(f"{self.root} is not empty")
        self.root.mkdir(parents=True, exist_ok=True)
        for src in Path(template_dir).rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(template_dir)
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_text(encoding="utf-8").replace("__OWNER__", owner)
            dst.write_text(text, encoding="utf-8")
        self.git.init()
        self.git.commit_paths(["."], "career(vault): initialise from template")


# --------------------------------------------------------------------------- search


class FactHit(BaseModel):
    id: str
    collection: str
    title: str
    text: str
    score: float


def search_facts(data: s.VaultData, query: str, limit: int = 20) -> list[FactHit]:
    """Cheap in-memory search over fact-bearing items (P0); pgvector semantic search is P1."""
    from rapidfuzz import fuzz

    q = query.lower().strip()
    if not q:
        return []
    corpus: list[tuple[str, str, str, str]] = []
    for a in data.achievements:
        corpus.append(
            (
                a.id,
                "achievements",
                a.title,
                " ".join([a.title, *a.facts, *a.keywords, *a.technologies.all()]),
            )
        )
    for p in data.projects:
        corpus.append(
            (
                p.id,
                "projects",
                p.name,
                " ".join(
                    filter(
                        None, [p.name, p.summary, p.problem, p.solution, p.outcome, *p.technologies]
                    )
                ),
            )
        )
    for e in data.experience:
        corpus.append(
            (
                e.id,
                "experience",
                e.company_name,
                " ".join([e.company_name, e.summary, *e.responsibilities, *e.technologies]),
            )
        )
    for sk in data.skills:
        corpus.append((sk.id, "skills", sk.name, " ".join([sk.name, sk.category, *sk.aliases])))
    for o in data.offers:
        corpus.append(
            (
                o.id,
                "offers",
                o.title,
                " ".join([o.title, o.customer_problem, *o.deliverables, *o.technologies]),
            )
        )
    hits = []
    for item_id, coll, title, text in corpus:
        lowered = text.lower()
        score = 100.0 if q in lowered else float(fuzz.partial_token_set_ratio(q, lowered))
        if score >= 60:
            hits.append(
                FactHit(id=item_id, collection=coll, title=title, text=text[:300], score=score)
            )
    hits.sort(key=lambda h: (-h.score, h.id))
    return hits[:limit]
