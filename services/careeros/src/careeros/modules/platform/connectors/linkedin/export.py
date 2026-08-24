"""Reader for LinkedIn's "Download your data" archive (ADR-005: user-downloaded, never fetched).

Accepts the ``.zip`` LinkedIn e-mails to the member or a directory it was unpacked into. CSVs are
read with the standard ``csv`` module, UTF-8 with BOM tolerance, header-name based; a ``Notes:``
preamble before the header row (LinkedIn puts one in some files, e.g. ``Connections.csv``) is
skipped by scanning for a row that carries one of the expected column names.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from careeros.modules.platform.base import ParseError

PROFILE = "Profile.csv"
POSITIONS = "Positions.csv"
SKILLS = "Skills.csv"
EDUCATION = "Education.csv"
CERTIFICATIONS = "Certifications.csv"
PROJECTS = "Projects.csv"
LANGUAGES = "Languages.csv"
JOB_APPLICATIONS = "Job Applications.csv"
SAVED_JOBS = "Saved Jobs.csv"

#: file → column names, one of which must appear on the header row (preamble detection).
ANCHOR_COLUMNS: dict[str, tuple[str, ...]] = {
    PROFILE: ("First Name", "Headline"),
    POSITIONS: ("Company Name", "Title"),
    SKILLS: ("Name",),
    EDUCATION: ("School Name", "Degree Name"),
    CERTIFICATIONS: ("Name", "Authority"),
    PROJECTS: ("Title",),
    LANGUAGES: ("Name", "Proficiency"),
    JOB_APPLICATIONS: ("Application Date", "Job Title"),
    SAVED_JOBS: ("Saved Date", "Job Title"),
}

#: what to tick in "Get a copy of your data" when a file is missing.
HINTS: dict[str, str] = {
    PROFILE: "request the archive with 'Profile' selected (or the larger data archive)",
    JOB_APPLICATIONS: "request the archive with 'Job applications' selected (Jobs section)",
    SAVED_JOBS: "request the archive with 'Saved jobs' selected (Jobs section)",
}

_MAX_MEMBER_BYTES = 50 * 1024 * 1024  # a CSV in the archive is never anywhere near this
_HEADER_SCAN_LINES = 50


class ExportReader:
    """Case-insensitive, basename-based access to the CSV files of an archive or a directory."""

    def __init__(self, path: Path, entries: dict[str, str], load: Callable[[str], bytes]) -> None:
        self.path = path
        self._entries = entries  # lower-case basename → member name / relative path
        self._load = load

    def files(self) -> list[str]:
        return sorted(PurePosixPath(p).name for p in self._entries.values())

    def has(self, name: str) -> bool:
        return name.lower() in self._entries

    def read_text(self, name: str) -> str | None:
        """Decoded (UTF-8, BOM-tolerant) text with normalised newlines; ``None`` when absent."""
        member = self._entries.get(name.lower())
        if member is None:
            return None
        text = self._load(member).decode("utf-8-sig", errors="replace")
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def rows(self, name: str, *, required: bool = False) -> list[dict[str, str]]:
        """Header-keyed rows (values stripped, kept verbatim otherwise); ``[]`` when absent."""
        text = self.read_text(name)
        if text is None:
            if required:
                hint = HINTS.get(name, "the file is optional in LinkedIn's archive")
                raise ParseError(f"{name} not found in export {self.path}: {hint}")
            return []
        lines = text.split("\n")
        start = _header_index(lines, ANCHOR_COLUMNS.get(name, ()))
        reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
        out: list[dict[str, str]] = []
        for row in reader:
            clean: dict[str, str] = {}
            for key, value in row.items():
                if not isinstance(key, str):  # values beyond the header (restkey) — not ours
                    continue
                clean[key.strip()] = value.strip() if isinstance(value, str) else ""
            if any(clean.values()):
                out.append(clean)
        return out


def open_export(path: Path | str) -> ExportReader:
    """Open a LinkedIn export given as a directory or a ``.zip`` archive."""
    p = Path(path).expanduser()
    if not p.exists():
        raise ParseError(f"export path not found: {p}")
    if p.is_dir():
        return ExportReader(p, _dir_entries(p), lambda rel: (p / rel).read_bytes())
    if zipfile.is_zipfile(p):
        return ExportReader(p, _zip_entries(p), lambda member: _zip_read(p, member))
    raise ParseError(f"{p}: expected the LinkedIn export as a directory or a .zip archive")


def _header_index(lines: list[str], anchors: tuple[str, ...]) -> int:
    """Index of the header row: first line whose CSV fields contain an anchor column."""
    wanted = {a.lower() for a in anchors}
    if wanted:
        for i, line in enumerate(lines[:_HEADER_SCAN_LINES]):
            if not line.strip():
                continue
            try:
                fields = next(csv.reader([line]))
            except (csv.Error, StopIteration):
                continue
            if wanted & {f.strip().lower() for f in fields}:
                return i
    if lines and lines[0].strip().lower().startswith("notes:"):
        # unknown columns but a known preamble: header follows the first blank line
        for i, line in enumerate(lines[:_HEADER_SCAN_LINES]):
            if i and not line.strip():
                return i + 1
    return 0


def _prefer_shallower(entries: dict[str, str], key: str, candidate: str) -> None:
    current = entries.get(key)
    if current is None or candidate.count("/") < current.count("/"):
        entries[key] = candidate


def _dir_entries(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for f in sorted(root.rglob("*.csv")):
        if not f.is_file() or f.name.startswith("."):
            continue
        _prefer_shallower(entries, f.name.lower(), f.relative_to(root).as_posix())
    return entries


def _zip_entries(archive: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename
            base = PurePosixPath(name).name
            if info.is_dir() or name.startswith("__MACOSX/") or base.startswith("."):
                continue
            if not base.lower().endswith(".csv"):
                continue
            _prefer_shallower(entries, base.lower(), name)
    return entries


def _zip_read(archive: Path, member: str) -> bytes:
    with zipfile.ZipFile(archive) as zf:
        info = zf.getinfo(member)
        if info.file_size > _MAX_MEMBER_BYTES:
            raise ParseError(f"{archive}: {member} is unexpectedly large ({info.file_size} bytes)")
        return zf.read(member)
