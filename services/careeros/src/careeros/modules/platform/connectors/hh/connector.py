"""hh.ru connector: official OAuth2 JSON API + Russian paste parsers (ADR-004 / ADR-005).

API tier — ``GET /resumes/mine`` + ``GET /resumes/{id}`` (profile), ``GET /vacancies`` or
``GET /resumes/{id}/similar_vacancies`` (jobs), ``GET /negotiations`` (application statuses).
Vacancy search works without a token; resumes, similar-vacancies and negotiations need the user's
OAuth token from ``ctx.tokens``. Paste tier — see ``parsers.py``.

Read-one (ADR-015) — ``GET /vacancies/{id}`` behind a URL the user gives, with the user's token
when connected, else an application token (``grant_type=client_credentials``), else anonymously.
Anonymous reads were refused with ``403 {"errors":[{"type":"forbidden"}]}`` on 2026-08-26, so the
403 branch carries an actionable message instead of a bare failure. hh's own HTML is never
fetched: it is served behind a WAF/captcha challenge and working around that is forbidden
(ADR-005) — ``jina`` and ``wayback`` are the declared fallbacks.
"""

from __future__ import annotations

import json
import re
from time import perf_counter
from typing import Any
from urllib.parse import urlencode, urlsplit

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.platform.base import (
    BaseConnector,
    ConnectorContext,
    NotConnected,
    PlatformError,
    UpstreamError,
)
from careeros.modules.platform.connectors.hh import hosts, mapping
from careeros.modules.platform.connectors.hh import parsers as hh_parsers
from careeros.modules.platform.connectors.hh.client import (
    MAX_DETAIL_FETCHES,
    MAX_NEGOTIATION_PAGES,
    MAX_PER_PAGE,
    HHClient,
)
from careeros.modules.platform.enums import AccessMode, AuthKind, FetchStrategy, SyncMethod
from careeros.modules.platform.fetch.artifact import FetchArtifact
from careeros.modules.platform.fetch.extract.text import as_json_value
from careeros.modules.platform.schemas import (
    AccountInfo,
    ApplicationObservationIn,
    Capabilities,
    DoctorCheck,
    FieldEvidence,
    JobPosting,
    JobQuery,
    OAuthConfig,
    ProfileRead,
)
from careeros.modules.platform.sources import (
    CanonicalSource,
    DetectionResult,
    SourceKind,
    SourceRef,
    canonical_source,
    is_http_url,
)
from careeros.modules.platform.tokens import OAuthTokens, client_credentials
from careeros.modules.vault.enums import Platform

log = get_logger(__name__)

AUTHORIZE_URL = "https://hh.ru/oauth/authorize"
TOKEN_URL = "https://api.hh.ru/token"

# ---- read one vacancy (ADR-015)
#: ``/vacancy/136537758``, ``/vacancy/136537758/``, ``…?from=share_ios`` — nothing else is a job.
VACANCY_PATH = re.compile(r"^/vacancy/(\d+)/?$")
#: A URL shape this specific leaves no doubt about the provider (the generic connector answers 0.1).
READ_CONFIDENCE = 0.95
#: The vacancy used in the research record; ``doctor`` falls back to it when the search probe fails.
REFERENCE_VACANCY_ID = "136537758"
FORBIDDEN_MESSAGE = (
    "hh API refused anonymous/app access (403) — connect with "
    "`careeros platform connect hh` or set CAREEROS_HH_CLIENT_ID/SECRET"
)
NO_HTML_DETAIL = "direct HTML: not used (WAF/captcha, ADR-005) — fallbacks are jina and wayback"
#: Evidence source per fallback strategy: a transformed copy and a historical copy are not the
#: board speaking, and must never outrank ``board_api`` fields when sources are merged (ADR-016).
FALLBACK_EVIDENCE: dict[FetchStrategy, str] = {
    FetchStrategy.jina: "aggregator",
    FetchStrategy.wayback: "archive",
}
API_EVIDENCE_SOURCE = "board_api"
API_EVIDENCE_CONFIDENCE = 0.95
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

    def __init__(self) -> None:
        # Application token (client_credentials) reused while valid: one token request per
        # process, not per read. In memory only — the token store holds user grants.
        self._app_tokens: OAuthTokens | None = None

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
    # Every hh front-end; ``www.``/``m.``/city subdomains resolve through ``hosts.find`` (the
    # default host matcher is not used — ``detect`` below insists on a ``/vacancy/<id>`` path).
    detect_hosts = hosts.HOST_NAMES
    capabilities = Capabilities(
        platform=Platform.hh,
        profile=[SyncMethod.api, SyncMethod.paste],
        jobs=[SyncMethod.api, SyncMethod.paste],
        applications=[SyncMethod.api, SyncMethod.paste],
        read_job=[FetchStrategy.api, FetchStrategy.jina, FetchStrategy.wayback],
        access=AccessMode.public,
        official_api=True,
        auth=AuthKind.oauth2,
        email_fallback=False,
        notes=(
            "Full official API (OAuth2). Vacancy search works without a token; "
            "resumes and negotiations need the user token. Reading one vacancy by URL goes "
            "through the API (user token, else an application token — anonymous reads answered "
            "403 on 2026-08-26); hh's own HTML is never fetched (WAF/captcha, ADR-005), so the "
            "fallbacks are Jina Reader and the Wayback Machine."
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
        probe_id: str | None = None
        try:
            data = await self._client(ctx, auth=False).vacancies({"per_page": 1}, retries=0)
            probe_id = self._first_vacancy_id(data)
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
        checks.extend(await self._read_checks(ctx, probe_id))
        return checks

    # ---- read-one health (ADR-015 §57)
    async def _read_checks(self, ctx: ConnectorContext, probe_id: str | None) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = [self._detect_check()]
        if probe_id is None:
            checks.append(
                DoctorCheck(
                    name="read_api",
                    ok=False,
                    detail="not probed — GET /vacancies did not answer",
                    fix="fix api_reachable first, then re-run doctor",
                )
            )
        else:
            checks.append(await self._read_api_check(ctx, probe_id))
        checks.append(DoctorCheck(name="direct_html", ok=True, detail=NO_HTML_DETAIL))
        return checks

    def _detect_check(self) -> DoctorCheck:
        probe = f"https://hh.ru/vacancy/{REFERENCE_VACANCY_ID}?from=share_ios"
        hit = self.detect(probe)
        if hit is None:
            return DoctorCheck(
                name="read_detect",
                ok=False,
                detail=f"{probe} was not recognised as an hh vacancy",
                fix="report this: connectors/hh/hosts.py or VACANCY_PATH is out of date",
            )
        return DoctorCheck(
            name="read_detect",
            ok=True,
            detail=(
                f"{probe} → {hit.canonical.canonical_url} "
                f"(id={hit.canonical.external_id}, confidence={hit.confidence}); "
                f"{len(hosts.HOST_NAMES)} hosts"
            ),
        )

    async def _read_api_check(self, ctx: ConnectorContext, probe_id: str) -> DoctorCheck:
        """Classify one real ``GET /vacancies/{id}`` — the read path's own probe, not the search."""
        artifact = await self.fetch_job_api(ctx, self._canonical(hosts.canonical(), probe_id))
        detail = f"GET /vacancies/{probe_id} → {artifact.status_code or '-'}"
        if artifact.error_type is None:
            return DoctorCheck(name="read_api", ok=True, detail=f"{detail} ok")
        if artifact.error_type == "not_found":
            return DoctorCheck(
                name="read_api",
                ok=True,
                detail=f"{detail} not_found (the probe vacancy is closed; the read path answered)",
            )
        return DoctorCheck(
            name="read_api",
            ok=False,
            detail=f"{detail} {artifact.error_type}: {artifact.error_message or ''}".strip(),
            fix=(
                FORBIDDEN_MESSAGE
                if artifact.error_type == "forbidden"
                else "retry later; hh may be degraded or rate-limiting this workstation"
            ),
        )

    @staticmethod
    def _first_vacancy_id(data: dict[str, Any]) -> str | None:
        items = data.get("items")
        if not isinstance(items, list):
            return None
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "").isdigit():
                return str(item["id"])
        return None

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

    # ------------------------------------------------------------------ read one vacancy (ADR-015)
    def detect(self, url: str) -> DetectionResult | None:
        """``https://<hh site>/vacancy/<digits>`` — anything else on hh is not one vacancy."""
        if not is_http_url(url):
            return None
        parts = urlsplit(url.strip())
        site = hosts.find(parts.netloc)
        if site is None:
            return None
        match = VACANCY_PATH.match(parts.path)
        if match is None:
            return None  # search pages, resumes, employers: not a job read
        return DetectionResult(
            platform=self.platform,
            confidence=READ_CONFIDENCE,
            canonical=self._canonical(site, match.group(1)),
        )

    def canonicalize(self, source: SourceRef | str) -> CanonicalSource:
        """``…/vacancy/<id>?from=share_ios`` → ``https://<site>/vacancy/<id>`` + ``external_id``.

        City subdomains collapse into ``hh.ru``; a regional front-end keeps its own host, because
        the same vacancy id is read with ``host=<site>`` (see ``hosts.api_params_for``). A
        ``provider_id`` reference (a bare vacancy id) is canonicalised against ``hh.ru`` unless
        ``metadata["host"]`` names another site.
        """
        ref = SourceRef(value=source) if isinstance(source, str) else source
        locale = ref.metadata.get("locale")
        locale = locale if isinstance(locale, str) else None
        if ref.kind == SourceKind.provider_id:
            vacancy_id = ref.value.strip()
            if not vacancy_id.isdigit():
                raise ValueError(f"hh: {ref.value!r} is not a vacancy id")
            hint = ref.metadata.get("host")
            site = hosts.find(hint) if isinstance(hint, str) else None
            return self._canonical(
                site or hosts.canonical(), vacancy_id, locale=locale, private=ref.is_private
            )
        url = ref.url()
        if url is None:
            raise ValueError(f"hh: {ref.kind} reference without a URL")
        parts = urlsplit(url.strip())
        site = hosts.find(parts.netloc)
        match = VACANCY_PATH.match(parts.path)
        if site is not None and match is not None:
            return self._canonical(site, match.group(1), locale=locale, private=ref.is_private)
        # not a vacancy URL (a search page pasted by hand): keep the generic normalisation
        return canonical_source(self.platform, url, locale=locale, private=ref.is_private)

    def _canonical(
        self,
        site: hosts.HHHost,
        vacancy_id: str,
        *,
        locale: str | None = None,
        private: bool = False,
    ) -> CanonicalSource:
        return CanonicalSource(
            platform=self.platform,
            external_id=vacancy_id,
            canonical_url=f"https://{site.host}/vacancy/{vacancy_id}",
            locale=locale,
            host=site.host,
            private=private,
        )

    async def fetch_job_api(self, ctx: ConnectorContext, source: CanonicalSource) -> FetchArtifact:
        """``GET /vacancies/{id}`` → a ``FetchArtifact`` that states what actually happened.

        No status collapses into ``None``: 200 carries the payload, 403/404/429/5xx and malformed
        bodies carry an ``error_type`` the chain records as an attempt and the owner can act on.
        """
        started = perf_counter()
        vacancy_id = self._vacancy_id(source)

        def artifact(**fields: Any) -> FetchArtifact:
            base: dict[str, Any] = {
                "provider": self.platform,
                "strategy": FetchStrategy.api,
                "requested_url": source.canonical_url,
                "external_id": vacancy_id,
                "fetched_at": ctx.now,
                "duration_ms": int((perf_counter() - started) * 1000),
            }
            return FetchArtifact.model_validate({**base, **fields})

        if vacancy_id is None:
            return artifact(
                error_type="not_a_vacancy",
                error_message=f"{source.canonical_url} is not an hh vacancy URL",
            )

        client = HHClient(
            ctx.http,
            user_agent=ctx.settings.platform_user_agent,
            auth=await self._read_auth(ctx),
        )
        try:
            status, body, content_type = await client.vacancy_raw(
                vacancy_id, params=hosts.api_params_for(source.host)
            )
        except NotConnected as exc:
            return artifact(status_code=403, error_type="forbidden", error_message=str(exc))
        except UpstreamError as exc:
            timed_out = exc.status_code is None and "timeout" in exc.detail.lower()
            return artifact(
                status_code=exc.status_code,
                error_type="timeout" if timed_out else "network",
                error_message=exc.detail,
            )

        if status == 200:
            try:
                payload = json.loads(body)
            except ValueError:
                return artifact(
                    status_code=status,
                    content_type=content_type,
                    error_type="malformed",
                    error_message="GET /vacancies/{id} answered 200 with a non-JSON body",
                )
            if not isinstance(payload, dict):
                return artifact(
                    status_code=status,
                    content_type=content_type,
                    error_type="malformed",
                    error_message="GET /vacancies/{id}: expected a JSON object",
                )
            return artifact(
                status_code=status,
                content_type=content_type,
                raw_json=payload,
                flags=["job_closed"] if mapping.is_archived(payload) else [],
            )
        error_type, message = self._read_failure(status, body, vacancy_id)
        return artifact(
            status_code=status,
            content_type=content_type,
            error_type=error_type,
            error_message=message,
            flags=["job_closed"] if error_type == "not_found" else [],
        )

    @staticmethod
    def _read_failure(status: int, body: str, vacancy_id: str) -> tuple[str, str]:
        """Non-2xx status → ``(error_type, message)``; the message is what the owner can act on."""
        if status == 403:
            if '"oauth"' in body:
                return "forbidden", (
                    "hh rejected the token (403 oauth) — careeros platform refresh hh, "
                    "or connect again"
                )
            return "forbidden", FORBIDDEN_MESSAGE
        if status in (404, 410):
            return "not_found", (
                f"hh has no vacancy {vacancy_id} ({status}) — closed, archived or never public"
            )
        if status == 429:
            return "rate_limited", "hh answered 429 after the retries (Retry-After honoured)"
        if status >= 500:
            return "upstream", f"hh answered {status}"
        if status == 401:
            return "forbidden", "hh rejected the token (401) — careeros platform refresh hh"
        return "http_error", f"hh answered {status}: {body[:120]}"

    def _vacancy_id(self, source: CanonicalSource) -> str | None:
        if source.external_id and source.external_id.isdigit():
            return source.external_id
        match = VACANCY_PATH.match(urlsplit(source.canonical_url).path)
        return match.group(1) if match else None

    async def _read_auth(self, ctx: ConnectorContext) -> dict[str, str]:
        """Bearer for a read: the user's token, else an application token, else anonymous."""
        if ctx.tokens is not None:
            return self.auth_headers(ctx)
        tokens = await self._application_token(ctx)
        if tokens is None:
            return {}
        return {"Authorization": f"Bearer {tokens.access_token.get_secret_value()}"}

    async def _application_token(self, ctx: ConnectorContext) -> OAuthTokens | None:
        """Cached ``client_credentials`` token, or ``None`` when hh app credentials are absent.

        A failed token request is a warning, not an error: the read still goes out anonymously
        and the 403 branch explains what to configure.
        """
        creds = client_credentials(ctx.settings, self.platform)
        if creds is None:
            return None
        cached = self._app_tokens
        if cached is not None and not cached.is_expired(ctx.now):
            return cached
        try:
            tokens = await self._client(ctx, auth=False).app_token(*creds)
        except PlatformError as exc:
            ctx.warnings.append(
                f"hh: application token request failed ({exc}) — reading anonymously"
            )
            log.warning("platform.hh.app_token_failed", error=str(exc))
            return None
        self._app_tokens = tokens
        log.info("platform.hh.app_token", expires_at=str(tokens.expires_at))
        return tokens

    def extract_job(self, artifact: FetchArtifact) -> JobPosting:
        """API payload → the search mapping plus provenance; fallbacks → the shared extractors."""
        if artifact.strategy == FetchStrategy.api and isinstance(artifact.raw_json, dict):
            return self._posting_from_vacancy(artifact, artifact.raw_json)
        posting = super().extract_job(artifact)
        evidence_source = FALLBACK_EVIDENCE.get(artifact.strategy)
        if evidence_source is None:
            return posting
        return posting.model_copy(
            update={
                "field_evidence": [
                    e.model_copy(update={"source": evidence_source}) for e in posting.field_evidence
                ]
            }
        )

    def _posting_from_vacancy(self, artifact: FetchArtifact, vacancy: dict[str, Any]) -> JobPosting:
        posting = mapping.vacancy_detail_to_job(vacancy)
        url = posting.url or artifact.resolved_url or artifact.requested_url
        published = mapping.parse_ts(vacancy.get("published_at")) or mapping.parse_ts(
            vacancy.get("created_at")
        )
        expires = mapping.parse_ts(vacancy.get("expires_at")) or mapping.parse_ts(
            vacancy.get("valid_through")
        )
        return posting.model_copy(
            update={
                "canonical_url": artifact.requested_url,
                "external_id": posting.external_id or artifact.external_id,
                "published_at": published,
                "expires_at": expires,
                "is_archive": False,
                "field_evidence": self._api_evidence(
                    posting, vacancy, url=url, observed_at=artifact.fetched_at
                ),
            }
        )

    @staticmethod
    def _api_evidence(
        posting: JobPosting,
        vacancy: dict[str, Any],
        *,
        url: str | None,
        observed_at: Any,
    ) -> list[FieldEvidence]:
        """Per-field provenance for what the board itself stated (ADR-016).

        Only fields hh actually returned get evidence — everything else stays derived at ingest.
        """
        extraction = posting.extraction
        stated: dict[str, Any] = {
            "title": posting.title,
            "company": posting.company,
            "location": posting.location,
            "compensation": as_json_value(extraction.compensation) if extraction else None,
            "employment_type": str(extraction.employment_type)
            if extraction and extraction.employment_type
            else None,
            "remote_policy": str(extraction.remote_policy)
            if extraction and extraction.remote_policy
            else None,
            "experience": mapping.strip_tags((vacancy.get("experience") or {}).get("name"))
            if isinstance(vacancy.get("experience"), dict)
            else None,
            "technologies": list(extraction.technologies)
            if extraction and extraction.technologies
            else None,
        }
        return [
            FieldEvidence(
                field=name,
                value=value,
                source=API_EVIDENCE_SOURCE,
                source_url=url,
                observed_at=observed_at,
                confidence=API_EVIDENCE_CONFIDENCE,
            )
            for name, value in stated.items()
            if value not in (None, "", [], {})
        ]

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
