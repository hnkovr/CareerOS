"""Wellfound connector — paste-only (ADR-005): no public API, the site is never fetched.

The user copies the page text (profile, jobs list / job page, "Applied" tab) and the parsers in
``parsers.py`` map it onto the platform DTOs. Job-alert e-mails are a P1 inbox concern.
"""

from __future__ import annotations

from careeros.modules.platform.base import BaseConnector
from careeros.modules.platform.connectors.wellfound import parsers
from careeros.modules.platform.enums import AuthKind, SyncMethod
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    ProfileRead,
)
from careeros.modules.vault.enums import Platform


class Connector(BaseConnector):
    platform = Platform.wellfound
    capabilities = Capabilities(
        platform=Platform.wellfound,
        profile=[SyncMethod.paste],
        jobs=[SyncMethod.paste],
        applications=[SyncMethod.paste],
        official_api=False,
        email_fallback=True,
        auth=AuthKind.none,
        notes=(
            "No public API; the site is never fetched. Paste your profile, a job list, or the "
            "Applied tab; job-alert emails arrive via the inbox (P1)."
        ),
    )

    def parse_profile_text(self, text: str) -> ProfileRead:
        return parsers.parse_profile(text)

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return parsers.parse_jobs(text)

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return parsers.parse_applications(text)
