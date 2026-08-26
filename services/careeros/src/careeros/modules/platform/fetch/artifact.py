"""What one fetch produced (spec §5.3). Never carries headers or cookies — by construction."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from careeros.modules.platform.base import PlatformError
from careeros.modules.platform.enums import FetchStrategy
from careeros.modules.platform.schemas import FetchAttempt, JobPosting
from careeros.modules.vault.enums import Platform

__all__ = [
    "FetchArtifact",
    "FetchAttempt",
    "JobRead",
    "JobReadError",
    "fingerprint_text",
    "summarize_attempts",
]

CacheStatus = Literal["hit", "miss", "negative", "bypass", "skip"]

_VOLATILE = re.compile(r"\d+|[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def fingerprint_text(text: str | None) -> str | None:
    """Stable hash of the content without volatile noise (numbers, punctuation, whitespace).

    Dates, view counters, "N applicants" and salary digits change between captures of the same
    posting; the words do not. Used to decide whether a refresh is a new snapshot.
    """
    if not text:
        return None
    norm = _WS.sub(" ", _VOLATILE.sub(" ", text.lower())).strip()
    if not norm:
        return None
    return hashlib.sha256(norm.encode()).hexdigest()[:32]


class FetchArtifact(BaseModel):
    """One strategy's result for one source. ``extra="forbid"``: nothing but these fields can
    ever be attached, so request/response headers and cookies cannot leak into storage."""

    model_config = ConfigDict(extra="forbid")

    provider: Platform
    strategy: FetchStrategy
    requested_url: str
    resolved_url: str | None = None
    external_id: str | None = None
    fetched_at: datetime
    status_code: int | None = None
    content_type: str | None = None
    raw_text: str | None = None
    raw_json: dict[str, Any] | list[Any] | None = None
    is_archive: bool = False
    archive_ts: datetime | None = Field(default=None, description="capture time, never a date")
    cache_status: CacheStatus = "miss"
    duration_ms: int = 0
    quality: float | None = None
    completeness: float | None = None
    usable: bool = False
    flags: list[str] = Field(default_factory=list, description="job_closed, transformed, archive")
    error_type: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        """Transport-level success: no error and a 2xx status."""
        return (
            self.error_type is None
            and self.status_code is not None
            and 200 <= self.status_code < 300
        )

    @property
    def is_html(self) -> bool:
        ctype = (self.content_type or "").lower()
        if "html" in ctype:
            return True
        return not ctype and bool(self.raw_text) and "<" in (self.raw_text or "")[:2000]

    @property
    def is_markdown(self) -> bool:
        ctype = (self.content_type or "").lower()
        return "markdown" in ctype or self.strategy == FetchStrategy.jina

    def content_hash(self) -> str | None:
        if self.raw_text:
            return hashlib.sha256(self.raw_text.encode()).hexdigest()
        if self.raw_json is not None:
            dumped = json.dumps(self.raw_json, sort_keys=True, ensure_ascii=False, default=str)
            return hashlib.sha256(dumped.encode()).hexdigest()
        return None

    def to_attempt(self) -> FetchAttempt:
        return FetchAttempt(
            strategy=self.strategy,
            url=self.requested_url,
            status_code=self.status_code,
            ok=self.usable,
            error_type=self.error_type,
            error_message=self.error_message,
            duration_ms=self.duration_ms,
            cache_status=self.cache_status,
        )

    def brief(self) -> str:
        status = str(self.status_code) if self.status_code is not None else "-"
        if self.usable:
            tail = f"ok q={self.quality or 0:.2f} c={self.completeness or 0:.2f}"
        else:
            tail = self.error_type or "unusable"
            if self.error_message:
                tail += f" ({self.error_message[:80]})"
        return f"{self.strategy}: {status} {tail}"


@dataclass(slots=True)
class JobRead:
    """Outcome of a job read: the posting (when extracted), the artifact it came from, and
    every attempt the chain made — the diagnostics are for the owner, not a log line."""

    posting: JobPosting | None
    artifact: FetchArtifact | None
    attempts: list[FetchAttempt] = field(default_factory=list)
    diagnostics: str = ""


def summarize_attempts(attempts: list[FetchAttempt]) -> str:
    parts: list[str] = []
    for a in attempts:
        status = str(a.status_code) if a.status_code is not None else "-"
        if a.ok:
            parts.append(f"{a.strategy}: {status} ok")
            continue
        detail = a.error_type or "unusable"
        if a.error_message:
            detail += f" ({a.error_message[:80]})"
        parts.append(f"{a.strategy}: {status} {detail}")
    return "; ".join(parts) or "no attempts"


class JobReadError(PlatformError):
    """No strategy produced a usable artifact. Carries every attempt and the best partial one."""

    def __init__(
        self,
        platform: Platform,
        attempts: list[FetchAttempt],
        best_partial: FetchArtifact | None = None,
        diagnostics: str = "",
    ) -> None:
        self.platform = platform
        self.attempts = list(attempts)
        self.best_partial = best_partial
        self.diagnostics = diagnostics or summarize_attempts(self.attempts)
        super().__init__(f"{platform}: job read failed — {self.diagnostics}")
