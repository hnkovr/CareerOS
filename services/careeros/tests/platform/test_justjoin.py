"""JustJoin.it connector (ADR-015 §6): read one offer by URL, offline.

Nothing here touches the network: the candidate API, the offer page, ``robots.txt`` and the
Wayback CDX index are all served by one ``httpx.MockTransport``. The fixtures are sanitised
copies of a real payload (company, title, skills and salaries invented; recorded 2026-08-26).
"""

from __future__ import annotations

import inspect
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from careeros.core.config import Settings
from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    RemotePolicy,
    Seniority,
)
from careeros.modules.platform.base import ConnectorContext
from careeros.modules.platform.connectors.generic.connector import Connector as GenericConnector
from careeros.modules.platform.connectors.justjoin import client, mapping
from careeros.modules.platform.connectors.justjoin.connector import PROBE_SLUG, Connector
from careeros.modules.platform.enums import AccessMode, AuthKind, FetchStrategy, SyncMethod
from careeros.modules.platform.fetch.artifact import JobReadError
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.fetch.cache import FetchCache
from careeros.modules.platform.fetch.robots import RobotsPolicy
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.schemas import JobQuery
from careeros.modules.platform.sources import SourceKind, SourceRef, detect
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "justjoin"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
SLUG = "northwind-tech-senior-data-engineer-b2b--warszawa-data-7c1e9a42"
OFFER_URL = f"https://justjoin.it/job-offer/{SLUG}"
API_PATH = f"/api/candidate-api/offers/{SLUG}"


def _fx(name: str) -> str:
    return (FIXTURES / name).read_text()


def _json(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_fx(name))
    return data


class Board:
    """JustJoin.it, offline: the candidate API, the offer page, robots.txt and the CDX index."""

    def __init__(
        self,
        *,
        detail: dict[str, Any] | None = None,
        api_status: int = 200,
        api_body: str | None = None,
        api_error: Exception | None = None,
        page: str | None = None,
        page_status: int = 200,
        cdx: str = "[]",
    ) -> None:
        self.detail = detail
        self.api_status = api_status
        self.api_body = api_body
        self.api_error = api_error
        self.page = page
        self.page_status = page_status
        self.cdx = cdx
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "wayback.example":
            if path.startswith("/cdx"):
                return httpx.Response(
                    200, text=self.cdx, headers={"content-type": "application/json"}
                )
            return httpx.Response(200, text=self.page or "", headers={"content-type": "text/html"})
        if path == "/robots.txt":
            return httpx.Response(200, text=_fx("robots.txt"))
        if path.startswith("/api/candidate-api/offers/"):
            if self.api_error is not None:
                raise self.api_error
            if self.api_body is not None:
                return httpx.Response(self.api_status, text=self.api_body)
            if self.api_status != 200:
                return httpx.Response(self.api_status, text=_fx("offer_404.json"))
            return httpx.Response(200, json=self.detail)
        if path.startswith("/job-offer/"):
            return httpx.Response(
                self.page_status, text=self.page or "", headers={"content-type": "text/html"}
            )
        return httpx.Response(404, text="not found")


def _ctx(settings: Settings, board: Board, **overrides: Any) -> ConnectorContext:
    s = settings.model_copy(update={"wayback_cdx_base": "https://wayback.example", **overrides})
    return ConnectorContext(
        settings=s, http=build_http(s, transport=httpx.MockTransport(board)), now=NOW
    )


def _policy(ctx: ConnectorContext) -> RobotsPolicy:
    return RobotsPolicy(ctx.http, user_agent=ctx.settings.platform_user_agent, cache={})


def _source(connector: Connector, url: str = OFFER_URL) -> Any:
    return connector.canonicalize(url)


# ------------------------------------------------------------------------------ declaration


def test_capabilities_are_declared_and_implemented() -> None:
    registry = PlatformRegistry([Connector()])
    assert registry.verify() == []
    caps = Connector.capabilities
    assert caps.platform == Platform.justjoin
    assert caps.read_job == [
        FetchStrategy.api,
        FetchStrategy.public_html,
        FetchStrategy.wayback,
    ]
    assert caps.access == AccessMode.public and caps.auth == AuthKind.none
    assert caps.jobs == [SyncMethod.paste] and caps.official_api is False
    assert caps.read_one is True and caps.manual_capture is True
    assert "no listing search" in caps.notes
    assert Connector.detect_hosts == ("justjoin.it", "www.justjoin.it")


def test_client_exposes_the_detail_endpoint_and_nothing_else() -> None:
    defined = {
        name
        for name, obj in inspect.getmembers(client, inspect.isfunction)
        if obj.__module__ == client.__name__
    }
    assert defined == {"offer_detail", "offer_detail_url"}
    assert not [n for n in dir(client) if "list" in n.lower() or "search" in n.lower()]
    assert client.offer_detail_url(SLUG) == f"https://justjoin.it{API_PATH}"


# ------------------------------------------------------------------------------ detection


def test_detect_and_canonicalize_every_url_shape() -> None:
    c = Connector()
    variants = (
        OFFER_URL,
        OFFER_URL + "/",
        OFFER_URL + "?utm_source=telegram&ref=x",
        OFFER_URL.replace("https://justjoin.it", "https://www.JustJoin.it"),
        f"https://justjoin.it/offers/{SLUG}",  # legacy path, same slug
    )
    for url in variants:
        hit = c.detect(url)
        assert hit is not None, url
        assert hit.platform == Platform.justjoin and hit.confidence == 0.95
        assert hit.canonical.canonical_url == OFFER_URL
        assert hit.canonical.external_id == SLUG and hit.canonical.host == "justjoin.it"

    for url in (
        "https://justjoin.it/",
        "https://justjoin.it/job-offers/all-locations?keyword=data",
        "https://justjoin.it/job-offer/",
        "https://rocketjobs.pl/job-offer/some-slug",
        "job-offer/some-slug",
        "mailto:jobs@justjoin.it",
    ):
        assert c.detect(url) is None, url


def test_canonicalize_references_and_registry_detection() -> None:
    c = Connector()
    ref = SourceRef(
        kind=SourceKind.telegram_message,
        value=f"look at this: {OFFER_URL}?fbclid=1",
        metadata={"locale": "pl"},
    )
    src = c.canonicalize(ref)
    assert src.canonical_url == OFFER_URL and src.external_id == SLUG
    assert src.private is True and src.locale == "pl"

    by_id = c.canonicalize(SourceRef(kind=SourceKind.provider_id, value=SLUG))
    assert by_id.canonical_url == OFFER_URL and by_id.external_id == SLUG

    # a non-offer JustJoin URL still canonicalises, just without an id
    listing = c.canonicalize("https://justjoin.it/job-offers/all-locations?utm_source=x")
    assert listing.canonical_url == "https://justjoin.it/job-offers/all-locations"
    assert listing.external_id is None

    registry = PlatformRegistry([Connector(), GenericConnector()])
    found = detect(OFFER_URL, registry)
    assert found is not None and found.platform == Platform.justjoin


# ------------------------------------------------------------------------------ api read


async def test_api_read_maps_the_whole_offer(settings: Settings) -> None:
    board = Board(detail=_json("offer_detail.json"))
    ctx = _ctx(settings, board)
    c = Connector()
    source = c.canonicalize(OFFER_URL + "?utm_source=share")
    read = await c.fetch_job(ctx, source, FetchBudget(), cache=FetchCache(), policy=_policy(ctx))

    # exactly one request, to the detail endpoint — no listing call, no robots (API is not a page)
    assert [r.url.path for r in board.requests] == [API_PATH]
    posting = read.posting
    assert posting is not None
    assert posting.title == "Senior Data Engineer (B2B)" and posting.company == "Northwind Tech"
    assert posting.external_id == SLUG and posting.url == OFFER_URL
    assert posting.canonical_url == OFFER_URL and posting.resolved_url == OFFER_URL
    assert posting.strategy == FetchStrategy.api and posting.fetched_at == NOW
    assert posting.published_at == datetime(2026, 8, 20, 9, 15, 30, 123456, tzinfo=UTC)
    assert posting.expires_at == datetime(2026, 9, 30, 21, 59, 59, 999000, tzinfo=UTC)
    assert posting.location == "Warszawa, Kraków, PL"
    assert posting.content_hash and posting.fingerprint and posting.is_archive is False

    x = posting.extraction
    assert x is not None
    assert x.remote_policy == RemotePolicy.remote_region and x.remote_regions == ["PL"]
    assert x.contract_type == ContractType.b2b and x.employment_type is None
    assert x.seniority == Seniority.senior
    assert x.technologies == ["Python", "SQL", "dbt"]
    assert x.preferred == ["Airflow", "Terraform"]
    assert x.requirements == ["Languages: EN B2, PL C1"]
    assert x.summary is not None and x.summary.startswith("Northwind Tech is looking for")
    assert x.deadline == date(2026, 9, 30)
    comp = x.compensation
    assert comp is not None
    assert (comp.min, comp.max, comp.currency) == (115.0, 143.0, "PLN")
    assert comp.period == CompensationPeriod.hour and comp.type == "rate"

    raw = posting.raw_payload
    assert raw is not None
    assert raw["guid"] == "3f2b8c10-5a44-4c9e-9d61-7ba2e5c14d08" and raw["slug"] == SLUG
    assert raw["company"] == {
        "name": "Northwind Tech",
        "profile_slug": "northwind-tech",
        "size": "150+",
        "url": "https://northwind.example",
    }
    assert raw["apply_url"] == "https://apply.northwind.example/jobs/senior-data-engineer"
    assert raw["category"] == {"key": "data", "parentKey": None}
    assert raw["workplace_type"] == "remote" and raw["working_time"] == "b2b_contract"
    assert raw["salary_gross"] is False and raw["is_active"] is True
    # every per-currency row is kept; only the employer's own row became `compensation`
    assert [s["currency"] for s in raw["salaries"]] == ["EUR", "PLN"]
    assert [s["currency_source"] for s in raw["salaries"]] == ["conversion", "original"]
    assert raw["salaries"][0]["compensation"]["min"] == 26.56
    assert raw["schema_fingerprint"] == mapping.BASELINE_FINGERPRINT
    assert raw["api"]["slug"] == SLUG  # the verbatim payload travels along

    assert {e.source for e in posting.field_evidence} == {"board_api"}
    assert {e.confidence for e in posting.field_evidence} == {0.9}
    fields = {e.field for e in posting.field_evidence}
    assert {"title", "company", "location", "compensation", "seniority", "apply_url"} <= fields

    assert posting.raw_text.startswith("Senior Data Engineer (B2B)\nNorthwind Tech")
    assert "Model the warehouse layer with dbt" in posting.raw_text

    request = posting.to_ingest()
    assert request.source == "justjoin" and request.external_id == SLUG
    assert request.url == OFFER_URL and request.received_at == posting.published_at
    assert request.raw_payload is not None
    assert request.raw_payload["provenance"]["strategy"] == "api"
    assert len(read.attempts) == 1 and read.attempts[0].ok
    assert read.attempts[0].strategy == FetchStrategy.api
    assert ctx.warnings == []


def test_workplace_types_and_open_vocabularies_never_raise() -> None:
    base = _json("offer_detail.json")
    cases = {
        "remote": (RemotePolicy.remote_region, ["PL"]),
        "office": (RemotePolicy.onsite, []),
        "hybrid": (RemotePolicy.hybrid, []),
        "partly_remote": (RemotePolicy.hybrid, []),
        "teleportation": (RemotePolicy.unknown, []),
    }
    for value, (policy, regions) in cases.items():
        posting = mapping.offer_to_posting({**base, "workplaceType": value}, fetched_at=NOW)
        extraction = posting.extraction
        assert extraction is not None
        assert (extraction.remote_policy, extraction.remote_regions) == (policy, regions), value
    unknown = mapping.offer_to_posting({**base, "workplaceType": "teleportation"})
    assert unknown.raw_payload is not None
    assert any("teleportation" in n for n in unknown.raw_payload["notes"])

    # "remote" with no country stated stays global rather than inventing a region
    global_remote = mapping.offer_to_posting({**base, "countryCode": None, "locations": []})
    assert global_remote.extraction is not None
    assert global_remote.extraction.remote_policy == RemotePolicy.remote_global
    assert global_remote.location == "Warszawa"

    assert mapping.SENIORITY["junior"] == Seniority.junior
    assert mapping.SENIORITY["mid"] == Seniority.mid
    assert mapping.SENIORITY["c_level"] == Seniority.principal


def test_drift_fixture_is_mapped_with_unknown_values_kept() -> None:
    payload = _json("offer_detail_drift.json")
    posting = mapping.offer_to_posting(payload, url=OFFER_URL, fetched_at=NOW)
    x = posting.extraction
    assert x is not None
    assert x.remote_policy == RemotePolicy.hybrid
    assert "Hybrid work schedule: 3 days from the office" in x.requirements
    assert x.contract_type is None and x.seniority is None  # unknown values, no guessing
    comp = x.compensation
    assert comp is not None
    assert (comp.min, comp.max, comp.currency) == (8000.0, 9600.0, "XAU")
    assert comp.period == CompensationPeriod.month and comp.type == "salary"

    raw = posting.raw_payload
    assert raw is not None
    assert raw["category"] == {"key": "quantum-ops", "parentKey": "engineering"}
    assert raw["experience_level"] == "principal_x"
    assert any("internship_x" in n for n in raw["notes"])
    assert any("principal_x" in n for n in raw["notes"])
    assert raw["api"]["aiSummary"] == "Board-generated summary of the offer."
    # a changed key set is visible; the required keys are all still there
    assert raw["schema_fingerprint"] != mapping.BASELINE_FINGERPRINT
    assert mapping.missing_required(payload) == []


async def test_schema_drift_warns_but_still_reads(settings: Settings) -> None:
    payload = {
        k: v for k, v in _json("offer_detail.json").items() if k not in ("body", "publishedAt")
    }
    board = Board(detail=payload)
    ctx = _ctx(settings, board)
    c = Connector()
    artifact = await c.fetch_job_api(ctx, _source(c))
    assert artifact.status_code == 200 and artifact.error_type is None
    assert len(ctx.warnings) == 1
    warning = ctx.warnings[0]
    assert "schema drift" in warning and "body" in warning and "publishedAt" in warning
    assert mapping.BASELINE_FINGERPRINT in warning

    posting = c.extract_job(artifact)
    assert posting.title == "Senior Data Engineer (B2B)" and posting.published_at is None
    assert posting.raw_payload is not None
    assert any("schema drift" in n for n in posting.raw_payload["notes"])


# ------------------------------------------------------------------------------ failures


async def test_missing_offer_walks_the_whole_chain_and_fails_loudly(settings: Settings) -> None:
    board = Board(
        api_status=404,
        page="<html><head><title>Page not found</title></head><body>404</body></html>",
        page_status=404,
    )
    ctx = _ctx(settings, board)
    c = Connector()
    with pytest.raises(JobReadError) as exc:
        await c.fetch_job(ctx, _source(c), FetchBudget(), use_cache=False, policy=_policy(ctx))
    attempts = exc.value.attempts
    assert [a.strategy for a in attempts] == [
        FetchStrategy.api,
        FetchStrategy.public_html,
        FetchStrategy.wayback,
    ]
    assert [a.error_type for a in attempts] == ["not_found", "not_found", "archive_not_found"]
    assert [r.url.path for r in board.requests] == [
        API_PATH,
        "/robots.txt",
        f"/job-offer/{SLUG}",
        "/cdx/search/cdx",
    ]
    assert "not_found" in exc.value.diagnostics


async def test_api_failures_are_classified(settings: Settings) -> None:
    c = Connector()
    cases: list[tuple[dict[str, Any], str, int | None]] = [
        ({"api_status": 403}, "forbidden", 403),
        ({"api_status": 429}, "rate_limited", 429),
        ({"api_status": 503}, "upstream", 503),
        ({"api_status": 200, "api_body": "<html>nope</html>"}, "malformed", 200),
        ({"api_error": httpx.ReadTimeout("timed out")}, "timeout", None),
        ({"api_error": httpx.ConnectError("no route")}, "network", None),
    ]
    for kwargs, expected, status in cases:
        board = Board(**kwargs)
        ctx = _ctx(settings, board)
        artifact = await c.fetch_job_api(ctx, _source(c))
        assert artifact.error_type == expected, kwargs
        assert artifact.status_code == status and artifact.usable is False
        assert artifact.requested_url == f"https://justjoin.it{API_PATH}"


async def test_public_api_kill_switch(settings: Settings) -> None:
    c = Connector()
    off = Board(detail=_json("offer_detail.json"))
    ctx_off = _ctx(settings, off, justjoin_enable_public_api=False)
    artifact = await c.fetch_job_api(ctx_off, _source(c))
    assert artifact.error_type == "disabled" and off.requests == []

    board = Board(page=_fx("offer_page.html"))
    ctx = _ctx(settings, board, justjoin_enable_public_api=False)
    read = await c.fetch_job(ctx, _source(c), FetchBudget(), use_cache=False, policy=_policy(ctx))
    assert [r.url.path for r in board.requests] == ["/robots.txt", f"/job-offer/{SLUG}"]
    assert [a.strategy for a in read.attempts] == [FetchStrategy.public_html]
    assert "disabled" in read.diagnostics

    posting = read.posting
    assert posting is not None
    assert posting.strategy == FetchStrategy.public_html
    assert posting.title == "Senior Data Engineer (B2B)" and posting.company == "Northwind Tech"
    assert posting.external_id == SLUG and posting.canonical_url == OFFER_URL
    assert {e.source for e in posting.field_evidence} == {"board_page"}
    assert posting.raw_payload is not None and posting.raw_payload["extractor"] == ["jsonld"]
    x = posting.extraction
    assert x is not None and x.compensation is not None
    assert (x.compensation.min, x.compensation.currency) == (115.0, "PLN")
    assert x.remote_policy == RemotePolicy.remote_region and x.remote_regions == ["Poland"]


# ------------------------------------------------------------------------------ surfaces


def test_search_url_is_a_deep_link_and_paste_uses_the_shared_parser() -> None:
    c = Connector()
    assert (
        c.search_url(JobQuery(text="data engineer"))
        == "https://justjoin.it/job-offers/all-locations?keyword=data+engineer"
    )
    assert c.search_url(JobQuery()) is None and c.search_url(JobQuery(text="   ")) is None

    jobs = c.parse_jobs_text(
        f"Senior Data Engineer at Northwind Tech\nRemote\n{OFFER_URL}\n\n"
        "Analytics Engineer at Contoso\nWarszawa\nhttps://justjoin.it/job-offer/other\n"
    )
    assert len(jobs) == 2
    assert jobs[0].platform == Platform.justjoin and jobs[0].url == OFFER_URL
    assert jobs[0].company == "Northwind Tech"


async def test_doctor_reports_detection_reachability_and_policy(settings: Settings) -> None:
    board = Board(api_status=404)
    ctx = _ctx(settings, board)
    checks = {c.name: c for c in await Connector().doctor(ctx)}
    assert checks["detection"].ok and PROBE_SLUG in checks["detection"].detail
    assert checks["api_reachable"].ok and "404" in checks["api_reachable"].detail
    assert checks["listing_search"].ok
    assert "not implemented by policy" in checks["listing_search"].detail
    assert mapping.BASELINE_FINGERPRINT in checks["schema_fingerprint"].detail
    assert [r.url.path for r in board.requests] == [
        f"/api/candidate-api/offers/{PROBE_SLUG}",
    ]

    blocked = {c.name: c for c in await Connector().doctor(_ctx(settings, Board(api_status=403)))}
    assert not blocked["api_reachable"].ok and "forbidden" in blocked["api_reachable"].detail

    disabled_ctx = _ctx(settings, Board(), justjoin_enable_public_api=False)
    disabled = {c.name: c for c in await Connector().doctor(disabled_ctx)}
    assert not disabled["api_reachable"].ok
    assert disabled["api_reachable"].fix == "set CAREEROS_JUSTJOIN_ENABLE_PUBLIC_API=true"
