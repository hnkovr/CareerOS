"""hh.ru read-one (ADR-015 §53): detection, the API strategy's status semantics, fallbacks.

Nothing here touches the network — the whole chain (api.hh.ru → r.jina.example →
wayback.example) is served by one ``httpx.MockTransport``. The single ``live`` test at the
bottom does read the real vacancy and is skipped unless ``CAREEROS_LIVE_TESTS=1``.

Synthetic persona only (Lumen Analytics); the payload shape follows
https://api.hh.ru/openapi/redoc.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.opportunities.enums import CompensationPeriod, EmploymentType, RemotePolicy
from careeros.modules.platform.base import ConnectorContext
from careeros.modules.platform.connectors.hh import hosts
from careeros.modules.platform.connectors.hh.connector import Connector
from careeros.modules.platform.enums import (
    AccessMode,
    FetchStrategy,
    SourceRelation,
)
from careeros.modules.platform.fetch.artifact import JobReadError
from careeros.modules.platform.fetch.budget import FetchBudget
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.platform.sources import SourceKind, SourceRef, detect
from careeros.modules.platform.tokens import OAuthTokens
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "hh"
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
VACANCY_ID = "136537758"
CANONICAL = f"https://hh.ru/vacancy/{VACANCY_ID}"
SHARE_URL = f"{CANONICAL}?from=share_ios&hhtmFrom=vacancy_search_list"
JINA_BASE = "https://r.jina.example"
WAYBACK_BASE = "https://wayback.example"
FORBIDDEN_BODY = {"errors": [{"type": "forbidden"}]}


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


class ReadMock:
    """Routes the whole read chain and records every request.

    ``api`` is a queue: one response per request to ``api.hh.ru/vacancies/…`` (so a retry has to
    be declared, it cannot pass unnoticed). Anything the test did not declare answers 404, and an
    unexpected host fails the test outright — hh's own HTML must never be fetched.
    """

    def __init__(
        self,
        *,
        api: list[httpx.Response] | None = None,
        token: httpx.Response | None = None,
        jina: httpx.Response | None = None,
        cdx: httpx.Response | None = None,
        snapshot: httpx.Response | None = None,
    ) -> None:
        self.api = list(api or [])
        self.token = token
        self.jina = jina
        self.cdx = cdx
        self.snapshot = snapshot
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "api.hh.ru":
            if request.url.path == "/token":
                return self.token or httpx.Response(400, json={"error": "invalid_client"})
            if not self.api:
                raise AssertionError(f"unexpected extra API request: {request.url}")
            return self.api.pop(0)
        if host == "r.jina.example":
            return self.jina or httpx.Response(404, text="not found")
        if host == "wayback.example":
            if request.url.path.startswith("/cdx/"):
                return self.cdx or httpx.Response(404, text="not found")
            return self.snapshot or httpx.Response(404, text="not found")
        raise AssertionError(f"the hh read chain must not call {host}")

    def paths(self, host: str = "api.hh.ru") -> list[str]:
        return [r.url.path for r in self.requests if r.url.host == host]


def _settings(settings: Settings, *, creds: bool = False) -> Settings:
    update: dict[str, Any] = {
        "jina_reader_base": JINA_BASE,
        "wayback_cdx_base": WAYBACK_BASE,
    }
    if creds:
        update |= {"hh_client_id": "cid", "hh_client_secret": SecretStr("secret")}
    return settings.model_copy(update=update)


def _ctx(
    settings: Settings,
    mock: ReadMock,
    *,
    tokens: bool = False,
    creds: bool = False,
    now: datetime = NOW,
) -> ConnectorContext:
    s = _settings(settings, creds=creds)
    return ConnectorContext(
        settings=s,
        http=build_http(s, transport=httpx.MockTransport(mock)),
        tokens=OAuthTokens(access_token="user-token") if tokens else None,  # type: ignore[arg-type]
        now=now,
    )


# --------------------------------------------------------------------------- capabilities / hosts


def test_capabilities_declare_the_read_chain_and_verify() -> None:
    caps = Connector.capabilities
    assert caps.read_job == [FetchStrategy.api, FetchStrategy.jina, FetchStrategy.wayback]
    assert caps.access == AccessMode.public and caps.read_one is True
    assert FetchStrategy.public_html not in caps.read_job  # WAF/captcha, ADR-005
    assert PlatformRegistry([Connector()]).verify() == []


def test_host_table_marks_only_hh_ru_as_verified() -> None:
    by_host = {h.host: h for h in hosts.HOSTS}
    assert set(by_host) == {
        "hh.ru",
        "hh.kz",
        "headhunter.ge",
        "headhunter.kg",
        "hh.uz",
        "rabota.by",
        "hh1.az",
    }
    assert by_host["hh.ru"].verified is True and by_host["hh.ru"].canonical is True
    assert [h.host for h in hosts.HOSTS if h.verified] == ["hh.ru"]
    assert {h.api_base for h in hosts.HOSTS} == {hosts.API_BASE}
    assert hosts.canonical().host == "hh.ru"


def test_api_params_pin_regional_sites_only() -> None:
    assert hosts.api_params_for("hh.ru") == {}
    assert hosts.api_params_for("spb.hh.ru") == {}  # a city subdomain is still hh.ru
    assert hosts.api_params_for("www.hh.kz") == {"host": "hh.kz"}
    assert hosts.api_params_for("rabota.by") == {"host": "rabota.by"}
    assert hosts.api_params_for("hh1.az") == {"host": "hh1.az"}
    assert hosts.api_params_for("example.org") == {} and hosts.api_params_for("") == {}
    assert hosts.api_base_for("hh.kz") == "https://api.hh.ru"
    assert hosts.find("m.hh.ru:443") is hosts.find("HH.RU.")


# --------------------------------------------------------------------------- detect / canonicalize


def test_detect_canonicalises_a_shared_vacancy_link() -> None:
    hit = Connector().detect(SHARE_URL)
    assert hit is not None
    assert hit.platform == Platform.hh and hit.confidence == 0.95
    assert hit.canonical.canonical_url == CANONICAL
    assert hit.canonical.external_id == VACANCY_ID and hit.canonical.host == "hh.ru"
    assert hit.canonical.private is False


@pytest.mark.parametrize(
    ("url", "canonical_url", "host"),
    [
        (f"https://hh.ru/vacancy/{VACANCY_ID}/", CANONICAL, "hh.ru"),
        (f"https://www.hh.ru/vacancy/{VACANCY_ID}", CANONICAL, "hh.ru"),
        (f"https://m.hh.ru/vacancy/{VACANCY_ID}", CANONICAL, "hh.ru"),
        (f"https://spb.hh.ru/vacancy/{VACANCY_ID}?from=share", CANONICAL, "hh.ru"),
        ("https://hh.kz/vacancy/98765", "https://hh.kz/vacancy/98765", "hh.kz"),
        ("https://m.rabota.by/vacancy/4242/", "https://rabota.by/vacancy/4242", "rabota.by"),
        ("https://headhunter.ge/vacancy/17", "https://headhunter.ge/vacancy/17", "headhunter.ge"),
        ("https://headhunter.kg/vacancy/18", "https://headhunter.kg/vacancy/18", "headhunter.kg"),
        ("https://hh.uz/vacancy/19", "https://hh.uz/vacancy/19", "hh.uz"),
        ("https://hh1.az/vacancy/20", "https://hh1.az/vacancy/20", "hh1.az"),
    ],
)
def test_detect_covers_regional_hosts_and_subdomains(
    url: str, canonical_url: str, host: str
) -> None:
    hit = Connector().detect(url)
    assert hit is not None and hit.canonical.canonical_url == canonical_url
    assert hit.canonical.host == host


@pytest.mark.parametrize(
    "url",
    [
        "https://hh.ru/search/vacancy?text=data+engineer",
        "https://hh.ru/employer/1002",
        "https://hh.ru/resume/9d1f0c2e8a7b6543",
        "https://hh.ru/vacancy/",
        "https://hh.ru/vacancy/abc",
        "https://hh.ru/vacancy/123/responses",
        "https://hh.ru/",
        "https://not-hh.example/vacancy/123",
        "mailto:jobs@hh.ru",
        "hh.ru/vacancy/123",
    ],
)
def test_detect_ignores_everything_that_is_not_one_vacancy(url: str) -> None:
    assert Connector().detect(url) is None


def test_detect_through_the_registry_beats_the_generic_connector() -> None:
    from careeros.modules.platform.registry import get_registry

    hit = detect(SHARE_URL, get_registry())
    assert hit is not None and hit.platform == Platform.hh and hit.confidence == 0.95
    assert hit.canonical.canonical_url == CANONICAL


def test_canonicalize_handles_references_and_ids() -> None:
    c = Connector()
    private = SourceRef(
        kind=SourceKind.telegram_message,
        value=f"смотри {SHARE_URL}",
        metadata={"locale": "ru"},
    )
    source = c.canonicalize(private)
    assert source.canonical_url == CANONICAL and source.external_id == VACANCY_ID
    assert source.private is True and source.locale == "ru"

    by_id = c.canonicalize(SourceRef(kind=SourceKind.provider_id, value=VACANCY_ID))
    assert by_id.canonical_url == CANONICAL and by_id.external_id == VACANCY_ID
    regional = c.canonicalize(
        SourceRef(kind=SourceKind.provider_id, value="42", metadata={"host": "hh.kz"})
    )
    assert regional.canonical_url == "https://hh.kz/vacancy/42" and regional.host == "hh.kz"

    # a non-vacancy hh URL still canonicalises (the generic normalisation), with no vacancy id
    search = c.canonicalize("https://hh.ru/search/vacancy?text=dbt&utm_source=x")
    assert search.canonical_url == "https://hh.ru/search/vacancy?text=dbt"
    assert search.external_id is None

    with pytest.raises(ValueError):
        c.canonicalize(SourceRef(kind=SourceKind.provider_id, value="not-a-number"))
    with pytest.raises(ValueError):
        c.canonicalize(SourceRef(kind=SourceKind.text, value="no url here"))


# --------------------------------------------------------------------------- the api strategy


async def test_read_api_200_yields_a_posting_with_provenance(settings: Settings) -> None:
    mock = ReadMock(api=[httpx.Response(200, json=_json(f"vacancy_{VACANCY_ID}.json"))])
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    source = c.canonicalize(SHARE_URL)
    async with ctx.http:
        read = await c.fetch_job(ctx, source, FetchBudget(), use_cache=False)

    assert mock.paths() == [f"/vacancies/{VACANCY_ID}"]
    request = mock.requests[0]
    assert request.headers["Authorization"] == "Bearer user-token"
    assert request.headers["HH-User-Agent"] == ctx.settings.platform_user_agent
    assert "host" not in request.url.params  # hh.ru is the canonical site

    posting = read.posting
    assert posting is not None
    assert posting.title == "Analytics Engineer (dbt, ClickHouse)"
    assert posting.company == "Lumen Analytics" and posting.location == "Санкт-Петербург"
    assert posting.external_id == VACANCY_ID and posting.url == CANONICAL
    assert posting.canonical_url == CANONICAL and posting.strategy == FetchStrategy.api
    assert posting.fetched_at == NOW
    assert posting.published_at == datetime(2026, 8, 21, 6, 30, tzinfo=UTC)
    assert posting.expires_at is None
    assert posting.is_archive is False and posting.archive_ts is None
    assert posting.relation == SourceRelation.primary
    assert posting.quality == 1.0 and posting.completeness == 1.0
    assert posting.content_hash and posting.fingerprint

    extraction = posting.extraction
    assert extraction is not None and extraction.employment_type == EmploymentType.full_time
    assert extraction.remote_policy == RemotePolicy.remote_global
    assert extraction.compensation is not None
    assert extraction.compensation.currency == "RUB"
    assert extraction.compensation.period == CompensationPeriod.month
    assert extraction.technologies == ["dbt", "ClickHouse", "SQL", "Python", "Airflow"]

    assert {e.source for e in posting.field_evidence} == {"board_api"}
    assert {e.confidence for e in posting.field_evidence} == {0.95}
    fields = {e.field for e in posting.field_evidence}
    assert {
        "title",
        "company",
        "location",
        "compensation",
        "employment_type",
        "experience",
        "technologies",
    } <= fields

    assert [a.strategy for a in read.attempts] == [FetchStrategy.api]
    assert read.attempts[0].ok and read.attempts[0].status_code == 200
    ingest = posting.to_ingest()
    assert ingest.source == "hh" and ingest.external_id == VACANCY_ID
    assert ingest.raw_payload is not None
    assert ingest.raw_payload["provenance"]["strategy"] == "api"
    assert ingest.raw_payload["provenance"]["canonical_url"] == CANONICAL


async def test_read_regional_host_pins_the_site(settings: Settings) -> None:
    payload = _json(f"vacancy_{VACANCY_ID}.json")
    mock = ReadMock(api=[httpx.Response(200, json=payload)])
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(ctx, c.canonicalize("https://hh.kz/vacancy/136537758"))
    assert artifact.status_code == 200
    assert mock.requests[0].url.params["host"] == "hh.kz"


async def test_read_403_records_the_attempt_then_tries_the_fallbacks(settings: Settings) -> None:
    mock = ReadMock(api=[httpx.Response(403, json=FORBIDDEN_BODY)])
    ctx = _ctx(settings, mock)
    c = Connector()
    async with ctx.http:
        with pytest.raises(JobReadError) as exc:
            await c.fetch_job(ctx, c.canonicalize(SHARE_URL), FetchBudget(), use_cache=False)

    error = exc.value
    assert [a.strategy for a in error.attempts] == [
        FetchStrategy.api,
        FetchStrategy.jina,
        FetchStrategy.wayback,
    ]
    assert [a.error_type for a in error.attempts] == [
        "forbidden",
        "not_found",
        "archive_lookup_failed",
    ]
    assert error.attempts[0].status_code == 403
    assert error.attempts[0].error_message == (
        "hh API refused anonymous/app access (403) — connect with "
        "`careeros platform connect hh` or set CAREEROS_HH_CLIENT_ID/SECRET"
    )
    assert "forbidden" in error.diagnostics and "403" in error.diagnostics
    assert "authorization" not in {k.lower() for k in mock.requests[0].headers}
    assert mock.paths("r.jina.example") == [f"/{CANONICAL}"]


async def test_read_403_oauth_points_at_the_token_not_the_credentials(settings: Settings) -> None:
    body = {"errors": [{"type": "oauth", "value": "token_expired"}]}
    mock = ReadMock(api=[httpx.Response(403, json=body)])
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(ctx, c.canonicalize(SHARE_URL))
    assert artifact.error_type == "forbidden" and artifact.usable is False
    assert "refresh hh" in (artifact.error_message or "")


async def test_read_404_is_a_closed_job_and_wayback_answers_with_the_archive(
    settings: Settings,
) -> None:
    cdx = httpx.Response(
        200,
        text=json.dumps(
            [
                ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
                [
                    "ru,hh)/vacancy/136537758",
                    "20260822101500",
                    CANONICAL,
                    "text/html",
                    "200",
                    "AAA",
                    "40000",
                ],
            ]
        ),
        headers={"content-type": "application/json"},
    )
    mock = ReadMock(
        api=[httpx.Response(404, json={"errors": [{"type": "not_found"}]})],
        cdx=cdx,
        snapshot=httpx.Response(
            200, text=_text(f"wayback_{VACANCY_ID}.html"), headers={"content-type": "text/html"}
        ),
    )
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        read = await c.fetch_job(ctx, c.canonicalize(SHARE_URL), FetchBudget(), use_cache=False)

    assert [a.strategy for a in read.attempts] == [
        FetchStrategy.api,
        FetchStrategy.jina,
        FetchStrategy.wayback,
    ]
    assert read.attempts[0].error_type == "not_found"
    posting = read.posting
    assert posting is not None
    assert posting.strategy == FetchStrategy.wayback
    assert posting.is_archive is True
    assert posting.archive_ts == datetime(2026, 8, 22, 10, 15, tzinfo=UTC)
    assert posting.relation == SourceRelation.historical_version_of
    assert posting.title == "Analytics Engineer (dbt, ClickHouse)"
    assert posting.company == "Lumen Analytics"
    assert posting.canonical_url == CANONICAL and posting.external_id == VACANCY_ID
    assert {e.source for e in posting.field_evidence} == {"archive"}
    assert posting.fetched_at == NOW  # the read happened now; the capture is archive_ts


async def test_read_404_marks_the_artifact_as_a_closed_candidate(settings: Settings) -> None:
    mock = ReadMock(api=[httpx.Response(404, json={"errors": [{"type": "not_found"}]})])
    ctx = _ctx(settings, mock, tokens=True)
    async with ctx.http:
        artifact = await Connector().fetch_job_api(ctx, Connector().canonicalize(SHARE_URL))
    assert artifact.status_code == 404 and artifact.error_type == "not_found"
    assert artifact.flags == ["job_closed"]
    assert VACANCY_ID in (artifact.error_message or "")


async def test_read_429_is_retried_with_retry_after(settings: Settings) -> None:
    mock = ReadMock(
        api=[
            httpx.Response(
                429, json={"errors": [{"type": "rate_limited"}]}, headers={"retry-after": "0"}
            ),
            httpx.Response(200, json=_json(f"vacancy_{VACANCY_ID}.json")),
        ]
    )
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        read = await c.fetch_job(ctx, c.canonicalize(SHARE_URL), FetchBudget(), use_cache=False)
    assert mock.paths() == [f"/vacancies/{VACANCY_ID}"] * 2
    assert read.posting is not None and read.posting.strategy == FetchStrategy.api
    assert [a.status_code for a in read.attempts] == [200]


async def test_read_429_that_never_clears_is_reported_as_rate_limited(settings: Settings) -> None:
    responses = [httpx.Response(429, json={"errors": [{"type": "rate_limited"}]}) for _ in range(3)]
    mock = ReadMock(api=responses)
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(ctx, c.canonicalize(SHARE_URL))
    assert artifact.status_code == 429 and artifact.error_type == "rate_limited"
    assert len(mock.paths()) == 3  # the initial request plus the two retries


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (
            httpx.Response(
                200, text="<html>oops</html>", headers={"content-type": "application/json"}
            ),
            "malformed",
        ),
        (httpx.Response(200, json=[1, 2, 3]), "malformed"),
        (httpx.Response(500, text="boom"), "upstream"),
        (httpx.Response(503, text="maintenance"), "upstream"),
        (httpx.Response(400, json={"errors": [{"type": "bad_user_agent"}]}), "http_error"),
    ],
)
async def test_read_status_semantics_never_collapse_to_none(
    settings: Settings, response: httpx.Response, error_type: str
) -> None:
    mock = ReadMock(api=[response] * 3)
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(ctx, c.canonicalize(SHARE_URL))
    assert artifact.error_type == error_type
    assert artifact.error_message and artifact.usable is False
    assert artifact.raw_json is None


async def test_read_network_failure_is_classified(settings: Settings) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    s = _settings(settings)
    ctx = ConnectorContext(
        settings=s, http=build_http(s, transport=httpx.MockTransport(boom)), now=NOW
    )
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(ctx, c.canonicalize(SHARE_URL))
    assert artifact.error_type == "timeout" and artifact.status_code is None


async def test_archived_payload_is_flagged_closed(settings: Settings) -> None:
    payload = _json(f"vacancy_{VACANCY_ID}.json") | {"archived": True}
    mock = ReadMock(api=[httpx.Response(200, json=payload)])
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(ctx, c.canonicalize(SHARE_URL))
    assert artifact.status_code == 200 and artifact.flags == ["job_closed"]
    posting = c.extract_job(artifact)
    assert posting.title == "Analytics Engineer (dbt, ClickHouse)"


async def test_a_non_vacancy_source_is_refused_without_a_request(settings: Settings) -> None:
    mock = ReadMock()
    ctx = _ctx(settings, mock, tokens=True)
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(
            ctx, c.canonicalize("https://hh.ru/search/vacancy?text=dbt")
        )
    assert artifact.error_type == "not_a_vacancy" and mock.requests == []


# --------------------------------------------------------------------------- application tokens


async def test_application_token_is_requested_once_and_reused(settings: Settings) -> None:
    now = datetime.now(UTC)
    mock = ReadMock(
        api=[
            httpx.Response(200, json=_json(f"vacancy_{VACANCY_ID}.json")),
            httpx.Response(200, json=_json(f"vacancy_{VACANCY_ID}.json")),
        ],
        token=httpx.Response(
            200, json={"access_token": "app-token", "token_type": "bearer", "expires_in": 1209600}
        ),
    )
    ctx = _ctx(settings, mock, creds=True, now=now)
    c = Connector()
    source = c.canonicalize(SHARE_URL)
    async with ctx.http:
        first = await c.fetch_job_api(ctx, source)
        second = await c.fetch_job_api(ctx, source)
    assert first.status_code == 200 and second.status_code == 200
    assert mock.paths() == ["/token", f"/vacancies/{VACANCY_ID}", f"/vacancies/{VACANCY_ID}"]
    reads = [r for r in mock.requests if r.url.path != "/token"]
    assert {r.headers["Authorization"] for r in reads} == {"Bearer app-token"}
    token_request = mock.requests[0]
    assert b"grant_type=client_credentials" in token_request.content
    assert "authorization" not in {k.lower() for k in token_request.headers}


async def test_a_failed_application_token_degrades_to_an_anonymous_read(
    settings: Settings,
) -> None:
    mock = ReadMock(
        api=[httpx.Response(403, json=FORBIDDEN_BODY)],
        token=httpx.Response(403, json={"error": "invalid_client"}),
    )
    ctx = _ctx(settings, mock, creds=True)
    c = Connector()
    async with ctx.http:
        artifact = await c.fetch_job_api(ctx, c.canonicalize(SHARE_URL))
    assert artifact.error_type == "forbidden"
    assert any("application token request failed" in w for w in ctx.warnings)
    read = next(r for r in mock.requests if r.url.path != "/token")
    assert "authorization" not in {k.lower() for k in read.headers}


async def test_no_credentials_means_no_token_request(settings: Settings) -> None:
    mock = ReadMock(api=[httpx.Response(403, json=FORBIDDEN_BODY)])
    ctx = _ctx(settings, mock)
    c = Connector()
    async with ctx.http:
        await c.fetch_job_api(ctx, c.canonicalize(SHARE_URL))
    assert mock.paths() == [f"/vacancies/{VACANCY_ID}"] and ctx.warnings == []


# --------------------------------------------------------------------------- live (opt-in)


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("CAREEROS_LIVE_TESTS") != "1",
    reason="live read of hh.ru — set CAREEROS_LIVE_TESTS=1",
)
async def test_live_read_of_the_reference_vacancy(settings: Settings) -> None:
    """The real ``https://hh.ru/vacancy/136537758``: current, closed, archived or blocked.

    All four are acceptable outcomes — what must never happen is a read that says nothing about
    why it ended the way it did.
    """
    c = Connector()
    s = settings.model_copy(update={"job_fetch_max_total_s": 60.0})
    async with build_http(s) as http:
        ctx = ConnectorContext(settings=s, http=http, now=datetime.now(UTC))
        source = c.canonicalize(SHARE_URL)
        assert source.canonical_url == CANONICAL
        try:
            read = await c.fetch_job(ctx, source, FetchBudget.from_settings(s), use_cache=False)
        except JobReadError as exc:
            assert exc.attempts, "a failed read must still report its attempts"
            assert exc.diagnostics.strip()
            assert all(a.error_type for a in exc.attempts)
            return
    posting = read.posting
    assert posting is not None and posting.title.strip()
    assert posting.strategy in (FetchStrategy.api, FetchStrategy.jina, FetchStrategy.wayback)
    assert posting.canonical_url == CANONICAL
    assert read.attempts and read.diagnostics.strip()
