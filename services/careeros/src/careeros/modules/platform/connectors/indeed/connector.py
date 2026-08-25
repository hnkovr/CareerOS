"""Indeed: paste-only connector (ADR-005).

The Publisher API was discontinued and the site is JS-rendered behind bot protection, so nothing
is ever fetched: the user copies the Indeed Profile page, a search-results page or
"My jobs → Applied" and the parsers in ``parsers.py`` map the text onto the platform DTOs.
Job-alert e-mails arrive through the inbox (P1) — declared as ``email_fallback``.
"""

from __future__ import annotations

from urllib.parse import urlencode

from careeros.modules.platform.base import BaseConnector
from careeros.modules.platform.connectors.indeed import parsers
from careeros.modules.platform.enums import AuthKind, SyncMethod
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    JobQuery,
    ProfileRead,
)
from careeros.modules.vault.enums import Platform


class Connector(BaseConnector):
    platform = Platform.indeed

    def search_url(self, query: JobQuery) -> str | None:
        params: dict[str, str] = {}
        if query.text:
            params["q"] = query.text
        location = query.location or ("Remote" if query.remote else None)
        if location:
            params["l"] = location
        return "https://www.indeed.com/jobs" + ("?" + urlencode(params) if params else "")

    def profile_url(self, handle: str | None = None) -> str | None:
        return None  # Indeed profiles have no public handle-based URL

    capabilities = Capabilities(
        platform=Platform.indeed,
        profile=[SyncMethod.paste],
        jobs=[SyncMethod.paste],
        applications=[SyncMethod.paste],
        official_api=False,
        auth=AuthKind.none,
        email_fallback=True,
        notes=(
            "Publisher API discontinued; the site is never fetched. Paste your Indeed "
            "profile/resume, a job list, or 'My jobs → Applied'; job-alert emails arrive via "
            "the inbox (P1)."
        ),
    )

    def parse_profile_text(self, text: str) -> ProfileRead:
        return parsers.parse_profile(text)

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return parsers.parse_jobs(text)

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return parsers.parse_applications(text)
