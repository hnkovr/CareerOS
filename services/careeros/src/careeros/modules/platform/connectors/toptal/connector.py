"""Toptal connector — talent portal only; paste-tier for every capability.

Toptal offers neither an API nor a data export for talent, and the site is never fetched
(ADR-005). The user copies the page text (public talent profile, portal "Jobs" list, "My
Applications") and the parsers in ``parsers.py`` map it onto the platform DTOs. Portal emails
will arrive through the inbox (P1) — hence ``email_fallback``.
"""

from __future__ import annotations

from careeros.modules.platform.base import BaseConnector
from careeros.modules.platform.connectors.toptal import parsers as toptal
from careeros.modules.platform.enums import AuthKind, SyncMethod
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    ProfileRead,
)
from careeros.modules.vault.enums import Platform


class Connector(BaseConnector):
    platform = Platform.toptal
    capabilities = Capabilities(
        platform=Platform.toptal,
        profile=[SyncMethod.paste],
        jobs=[SyncMethod.paste],
        applications=[SyncMethod.paste],
        official_api=False,
        email_fallback=True,
        auth=AuthKind.none,
        notes=(
            "Talent portal only — no API or export; the site is never fetched. Paste your public "
            "talent profile, the portal job list, or your applied jobs; portal emails arrive via "
            "the inbox (P1)."
        ),
    )

    def parse_profile_text(self, text: str) -> ProfileRead:
        return toptal.parse_profile(text)

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return toptal.parse_jobs(text)

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return toptal.parse_applications(text)
