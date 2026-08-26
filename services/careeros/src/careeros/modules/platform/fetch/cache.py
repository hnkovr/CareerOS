"""L1 in-process fetch cache (spec §5.3): ≤ 1 request per URL per TTL, short negative TTL.

Keyed by ``(provider, strategy, canonical_url, locale)``. Only *usable* artifacts are stored as
success; captcha / not-found / interstitial outcomes become negative entries; transient
failures (network, 5xx, timeouts) are never cached at all. Persistent storage is the sync
layer's job (``OpportunityRaw``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from careeros.core.config import Settings
from careeros.modules.platform.enums import FetchStrategy
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.vault.enums import Platform

CacheKey = tuple[str, str, str, str]

#: Outcomes worth remembering for a short while (the page will not change in five minutes).
NEGATIVE_REASONS: frozenset[str] = frozenset(
    {
        "not_found",
        "gone",
        "captcha",
        "login_wall",
        "cookie_wall",
        "waf_blocked",
        "error_page",
        "js_shell",
        "search_results",
        "too_thin",
        "job_closed",
        "empty",
        "archive_not_found",
    }
)


@dataclass(slots=True)
class CacheEntry:
    artifact: FetchArtifact | None
    negative: bool
    reason: str | None
    expires_at: float


class FetchCache:
    def __init__(
        self,
        *,
        ttl_s: float = 3600.0,
        negative_ttl_s: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 512,
    ) -> None:
        self.ttl_s = ttl_s
        self.negative_ttl_s = negative_ttl_s
        self._clock = clock
        self._max = max(1, max_entries)
        self._entries: dict[CacheKey, CacheEntry] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> FetchCache:
        return cls(
            ttl_s=float(settings.job_fetch_cache_ttl_s),
            negative_ttl_s=float(settings.job_fetch_negative_cache_ttl_s),
        )

    @staticmethod
    def key(
        provider: Platform, strategy: FetchStrategy, canonical_url: str, locale: str | None
    ) -> CacheKey:
        return (str(provider), str(strategy), canonical_url, locale or "")

    def __len__(self) -> int:
        return len(self._entries)

    def get(
        self,
        provider: Platform,
        strategy: FetchStrategy,
        canonical_url: str,
        locale: str | None = None,
    ) -> CacheEntry | None:
        key = self.key(provider, strategy, canonical_url, locale)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            return None
        return entry

    def put(
        self, artifact: FetchArtifact, *, canonical_url: str, locale: str | None = None
    ) -> bool:
        """Store the outcome of ``artifact``; returns True only when stored as a success.

        An unusable artifact is never stored as success — it becomes a negative entry when its
        failure is stable (see ``NEGATIVE_REASONS`` / 404-410), otherwise it is not cached.
        """
        key = self.key(artifact.provider, artifact.strategy, canonical_url, locale)
        if artifact.usable:
            self._store(key, CacheEntry(artifact, False, None, self._clock() + self.ttl_s))
            return True
        reason = artifact.error_type
        stable = reason in NEGATIVE_REASONS or artifact.status_code in (404, 410)
        if stable:
            self._store(
                key,
                CacheEntry(None, True, reason or "unusable", self._clock() + self.negative_ttl_s),
            )
        else:
            self._entries.pop(key, None)
        return False

    def put_negative(
        self,
        provider: Platform,
        strategy: FetchStrategy,
        canonical_url: str,
        locale: str | None,
        reason: str,
    ) -> None:
        key = self.key(provider, strategy, canonical_url, locale)
        self._store(key, CacheEntry(None, True, reason, self._clock() + self.negative_ttl_s))

    def invalidate(self, canonical_url: str) -> int:
        doomed = [k for k in self._entries if k[2] == canonical_url]
        for k in doomed:
            self._entries.pop(k, None)
        return len(doomed)

    def clear(self) -> None:
        self._entries.clear()

    def _store(self, key: CacheKey, entry: CacheEntry) -> None:
        self._entries.pop(key, None)
        self._entries[key] = entry
        if len(self._entries) > self._max:
            now = self._clock()
            for k in [k for k, e in self._entries.items() if e.expires_at <= now]:
                self._entries.pop(k, None)
            while len(self._entries) > self._max:
                self._entries.pop(next(iter(self._entries)))


_default: FetchCache | None = None


def default_cache(settings: Settings) -> FetchCache:
    """Process-wide cache (one per interpreter), sized from settings on first use."""
    global _default
    if _default is None:
        _default = FetchCache.from_settings(settings)
    return _default


def reset_default_cache() -> None:
    global _default
    _default = None
