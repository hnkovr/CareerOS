"""Upwork connector: official GraphQL API (approved API key + OAuth2) plus a paste path for
every capability (ADR-004 / ADR-005 / ADR-011). No HTML fetching, no browser, no cookies.
"""

from __future__ import annotations

from urllib.parse import urlencode

from careeros.core.config import Settings
from careeros.modules.platform.base import (
    BaseConnector,
    ConnectorContext,
    NotConnected,
    PlatformError,
)
from careeros.modules.platform.connectors.upwork import mapping, parsers, queries
from careeros.modules.platform.connectors.upwork.client import UpworkClient
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

AUTHORIZE_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
API_KEYS_URL = "https://www.upwork.com/developer/keys"


class Connector(BaseConnector):
    platform = Platform.upwork

    def search_url(self, query: JobQuery) -> str | None:
        params: dict[str, str] = {}
        if query.text:
            params["q"] = query.text
        return "https://www.upwork.com/nx/search/jobs/" + (
            "?" + urlencode(params) if params else ""
        )

    def profile_url(self, handle: str | None = None) -> str | None:
        return f"https://www.upwork.com/freelancers/{handle}" if handle else None

    capabilities = Capabilities(
        platform=Platform.upwork,
        profile=[SyncMethod.api, SyncMethod.paste],
        jobs=[SyncMethod.api, SyncMethod.paste],
        applications=[SyncMethod.api, SyncMethod.paste],
        official_api=True,
        auth=AuthKind.oauth2,
        email_fallback=True,
        notes="GraphQL API needs an approved Upwork API key; paste works without it.",
    )

    # ---- auth

    def oauth_config(self, settings: Settings) -> OAuthConfig | None:
        creds = client_credentials(settings, Platform.upwork)
        if creds is None:
            raise NotConnected(
                Platform.upwork,
                "set CAREEROS_UPWORK_CLIENT_ID / CAREEROS_UPWORK_CLIENT_SECRET "
                f"(API key at {API_KEYS_URL})",
            )
        return OAuthConfig(
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            client_id=creds[0],
            client_secret=creds[1],
            scopes=[],
            redirect_uri=f"{settings.platform_oauth_redirect_base}/upwork/callback",
            token_auth="body",
        )

    def _client(self, ctx: ConnectorContext) -> UpworkClient:
        self.require_tokens(ctx)
        return UpworkClient(ctx, self.auth_headers(ctx))

    # ---- api tier

    async def read_profile(self, ctx: ConnectorContext) -> ProfileRead:
        user = await self._client(ctx).freelancer_profile()
        return mapping.map_profile(user, captured_at=ctx.now)

    async def search_jobs(self, ctx: ConnectorContext, query: JobQuery) -> list[JobPosting]:
        nodes = await self._client(ctx).search_jobs(query)
        jobs = [mapping.map_job(node) for node in nodes]
        since = query.posted_since
        if since is not None:  # the search filter has no date field → filter client-side
            jobs = [j for j in jobs if j.posted_at is None or j.posted_at.date() >= since]
        return jobs[: query.limit]

    async def application_statuses(self, ctx: ConnectorContext) -> list[ApplicationObservationIn]:
        nodes = await self._client(ctx).proposals()
        return [mapping.map_proposal(node) for node in nodes]

    # ---- paste tier

    def parse_profile_text(self, text: str) -> ProfileRead:
        return parsers.parse_profile(text)

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return parsers.parse_jobs(text)

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return parsers.parse_proposals(text)

    # ---- health

    async def whoami(self, ctx: ConnectorContext) -> AccountInfo:
        user = await self._client(ctx).user_info()
        return mapping.map_account(user)

    async def doctor(self, ctx: ConnectorContext) -> list[DoctorCheck]:
        """Generic checks + (with tokens) a schema introspection telling which GraphQL root
        fields used by ``queries.py`` exist on the live schema. A failed probe never raises."""
        checks = self.generic_checks(ctx)
        if ctx.tokens is None:
            return checks
        try:
            fields = set(await UpworkClient(ctx, self.auth_headers(ctx)).introspect_query_fields())
        except (PlatformError, ValueError, TypeError, KeyError) as exc:
            checks.append(
                DoctorCheck(
                    name="graphql:introspection",
                    ok=False,
                    detail=str(exc)[:200],
                    fix=(
                        "introspection failed or is disabled for this API key; probe the documents "
                        "directly: careeros platform profile upwork --api --dry-run"
                    ),
                )
            )
            return checks
        for root, documents in queries.ROOT_FIELDS.items():
            present = root in fields
            checks.append(
                DoctorCheck(
                    name=f"graphql:{root}",
                    ok=present,
                    detail=(
                        f"present on live schema (used by {documents})"
                        if present
                        else f"missing on live schema (used by {documents})"
                    ),
                    fix=None
                    if present
                    else (
                        f"root field renamed or not granted to this key — update {documents} in "
                        "connectors/upwork/queries.py (VERIFY LIVE) or request the matching "
                        "API permission"
                    ),
                )
            )
        return checks
