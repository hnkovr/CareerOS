"""Source abstraction of a job read (ADR-015 §5.1): what the user gave us, before any network.

Pure: no database, no domain services. ``detect()`` asks every connector — there is no central
hostname ``if/elif``; the generic connector answers with low confidence for any http(s) URL.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from careeros.core.logging import get_logger
from careeros.modules.opportunities.dedup import normalize_url
from careeros.modules.vault.enums import Platform

if TYPE_CHECKING:
    from careeros.modules.platform.registry import PlatformRegistry

log = get_logger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


class SourceKind(StrEnum):
    url = "url"
    provider_id = "provider_id"
    search_result = "search_result"
    api = "api"
    html = "html"
    markdown = "markdown"
    text = "text"
    email = "email"
    telegram_message = "telegram_message"
    manual = "manual"
    archive = "archive"
    repost = "repost"


#: Sources that came out of a private message: their URLs never go to a third party (ADR-015 §3).
PRIVATE_KINDS: frozenset[SourceKind] = frozenset({SourceKind.email, SourceKind.telegram_message})

#: Kinds whose ``value`` is the URL itself.
_URL_VALUE_KINDS: frozenset[SourceKind] = frozenset(
    {SourceKind.url, SourceKind.search_result, SourceKind.archive, SourceKind.repost}
)


class SourceRef(BaseModel):
    """Where a job came from. A source may reference another (RocketHunt → the original post)."""

    kind: SourceKind = SourceKind.url
    value: str
    provider_hint: Platform | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent: SourceRef | None = None

    @property
    def is_private(self) -> bool:
        return self.kind in PRIVATE_KINDS or (self.parent is not None and self.parent.is_private)

    def url(self) -> str | None:
        """The http(s) URL this reference points at, if any (first URL in message-like kinds)."""
        if self.kind in _URL_VALUE_KINDS and is_http_url(self.value):
            return self.value.strip()
        meta_url = self.metadata.get("url")
        if isinstance(meta_url, str) and is_http_url(meta_url):
            return meta_url.strip()
        if self.kind == SourceKind.provider_id:
            return None
        return find_first_url(self.value)


class CanonicalSource(BaseModel):
    """One resource to read: the platform that owns it and its normalised URL."""

    platform: Platform
    external_id: str | None = None
    canonical_url: str
    locale: str | None = None
    host: str
    #: Came from a private message → third-party strategies (jina, archives) are skipped.
    private: bool = False


class DetectionResult(BaseModel):
    platform: Platform
    confidence: float = Field(ge=0.0, le=1.0)
    canonical: CanonicalSource


def is_http_url(value: str) -> bool:
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def host_of(url: str) -> str:
    """Lower-cased host without credentials, port or ``www.``; ``""`` when not a URL."""
    try:
        netloc = urlsplit(url.strip()).netloc
    except ValueError:
        return ""
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower()
    return host.removeprefix("www.")


def find_first_url(text: str) -> str | None:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".,;") if m else None


def canonical_source(
    platform: Platform,
    url: str,
    *,
    external_id: str | None = None,
    locale: str | None = None,
    private: bool = False,
) -> CanonicalSource:
    """Default canonical form: ``dedup.normalize_url`` (tracking params dropped, host lowered)."""
    if not is_http_url(url):
        raise ValueError(f"not an http(s) URL: {url!r}")
    canonical = normalize_url(url)
    return CanonicalSource(
        platform=platform,
        external_id=external_id,
        canonical_url=canonical,
        locale=locale,
        host=host_of(canonical),
        private=private,
    )


def detect(url_or_ref: str | SourceRef, registry: PlatformRegistry) -> DetectionResult | None:
    """Highest-confidence connector answer for a URL / source reference; ``None`` = nothing.

    Ties keep registry order (specific connectors come before the generic one). A
    ``provider_hint`` is asked first and wins when it recognises the source; a
    ``provider_id`` reference is canonicalised by the hinted connector directly.
    """
    ref = url_or_ref if isinstance(url_or_ref, SourceRef) else SourceRef(value=url_or_ref)
    connectors = registry.all()
    hinted = None
    if ref.provider_hint is not None:
        hinted = next((c for c in connectors if c.platform == ref.provider_hint), None)

    if ref.kind == SourceKind.provider_id:
        if hinted is None:
            return None
        try:
            canonical = hinted.canonicalize(ref)
        except (ValueError, NotImplementedError):
            return None
        return DetectionResult(
            platform=hinted.platform, confidence=1.0, canonical=_mark(canonical, ref)
        )

    url = ref.url()
    if url is None:
        return None

    ordered = ([hinted] if hinted is not None else []) + [c for c in connectors if c is not hinted]
    best: DetectionResult | None = None
    for connector in ordered:
        try:
            result = connector.detect(url)
        except Exception as exc:  # one broken detector must not hide the others
            log.warning(
                "platform.detect_failed",
                platform=str(connector.platform),
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        if result is None:
            continue
        if connector is hinted:
            return result.model_copy(update={"canonical": _mark(result.canonical, ref)})
        if best is None or result.confidence > best.confidence:
            best = result
    if best is None:
        return None
    return best.model_copy(update={"canonical": _mark(best.canonical, ref)})


def _mark(canonical: CanonicalSource, ref: SourceRef) -> CanonicalSource:
    if ref.is_private and not canonical.private:
        return canonical.model_copy(update={"private": True})
    return canonical
