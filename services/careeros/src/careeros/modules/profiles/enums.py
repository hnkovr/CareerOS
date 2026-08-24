from __future__ import annotations

from enum import StrEnum

from careeros.modules.vault.enums import Platform


class CaptureMethod(StrEnum):
    api = "api"
    export = "export"
    upload = "upload"
    paste = "paste"
    share = "share"
    email = "email"


class AuditCategory(StrEnum):
    completeness = "completeness"
    freshness = "freshness"
    consistency = "consistency"
    keyword_coverage = "keyword_coverage"
    channel_fit = "channel_fit"
    positioning_fit = "positioning_fit"
    proof_metrics = "proof_metrics"
    credibility = "credibility"
    call_to_action = "call_to_action"
    portfolio_coverage = "portfolio_coverage"
    compensation_positioning = "compensation_positioning"
    remote_eligibility_clarity = "remote_eligibility_clarity"
    location_clarity = "location_clarity"


class Severity(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    nice = "nice"


class FindingResolution(StrEnum):
    open = "open"
    applied = "applied"
    dismissed = "dismissed"


SEVERITY_PENALTY = {Severity.critical: 30, Severity.high: 18, Severity.medium: 9, Severity.nice: 3}

# Platforms that carry a public professional profile the audit engine knows how to check.
PROFILE_PLATFORMS: tuple[Platform, ...] = (
    Platform.linkedin,
    Platform.wellfound,
    Platform.upwork,
    Platform.toptal,
    Platform.hh,
    Platform.indeed,
    Platform.getmatch,
)
