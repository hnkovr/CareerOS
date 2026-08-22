"""Load a vault directory into ``VaultData``; schema errors are collected per file, not raised."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from careeros.modules.vault import schema as s
from careeros.modules.vault.layout import COLLECTIONS, OPTIONAL_COLLECTIONS, Collection, yaml_files
from careeros.modules.vault.yamlio import load_yaml


@dataclass(frozen=True)
class VaultIssue:
    level: str  # error | warning
    file: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level}] {self.file}:{self.location}: {self.message}"


@dataclass
class LoadResult:
    data: s.VaultData | None
    issues: list[VaultIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[VaultIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.errors


def _validation_issues(exc: ValidationError, file: str) -> list[VaultIssue]:
    out: list[VaultIssue] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        out.append(VaultIssue("error", file, loc, err["msg"]))
    return out


def _parse(
    model: type[BaseModel], raw: Any, file: str, issues: list[VaultIssue]
) -> BaseModel | None:
    try:
        return model.model_validate(raw if raw is not None else {})
    except ValidationError as exc:
        issues.extend(_validation_issues(exc, file))
        return None


def _read(path: Path, root: Path, issues: list[VaultIssue]) -> Any:
    try:
        return load_yaml(path)
    except Exception as exc:  # ruamel raises many types
        issues.append(VaultIssue("error", str(path.relative_to(root)), "<yaml>", str(exc)))
        return None


def load_collection(root: Path, collection: Collection, issues: list[VaultIssue]) -> Any:
    """Return the parsed collection: a model for singletons, a list of items otherwise."""
    files = yaml_files(root, collection)
    if not files:
        if collection.name not in OPTIONAL_COLLECTIONS:
            issues.append(VaultIssue("error", collection.path, "<file>", "missing required file"))
        return None if collection.singleton else []

    if collection.singleton:
        raw = _read(files[0], root, issues)
        return _parse(collection.file_model, raw, collection.path, issues)

    items: list[Any] = []
    if collection.per_file:
        for path in files:
            rel = str(path.relative_to(root))
            raw = _read(path, root, issues)
            if raw is None:
                continue
            if isinstance(raw, dict) and "id" not in raw:
                raw["id"] = path.stem
            parsed = _parse(collection.file_model, raw, rel, issues)
            if parsed is not None:
                items.append(parsed)
        return items

    raw = _read(files[0], root, issues)
    parsed = _parse(collection.file_model, raw, collection.path, issues)
    return list(getattr(parsed, "items", [])) if parsed is not None else []


def load_vault(root: Path) -> LoadResult:
    root = Path(root)
    issues: list[VaultIssue] = []
    if not root.exists():
        return LoadResult(
            None, [VaultIssue("error", str(root), "<dir>", "vault directory not found")]
        )

    parsed: dict[str, Any] = {}
    for name, collection in COLLECTIONS.items():
        parsed[name] = load_collection(root, collection, issues)

    if (
        any(i.level == "error" for i in issues)
        or parsed["meta"] is None
        or parsed["profile"] is None
    ):
        return LoadResult(None, issues)

    data = s.VaultData(
        meta=parsed["meta"],
        profile=parsed["profile"],
        experience=parsed["experience"],
        achievements=parsed["achievements"],
        projects=parsed["projects"],
        skills=parsed["skills"],
        education=parsed["education"],
        certifications=parsed["certifications"],
        languages=parsed["languages"],
        publications=parsed["publications"],
        testimonials=parsed["testimonials"],
        links=parsed["links"],
        offers=parsed["offers"],
        positioning=parsed["positioning"],
        channels=parsed["channels"],
        cv_variants=parsed["cv_variants"],
        scoring=parsed["scoring"],
        prompts=parsed["prompts"],
    )
    return LoadResult(data, issues)
