"""hh.ru connector: official OAuth2 JSON API + Russian paste parsers (ADR-004 / ADR-005).

API tier — ``GET /resumes/mine`` + ``GET /resumes/{id}`` (profile), ``GET /vacancies`` or
``GET /resumes/{id}/similar_vacancies`` (jobs), ``GET /negotiations`` (application statuses).
Vacancy search works without a token; resumes, similar-vacancies and negotiations need the user's
OAuth token from ``ctx.tokens``. Paste tier — see ``parsers.py``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.platform.base import (
    BaseConnector,
    ConnectorContext,
    NotConnected,
    PlatformError,
    UpstreamError,
)
from careeros.modules.platform.connectors.hh import mapping
from careeros.modules.platform.connectors.hh import parsers as hh_parsers
from careeros.modules.platform.connectors.hh.client import (
    MAX_DETAIL_FETCHES,
    MAX_NEGOTIATION_PAGES,
    MAX_PER_PAGE,
    HHClient,
)
from careeros.modules.platform.enums import AuthKind, SyncMethod
from careeros.modules.platform.schemas import (
    AccountInfo,
    ApplicationObservationIn,
    Capabilities,
    DoctorCheck,
    JobPosting,
    JobQuery,
    OAuthConfig,
    ProfileRead,
)
from careeros.modules.platform.tokens import client_credentials
from careeros.modules.vault.enums import Platform

log = get_logger(__name__)

AUTHORIZE_URL = "https://hh.ru/oauth/authorize"
TOKEN_URL = "https://api.hh.ru/token"
# ``JobQuery.extra`` keys forwarded verbatim to GET /vacancies (ids from /dictionaries, /areas …).
PASSTHROUGH_EXTRA = (
    "area",
    "order_by",
    "search_field",
    "experience",
    "professional_role",
    "employer_id",
    "label",
    "period",
    "work_format",
    "employment_form",
)


class Connector(BaseConnector):
    platform = Platform.hh

    def search_url(self, query: JobQuery) -> str | None:
        params: dict[str, str] = {}
        if query.text:
            params["text"] = query.text
        if query.remote:
            params["schedule"] = "remote"
        if query.extra.get("area"):
            params["area"] = str(query.extra["area"])
        if query.salary_min:
            params["salary"] = str(int(query.salary_min))
        return "https://hh.ru/search/vacancy" + ("?" + urlencode(params) if params else "")

    def profile_url(self, handle: str | None = None) -> str | None:
        return f"https://hh.ru/resume/{handle}" if handle else None

    jobs_without_token = True  # GET /vacancies is public; resumes/negotiations need the token
    capabilities = Capabilities(
        platform=Platform.hh,
        profile=[SyncMethod.api, SyncMethod.paste],
        jobs=[SyncMethod.api, SyncMethod.paste],
        applications=[SyncMethod.api, SyncMethod.paste],
        official_api=True,
        auth=AuthKind.oauth2,
        email_fallback=False,
        notes=(
            "Full official API (OAuth2). Vacancy search works without a token; "
            "resumes and negotiations need the user token."
        ),
    )

    # ------------------------------------------------------------------ helpers
    def _client(self, ctx: ConnectorContext, *, auth: bool = True) -> HHClient:
        return HHClient(
            ctx.http,
            user_agent=ctx.settings.platform_user_agent,
            auth=self.auth_headers(ctx) if auth else None,
        )

    async def _newest_resume_id(self, client: HHClient) -> str:
        """The most recently updated resume of the account (list order is not stable)."""
        items = await client.resumes_mine()
        if not items:
            raise PlatformError(
                "hh: the account has no resumes — create one at https://hh.ru/applicant/resumes"
            )

        def updated(item: dict[str, Any]) -> float:
            ts = mapping.parse_ts(item.get("updated_at"))
            return ts.timestamp() if ts else float("-inf")

        resume_id = max(items, key=updated).get("id")
        if not isinstance(resume_id, str) or not resume_id:
            raise UpstreamError(self.platform, None, "GET /resumes/mine: item without id")
        return resume_id

    # ------------------------------------------------------------------ auth
    def oauth_config(self, settings: Settings) -> OAuthConfig | None:
        creds = client_credentials(settings, Platform.hh)
        if creds is None:
            raise NotConnected(
                Platform.hh,
                "set CAREEROS_HH_CLIENT_ID / CAREEROS_HH_CLIENT_SECRET (app at https://dev.hh.ru)",
            )
        return OAuthConfig(
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            client_id=creds[0],
            client_secret=creds[1],
            scopes=[],
            redirect_uri=f"{settings.platform_oauth_redirect_base}/hh/callback",
            token_auth="body",
        )

    async def whoami(self, ctx: ConnectorContext) -> AccountInfo:
        self.require_tokens(ctx)
        return mapping.me_to_account(await self._client(ctx).me())

    async def doctor(self, ctx: ConnectorContext) -> list[DoctorCheck]:
        checks = self.generic_checks(ctx)
        try:
            data = await self._client(ctx, auth=False).vacancies({"per_page": 1}, retries=0)
            checks.append(
                DoctorCheck(
                    name="api_reachable",
                    ok=True,
                    detail=f"GET /vacancies?per_page=1 → found={data.get('found')}",
                )
            )
        except PlatformError as exc:
            checks.append(
                DoctorCheck(
                    name="api_reachable",
                    ok=False,
                    detail=str(exc),
                    fix=(
                        "check network access to https://api.hh.ru and "
                        "CAREEROS_PLATFORM_USER_AGENT (hh rejects requests without an "
                        "identifying User-Agent)"
                    ),
                )
            )
        if ctx.tokens is not None:
            try:
                info = await self.whoami(ctx)
                checks.append(
                    DoctorCheck(
                        name="me",
                        ok=True,
                        detail=f"GET /me → {info.label or info.account_id or '?'}",
                    )
                )
            except NotConnected as exc:
                checks.append(
                    DoctorCheck(
                        name="me",
                        ok=False,
                        detail=str(exc),
                        fix="careeros platform refresh hh (or connect again)",
                    )
                )
            except PlatformError as exc:
                checks.append(
                    DoctorCheck(
                        name="me",
                        ok=False,
                        detail=str(exc),
                        fix="retry later; hh may be degraded (careeros platform doctor hh --json)",
                    )
                )
        return checks

    # ------------------------------------------------------------------ profile
    async def read_profile(self, ctx: ConnectorContext) -> ProfileRead:
        self.require_tokens(ctx)
        client = self._client(ctx)
        resume = await client.resume(await self._newest_resume_id(client))
        return mapping.resume_to_profile(resume, captured_at=ctx.now)

    def parse_profile_text(self, text: str) -> ProfileRead:
        return hh_parsers.parse_profile(text)

    # ------------------------------------------------------------------ jobs
    async def search_jobs(self, ctx: ConnectorContext, query: JobQuery) -> list[JobPosting]:
        """Text search (public) or, with a token and no text / ``extra.similar_to_resume``,
        vacancies similar to the newest resume. ``extra.full=True`` fetches up to 20 details."""
        per_page = min(query.limit, MAX_PER_PAGE)
        similar = query.extra.get("similar_to_resume")
        text = (query.text or "").strip()
        if similar or not text:
            if ctx.tokens is None:
                raise NotConnected(
                    self.platform,
                    "vacancy search needs --query text; the similar-to-resume search "
                    "(no text) needs a connected account",
                )
            client = self._client(ctx)
            resume_id = (
                similar
                if isinstance(similar, str) and similar
                else await self._newest_resume_id(client)
            )
            page = await client.similar_vacancies(resume_id, per_page=per_page)
        else:
            client = self._client(ctx, auth=ctx.tokens is not None)
            page = await client.vacancies(await self._search_params(client, query, text, per_page))

        items = [i for i in (page.get("items") or []) if isinstance(i, dict)][: query.limit]
        full = query.extra.get("full") is True
        jobs: list[JobPosting] = []
        for idx, item in enumerate(items):
            detail: dict[str, Any] | None = None
            detail_error: str | None = None
            if full and idx < MAX_DETAIL_FETCHES and item.get("id"):
                try:
                    detail = await client.vacancy(str(item["id"]))
                except PlatformError as exc:
                    detail_error = str(exc)
                    log.warning(
                        "platform.hh.vacancy_detail_failed",
                        vacancy_id=item.get("id"),
                        error=str(exc),
                    )
            jobs.append(mapping.vacancy_to_job(item, detail=detail, detail_error=detail_error))
        return jobs

    async def _search_params(
        self, client: HHClient, query: JobQuery, text: str, per_page: int
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "text": text,
            "per_page": per_page,
            "page": 0,
            "order_by": "publication_time",
        }
        for key in PASSTHROUGH_EXTRA:
            value = query.extra.get(key)
            if value not in (None, ""):
                params[key] = value
        if "area" not in params and query.location:
            area_id = await self._area_id(client, query.location)
            if area_id:
                params["area"] = area_id
            else:
                log.warning("platform.hh.area_not_found", location=query.location)
        if query.remote:
            params.setdefault("schedule", "remote")  # legacy filter  # VERIFY LIVE
            params.setdefault("work_format", "REMOTE")  # current ``work_format`` dictionary
        if query.salary_min is not None:
            params["salary"] = int(query.salary_min)
            params["currency"] = mapping.to_hh_currency(query.currency) or "RUR"
        if query.posted_since is not None:
            params["date_from"] = query.posted_since.isoformat()
        return params

    async def _area_id(self, client: HHClient, location: str) -> str | None:
        items = await client.suggest_areas(location.strip())
        area_id = items[0].get("id") if items else None
        return str(area_id) if area_id else None

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return hh_parsers.parse_jobs(text)

    # ------------------------------------------------------------------ applications
    async def application_statuses(self, ctx: ConnectorContext) -> list[ApplicationObservationIn]:
        self.require_tokens(ctx)
        client = self._client(ctx)
        out: list[ApplicationObservationIn] = []
        page = 0
        while page < MAX_NEGOTIATION_PAGES:
            data = await client.negotiations(page)
            for item in data.get("items") or []:
                if isinstance(item, dict):
                    out.append(mapping.negotiation_to_observation(item))
            pages = data.get("pages")
            page += 1
            if page >= (pages if isinstance(pages, int) else 1):
                break
        return out

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return hh_parsers.parse_applications(text)
