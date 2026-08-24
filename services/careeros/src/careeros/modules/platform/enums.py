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


class SyncMethod(StrEnum):
    api = "api"
    export = "export"
    paste = "paste"


class SyncStatus(StrEnum):
    ok = "ok"
    partial = "partial"
    failed = "failed"
    skipped = "skipped"


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

# Platforms served by a connector submodule (order = display order).
PLATFORMS: tuple[Platform, ...] = (
    Platform.hh,
    Platform.upwork,
    Platform.linkedin,
    Platform.wellfound,
    Platform.indeed,
    Platform.getmatch,
    Platform.toptal,
)

SOURCE_BY_PLATFORM: dict[Platform, Source] = {
    Platform.hh: Source.hh,
    Platform.upwork: Source.upwork,
    Platform.linkedin: Source.linkedin,
    Platform.wellfound: Source.wellfound,
    Platform.indeed: Source.indeed,
    Platform.getmatch: Source.getmatch,
    Platform.toptal: Source.toptal,
}
