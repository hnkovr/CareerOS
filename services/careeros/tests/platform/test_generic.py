"""Generic website connector: any http(s) URL, JSON-LD → og → text, the full read chain offline."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from careeros.core.config import Settings
from careeros.modules.platform.base import ConnectorContext, ReadUnavailable
from careeros.modules.platform.connectors.generic.connector import Connector
from careeros.modules.platform.enums import AccessMode, FetchStrategy, SyncMethod
from careeros.modules.platform.fetch.artifact import FetchArtifact, JobReadError
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.fetch.cache import FetchCache
from careeros.modules.platform.fetch.robots import RobotsPolicy
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import get_registry
from careeros.modules.platform.sources import SourceKind, SourceRef
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
JOB_URL = "https://careers.northwind.example/jobs/senior-data-engineer-4711"


def _fx(*parts: str) -> str:
    return (FIXTURES / Path(*parts)).read_text()


class Site:
    def __init__(self, page: str, *, status: int = 200) -> None:
        self.page = page
        self.status = status
        self.requests: list[httpx.Request] = []
        self.jina: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "r.jina.example":
            if self.jina is None:
                return httpx.Response(422, json={"code": 422, "message": "blocked"})
            return httpx.Response(200, text=self.jina, headers={"content-type": "text/markdown"})
        if request.url.host == "wayback.example":
            return httpx.Response(200, text="[]", headers={"content-type": "application/json"})
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=_fx("generic", "robots_allow.txt"))
        return httpx.Response(self.status, text=self.page, headers={"content-type": "text/html"})


def _ctx(settings: Settings, site: Site) -> ConnectorContext:
    s = settings.model_copy(
        update={
            "jina_reader_base": "https://r.jina.example",
            "wayback_cdx_base": "https://wayback.example",
        }
    )
    return ConnectorContext(
        settings=s, http=build_http(s, transport=httpx.MockTransport(site)), now=NOW
    )


def _policy(ctx: ConnectorContext) -> RobotsPolicy:
    return RobotsPolicy(ctx.http, user_agent=ctx.settings.platform_user_agent, cache={})


def test_generic_is_registered_last_with_the_declared_read_chain() -> None:
    reg = get_registry()
    c = reg.get("website")
    assert isinstance(c, Connector) and reg.platforms()[-1] == Platform.website
    caps = c.capabilities
    assert caps.read_job == [FetchStrategy.public_html, FetchStrategy.jina, FetchStrategy.wayback]
    assert caps.access == AccessMode.public and caps.read_one is True
    assert caps.jobs == [SyncMethod.paste] and caps.manual_capture is True
    assert reg.verify() == []


def test_detect_and_canonicalize() -> None:
    c = Connector()
    hit = c.detect("https://www.example.org/careers/12?utm_campaign=x&ref=y")
    assert hit is not None and hit.confidence == 0.1 and hit.platform == Platform.website
    assert hit.canonical.canonical_url == "https://example.org/careers/12"
    assert c.detect("mailto:jobs@example.org") is None and c.detect("careers/12") is None
    ref = SourceRef(
        kind=SourceKind.telegram_message, value=f"see {JOB_URL}?fbclid=1", metadata={"locale": "en"}
    )
    src = c.canonicalize(ref)
    assert src.canonical_url == JOB_URL and src.private is True and src.locale == "en"
    with pytest.raises(ValueError):
        c.canonicalize(SourceRef(kind=SourceKind.text, value="nothing"))


def test_paste_reuses_the_shared_jobs_parser() -> None:
    jobs = Connector().parse_jobs_text(
        "Data Engineer at Northwind Commerce\nRemote\nhttps://x.example/1\n"
    )
    assert (
        len(jobs) == 1
        and jobs[0].platform == Platform.website
        and jobs[0].url == "https://x.example/1"
    )


async def test_read_jsonld_page_through_the_chain(settings: Settings) -> None:
    site = Site(_fx("generic", "jobposting.html"))
    ctx = _ctx(settings, site)
    c = Connector()
    source = c.canonicalize(JOB_URL + "?utm_source=share")
    read = await c.fetch_job(ctx, source, FetchBudget(), cache=FetchCache(), policy=_policy(ctx))
    assert [r.url.path for r in site.requests] == ["/robots.txt", "/jobs/senior-data-engineer-4711"]
    posting = read.posting
    assert posting is not None
    assert posting.title == "Senior Data Engineer" and posting.company == "Northwind Commerce"
    assert posting.external_id == "NW-4711" and posting.canonical_url == JOB_URL
    assert posting.strategy == FetchStrategy.public_html and posting.resolved_url == JOB_URL
    assert posting.quality == 1.0 and posting.completeness == 1.0 and posting.fetched_at == NOW
    assert posting.content_hash and posting.fingerprint and not posting.is_archive
    assert {e.source for e in posting.field_evidence} == {"jsonld"}
    req = posting.to_ingest()
    assert req.source == "website" and req.url == JOB_URL and req.external_id == "NW-4711"
    assert (
        req.raw_payload is not None and req.raw_payload["provenance"]["strategy"] == "public_html"
    )
    assert req.received_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert len(read.attempts) == 1 and read.attempts[0].ok


async def test_read_og_page_falls_back_to_meta_and_text(settings: Settings) -> None:
    site = Site(_fx("generic", "og_only.html"))
    ctx = _ctx(settings, site)
    c = Connector()
    read = await c.fetch_job(
        ctx, c.canonicalize(JOB_URL), FetchBudget(), use_cache=False, policy=_policy(ctx)
    )
    posting = read.posting
    assert posting is not None and posting.title == "Analytics Engineer (Remote, EU)"
    assert posting.company == "Lumen Analytics"  # og:site_name
    assert posting.raw_text.startswith("Lumen Analytics is hiring")  # og:description first
    assert "EUR 70 000" in posting.raw_text  # salary etc. are parsed at ingest, from raw_text
    assert {e.source for e in posting.field_evidence} == {"og_meta"}
    assert (
        posting.raw_payload is not None and posting.raw_payload["meta"]["og:title"] == posting.title
    )


async def test_read_captcha_then_jina_then_diagnostics(settings: Settings) -> None:
    site = Site(_fx("generic", "captcha.html"))
    site.jina = _fx("jina", "reader.md")
    ctx = _ctx(settings, site)
    c = Connector()
    read = await c.fetch_job(
        ctx, c.canonicalize(JOB_URL), FetchBudget(), use_cache=False, policy=_policy(ctx)
    )
    assert read.posting is not None and read.posting.strategy == FetchStrategy.jina
    assert read.posting.title == "Senior Data Engineer – Northwind Commerce Careers"
    assert [a.strategy for a in read.attempts] == [FetchStrategy.public_html, FetchStrategy.jina]
    assert read.attempts[0].error_type == "captcha"
    assert {e.source for e in read.posting.field_evidence} == {"jina_markdown"}

    blocked = Site(_fx("generic", "captcha.html"))  # jina 422, wayback has nothing
    ctx = _ctx(settings, blocked)
    with pytest.raises(JobReadError) as exc:
        await c.fetch_job(
            ctx, c.canonicalize(JOB_URL), FetchBudget(), use_cache=False, policy=_policy(ctx)
        )
    assert [a.error_type for a in exc.value.attempts] == [
        "captcha",
        "http_error",
        "archive_not_found",
    ]
    assert "captcha" in exc.value.diagnostics and "422" in exc.value.diagnostics


async def test_read_honours_only_and_kill_switches(settings: Settings) -> None:
    site = Site(_fx("generic", "jobposting.html"))
    ctx = _ctx(settings, site)
    c = Connector()
    read = await c.fetch_job(
        ctx,
        c.canonicalize(JOB_URL),
        FetchBudget(),
        only=FetchStrategy.public_html,
        use_cache=False,
        policy=_policy(ctx),
    )
    assert read.posting is not None and len(read.attempts) == 1

    off = ctx.settings.model_copy(
        update={
            "job_fetch_enable_public_html": False,
            "job_fetch_enable_jina": False,
            "job_fetch_enable_wayback": False,
        }
    )
    ctx_off = ConnectorContext(settings=off, http=ctx.http, now=NOW)
    with pytest.raises(JobReadError) as exc:
        await c.fetch_job(
            ctx_off, c.canonicalize(JOB_URL), FetchBudget(), use_cache=False, policy=_policy(ctx)
        )
    assert exc.value.attempts == [] and "disabled" in exc.value.diagnostics


async def test_base_connector_without_read_job_refuses(settings: Settings) -> None:
    c = get_registry().get(Platform.indeed)
    with pytest.raises(ReadUnavailable):
        await c.fetch_job(
            _ctx(settings, Site("")), Connector().canonicalize(JOB_URL), FetchBudget()
        )
    with pytest.raises(ReadUnavailable):
        await c.fetch_job_api(_ctx(settings, Site("")), Connector().canonicalize(JOB_URL))


def test_extract_job_handles_api_payloads_and_empty_artifacts() -> None:
    c = Connector()
    api = FetchArtifact(
        provider=Platform.website,
        strategy=FetchStrategy.api,
        requested_url=JOB_URL,
        fetched_at=NOW,
        status_code=200,
        raw_json={
            "data": {
                "title": "Data Engineer",
                "company": "Northwind Commerce",
                "description": "Build pipelines with dbt. Remote.",
            }
        },
    )
    posting = c.extract_job(api)
    assert posting.title == "Data Engineer" and posting.company == "Northwind Commerce"
    assert posting.raw_payload == {"api": api.raw_json}
    assert {e.source for e in posting.field_evidence} == {"api"}
    with pytest.raises(ValueError):
        c.extract_job(
            FetchArtifact(
                provider=Platform.website,
                strategy=FetchStrategy.public_html,
                requested_url=JOB_URL,
                fetched_at=NOW,
                raw_text="  ",
            )
        )
