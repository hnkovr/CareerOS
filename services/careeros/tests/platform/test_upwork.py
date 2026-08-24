# ruff: noqa: E501
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
    Seniority,
)
from careeros.modules.platform.base import ConnectorContext, NotConnected, UpstreamError
from careeros.modules.platform.connectors.upwork import client as upwork_client
from careeros.modules.platform.connectors.upwork import mapping, parsers, queries
from careeros.modules.platform.connectors.upwork.connector import Connector
from careeros.modules.platform.enums import ApplicationStatus, AuthKind, SyncMethod
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import get_registry
from careeros.modules.platform.schemas import JobQuery
from careeros.modules.platform.tokens import OAuthTokens
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "upwork"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

Handler = Callable[[httpx.Request], httpx.Response]


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def paste(name: str) -> str:
    return (FIXTURES / name).read_text()


def make_handler(
    seen: list[httpx.Request], *, introspection: str = "introspection.json"
) -> Handler:
    """Route by the GraphQL document in the JSON body (no network, no real platform)."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        query: str = body["query"]
        variables: dict[str, Any] = body.get("variables") or {}
        if "__type" in query:
            return httpx.Response(200, json=load(introspection))
        if "marketplaceJobPostingsSearch" in query:
            return httpx.Response(200, json=load("jobs.json"))
        if "vendorProposals" in query:
            payload = load("proposals.json")
            wanted = variables["filter"]["status_eq"]
            conn = payload["data"]["vendorProposals"]
            conn["edges"] = [e for e in conn["edges"] if e["node"]["status"]["status"] == wanted]
            conn["totalCount"] = len(conn["edges"])
            return httpx.Response(200, json=payload)
        if "freelancerProfile" in query:
            return httpx.Response(200, json=load("profile.json"))
        if "user" in query:
            return httpx.Response(200, json=load("user.json"))
        return httpx.Response(400, json={"errors": [{"message": "unknown document"}]})

    return handler


def errors_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=load("errors.json"))


@asynccontextmanager
async def api_ctx(
    settings: Settings, handler: Handler, *, tokens: bool = True
) -> AsyncIterator[ConnectorContext]:
    client = build_http(settings, transport=httpx.MockTransport(handler))
    try:
        yield ConnectorContext(
            settings=settings,
            http=client,
            tokens=OAuthTokens(access_token="t") if tokens else None,  # type: ignore[arg-type]
            now=NOW,
        )
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- declaration / auth


def test_capabilities_and_registry() -> None:
    c = Connector()
    caps = c.capabilities
    assert c.platform == Platform.upwork and caps.platform == Platform.upwork
    assert caps.profile == [SyncMethod.api, SyncMethod.paste]
    assert caps.jobs == [SyncMethod.api, SyncMethod.paste]
    assert caps.applications == [SyncMethod.api, SyncMethod.paste]
    assert caps.official_api is True and caps.auth == AuthKind.oauth2
    assert caps.email_fallback is True and caps.manual_capture is True
    assert "API key" in caps.notes
    reg = get_registry()
    assert reg.verify() == []
    assert type(reg.get("upwork")) is Connector


def test_oauth_config_requires_client_credentials(settings: Settings) -> None:
    with pytest.raises(NotConnected) as exc:
        Connector().oauth_config(settings)
    assert "CAREEROS_UPWORK_CLIENT_ID" in exc.value.hint
    assert "upwork.com/developer/keys" in exc.value.hint


def test_oauth_config_with_credentials(settings: Settings) -> None:
    cfg = Connector().oauth_config(
        settings.model_copy(
            update={"upwork_client_id": "cid", "upwork_client_secret": SecretStr("s")}
        )
    )
    assert cfg is not None
    assert cfg.authorize_url == "https://www.upwork.com/ab/account-security/oauth2/authorize"
    assert cfg.token_url == "https://www.upwork.com/api/v3/oauth2/token"
    assert cfg.client_id == "cid" and cfg.client_secret.get_secret_value() == "s"
    assert cfg.scopes == [] and cfg.token_auth == "body"
    assert cfg.redirect_uri == f"{settings.platform_oauth_redirect_base}/upwork/callback"
    assert cfg.redirect_uri == "http://localhost:8000/api/platform/oauth/upwork/callback"


def test_queries_carry_verify_live_markers() -> None:
    for name in (
        "USER_INFO",
        "FREELANCER_PROFILE",
        "JOB_SEARCH",
        "PROPOSALS",
        "INTROSPECT_QUERY_FIELDS",
    ):
        assert isinstance(getattr(queries, name), str) and getattr(queries, name).strip()
    src = (Path(queries.__file__)).read_text()
    assert src.count("# VERIFY LIVE") >= 5
    assert "marketplaceJobPostingsSearch" in queries.JOB_SEARCH
    assert "vendorProposals" in queries.PROPOSALS
    assert '__type(name: "Query")' in queries.INTROSPECT_QUERY_FIELDS
    assert set(queries.ROOT_FIELDS) == {"user", "marketplaceJobPostingsSearch", "vendorProposals"}


# --------------------------------------------------------------------------- API: whoami / errors


async def test_whoami_sends_bearer_and_maps_account(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    async with api_ctx(settings, make_handler(seen)) as ctx:
        info = await Connector().whoami(ctx)
    assert info.account_id == "danakovalenko" and info.label == "Dana Kovalenko"
    assert info.raw == {"id": "1494512345678901234", "email": "dana.kovalenko@example.com"}
    assert len(seen) == 1
    req = seen[0]
    assert (
        req.method == "POST"
        and str(req.url) == upwork_client.GRAPHQL_URL == "https://api.upwork.com/graphql"
    )
    assert req.headers["Authorization"] == "Bearer t"
    assert req.headers["User-Agent"] == settings.platform_user_agent
    body = json.loads(req.content)
    assert body["query"].strip() == queries.USER_INFO.strip() and body["variables"] == {}


async def test_graphql_errors_raise_upstream_error(settings: Settings) -> None:
    async with api_ctx(settings, errors_handler) as ctx:
        with pytest.raises(UpstreamError) as exc:
            await Connector().whoami(ctx)
    assert exc.value.platform == Platform.upwork and exc.value.status_code == 200
    assert 'Cannot query field "vendorProposals"' in exc.value.detail


async def test_api_methods_require_tokens(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    c = Connector()
    async with api_ctx(settings, make_handler(seen), tokens=False) as ctx:
        with pytest.raises(NotConnected):
            await c.whoami(ctx)
        with pytest.raises(NotConnected):
            await c.read_profile(ctx)
        with pytest.raises(NotConnected):
            await c.search_jobs(ctx, JobQuery(text="dbt"))
        with pytest.raises(NotConnected):
            await c.application_statuses(ctx)
    assert seen == []


# --------------------------------------------------------------------------- API: profile


async def test_read_profile_maps_freelancer_profile(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    async with api_ctx(settings, make_handler(seen)) as ctx:
        pr = await Connector().read_profile(ctx)
    assert pr.platform == Platform.upwork and pr.capture_method == CaptureMethod.api
    assert pr.external_id == "1494512345678901234"
    assert pr.profile_url == "https://www.upwork.com/freelancers/~01ab2cd3ef4567890a"
    assert pr.headline == "Senior Data Engineer | dbt · Dagster · ClickHouse"
    assert pr.about is not None and pr.about.startswith("I build analytics platforms")
    assert pr.skills == ["Python", "SQL", "dbt", "Dagster", "ClickHouse"]
    assert pr.rates == {"hourly": 85.0, "currency": "USD", "raw": "$85.00"}
    assert pr.availability == "Available now · More than 30 hrs/week"
    assert pr.portfolio == [
        {"name": "Northwind analytics platform", "url": "https://example.com/portfolio/northwind"},
        {"name": "Lumen customer 360", "url": None},
    ]
    assert pr.captured_at == NOW and pr.raw_payload is not None
    assert pr.raw_payload["freelancerProfile"]["fullName"] == "Dana Kovalenko"
    assert "freelancerProfile" in json.loads(seen[0].content)["query"]


def test_map_profile_tolerates_missing_freelancer_profile() -> None:
    pr = mapping.map_profile({"id": "u-1", "nid": "n-1", "freelancerProfile": None})
    assert pr.external_id == "u-1" and pr.headline is None and pr.skills == []
    assert pr.rates is None and pr.profile_url is None and pr.portfolio == []


def test_url_helpers_add_tilde_prefix() -> None:
    assert (
        mapping.job_url("~021830001234567890") == "https://www.upwork.com/jobs/~021830001234567890"
    )
    assert (
        mapping.job_url("021830001234567890") == "https://www.upwork.com/jobs/~021830001234567890"
    )
    assert mapping.profile_url("~01ab") == "https://www.upwork.com/freelancers/~01ab"
    assert mapping.profile_url("01ab") == "https://www.upwork.com/freelancers/~01ab"


# --------------------------------------------------------------------------- API: jobs


async def test_search_jobs_maps_hourly_and_fixed_nodes(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    async with api_ctx(settings, make_handler(seen)) as ctx:
        jobs = await Connector().search_jobs(ctx, JobQuery(text="data engineer dbt", limit=25))
    assert [j.external_id for j in jobs] == ["1830001234567890123", "1830009876543210987"]
    hourly, fixed = jobs

    assert hourly.platform == Platform.upwork
    assert hourly.url == "https://www.upwork.com/jobs/~021830001234567890"
    assert hourly.title == "Senior Data Engineer for e-commerce analytics platform"
    assert hourly.company is None and hourly.location == "United States"
    assert hourly.posted_at == datetime(2026, 8, 24, 9, 30, tzinfo=UTC)
    assert hourly.raw_text.startswith(
        "Senior Data Engineer for e-commerce analytics platform\nNorthwind Commerce"
    )
    assert (
        "Skills: Python, dbt, ClickHouse" in hourly.raw_text
        and "Budget: $60.00-$90.00/hr" in hourly.raw_text
    )
    ex = hourly.extraction
    assert ex is not None and ex.title == hourly.title
    assert (
        ex.contract_type == ContractType.freelance
        and ex.remote_policy == RemotePolicy.remote_global
    )
    assert ex.technologies == ["Python", "dbt", "ClickHouse"]
    assert ex.summary is not None and ex.summary.startswith("Northwind Commerce is rebuilding")
    assert ex.compensation is not None
    assert (ex.compensation.min, ex.compensation.max) == (60.0, 90.0)
    assert ex.compensation.currency == "USD" and ex.compensation.period == CompensationPeriod.hour
    assert ex.compensation.type == "rate"
    assert (
        hourly.raw_payload is not None and hourly.raw_payload["ciphertext"] == "~021830001234567890"
    )

    assert fixed.url == "https://www.upwork.com/jobs/~021830009876543210"  # tilde added
    assert fixed.location == "Georgia" and fixed.posted_at == datetime(
        2026, 8, 10, 15, 0, tzinfo=UTC
    )
    fx = fixed.extraction
    assert fx is not None and fx.compensation is not None
    assert (fx.compensation.min, fx.compensation.max) == (2500.0, 2500.0)
    assert (
        fx.compensation.period == CompensationPeriod.project and fx.compensation.currency == "USD"
    )
    assert fx.employment_type == EmploymentType.project
    assert "Budget: $2,500.00 fixed" in fixed.raw_text

    body = json.loads(seen[0].content)
    assert body["query"].strip() == queries.JOB_SEARCH.strip()
    flt = body["variables"]["filter"]
    assert flt["searchExpression_eq"] == "data engineer dbt"
    assert flt["pagination_eq"] == {"after": "0", "first": 25}
    assert seen[0].headers["Authorization"] == "Bearer t"


def test_job_filter_maps_query_and_extra() -> None:
    q = JobQuery(
        text="dbt",
        location="Germany",
        limit=10,
        extra={
            "title": "data engineer",
            "skills": "clickhouse",
            "category_ids": ["531770282580668418"],
            "job_type": "hourly",
            "verified_payment_only": True,
            "bogus": "ignored",
        },
    )
    flt = upwork_client.job_filter(q)
    assert flt == {
        "searchExpression_eq": "dbt",
        "titleExpression_eq": "data engineer",
        "skillExpression_eq": "clickhouse",
        "categoryIds_any": ["531770282580668418"],
        "jobType_eq": "HOURLY",
        "verifiedPaymentOnly_eq": True,
        "locations_any": ["Germany"],
        "pagination_eq": {"after": "0", "first": 10},
    }
    assert upwork_client.job_filter(JobQuery()) == {"pagination_eq": {"after": "0", "first": 30}}


async def test_search_jobs_applies_posted_since_client_side(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    async with api_ctx(settings, make_handler(seen)) as ctx:
        jobs = await Connector().search_jobs(ctx, JobQuery(posted_since=date(2026, 8, 20)))
    assert [j.external_id for j in jobs] == ["1830001234567890123"]


def test_map_job_without_budget_or_ciphertext() -> None:
    job = mapping.map_job({"id": "x-1", "title": "Quick fix", "description": "", "skills": None})
    assert job.url is None and job.extraction is not None and job.extraction.compensation is None
    assert job.raw_text == "Quick fix" and job.extraction.technologies == []


# --------------------------------------------------------------------------- API: applications


async def test_application_statuses_maps_proposals(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    async with api_ctx(settings, make_handler(seen)) as ctx:
        obs = await Connector().application_statuses(ctx)
    by_id = {o.external_id: o for o in obs}
    assert set(by_id) == {
        "prop-accepted-1",
        "prop-accepted-2",
        "prop-activated-1",
        "prop-offered-1",
        "prop-declined-1",
        "prop-withdrawn-1",
        "prop-archived-1",
    }
    assert by_id["prop-accepted-1"].status == ApplicationStatus.applied
    assert by_id["prop-accepted-2"].status == ApplicationStatus.viewed  # viewedByClient
    assert by_id["prop-activated-1"].status == ApplicationStatus.interview
    assert by_id["prop-offered-1"].status == ApplicationStatus.offer
    assert by_id["prop-declined-1"].status == ApplicationStatus.rejected
    assert by_id["prop-withdrawn-1"].status == ApplicationStatus.withdrawn
    assert by_id["prop-archived-1"].status == ApplicationStatus.rejected

    first = by_id["prop-accepted-1"]
    assert first.platform == Platform.upwork and first.status_raw == "Accepted"
    assert first.job_title == "Senior Data Engineer for e-commerce analytics platform"
    assert first.company == "Northwind Commerce" and first.job_url is None
    assert first.applied_at == datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    assert by_id["prop-accepted-2"].updated_at_platform == datetime(2026, 8, 16, 12, 30, tzinfo=UTC)
    assert by_id["prop-declined-1"].company is None
    assert (
        first.raw_payload is not None
        and first.raw_payload["terms"]["chargeRate"]["displayValue"] == "$85.00"
    )

    statuses = [json.loads(r.content)["variables"]["filter"]["status_eq"] for r in seen]
    assert statuses == list(upwork_client.PROPOSAL_STATUS_FILTERS)
    assert all(r.headers["Authorization"] == "Bearer t" for r in seen)
    sort = json.loads(seen[0].content)["variables"]["sortAttribute"]
    assert sort == {"field": "CREATEDDATETIME", "sortOrder": "DESC"}


async def test_application_statuses_follows_cursor_and_tolerates_partial_failures(
    settings: Settings,
) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["variables"])
        status = body["variables"]["filter"]["status_eq"]
        if status == "Hired":
            return httpx.Response(200, json=load("errors.json"))
        if status != "Accepted":
            return httpx.Response(
                200,
                json={
                    "data": {"vendorProposals": {"totalCount": 0, "edges": [], "pageInfo": None}}
                },
            )
        page = body["variables"]["pagination"].get("after")
        node = {"id": f"acc-{page or 'p1'}", "status": {"status": "Accepted"}}
        page_info = {"hasNextPage": page is None, "endCursor": "cur-2" if page is None else None}
        payload = {"data": {"vendorProposals": {"edges": [{"node": node}], "pageInfo": page_info}}}
        return httpx.Response(200, json=payload)

    async with api_ctx(settings, handler) as ctx:
        obs = await Connector().application_statuses(ctx)
    assert sorted(o.external_id or "" for o in obs) == ["acc-cur-2", "acc-p1"]
    accepted_pages = [c["pagination"] for c in calls if c["filter"]["status_eq"] == "Accepted"]
    assert accepted_pages == [{"first": 50}, {"first": 50, "after": "cur-2"}]


async def test_application_statuses_raises_when_every_status_fails(settings: Settings) -> None:
    async with api_ctx(settings, errors_handler) as ctx:
        with pytest.raises(UpstreamError):
            await Connector().application_statuses(ctx)


@pytest.mark.parametrize(
    ("raw", "viewed", "expected"),
    [
        ("Accepted", False, ApplicationStatus.applied),
        ("Accepted", True, ApplicationStatus.viewed),
        ("ACTIVE", False, ApplicationStatus.applied),
        ("SUBMITTED", False, ApplicationStatus.applied),
        ("Pending", False, ApplicationStatus.applied),
        ("VIEWED", False, ApplicationStatus.viewed),
        ("Activated", False, ApplicationStatus.interview),
        ("INTERVIEW", False, ApplicationStatus.interview),
        ("SHORTLISTED", False, ApplicationStatus.interview),
        ("Offered", False, ApplicationStatus.offer),
        ("OFFER_SENT", False, ApplicationStatus.offer),
        ("Hired", True, ApplicationStatus.offer),
        ("Declined", True, ApplicationStatus.rejected),
        ("REJECTED", False, ApplicationStatus.rejected),
        ("Archived", False, ApplicationStatus.rejected),
        ("CLOSED", False, ApplicationStatus.rejected),
        ("Withdrawn", True, ApplicationStatus.withdrawn),
        ("Not selected", False, ApplicationStatus.rejected),  # normalize_status fallback
        ("banana", False, ApplicationStatus.unknown),
        ("", False, ApplicationStatus.unknown),
    ],
)
def test_proposal_status_mapping(raw: str, viewed: bool, expected: ApplicationStatus) -> None:
    assert mapping.proposal_status(raw, viewed=viewed) == expected


# --------------------------------------------------------------------------- doctor


async def test_doctor_reports_root_fields_on_live_schema(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    async with api_ctx(settings, make_handler(seen)) as ctx:
        checks = await Connector().doctor(ctx)
    by_name = {c.name: c for c in checks}
    assert by_name["capabilities"].ok and by_name["tokens"].ok
    assert by_name["client_credentials"].ok is False  # test settings carry no creds
    assert by_name["graphql:user"].ok and by_name["graphql:marketplaceJobPostingsSearch"].ok
    missing = by_name["graphql:vendorProposals"]
    assert missing.ok is False and "missing" in missing.detail
    assert missing.fix is not None and "queries.py" in missing.fix
    assert len(seen) == 1 and "__type" in json.loads(seen[0].content)["query"]


async def test_doctor_without_tokens_skips_probe(settings: Settings) -> None:
    seen: list[httpx.Request] = []
    async with api_ctx(settings, make_handler(seen), tokens=False) as ctx:
        checks = await Connector().doctor(ctx)
    names = [c.name for c in checks]
    assert "tokens" in names and not any(n.startswith("graphql:") for n in names)
    assert seen == []


async def test_doctor_never_raises_on_failed_probe(settings: Settings) -> None:
    async with api_ctx(settings, errors_handler) as ctx:
        checks = await Connector().doctor(ctx)
    probe = next(c for c in checks if c.name == "graphql:introspection")
    assert probe.ok is False and "Cannot query field" in probe.detail and probe.fix


# --------------------------------------------------------------------------- paste: profile


def test_parse_profile_paste() -> None:
    text = paste("paste_profile.txt")
    pr = Connector().parse_profile_text(text)
    assert pr.platform == Platform.upwork and pr.capture_method == CaptureMethod.paste
    assert pr.headline == "Senior Data Engineer | dbt · Dagster · ClickHouse"
    assert pr.about == (
        "I build analytics platforms for e-commerce and fintech teams: warehouse design, dbt models, "
        "Dagster orchestration and ClickHouse tuning. Twelve years in data, five of them leading small analytics teams."
    )
    assert pr.skills == ["Python", "SQL", "dbt", "Dagster", "ClickHouse"]
    assert pr.rates == {"hourly": 85.0, "currency": "USD", "raw": "$85.00/hr"}
    assert pr.availability == "Available now · More than 30 hrs/week · Open to contract to hire"
    assert pr.portfolio == [
        {"name": "Northwind analytics platform"},
        {"name": "Lumen customer 360"},
    ]
    assert pr.projects == [
        {"name": "Data pipeline for Orbit Fintech", "period": "Aug 2025 - Dec 2025"}
    ]
    assert [(e.title, e.company, e.period) for e in pr.experience] == [
        ("Senior Data Engineer", "Northwind Commerce", "January 2023 - Present"),
        ("Lead Analytics Engineer", "Lumen Analytics", "March 2020 - December 2022"),
    ]
    assert pr.raw_text == text


def test_parse_profile_paste_with_overview_header_and_no_rate() -> None:
    text = "Dana K.\nAnalytics Engineer\nOverview\nI model data.\nSkills: SQL, dbt\n"
    pr = parsers.parse_profile(text)
    assert pr.headline == "Analytics Engineer" and pr.about == "I model data."
    assert pr.skills == ["SQL", "dbt"] and pr.rates is None and pr.availability is None


def test_parse_profile_paste_falls_back_to_generic() -> None:
    text = "Dana Kovalenko\nSenior Data Engineer\nI build platforms.\n"  # no rate, no headers
    pr = parsers.parse_profile(text)
    assert pr.headline == "Dana Kovalenko" and pr.about is None and pr.skills == []
    assert pr.raw_text == text


# --------------------------------------------------------------------------- paste: jobs


def test_parse_jobs_paste() -> None:
    jobs = parsers.parse_jobs(paste("paste_jobs.txt"), now=NOW)
    assert [j.title for j in jobs] == [
        "Senior Data Engineer for e-commerce analytics platform (dbt + ClickHouse)",
        "Build a customer 360 dashboard in Metabase",
        "Airflow to Dagster migration for a data platform team",
    ]
    hourly, fixed, open_hourly = jobs
    assert all(j.platform == Platform.upwork and j.company is None for j in jobs)
    assert hourly.posted_at == NOW.replace(hour=10)
    assert fixed.posted_at == NOW.replace(day=24)
    assert open_hourly.posted_at == NOW.replace(day=22)
    assert [j.location for j in jobs] == ["United States", "Georgia", "Germany"]

    ex = hourly.extraction
    assert ex is not None and ex.contract_type == ContractType.freelance
    assert ex.remote_policy == RemotePolicy.remote_global and ex.seniority == Seniority.senior
    assert ex.employment_type == EmploymentType.full_time
    assert ex.compensation is not None and (ex.compensation.min, ex.compensation.max) == (
        60.0,
        90.0,
    )
    assert ex.compensation.period == CompensationPeriod.hour and ex.compensation.currency == "USD"
    assert ex.compensation.raw is not None and ex.compensation.raw.startswith(
        "Hourly: $60.00-$90.00"
    )
    assert ex.technologies == ["Python", "dbt", "ClickHouse", "Dagster"]
    assert ex.summary is not None and ex.summary.startswith("We are an online retailer")
    assert ex.red_flags == []
    assert hourly.raw_payload is not None and hourly.raw_payload["proposals"] == "5 to 10"
    assert (
        hourly.raw_payload["payment_verified"] is True
        and hourly.raw_payload["client_spent"] == "$50K+"
    )

    fx = fixed.extraction
    assert fx is not None and fx.compensation is not None
    assert (fx.compensation.min, fx.compensation.max) == (2500.0, 2500.0)
    assert (
        fx.compensation.period == CompensationPeriod.project and fx.compensation.currency == "USD"
    )
    assert fx.employment_type == EmploymentType.project and fx.seniority == Seniority.mid
    assert fx.technologies == ["SQL", "Metabase", "dbt"]
    assert fx.red_flags == ["payment unverified"]
    assert fixed.raw_payload is not None and fixed.raw_payload["payment_verified"] is False

    oh = open_hourly.extraction
    assert oh is not None and oh.compensation is not None
    assert oh.compensation.min is None and oh.compensation.max is None
    assert oh.compensation.period == CompensationPeriod.hour and oh.compensation.currency is None
    assert oh.employment_type == EmploymentType.part_time
    assert oh.technologies == ["Dagster", "Apache Airflow", "dbt"]
    assert open_hourly.raw_text.startswith("Posted 3 days ago\nAirflow to Dagster migration")


def test_parse_jobs_paste_splits_cards_without_blank_lines() -> None:
    text = (
        "Posted 1 hour ago\nFirst job title\nHourly: $40.00-$50.00 - Intermediate\nLong description one.\n"
        "Posted 2 days ago\nSecond job title\nFixed-price - Expert - Est. budget: $900\nLong description two.\n"
        "https://www.upwork.com/jobs/~02abc\n"
    )
    jobs = parsers.parse_jobs(text, now=NOW)
    assert [j.title for j in jobs] == ["First job title", "Second job title"]
    assert jobs[1].url == "https://www.upwork.com/jobs/~02abc"
    assert jobs[1].extraction is not None and jobs[1].extraction.compensation is not None
    assert jobs[1].extraction.compensation.min == 900.0


def test_parse_jobs_paste_falls_back_to_generic() -> None:
    text = "Data Engineer at Northwind Commerce\nRemote · Contract\n\nAnalytics Engineer — Lumen Analytics\nRemote\n"
    jobs = Connector().parse_jobs_text(text)
    assert [(j.title, j.company) for j in jobs] == [
        ("Data Engineer", "Northwind Commerce"),
        ("Analytics Engineer", "Lumen Analytics"),
    ]


# --------------------------------------------------------------------------- paste: proposals


def test_parse_proposals_paste() -> None:
    obs = parsers.parse_proposals(paste("paste_proposals.txt"), now=NOW)
    rows = [(o.job_title, o.status, o.company) for o in obs]
    assert rows == [
        ("Data pipeline modernization", ApplicationStatus.offer, "Orbit Fintech"),
        (
            "Senior Data Engineer for e-commerce analytics platform (dbt + ClickHouse)",
            ApplicationStatus.interview,
            None,
        ),
        ("Build a customer 360 dashboard in Metabase", ApplicationStatus.viewed, None),
        ("dbt models for a fintech data warehouse", ApplicationStatus.applied, None),
        ("Analytics Engineer (contract)", ApplicationStatus.rejected, None),
        (
            "Airflow to Dagster migration for a data platform team",
            ApplicationStatus.withdrawn,
            None,
        ),
    ]
    assert [o.applied_at for o in obs] == [
        datetime(2026, 8, 20, tzinfo=UTC),
        datetime(2026, 8, 12, tzinfo=UTC),
        datetime(2026, 8, 18, tzinfo=UTC),
        datetime(2026, 8, 15, tzinfo=UTC),
        datetime(2026, 7, 30, tzinfo=UTC),
        datetime(2026, 7, 25, tzinfo=UTC),
    ]
    assert obs[0].status_raw == "Offer received Aug 20, 2026"
    assert obs[1].status_raw == "Active proposals"  # section default, no row keyword
    assert obs[2].status_raw == "Viewed by client"
    assert obs[3].status_raw == "Submitted proposals"
    assert obs[4].status_raw == "Declined by client"
    assert obs[5].status_raw == "Withdrawn"
    assert all(o.platform == Platform.upwork and o.external_id is None for o in obs)
    assert obs[0].raw_payload == {
        "section": "Offers",
        "lines": ["Data pipeline modernization", "Orbit Fintech · Offer received Aug 20, 2026"],
    }


def test_parse_proposals_paste_multiline_rows_and_slash_form() -> None:
    text = (
        "Submitted proposals (2)\n"
        "Data Engineer for a retail warehouse\nNorthwind Commerce\nAug 12, 2026\nViewed by client\n"
        "Analytics Engineer / Lumen Analytics / Aug 10, 2026 / Interviewing\n"
        "Invitations to interview\n"
        "ClickHouse tuning\nOrbit Fintech · Received Aug 21, 2026\n"
    )
    obs = parsers.parse_proposals(text, now=NOW)
    assert [(o.job_title, o.company, o.status) for o in obs] == [
        ("Data Engineer for a retail warehouse", "Northwind Commerce", ApplicationStatus.viewed),
        ("Analytics Engineer", "Lumen Analytics", ApplicationStatus.interview),
        ("ClickHouse tuning", "Orbit Fintech", ApplicationStatus.invited),
    ]
    assert obs[0].applied_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert obs[2].applied_at == datetime(2026, 8, 21, tzinfo=UTC)


def test_parse_proposals_paste_section_default_is_not_downgraded_by_weak_words() -> None:
    text = "Active proposals\nRetail warehouse build\nSubmitted Aug 1, 2026 · Viewed by client\n"
    obs = parsers.parse_proposals(text, now=NOW)
    assert len(obs) == 1 and obs[0].status == ApplicationStatus.interview
    assert obs[0].applied_at == datetime(2026, 8, 1, tzinfo=UTC)


def test_parse_proposals_paste_falls_back_to_generic() -> None:
    text = "Data Engineer at Northwind Commerce\nApplied on Aug 12, 2026\nApplication viewed\n"
    obs = Connector().parse_applications_text(text)
    assert len(obs) == 1 and obs[0].company == "Northwind Commerce"
    assert obs[0].status == ApplicationStatus.viewed
