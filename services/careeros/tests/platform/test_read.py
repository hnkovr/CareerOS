# ruff: noqa: E501
"""ADR-015 §5 / ADR-016 §4: reading ONE job behind a URL through the application layer.

The connector here is a test double, so what is exercised is the *wiring*: detection, the
access policy, the strategy chain, identity, snapshots, provenance rows and the run record.
No network: every request goes through ``httpx.MockTransport``.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.modules.opportunities.enums import FieldSource, OpportunityStatus, SourceRelation
from careeros.modules.opportunities.models import Opportunity
from careeros.modules.platform.base import BaseConnector, ReadUnavailable
from careeros.modules.platform.enums import (
    AccessMode,
    AuthKind,
    FetchStrategy,
    SyncKind,
    SyncMethod,
)
from careeros.modules.platform.fetch.artifact import FetchArtifact, JobReadError
from careeros.modules.platform.fetch.cache import reset_default_cache
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.schemas import Capabilities, JobPosting, ReadRequest
from careeros.modules.platform.sync import PlatformSyncService, authority_for, closed_reason
from careeros.modules.platform.tokens import MemoryTokenStore
from careeros.modules.vault.enums import Platform

HOST = "board.example"
JOB_PATH = "/jobs/4711"
JOB_URL = f"https://{HOST}{JOB_PATH}"

ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"
ROBOTS_DISALLOW = "User-agent: *\nDisallow: /jobs/\n"

CAPTCHA = (
    "<!doctype html><html><head><title>Just a moment...</title></head>"
    "<body><div id='challenge-running'><h1>Checking your browser</h1>"
    "<p>This process is automatic.</p></div></body></html>"
)
GONE = (
    "<!doctype html><html><head><title>Page not found</title></head>"
    "<body><h1>404</h1><p>We could not find that page.</p></body></html>"
)


def page(*, salary_min: int = 25000, salary_max: int = 32000, note: str = "") -> str:
    """A server-rendered posting with a schema.org ``JobPosting`` — the deterministic path."""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Senior Data Engineer – Northwind Commerce</title>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"JobPosting","title":"Senior Data Engineer",
"description":"<p>Own the analytics platform of a mid-size e-commerce company. Design ELT pipelines with dbt and Dagster, operate ClickHouse for 2B events a day.{note}</p><p>Salary {salary_min} - {salary_max} PLN per month.</p>",
"datePosted":"2026-08-20",
"hiringOrganization":{{"@type":"Organization","name":"Northwind Commerce"}},
"jobLocation":{{"@type":"Place","address":{{"@type":"PostalAddress","addressLocality":"Warsaw","addressCountry":"PL"}}}},
"jobLocationType":"TELECOMMUTE",
"baseSalary":{{"@type":"MonetaryAmount","currency":"PLN","value":{{"@type":"QuantitativeValue","minValue":{salary_min},"maxValue":{salary_max},"unitText":"MONTH"}}}},
"skills":"Python, SQL, dbt, Dagster",
"qualifications":"5+ years of data engineering",
"identifier":{{"@type":"PropertyValue","name":"Northwind","value":"NW-4711"}},
"url":"{JOB_URL}"}}
</script></head><body><h1>Senior Data Engineer</h1>
<p>Own the analytics platform of a mid-size e-commerce company.</p></body></html>"""


class Site:
    """One host serving one page, plus the third parties the fallback strategies would call."""

    def __init__(self, body: str | None = None, *, status: int = 200) -> None:
        self.body = page() if body is None else body
        self.status = status
        self.robots = ROBOTS_ALLOW
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host.endswith("jina.ai"):  # Jina Reader refuses in these tests
            return httpx.Response(422, json={"code": 422, "message": "blocked"})
        if "archive.org" in host:  # Wayback knows nothing about this URL
            return httpx.Response(200, text="[]", headers={"content-type": "application/json"})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        return httpx.Response(
            self.status, text=self.body, headers={"content-type": "text/html; charset=utf-8"}
        )

    def paths(self) -> list[str]:
        return [str(r.url.path) for r in self.requests]


class FakeBoard(BaseConnector):
    """A job board that can be read as a public page and nothing else."""

    platform = Platform.getmatch
    detect_hosts = (HOST,)
    capabilities = Capabilities(
        platform=Platform.getmatch,
        read_job=[FetchStrategy.public_html],
        access=AccessMode.public,
        auth=AuthKind.none,
        notes="test double",
    )

    def extract_job(self, artifact: FetchArtifact) -> JobPosting:
        return super().extract_job(artifact)


class PasteOnlyBoard(BaseConnector):
    """Declares no read strategy at all — a URL for it must be refused, not attempted."""

    platform = Platform.toptal
    capabilities = Capabilities(
        platform=Platform.toptal, access=AccessMode.manual_import, jobs=[SyncMethod.paste]
    )

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return []


@pytest.fixture(autouse=True)
def _fresh_fetch_cache() -> Any:
    """The L1 fetch cache is process-wide; a test must not read another test's page."""
    reset_default_cache()
    yield
    reset_default_cache()


def _svc(
    settings: Settings,
    session: AsyncSession,
    user_id: uuid.UUID,
    site: Site,
    *,
    connectors: list[BaseConnector] | None = None,
) -> PlatformSyncService:
    return PlatformSyncService(
        settings,
        session=session,
        user_id=user_id,
        registry=PlatformRegistry(connectors or [FakeBoard(), PasteOnlyBoard()]),
        store=MemoryTokenStore(),
        http_transport=httpx.MockTransport(site),
    )


# ----------------------------------------------------------------------------- pure


def test_detect_asks_the_registry_and_can_be_forced(settings: Settings) -> None:
    svc = _svc(settings, None, uuid.uuid4(), Site())  # type: ignore[arg-type]
    hit = svc.detect(f"{JOB_URL}?utm_source=share")
    assert hit is not None and hit.platform == Platform.getmatch and hit.confidence == 0.9
    assert hit.canonical.canonical_url == JOB_URL and hit.canonical.host == HOST
    assert svc.detect("https://elsewhere.example/jobs/1") is None  # no generic in this registry
    forced = svc.detect("https://elsewhere.example/jobs/1", platform=Platform.toptal)
    assert forced is not None and forced.confidence == 1.0
    out = svc.detection_out(hit)
    assert out.canonical_url == JOB_URL and out.platform == Platform.getmatch


def test_the_access_policy_is_checked_before_any_request(settings: Settings) -> None:
    site = Site()
    svc = _svc(settings, None, uuid.uuid4(), site)  # type: ignore[arg-type]
    with pytest.raises(ReadUnavailable):
        svc._read_target(ReadRequest(url="https://x.example/1", platform=Platform.toptal), None)
    with pytest.raises(ReadUnavailable):
        svc._read_target(ReadRequest(url=JOB_URL, strategy=FetchStrategy.api), None)
    assert site.requests == [], "the policy must refuse before the network is touched"


def test_authority_ranks_the_employer_page_above_a_board_page() -> None:
    assert authority_for(Platform.website, FetchStrategy.public_html) == FieldSource.employer_page
    assert authority_for(Platform.getmatch, FetchStrategy.public_html) == FieldSource.board_page
    assert authority_for(Platform.hh, FetchStrategy.api) == FieldSource.board_api
    assert authority_for(Platform.hh, FetchStrategy.wayback) == FieldSource.archive
    assert authority_for(Platform.hh, FetchStrategy.api, is_archive=True) == FieldSource.archive


# ----------------------------------------------------------------------------- reading


@pytest.mark.db
async def test_read_creates_an_opportunity_with_its_provenance(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    site = Site()
    svc = _svc(settings, session, user_id, site)
    out = await svc.read_job(ReadRequest(url=f"{JOB_URL}?utm_source=telegram"))

    assert site.paths() == ["/robots.txt", JOB_PATH], "robots.txt is read before the page"
    assert out.created is True and out.duplicate_of is None and out.snapshot_created is True
    assert out.opportunity_id is not None and out.closed is False
    posting = out.posting
    assert posting is not None and posting.title == "Senior Data Engineer"
    assert posting.canonical_url == JOB_URL and posting.external_id == "NW-4711"
    assert posting.strategy == FetchStrategy.public_html
    assert [a.strategy for a in out.attempts] == [FetchStrategy.public_html]
    assert out.attempts[0].ok and out.attempts[0].status_code == 200

    opportunities = svc._opportunities()
    detail = await opportunities.get(out.opportunity_id)
    assert detail.platform == str(Platform.getmatch) and detail.external_id == "NW-4711"
    assert detail.canonical_url == JOB_URL and detail.company_name == "Northwind Commerce"

    sources = await opportunities.list_sources(out.opportunity_id)
    assert len(sources) == 1
    primary = sources[0]
    assert primary.relation == SourceRelation.primary
    assert primary.authority == FieldSource.board_page
    assert primary.strategy == str(FetchStrategy.public_html)
    assert primary.canonical_url == JOB_URL and primary.external_id == "NW-4711"
    assert primary.content_hash and primary.fetched_at is not None

    runs = await svc.platform.list_runs(kind=SyncKind.job)
    assert len(runs) == 1 and runs[0].status == "ok" and runs[0].items_created == 1
    assert runs[0].details["strategy"] == str(FetchStrategy.public_html)
    assert runs[0].details["attempts"][0]["strategy"] == str(FetchStrategy.public_html)
    assert runs[0].details["opportunity_id"] == str(out.opportunity_id)


@pytest.mark.db
async def test_reading_the_same_url_again_adds_no_snapshot(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    site = Site()
    svc = _svc(settings, session, user_id, site)
    first = await svc.read_job(ReadRequest(url=JOB_URL))
    again = await svc.read_job(ReadRequest(url=f"{JOB_URL}?ref=newsletter", no_cache=True))

    assert again.created is False and again.duplicate_of == first.opportunity_id
    assert again.opportunity_id == first.opportunity_id
    assert again.snapshot_created is False, "an unchanged page is not a new snapshot"
    opportunity_id = first.opportunity_id
    assert opportunity_id is not None
    opportunities = svc._opportunities()
    assert len(await opportunities.list_snapshots(opportunity_id)) == 1
    assert len(await opportunities.list_sources(opportunity_id)) == 1, "the source upserts"
    runs = await svc.platform.list_runs(kind=SyncKind.job)
    assert [r.items_skipped for r in runs] == [1, 0]


@pytest.mark.db
async def test_a_changed_salary_becomes_a_snapshot_and_shows_up_in_the_diff(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID, db_client: AsyncClient
) -> None:
    site = Site()
    svc = _svc(settings, session, user_id, site)
    first = await svc.read_job(ReadRequest(url=JOB_URL))
    site.body = page(salary_min=30000, salary_max=40000, note=" Salary band raised.")
    second = await svc.read_job(ReadRequest(url=JOB_URL, no_cache=True))

    assert second.created is False and second.snapshot_created is True
    opportunity_id = first.opportunity_id
    assert opportunity_id is not None
    snapshots = await svc._opportunities().list_snapshots(opportunity_id)
    assert len(snapshots) == 2 and snapshots[-1].strategy == str(FetchStrategy.public_html)
    assert snapshots[-1].fingerprint != snapshots[0].fingerprint

    resp = await db_client.get(f"/api/opportunities/{opportunity_id}/diff")
    assert resp.status_code == 200, resp.text
    changed = {c["field"] for c in resp.json()["changes"]}
    assert "compensation" in changed
    body = resp.json()
    salary = next(c for c in body["changes"] if c["field"] == "compensation")
    assert salary["before"]["max"] == 32000 and salary["after"]["max"] == 40000


@pytest.mark.db
async def test_dry_run_reads_but_writes_nothing(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id, Site())
    out = await svc.read_job(ReadRequest(url=JOB_URL, dry_run=True))
    assert out.posting is not None and out.posting.title == "Senior Data Engineer"
    assert out.opportunity_id is None and out.created is False and out.run_id is None
    assert (await session.scalars(select(Opportunity))).all() == []
    assert await svc.platform.list_runs(kind=SyncKind.job) == []


@pytest.mark.db
async def test_a_captcha_page_fails_with_every_attempt_named(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    svc = _svc(settings, session, user_id, Site(CAPTCHA))
    with pytest.raises(JobReadError) as exc:
        await svc.read_job(ReadRequest(url=JOB_URL))
    assert [a.error_type for a in exc.value.attempts] == ["captcha"]
    assert "captcha" in exc.value.diagnostics
    assert closed_reason(exc.value) is None, "a captcha is not a closed job"
    runs = await svc.platform.list_runs(kind=SyncKind.job)
    assert len(runs) == 1 and runs[0].status == "failed"
    assert runs[0].details["attempts"][0]["error_type"] == "captcha"
    assert (await session.scalars(select(Opportunity))).all() == []


@pytest.mark.db
async def test_robots_disallow_is_a_skipped_attempt_not_a_request(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    site = Site()
    site.robots = ROBOTS_DISALLOW
    blocked_url = "https://blocked.example/jobs/4711"

    class Blocked(FakeBoard):
        detect_hosts = ("blocked.example",)

    svc = _svc(settings, session, user_id, site, connectors=[Blocked()])
    with pytest.raises(JobReadError) as exc:
        await svc.read_job(ReadRequest(url=blocked_url))
    assert [a.error_type for a in exc.value.attempts] == ["robots_disallow"]
    assert exc.value.attempts[0].cache_status == "skip"
    assert site.paths() == ["/robots.txt"], "the page itself is never requested"


@pytest.mark.db
async def test_refresh_records_a_gone_posting_as_closed(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    site = Site()
    svc = _svc(settings, session, user_id, site)
    first = await svc.read_job(ReadRequest(url=JOB_URL))
    assert first.opportunity_id is not None

    site.body, site.status = GONE, 404
    out = await svc.refresh_job(first.opportunity_id)
    assert out.closed is True and out.posting is None
    assert out.opportunity_id == first.opportunity_id
    assert [a.error_type for a in out.attempts] == ["not_found"]
    assert "posting is gone" in out.warnings[0]

    detail = await svc._opportunities().get(first.opportunity_id)
    assert detail.status == OpportunityStatus.archived
    row = await session.get(Opportunity, first.opportunity_id)
    assert row is not None and row.field_evidence is not None
    assert row.field_evidence["closed"][0]["value"] is True
    runs = await svc.platform.list_runs(kind=SyncKind.job)
    assert runs[0].status == "failed" and runs[0].details["attempts"][0]["status_code"] == 404


@pytest.mark.db
async def test_refresh_needs_a_url(
    settings: Settings, session: AsyncSession, user_id: uuid.UUID
) -> None:
    from careeros.modules.opportunities.schemas import IngestRequest

    svc = _svc(settings, session, user_id, Site())
    detail = await svc._opportunities().ingest(
        IngestRequest(text="Senior Data Engineer at Northwind. Remote, dbt, Python." * 3)
    )
    with pytest.raises(Exception, match="no URL to refresh"):
        await svc.refresh_job(detail.id)


# ----------------------------------------------------------------------------- HTTP surface


def _patch_transport(monkeypatch: pytest.MonkeyPatch, site: Site) -> None:
    """The API builds its own client; give the whole read path the mock transport."""
    from careeros.modules.platform import http as platform_http
    from careeros.modules.platform import sync as platform_sync

    def build(settings: Settings, **kw: Any) -> httpx.AsyncClient:
        kw["transport"] = httpx.MockTransport(site)
        return platform_http.build_http(settings, **kw)

    monkeypatch.setattr(platform_sync, "build_http", build)


@pytest.mark.db
async def test_read_and_detect_over_http(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = Site()
    _patch_transport(monkeypatch, site)

    seen = await db_client.get("/api/platform/detect", params={"url": JOB_URL})
    assert seen.status_code == 200, seen.text
    assert seen.json()["canonical_url"] == JOB_URL
    assert seen.json()["platform"] == str(Platform.website)  # the generic fallback owns it
    assert (await db_client.get("/api/platform/detect", params={"url": "nope"})).status_code == 404

    created = await db_client.post("/api/platform/read", json={"url": JOB_URL})
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["created"] is True and body["opportunity_id"]
    assert body["posting"]["external_id"] == "NW-4711"
    assert body["attempts"][0]["strategy"] == str(FetchStrategy.public_html)

    refreshed = await db_client.post(f"/api/opportunities/{body['opportunity_id']}/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["created"] is False
    assert refreshed.json()["duplicate_of"] == body["opportunity_id"]

    sources = await db_client.get(f"/api/opportunities/{body['opportunity_id']}/sources")
    assert sources.status_code == 200 and len(sources.json()) == 1


@pytest.mark.db
async def test_a_failed_read_answers_422_with_the_attempts(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_transport(monkeypatch, Site(CAPTCHA))
    resp = await db_client.post("/api/platform/read", json={"url": JOB_URL})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "captcha" in detail["detail"] and detail["platform"] == str(Platform.website)
    strategies = [a["strategy"] for a in detail["attempts"]]
    assert strategies == [
        str(FetchStrategy.public_html),
        str(FetchStrategy.jina),
        str(FetchStrategy.wayback),
    ], "every fallback the generic provider declares is reported"


# ----------------------------------------------------------------------------- CLI wiring


def test_the_cli_exposes_read_detect_and_a_job_refresh() -> None:
    from careeros.modules.platform.cli import app

    commands = {}
    for command in app.registered_commands:
        callback = command.callback
        assert callback is not None, "every registered command has a callback"
        commands[command.name or callback.__name__] = callback
    assert {"read", "detect", "refresh"} <= commands.keys()
    code = commands["read"].__code__
    params = set(code.co_varnames[: code.co_argcount])
    assert {"dry_run", "as_json", "show_attempts", "no_cache", "strategy", "use_ai"} <= params
