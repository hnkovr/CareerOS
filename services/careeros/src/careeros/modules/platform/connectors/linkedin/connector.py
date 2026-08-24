"""LinkedIn: stub connector — paste-only via the shared generic parsers.

Replaced by the platform-specific implementation (see docs/superpowers/plans).
"""

from __future__ import annotations

from careeros.modules.platform import parsers
from careeros.modules.platform.base import BaseConnector
from careeros.modules.platform.enums import SyncMethod
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    ProfileRead,
)
from careeros.modules.vault.enums import Platform


class Connector(BaseConnector):
    platform = Platform.linkedin
    capabilities = Capabilities(
        platform=Platform.linkedin,
        profile=[SyncMethod.paste],
        jobs=[SyncMethod.paste],
        applications=[SyncMethod.paste],
        email_fallback=True,
        notes="stub: generic paste parsing only",
    )

    def parse_profile_text(self, text: str) -> ProfileRead:
        return parsers.generic_profile(text, self.platform)

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return parsers.generic_jobs(text, self.platform)

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return parsers.generic_applications(text, self.platform)
