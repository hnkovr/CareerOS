# ruff: noqa: E501
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.modules.platform.base import (
    BaseConnector,
    CapabilityUnavailable,
    ConnectorContext,
    NotConnected,
)
from careeros.modules.platform.enums import (
    ApplicationStatus,
    AuthKind,
    SyncKind,
    SyncMethod,
    SyncStatus,
)
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    JobQuery,
    OAuthConfig,
    ProfileRead,
    SyncRequest,
)
from careeros.modules.platform.sync import PlatformSyncService
from careeros.modules.platform.tokens import MemoryTokenStore, OAuthTokens
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

NOW = datetime(2026, 8, 25, tzinfo=UTC)


class FakeUpwork(BaseConnector):
    """API + paste for every capability; API answers are canned."""

    platform = Platform.upwork
    capabilities = Capabilities(
        platform=Platform.upwork,
        profile=[SyncMethod.api, SyncMethod.paste],
        jobs=[SyncMethod.api, SyncMethod.paste],
        applications=[SyncMethod.api, SyncMethod.paste],
        official_api=True,
        auth=AuthKind.oauth2,
    )

    async def read_profile(self, ctx: ConnectorContext) -> ProfileRead:
        return ProfileRead(
            platform=self.platform,
            capture_method=CaptureMethod.api,
            headline="Data Platform Consultant",
            skills=["dbt", "BigQuery"],
        )

    def parse_profile_text(self, text: str) -> ProfileRead:
        return ProfileRead(platform=self.platform, headline=text.splitlines()[0], raw_text=text)

    async def search_jobs(self, ctx: ConnectorContext, query: JobQuery) -> list[JobPosting]:
        return [
            JobPosting(
                platform=self.platform,
                external_id="j1",
                url="https://www.upwork.com/jobs/~j1",
                title="dbt migration to BigQuery",
                company="Northwind Commerce",
                raw_text="dbt migration to BigQuery. Senior data engineer, dbt, BigQuery, remote.",
            ),
            JobPosting(
                platform=self.platform,
                external_id="j2",
                url="https://www.upwork.com/jobs/~j2",
                title="ClickHouse cost optimisation",
                company="Lumen Analytics",
                raw_text="ClickHouse cost optimisation. Contract, remote, ClickHouse, Python.",
            ),
        ]

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return [
            JobPosting(platform=self.platform, title=ln, raw_text=ln)
            for ln in text.splitlines()
            if ln
        ]

    async def application_statuses(self, ctx: ConnectorContext) -> list[ApplicationObservationIn]:
        return [
            ApplicationObservationIn(
                platform=self.platform,
                external_id="p1",
                job_title="dbt migration to BigQuery",
                company="Northwind Commerce",
                job_url="https://www.upwork.com/jobs/~j1",
                status_raw="Viewed",
                status=ApplicationStatus.viewed,
            )
        ]

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return [
            ApplicationObservationIn(
                platform=self.platform,
                job_title=text.strip(),
                status_raw="Submitted",
                status=ApplicationStatus.applied,
            )
        ]


class PasteOnly(BaseConnector):
    platform = Platform.toptal
    capabilities = Capabilities(platform=Platform.toptal, profile=[SyncMethod.paste])

    def parse_profile_text(self, text: str) -> ProfileRead:
        return ProfileRead(platform=self.platform, headline=text.strip())


def _svc(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID, *, tokens: bool = True
) -> PlatformSyncService:
    store = MemoryTokenStore()
    if tokens:
        store.save(Platform.upwork, OAuthTokens(access_token="acc"))  # type: ignore[arg-type]
    return PlatformSyncService(
        settings,
        session=session,
        user_id=user_id,
        registry=PlatformRegistry([FakeUpwork(), PasteOnly()]),
        store=store,
    )


def test_choose_method_precedence(settings: Settings) -> None:
    svc = _svc(settings, None, uuid.uuid4())  # type: ignore[arg-type]
    choose = svc.choose_method
    assert choose(Platform.upwork, SyncKind.jobs, SyncRequest()) == SyncMethod.api
    assert choose(Platform.upwork, SyncKind.jobs, SyncRequest(text="x")) == SyncMethod.paste
    assert (
        choose(Platform.upwork, SyncKind.jobs, SyncRequest(method=SyncMethod.paste, text="x"))
        == SyncMethod.paste
    )
    with pytest.raises(CapabilityUnavailable) as exc:
        choose(Platform.upwork, SyncKind.jobs, SyncRequest(method=SyncMethod.export))
    assert exc.value.available == [SyncMethod.api, SyncMethod.paste]
    with pytest.raises(CapabilityUnavailable):
        choose(Platform.toptal, SyncKind.jobs, SyncRequest(text="x"))  # jobs not declared at all
    with pytest.raises(CapabilityUnavailable):
        choose(Platform.toptal, SyncKind.profile, SyncRequest())  # paste-only but no text
    no_tokens = _svc(settings, None, uuid.uuid4(), tokens=False)  # type: ignore[arg-type]
    with pytest.raises(NotConnected):
        no_tokens.choose_method(Platform.upwork, SyncKind.jobs, SyncRequest())


async def test_parse_is_offline(settings: Settings) -> None:
    svc = _svc(settings, None, uuid.uuid4())  # type: ignore[arg-type]
    res = await svc.parse(Platform.upwork, SyncKind.jobs, text="A\nB\n")
    assert res.count == 2 and res.items[0]["title"] == "A" and res.method == SyncMethod.paste
    with pytest.raises(CapabilityUnavailable):
        await svc.parse(Platform.upwork, SyncKind.jobs, file_path="/nope.zip")


@pytest.mark.db
async def test_sync_profile_jobs_applications_end_to_end(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)

    dry = await svc.sync(Platform.upwork, SyncKind.profile, SyncRequest(dry_run=True))
    assert (
        dry.status == SyncStatus.ok
        and dry.run is None
        and dry.preview[0]["headline"].startswith("Data Platform")
    )

    prof = await svc.sync(Platform.upwork, SyncKind.profile, SyncRequest())
    assert prof.method == SyncMethod.api and prof.items_created == 1 and prof.run is not None
    snaps = await svc._profiles().list_snapshots(platform=Platform.upwork)
    assert snaps[0].id == prof.created_ids[0] and snaps[0].capture_method == CaptureMethod.api

    pasted = await svc.sync(
        Platform.upwork, SyncKind.profile, SyncRequest(text="Pasted headline\nmore")
    )
    assert pasted.method == SyncMethod.paste and pasted.items_created == 1

    jobs = await svc.sync(Platform.upwork, SyncKind.jobs, SyncRequest())
    assert jobs.items_seen == 2 and jobs.items_created == 2 and jobs.status == SyncStatus.ok
    again = await svc.sync(Platform.upwork, SyncKind.jobs, SyncRequest())
    assert again.items_created == 0 and again.items_skipped == 2 and len(again.duplicates) == 2

    apps = await svc.sync(Platform.upwork, SyncKind.applications, SyncRequest())
    assert apps.items_created == 1
    rows = await svc.platform.list_observations(platform=Platform.upwork)
    assert rows[0].opportunity_id == jobs.created_ids[0]  # linked by job url
    assert rows[0].status == ApplicationStatus.viewed

    runs = await svc.platform.list_runs(platform=Platform.upwork)
    assert {r.kind for r in runs} == {SyncKind.profile, SyncKind.jobs, SyncKind.applications}
    conn = await svc.platform.get_connection(Platform.upwork)
    assert conn.last_sync_at is not None and conn.last_error is None

    everything = await svc.sync_all(dry_run=True)
    by_key = {(r.platform, r.kind): r for r in everything}
    assert by_key[(Platform.upwork, SyncKind.jobs)].status == SyncStatus.ok
    assert by_key[(Platform.toptal, SyncKind.profile)].status == SyncStatus.skipped
    assert "--text-file" in (by_key[(Platform.toptal, SyncKind.profile)].message or "")
    assert by_key[(Platform.toptal, SyncKind.jobs)].message == "not supported"


@pytest.mark.db
async def test_platform_api_smoke(db_client: AsyncClient) -> None:
    r = await db_client.get("/api/platform/capabilities")
    assert r.status_code == 200 and len(r.json()) == 7
    assert r.json()[0]["platform"] == "hh" and r.json()[0]["manual_capture"] is True

    r = await db_client.get("/api/platform/connections")
    assert r.status_code == 200 and {c["status"] for c in r.json()} <= {"disconnected", "connected"}

    text = "Data Engineer at Northwind Commerce\nApplied on Aug 12, 2026\nApplication viewed\n\nAnalytics Engineer — Lumen Analytics\nNot selected by employer\n"
    r = await db_client.post("/api/platform/indeed/parse/applications", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2 and r.json()["items"][1]["status"] == "rejected"

    r = await db_client.post(
        "/api/platform/toptal/sync/jobs",
        json={"text": "Data Engineer at Northwind Commerce\nRemote\n", "dry_run": True},
    )
    assert r.status_code == 200 and r.json()["preview"][0]["title"] == "Data Engineer"

    r = await db_client.post("/api/platform/toptal/sync/profile", json={"method": "api"})
    assert r.status_code == 409 and "available" in r.json()["detail"]

    r = await db_client.post("/api/platform/hh/sync/jobs", json={"method": "api"})
    assert r.status_code == 409  # not connected

    r = await db_client.post("/api/platform/indeed/sync/applications", json={"text": text})
    assert r.status_code == 200 and r.json()["items_created"] == 2
    r = await db_client.get("/api/platform/applications", params={"platform": "indeed"})
    assert r.status_code == 200 and len(r.json()) == 2
    r = await db_client.get("/api/platform/sync-runs", params={"platform": "indeed"})
    assert r.status_code == 200 and r.json()[0]["kind"] == "applications"
    r = await db_client.get("/api/platform/ats/doctor")
    assert r.status_code == 404


def test_cli_parse_extra() -> None:
    import typer

    from careeros.modules.platform.cli import parse_extra

    assert parse_extra(None) == {}
    assert parse_extra(["area=1", "full=true", "title=data engineer"]) == {
        "area": "1",
        "full": True,
        "title": "data engineer",
    }
    with pytest.raises(typer.BadParameter):
        parse_extra(["novalue"])


class PublicJobs(FakeUpwork):
    platform = Platform.hh
    capabilities = FakeUpwork.capabilities.model_copy(update={"platform": Platform.hh})
    jobs_without_token = True


async def test_public_job_search_needs_no_token(settings: Settings) -> None:
    svc = PlatformSyncService(
        settings,
        session=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        registry=PlatformRegistry([PublicJobs()]),
        store=MemoryTokenStore(),
    )
    assert svc.choose_method(Platform.hh, SyncKind.jobs, SyncRequest()) == SyncMethod.api
    with pytest.raises(NotConnected):
        svc.choose_method(Platform.hh, SyncKind.profile, SyncRequest())
    res = await svc.sync(Platform.hh, SyncKind.jobs, SyncRequest(dry_run=True))
    assert res.items_seen == 2 and res.method == SyncMethod.api
    from careeros.modules.platform.registry import get_registry

    assert get_registry().get("hh").jobs_without_token is True


class PublicJobsNoToken(PublicJobs):
    """hh.ru's real shape: the public search still needs query text when there is no token."""

    async def search_jobs(self, ctx: ConnectorContext, query: JobQuery) -> list[JobPosting]:
        if ctx.tokens is None and not query.text:
            raise NotConnected(self.platform, "vacancy search needs --query text")
        return await super().search_jobs(ctx, query)


async def test_sync_all_reports_not_connected_as_skipped(settings: Settings) -> None:
    """A sweep over a fresh install must stay clean: 'connect first' is a to-do, not a failure."""
    svc = PlatformSyncService(
        settings,
        session=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        registry=PlatformRegistry([PublicJobsNoToken(), PasteOnly()]),
        store=MemoryTokenStore(),
    )
    results = await svc.sync_all(dry_run=True)
    by_key = {(r.platform, r.kind): r for r in results}
    jobs = by_key[(Platform.hh, SyncKind.jobs)]
    assert jobs.status == SyncStatus.skipped and "not connected" in (jobs.message or "")
    assert by_key[(Platform.hh, SyncKind.profile)].status == SyncStatus.skipped
    assert not [r for r in results if r.status == SyncStatus.failed]


# --------------------------------------------------------------------------- review fixes


class RefreshingUpwork(FakeUpwork):
    """First API call is rejected (401 → NotConnected); succeeds after a refresh."""

    calls: list[str] = []

    def oauth_config(self, settings: Settings) -> OAuthConfig | None:
        return OAuthConfig(
            authorize_url="https://up.example/authorize",
            token_url="https://up.example/token",
            client_id="cid",
            client_secret="sec",  # type: ignore[arg-type]
            redirect_uri="http://localhost:8000/api/platform/oauth/upwork/callback",
        )

    async def search_jobs(self, ctx: ConnectorContext, query: JobQuery) -> list[JobPosting]:
        assert ctx.tokens is not None
        token = ctx.tokens.access_token.get_secret_value()
        self.calls.append(token)
        if token == "stale":
            raise NotConnected(self.platform, "token rejected (401)")
        return await super().search_jobs(ctx, query)

    async def application_statuses(self, ctx: ConnectorContext) -> list[ApplicationObservationIn]:
        ctx.warnings.append("proposals status Archived: field not found")
        return await super().application_statuses(ctx)


def _token_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/token"
    form = dict(httpx.QueryParams(request.content.decode()))
    assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "r1"
    return httpx.Response(
        200, json={"access_token": "fresh", "refresh_token": "r2", "expires_in": 3600}
    )


def _refreshing_svc(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID, tokens: OAuthTokens
):
    store = MemoryTokenStore()
    store.save(Platform.upwork, tokens)
    RefreshingUpwork.calls = []
    return PlatformSyncService(
        settings,
        session=session,
        user_id=user_id,
        registry=PlatformRegistry([RefreshingUpwork()]),
        store=store,
        http_transport=httpx.MockTransport(_token_handler),
    ), store


@pytest.mark.db
async def test_api_call_refreshes_rejected_token_once(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    creds = settings.model_copy(
        update={"upwork_client_id": "cid", "upwork_client_secret": SecretStr("sec")}
    )
    stale = OAuthTokens(access_token="stale", refresh_token="r1")  # type: ignore[arg-type]
    svc, store = _refreshing_svc(creds, session, user_id, stale)
    res = await svc.sync(Platform.upwork, SyncKind.jobs, SyncRequest(dry_run=True))
    assert res.items_seen == 2 and RefreshingUpwork.calls == ["stale", "fresh"]
    saved = store.load(Platform.upwork)
    assert saved is not None and saved.access_token.get_secret_value() == "fresh"
    assert saved.refresh_token is not None and saved.refresh_token.get_secret_value() == "r2"

    # proactive refresh: expired-but-refreshable tokens are refreshed before the first call
    expired = OAuthTokens(
        access_token="stale",  # type: ignore[arg-type]
        refresh_token="r1",  # type: ignore[arg-type]
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    svc, _ = _refreshing_svc(creds, session, user_id, expired)
    res = await svc.sync(Platform.upwork, SyncKind.jobs, SyncRequest(dry_run=True))
    assert res.items_seen == 2 and RefreshingUpwork.calls == ["fresh"]

    # without a refresh token the rejection surfaces as NotConnected
    svc, _ = _refreshing_svc(creds, session, user_id, OAuthTokens(access_token="stale"))  # type: ignore[arg-type]
    with pytest.raises(NotConnected):
        await svc.sync(Platform.upwork, SyncKind.jobs, SyncRequest(dry_run=True))


@pytest.mark.db
async def test_connector_warnings_make_the_run_partial(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc, _ = _refreshing_svc(settings, session, user_id, OAuthTokens(access_token="fresh"))  # type: ignore[arg-type]
    res = await svc.sync(Platform.upwork, SyncKind.applications, SyncRequest())
    assert res.status == SyncStatus.partial and res.warnings == [
        "proposals status Archived: field not found"
    ]
    assert res.run is not None and "warning: proposals status Archived" in (res.run.error or "")
    dry = await svc.sync(Platform.upwork, SyncKind.applications, SyncRequest(dry_run=True))
    assert dry.status == SyncStatus.partial and dry.run is None


def test_explicit_paste_or_export_never_falls_through_to_api(settings: Settings) -> None:
    class ApiOnly(BaseConnector):
        platform = Platform.hh
        capabilities = Capabilities(
            platform=Platform.hh, jobs=[SyncMethod.api], official_api=True, auth=AuthKind.oauth2
        )
        jobs_without_token = True

        async def search_jobs(self, ctx: ConnectorContext, query: JobQuery) -> list[JobPosting]:
            return []

    svc = PlatformSyncService(
        settings,
        session=None,  # type: ignore[arg-type]
        user_id=uuid.uuid4(),
        registry=PlatformRegistry([ApiOnly()]),
        store=MemoryTokenStore(),
    )
    with pytest.raises(CapabilityUnavailable) as exc:
        svc.choose_method(Platform.hh, SyncKind.jobs, SyncRequest(text="pasted page"))
    assert exc.value.method == SyncMethod.paste
    with pytest.raises(CapabilityUnavailable):
        svc.choose_method(Platform.hh, SyncKind.jobs, SyncRequest(file_path="/x.zip"))
    assert svc.choose_method(Platform.hh, SyncKind.jobs, SyncRequest()) == SyncMethod.api


@pytest.mark.db
async def test_unexpected_error_is_recorded_on_the_run(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id)

    class Broken:
        async def create_snapshot(self, snap):  # type: ignore[no-untyped-def]
            raise RuntimeError("vault exploded")

    svc._profiles = lambda: Broken()  # type: ignore[assignment, return-value]
    with pytest.raises(RuntimeError):
        await svc.sync(Platform.upwork, SyncKind.profile, SyncRequest(text="Headline\n"))
    runs = await svc.platform.list_runs(platform=Platform.upwork, kind=SyncKind.profile)
    assert runs[0].status == SyncStatus.failed and "RuntimeError: vault exploded" in (
        runs[0].error or ""
    )
    assert runs[0].finished_at is not None
    conn = await svc.platform.get_connection(Platform.upwork)
    assert conn.last_error == "vault exploded"


@pytest.mark.db
async def test_jobs_sync_keeps_external_id_and_payload(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    from sqlalchemy import select

    from careeros.modules.opportunities.models import Opportunity, OpportunityRaw

    svc = _svc(settings, session, user_id)
    res = await svc.sync(Platform.upwork, SyncKind.jobs, SyncRequest())
    assert res.items_created + res.items_skipped == 2
    opp = await session.scalar(
        select(Opportunity).where(Opportunity.url == "https://www.upwork.com/jobs/~j1")
    )
    assert opp is not None
    raw = await session.get(OpportunityRaw, opp.raw_id)
    assert raw is not None and raw.raw_payload is not None
    assert raw.raw_payload["external_id"] == "j1"


@pytest.mark.db
async def test_api_status_filter_and_callback_without_bearer(settings: Settings, db: bool) -> None:
    from httpx import ASGITransport, AsyncClient

    from careeros.api.app import create_app

    if not db:
        pytest.skip("PostgreSQL not reachable")
    from careeros.core.auth import _settings_dep

    guarded = settings.model_copy(update={"api_token": SecretStr("s3cret")})
    app = create_app(guarded)
    app.dependency_overrides[_settings_dep] = lambda: guarded  # auth reads ambient settings
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c,
    ):
        if True:  # keep the original indentation of the assertions below
            r = await c.get("/api/platform/capabilities")
            assert r.status_code == 401  # bearer required everywhere …
            r = await c.get(
                "/api/platform/oauth/hh/callback", params={"code": "x", "state": "nope"}
            )
            assert r.status_code == 400  # … except the browser redirect, which is auth'd by state
            headers = {"Authorization": "Bearer s3cret"}
            text = "Data Engineer at Northwind Commerce\nNot selected by employer\n\nAnalytics Engineer — Lumen Analytics\nApplication viewed\n"
            r = await c.post(
                "/api/platform/toptal/sync/applications", json={"text": text}, headers=headers
            )
            assert r.status_code == 200, r.text
            r = await c.get(
                "/api/platform/applications",
                params={"platform": "toptal", "status": "rejected"},
                headers=headers,
            )
            assert r.status_code == 200 and [o["status"] for o in r.json()] == ["rejected"]
