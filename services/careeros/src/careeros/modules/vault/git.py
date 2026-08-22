"""Minimal git wrapper over the ``git`` binary (no libgit dependency)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    date: str
    message: str


class GitRepo:
    def __init__(
        self, root: Path, *, user_name: str = "CareerOS", user_email: str = "careeros@localhost"
    ) -> None:
        self.root = Path(root)
        self.user_name = user_name
        self.user_email = user_email

    # --- plumbing ---
    def _run(self, *args: str, check: bool = True) -> str:
        cmd = [
            "git",
            "-c",
            f"user.name={self.user_name}",
            "-c",
            f"user.email={self.user_email}",
            "-c",
            "commit.gpgsign=false",
            *args,
        ]
        proc = subprocess.run(cmd, cwd=self.root, capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout

    # --- queries ---
    def is_repo(self) -> bool:
        if not self.root.exists():
            return False
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def head_sha(self) -> str | None:
        if not self.is_repo():
            return None
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, capture_output=True, text=True
        )
        return proc.stdout.strip() if proc.returncode == 0 else None

    def is_dirty(self) -> bool:
        return bool(self._run("status", "--porcelain").strip())

    def diff(self, path: str | None = None, *, staged: bool = False) -> str:
        args = ["diff", "--no-color"]
        if staged:
            args.append("--cached")
        if path:
            args.extend(["--", path])
        return self._run(*args)

    def log(self, n: int = 20, path: str | None = None) -> list[CommitInfo]:
        if not self.head_sha():
            return []
        args = ["log", f"-n{n}", "--date=iso-strict", "--format=%H%x1f%ad%x1f%s"]
        if path:
            args.extend(["--", path])
        out = self._run(*args)
        entries = []
        for line in out.splitlines():
            sha, date, msg = line.split("\x1f", 2)
            entries.append(CommitInfo(sha, date, msg))
        return entries

    def show_file(self, sha: str, path: str) -> str:
        return self._run("show", f"{sha}:{path}")

    # --- mutations ---
    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.is_repo():
            self._run("init", "-q", "-b", "main")

    def commit_paths(self, paths: list[str], message: str) -> str:
        self._run("add", "--", *paths)
        if not self._run("status", "--porcelain", "--", *paths).strip():
            raise GitError("nothing to commit")
        self._run("commit", "-q", "-m", message, "--", *paths)
        sha = self.head_sha()
        assert sha is not None
        return sha

    def push(self) -> None:
        self._run("push", "-q")
