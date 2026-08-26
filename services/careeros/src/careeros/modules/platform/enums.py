"""Enumerations of the platform integration layer (ADR-004 / ADR-013)."""

from __future__ import annotations

from enum import StrEnum

from careeros.modules.opportunities.enums import Source
from careeros.modules.vault.enums import Platform


class CapabilityLevel(StrEnum):
    none = "none"
    manual = "manual"
    export = "export"
    api = "api"


class ApplyLevel(StrEnum):
    """`apply` never rises above manual assistance (ADR-005)."""

    none = "none"
    manual_assist = "manual_assist"


class AuthKind(StrEnum):
    none = "none"
    oauth2 = "oauth2"
    api_key = "api_key"


class SyncKind(StrEnum):
    profile = "profile"
    jobs = "jobs"
    applications = "applications"
    #: One job behind a user-supplied URL (ADR-015). Has no ``SyncMethod`` — the method column of
    #: a ``job`` run is the ``FetchStrategy`` that produced the artifact.
    job = "job"


class SyncMethod(StrEnum):
    api = "api"
    export = "export"
    paste = "paste"


class SyncStatus(StrEnum):
    ok = "ok"
    partial = "partial"
    failed = "failed"
    skipped = "skipped"


class FetchStrategy(StrEnum):
    """How one job behind a URL is acquired (ADR-015), best first per connector.

    ``archive_today`` and ``search_recovery`` are reserved names: declared for the data model,
    not implemented in this slice (``run_chain`` skips them with a diagnostic).
    """

    api = "api"
    public_html = "public_html"
    jina = "jina"
    wayback = "wayback"
    archive_today = "archive_today"
    search_recovery = "search_recovery"


class AccessMode(StrEnum):
    """Access policy of a connector, enforced before any network call (ADR-015 §5)."""

    public = "public"
    authenticated_user_api = "authenticated_user_api"
    manual_import = "manual_import"
    unsupported = "unsupported"


class SourceRelation(StrEnum):
    """How a captured source relates to the opportunity it was attached to (ADR-016)."""

    primary = "primary"
    aggregates = "aggregates"
    repost_of = "repost_of"
    same_as = "same_as"
    mirror = "mirror"
    historical_version_of = "historical_version_of"
    possible_duplicate = "possible_duplicate"


class ConnectionStatus(StrEnum):
    disconnected = "disconnected"
    connected = "connected"
    needs_reauth = "needs_reauth"
    error = "error"


class ApplicationStatus(StrEnum):
    """Normalized application state observed on a platform (raw wording is kept alongside)."""

    applied = "applied"
    viewed = "viewed"
    invited = "invited"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"
    unknown = "unknown"


# Integration precedence (ADR-004): official API > official export > user paste.
METHOD_ORDER: tuple[SyncMethod, ...] = (SyncMethod.api, SyncMethod.export, SyncMethod.paste)

LEVEL_BY_METHOD: dict[SyncMethod, CapabilityLevel] = {
    SyncMethod.api: CapabilityLevel.api,
    SyncMethod.export: CapabilityLevel.export,
    SyncMethod.paste: CapabilityLevel.manual,
}

# Strategies that read a historical copy (archive budget) / a search engine (search budget).
ARCHIVE_STRATEGIES: frozenset[FetchStrategy] = frozenset(
    {FetchStrategy.wayback, FetchStrategy.archive_today}
)
SEARCH_STRATEGIES: frozenset[FetchStrategy] = frozenset({FetchStrategy.search_recovery})
# Strategies that hand the URL to a third party (never for URLs from private messages).
THIRD_PARTY_STRATEGIES: frozenset[FetchStrategy] = frozenset(
    {FetchStrategy.jina, FetchStrategy.wayback, FetchStrategy.archive_today}
)
# Strategies that need ``AccessMode.public`` (they read the page like a browser would).
PUBLIC_READ_STRATEGIES: frozenset[FetchStrategy] = frozenset(
    {FetchStrategy.public_html, FetchStrategy.jina, FetchStrategy.wayback}
)

# Platforms served by a connector submodule (order = display order).
PLATFORMS: tuple[Platform, ...] = (
    Platform.hh,
    Platform.upwork,
    Platform.linkedin,
    Platform.wellfound,
    Platform.indeed,
    Platform.getmatch,
    Platform.toptal,
    Platform.rockethunt,
    Platform.justjoin,
    Platform.website,  # generic fallback provider (any http(s) URL, ADR-015) — always last
)

SOURCE_BY_PLATFORM: dict[Platform, Source] = {
    Platform.hh: Source.hh,
    Platform.upwork: Source.upwork,
    Platform.linkedin: Source.linkedin,
    Platform.wellfound: Source.wellfound,
    Platform.indeed: Source.indeed,
    Platform.getmatch: Source.getmatch,
    Platform.toptal: Source.toptal,
    Platform.rockethunt: Source.rockethunt,
    Platform.justjoin: Source.justjoin,
    Platform.website: Source.website,
}
