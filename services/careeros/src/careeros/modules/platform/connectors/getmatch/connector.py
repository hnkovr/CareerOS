"""getmatch (getmatch.ru): paste-only connector — RU + EN page text, never fetched (ADR-005).

No public API and no export exist. The user copies «Мой профиль», the «Вакансии» list or
«Отклики» as text; :mod:`.parsers` turns them into DTOs without inventing values. Digest
e-mails are a P1 Inbox concern (``email_fallback``).
"""

from __future__ import annotations

from datetime import datetime

from careeros.modules.platform.base import BaseConnector
from careeros.modules.platform.connectors.getmatch import parsers as gm
from careeros.modules.platform.enums import AuthKind, SyncMethod
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    ProfileRead,
)
from careeros.modules.vault.enums import Platform


class Connector(BaseConnector):
    platform = Platform.getmatch
    capabilities = Capabilities(
        platform=Platform.getmatch,
        profile=[SyncMethod.paste],
        jobs=[SyncMethod.paste],
        applications=[SyncMethod.paste],
        official_api=False,
        email_fallback=True,
        auth=AuthKind.none,
        notes=(
            "No public API; the site is never fetched. Paste your profile (Мой профиль), the "
            "vacancies list, or Отклики; digest emails arrive via the inbox (P1)."
        ),
    )

    # ``now`` is optional so tests (and future callers) can pin relative dates ('2 дня назад').

    def parse_profile_text(self, text: str, *, now: datetime | None = None) -> ProfileRead:
        return gm.parse_profile(text, now=now)

    def parse_jobs_text(self, text: str, *, now: datetime | None = None) -> list[JobPosting]:
        return gm.parse_vacancies(text, now=now)

    def parse_applications_text(
        self, text: str, *, now: datetime | None = None
    ) -> list[ApplicationObservationIn]:
        return gm.parse_responses(text, now=now)
