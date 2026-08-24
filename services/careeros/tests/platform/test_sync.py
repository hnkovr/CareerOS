# ruff: noqa: E501
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
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
