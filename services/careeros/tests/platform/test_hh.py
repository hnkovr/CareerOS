# ruff: noqa: E501
"""hh.ru connector: official JSON API through a mock transport + Russian paste parsers.

Synthetic persona only (Dana Kovalenko; Northwind Commerce / Lumen Analytics / Orbit Fintech).
Payload shapes follow https://api.hh.ru/openapi/redoc; nothing here touches the network.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from careeros.core.config import Settings
from careeros.modules.opportunities.enums import CompensationPeriod, EmploymentType, RemotePolicy
from careeros.modules.platform.base import ConnectorContext, NotConnected
from careeros.modules.platform.connectors.hh import parsers as hh_parsers
from careeros.modules.platform.connectors.hh.connector import Connector
from careeros.modules.platform.enums import ApplicationStatus, AuthKind, SyncMethod
from careeros.modules.platform.http import build_http
from careeros.modules.platform.schemas import JobQuery
from careeros.modules.platform.tokens import OAuthTokens
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "hh"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
RESUME_ID = "9d1f0c2e8a7b6543"


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _text(name: str) -> str:
    return (FIXTURES / name).read_text()


class HHMock:
    """``httpx.MockTransport`` handler: routes by path (+ ``page``), records every request."""

    def __init__(self, fail: dict[str, int] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.fail = fail or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.fail:
            code = self.fail[path]
            body: dict[str, Any] = (
                {
                    "errors": [{"type": "oauth", "value": "token_expired"}],
                    "description": "Forbidden",
                }
                if code == 403
                else {"errors": [{"type": "unknown"}]}
            )
            return httpx.Response(code, json=body)
        if path == "/me":
            return httpx.Response(200, json=_json("me.json"))
        if path == "/resumes/mine":
            return httpx.Response(200, json=_json("resumes_mine.json"))
        if path == f"/resumes/{RESUME_ID}":
            return httpx.Response(200, json=_json("resume.json"))
        if path.endswith("/similar_vacancies"):
            return httpx.Response(200, json=_json("vacancies.json"))
        if path == "/vacancies":
            return httpx.Response(200, json=_json("vacancies.json"))
        if path == "/vacancies/112233445":
            return httpx.Response(200, json=_json("vacancy_112233445.json"))
        if path == "/suggests/areas":
            items = [{"id": "2", "text": "Санкт-Петербург", "url": "https://api.hh.ru/areas/2"}]
            return httpx.Response(200, json={"items": items})
        if path == "/negotiations":
            page = int(request.url.params.get("page", "0"))
            return httpx.Response(200, json=_json(f"negotiations_p{page + 1}.json"))
        return httpx.Response(404, json={"errors": [{"type": "not_found"}]})

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def _ctx(settings: Settings, http: httpx.AsyncClient, *, tokens: bool = True) -> ConnectorContext:
    return ConnectorContext(
        settings=settings,
        http=http,
        tokens=OAuthTokens(access_token="t") if tokens else None,  # type: ignore[arg-type]
        now=NOW,
    )


def _with_creds(settings: Settings) -> Settings:
    return settings.model_copy(update={"hh_client_id": "cid", "hh_client_secret": SecretStr("s")})


# --------------------------------------------------------------------------- capabilities / oauth


def test_capabilities_declare_api_and_paste() -> None:
    caps = Connector.capabilities
    assert caps.platform == Platform.hh
    assert caps.profile == [SyncMethod.api, SyncMethod.paste]
    assert caps.jobs == [SyncMethod.api, SyncMethod.paste]
    assert caps.applications == [SyncMethod.api, SyncMethod.paste]
    assert caps.official_api is True and caps.auth == AuthKind.oauth2
    assert caps.email_fallback is False and caps.manual_capture is True


def test_oauth_config_requires_client_credentials(settings: Settings) -> None:
    with pytest.raises(NotConnected) as exc:
        Connector().oauth_config(settings)
    assert "CAREEROS_HH_CLIENT_ID" in str(exc.value) and "dev.hh.ru" in str(exc.value)


def test_oauth_config_with_credentials(settings: Settings) -> None:
    cfg = Connector().oauth_config(_with_creds(settings))
    assert cfg is not None
    assert cfg.authorize_url == "https://hh.ru/oauth/authorize"
    assert cfg.token_url == "https://api.hh.ru/token"
    assert cfg.client_id == "cid" and cfg.client_secret.get_secret_value() == "s"
    assert cfg.scopes == [] and cfg.token_auth == "body"
    assert cfg.redirect_uri == "http://localhost:8000/api/platform/oauth/hh/callback"


# --------------------------------------------------------------------------- profile (API)


async def test_read_profile_maps_newest_resume(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        profile = await Connector().read_profile(_ctx(settings, http))

    # the newest resume by updated_at is chosen, not the first item of /resumes/mine
    assert mock.paths() == ["/resumes/mine", f"/resumes/{RESUME_ID}"]
    for req in mock.requests:
        assert req.headers["hh-user-agent"] == settings.platform_user_agent
        assert req.headers["user-agent"] == settings.platform_user_agent
        assert req.headers["authorization"] == "Bearer t"

    assert profile.platform == Platform.hh and profile.capture_method == CaptureMethod.api
    assert profile.external_id == RESUME_ID
    assert profile.profile_url == f"https://hh.ru/resume/{RESUME_ID}"
    assert profile.headline == "Senior Data Engineer"
    assert profile.about is not None and profile.about.startswith("Строю аналитические платформы")
    assert profile.skills == ["Python", "SQL", "dbt", "ClickHouse", "Dagster", "Airflow"]
    assert [e.company for e in profile.experience] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
    ]
    first = profile.experience[0]
    assert first.title == "Senior Data Engineer" and first.period == "2023-01-01 – now"
    assert first.description is not None and "40 %" in first.description
    assert profile.experience[1].period == "2020-03-01 – 2022-12-31"
    assert profile.experience[2].description is None
    assert profile.rates == {"salary": 450000, "currency": "RUB"}
    assert profile.preferences["schedules"] == ["remote", "flexible"]
    assert profile.preferences["employments"] == ["full"]
    assert profile.preferences["area"] == "Москва"
    assert profile.preferences["work_formats"] == ["REMOTE"]
    assert profile.captured_at == NOW
    from careeros.modules.platform.connectors.hh.mapping import public_resume

    assert profile.raw_payload == public_resume(_json("resume.json"))
    assert "first_name" not in (profile.raw_payload or {})
    assert profile.to_snapshot().preferences["external_id"] == RESUME_ID


async def test_read_profile_without_tokens_is_not_connected(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        with pytest.raises(NotConnected):
            await Connector().read_profile(_ctx(settings, http, tokens=False))
    assert mock.requests == []


# --------------------------------------------------------------------------- jobs (API)


async def test_search_jobs_params_and_mapping_without_token(settings: Settings) -> None:
    mock = HHMock()
    query = JobQuery(
        text="data engineer",
        remote=True,
        salary_min=300000,
        currency="RUB",
        posted_since=date(2026, 8, 1),
        limit=50,
        extra={"area": "1"},
    )
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        jobs = await Connector().search_jobs(_ctx(settings, http, tokens=False), query)

    assert mock.paths() == ["/vacancies"]
    req = mock.requests[0]
    p = req.url.params
    assert p["text"] == "data engineer" and p["area"] == "1"
    assert p["schedule"] == "remote" and p["work_format"] == "REMOTE"
    assert p["salary"] == "300000" and p["currency"] == "RUR"  # hh's code for roubles
    assert p["date_from"] == "2026-08-01"
    assert p["per_page"] == "50" and p["page"] == "0" and p["order_by"] == "publication_time"
    assert "authorization" not in req.headers  # vacancy search is public
    assert req.headers["hh-user-agent"] == settings.platform_user_agent
    assert req.headers["user-agent"] == settings.platform_user_agent

    assert [j.external_id for j in jobs] == ["112233445", "112233446"]
    first, second = jobs
    assert first.platform == Platform.hh and first.url == "https://hh.ru/vacancy/112233445"
    assert first.title == "Senior Data Engineer (dbt, ClickHouse)"
    assert first.company == "Northwind Commerce" and first.location == "Москва"
    assert first.posted_at == datetime(2026, 8, 20, 7, 0, tzinfo=UTC)
    assert first.raw_text.startswith(
        "Senior Data Engineer (dbt, ClickHouse) @ Northwind Commerce\nМосква\n"
    )
    assert "<highlighttext>" not in first.raw_text and "dbt и ClickHouse" in first.raw_text
    ex = first.extraction
    assert ex is not None
    assert (
        ex.title == first.title and ex.company == "Northwind Commerce" and ex.location == "Москва"
    )
    assert ex.remote_policy == RemotePolicy.remote_global
    assert ex.employment_type == EmploymentType.full_time
    assert ex.compensation is not None
    assert (ex.compensation.min, ex.compensation.max) == (300000, 400000)
    assert ex.compensation.currency == "RUB" and ex.compensation.period == CompensationPeriod.month
    assert (
        ex.compensation.type == "salary"
        and ex.compensation.raw == "300 000 – 400 000 RUB (на руки)"
    )
    assert ex.summary is not None and "Развитие платформы данных" in ex.summary
    assert "<highlighttext>" not in ex.summary
    assert ex.requirements == ["Опыт работы: От 3 до 6 лет"]
    assert first.raw_payload == _json("vacancies.json")["items"][0]

    assert second.company == "Lumen Analytics" and second.location == "Санкт-Петербург"
    assert second.extraction is not None and second.extraction.compensation is None
    assert second.extraction.remote_policy == RemotePolicy.hybrid  # work_format HYBRID
    assert second.extraction.employment_type == EmploymentType.part_time
    assert second.to_ingest().source == "hh"


async def test_search_jobs_similar_to_resume_uses_newest_resume(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        ctx = _ctx(settings, http)
        jobs = await Connector().search_jobs(
            ctx, JobQuery(limit=10, extra={"similar_to_resume": True})
        )
        again = await Connector().search_jobs(ctx, JobQuery(limit=10))  # empty text + token → same
        explicit = await Connector().search_jobs(
            ctx, JobQuery(extra={"similar_to_resume": "abc123"})
        )

    assert mock.paths() == [
        "/resumes/mine",
        f"/resumes/{RESUME_ID}/similar_vacancies",
        "/resumes/mine",
        f"/resumes/{RESUME_ID}/similar_vacancies",
        "/resumes/abc123/similar_vacancies",
    ]
    assert mock.requests[1].url.params["per_page"] == "10"
    assert mock.requests[1].headers["authorization"] == "Bearer t"
    assert len(jobs) == 2 and len(again) == 2 and len(explicit) == 2


async def test_search_jobs_without_text_or_token_is_not_connected(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        with pytest.raises(NotConnected):
            await Connector().search_jobs(_ctx(settings, http, tokens=False), JobQuery())
    assert mock.requests == []


async def test_search_jobs_full_details_tolerates_a_failed_detail(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        jobs = await Connector().search_jobs(
            _ctx(settings, http, tokens=False), JobQuery(text="dbt", extra={"full": True})
        )
    assert mock.paths() == ["/vacancies", "/vacancies/112233445", "/vacancies/112233446"]
    full, partial = jobs
    assert full.extraction is not None
    assert full.extraction.technologies == ["dbt", "ClickHouse", "Python", "SQL"]
    assert "Мы строим платформу данных" in full.raw_text and "<p>" not in full.raw_text
    assert "производительность & стоимость" in full.raw_text
    assert full.raw_payload is not None and full.raw_payload["detail"]["id"] == "112233445"
    # the 404 for the second vacancy is recorded on the item, not fatal for the search
    assert partial.title == "Analytics Engineer"
    assert partial.raw_payload is not None and "404" in partial.raw_payload["detail_error"]


async def test_search_jobs_resolves_location_via_area_suggest(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        await Connector().search_jobs(
            _ctx(settings, http, tokens=False), JobQuery(text="dbt", location="Санкт-Петербург")
        )
    assert mock.paths() == ["/suggests/areas", "/vacancies"]
    assert mock.requests[0].url.params["text"] == "Санкт-Петербург"
    assert mock.requests[1].url.params["area"] == "2"


# --------------------------------------------------------------------------- applications (API)


async def test_application_statuses_paginate_until_pages(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        obs = await Connector().application_statuses(_ctx(settings, http))

    assert mock.paths() == ["/negotiations", "/negotiations"]
    assert [r.url.params["page"] for r in mock.requests] == ["0", "1"]  # stops at pages == 2
    p = mock.requests[0].url.params
    assert p["order_by"] == "updated_at" and p["order"] == "desc" and p["per_page"] == "50"
    assert mock.requests[0].headers["authorization"] == "Bearer t"

    assert [o.external_id for o in obs] == ["n-5001", "n-5002", "n-5003", "n-5004"]
    assert [o.status for o in obs] == [
        ApplicationStatus.invited,
        ApplicationStatus.viewed,  # state=response + viewed_by_opponent
        ApplicationStatus.rejected,
        ApplicationStatus.applied,
    ]
    inv = obs[0]
    assert inv.platform == Platform.hh and inv.status_raw == "Приглашение"
    assert (
        inv.job_title == "Senior Data Engineer (dbt, ClickHouse)"
        and inv.company == "Northwind Commerce"
    )
    assert inv.job_url == "https://hh.ru/vacancy/112233445"
    assert inv.applied_at == datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    assert inv.updated_at_platform == datetime(2026, 8, 22, 12, 30, tzinfo=UTC)
    assert inv.raw_payload == _json("negotiations_p1.json")["items"][0]
    assert obs[2].status_raw == "Отказ" and obs[2].company == "Orbit Fintech"
    assert obs[3].status_raw == "Отклик" and obs[3].job_url == "https://hh.ru/vacancy/112233448"


async def test_application_statuses_cap_at_five_pages(settings: Settings) -> None:
    calls: list[int] = []
    template = _json("negotiations_p2.json")["items"][1]

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        calls.append(page)
        item = {**template, "id": f"n-{page}"}
        body = {"found": 999, "pages": 99, "page": page, "per_page": 1, "items": [item]}
        return httpx.Response(200, json=body)

    async with build_http(settings, transport=httpx.MockTransport(handler)) as http:
        obs = await Connector().application_statuses(_ctx(settings, http))
    assert calls == [0, 1, 2, 3, 4] and [o.external_id for o in obs] == [f"n-{i}" for i in range(5)]


async def test_application_statuses_without_tokens(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        with pytest.raises(NotConnected):
            await Connector().application_statuses(_ctx(settings, http, tokens=False))
    assert mock.requests == []


# --------------------------------------------------------------------------- auth errors / whoami / doctor


async def test_rejected_tokens_map_to_not_connected(settings: Settings) -> None:
    async with build_http(
        settings, transport=httpx.MockTransport(HHMock(fail={"/me": 401}))
    ) as http:
        with pytest.raises(NotConnected):
            await Connector().whoami(_ctx(settings, http))
    # hh answers 403 {"errors":[{"type":"oauth","value":"token_expired"}]} for bad/expired tokens
    async with build_http(
        settings, transport=httpx.MockTransport(HHMock(fail={"/resumes/mine": 403}))
    ) as http:
        with pytest.raises(NotConnected) as exc:
            await Connector().read_profile(_ctx(settings, http))
    assert "token_expired" in str(exc.value)


async def test_whoami(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        info = await Connector().whoami(_ctx(settings, http))
    assert mock.paths() == ["/me"]
    assert info.account_id == "70011223" and info.label == "Dana Kovalenko"
    assert info.raw == {
        "id": "70011223",
        "email": "dana.kovalenko@example.com",
        "auth_type": "applicant",
        "is_applicant": True,
    }


async def test_doctor_all_ok_with_mock_transport(settings: Settings) -> None:
    mock = HHMock()
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        checks = await Connector().doctor(_ctx(_with_creds(settings), http))
    assert [c.name for c in checks] == [
        "capabilities",
        "client_credentials",
        "tokens",
        "api_reachable",
        "me",
    ]
    assert all(c.ok for c in checks), [c.model_dump() for c in checks]
    assert mock.paths() == ["/vacancies", "/me"]
    probe = mock.requests[0]
    assert probe.url.params["per_page"] == "1" and "authorization" not in probe.headers


async def test_doctor_reports_failed_probes_without_raising(settings: Settings) -> None:
    mock = HHMock(fail={"/vacancies": 500, "/me": 401})
    async with build_http(settings, transport=httpx.MockTransport(mock)) as http:
        checks = await Connector().doctor(_ctx(settings, http))
    by_name = {c.name: c for c in checks}
    assert by_name["client_credentials"].ok is False and by_name["client_credentials"].fix
    assert by_name["api_reachable"].ok is False and "500" in by_name["api_reachable"].detail
    assert by_name["api_reachable"].fix
    assert by_name["me"].ok is False and "refresh hh" in (by_name["me"].fix or "")


# --------------------------------------------------------------------------- paste parsers


def test_parse_profile_text_from_resume_page() -> None:
    text = _text("paste_resume.txt")
    profile = Connector().parse_profile_text(text)
    assert profile.platform == Platform.hh and profile.capture_method == CaptureMethod.paste
    assert profile.headline == "Senior Data Engineer"  # the desired position, not the person's name
    assert profile.skills == ["Python", "SQL", "dbt", "ClickHouse", "Dagster", "Airflow"]
    assert (
        profile.about
        == "Строю аналитические платформы: dbt + ClickHouse, оркестрация Dagster. Люблю измеримые результаты."
    )
    assert [(e.company, e.title) for e in profile.experience] == [
        ("Northwind Commerce", "Senior Data Engineer"),
        ("Lumen Analytics", "Lead Analytics Engineer"),
        ("Orbit Fintech", "Data Engineer"),
    ]
    assert profile.experience[0].period == "Январь 2023 — настоящее время"
    assert (
        profile.experience[0].description
        == "Построила платформу аналитики на dbt + ClickHouse, оркестрация в Dagster. Сократила стоимость хранения на 40 %."
    )
    assert profile.experience[1].period == "Март 2020 — Декабрь 2022"
    assert profile.experience[2].description == "ETL на Airflow, хранилище на PostgreSQL."
    assert profile.rates == {"salary": 450000, "currency": "RUB", "raw": "450 000 ₽ на руки"}
    assert profile.preferences["employments"] == ["полная занятость"]
    assert profile.preferences["schedules"] == ["удаленная работа"]
    assert profile.preferences["area"] == "Москва"
    assert profile.preferences["specializations"] == ["Дата-инженер"]
    assert profile.raw_text == text


def test_parse_profile_text_from_my_resumes_list() -> None:
    text = (
        "Мои резюме\nSenior Data Engineer\nОбновлено 20 августа 2026, 10:15\n"
        "Показов: 145 · Просмотров: 32 · Приглашений: 3\nПоднять в поиске\n"
    )
    profile = Connector().parse_profile_text(text)
    assert profile.headline == "Senior Data Engineer"
    assert profile.skills == [] and profile.experience == [] and profile.raw_text == text


def test_parse_jobs_text_from_search_page() -> None:
    text = _text("paste_vacancies.txt")
    jobs = hh_parsers.parse_jobs(text, now=NOW)
    assert [j.title for j in jobs] == [
        "Senior Data Engineer (dbt, ClickHouse)",
        "Analytics Engineer",
        "Data Platform Lead",
    ]
    assert [j.company for j in jobs] == ["Northwind Commerce", "Lumen Analytics", "Orbit Fintech"]
    assert [j.location for j in jobs] == ["Москва", "Санкт-Петербург", "Москва, м. Тверская"]
    assert [j.posted_at for j in jobs] == [
        datetime(2026, 8, 20, tzinfo=UTC),
        NOW.replace(day=24),
        NOW,
    ]
    a, b, c = (j.extraction for j in jobs)
    assert a is not None and b is not None and c is not None
    assert a.compensation is not None and (a.compensation.min, a.compensation.max) == (300000, None)
    assert a.compensation.currency == "RUB" and a.compensation.period == CompensationPeriod.month
    assert (
        a.compensation.raw == "от 300 000 ₽ за месяц, на руки" and a.compensation.type == "salary"
    )
    assert a.remote_policy == RemotePolicy.remote_global and a.requirements == ["Опыт 3–6 лет"]
    assert b.compensation is not None and (b.compensation.min, b.compensation.max) == (
        250000,
        350000,
    )
    assert (
        b.remote_policy == RemotePolicy.remote_global
        and b.employment_type == EmploymentType.full_time
    )
    assert c.compensation is None and c.remote_policy == RemotePolicy.hybrid
    assert c.requirements == ["Опыт более 6 лет"] and c.company == "Orbit Fintech"
    assert jobs[0].raw_text.startswith("Senior Data Engineer (dbt, ClickHouse)\nот 300 000 ₽")
    assert jobs[0].platform == Platform.hh and jobs[0].url is None
    assert [j.title for j in Connector().parse_jobs_text(text)] == [j.title for j in jobs]


def test_parse_jobs_text_splits_cards_without_blank_lines() -> None:
    text = (
        "Data Engineer\nот 200 000 ₽ за месяц\nNorthwind Commerce\nМосква\nОткликнуться\n"
        "Analytics Engineer\nLumen Analytics\nСанкт-Петербург\nОткликнуться\n"
    )
    jobs = hh_parsers.parse_jobs(text, now=NOW)
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Data Engineer", "Northwind Commerce", "Москва"),
        ("Analytics Engineer", "Lumen Analytics", "Санкт-Петербург"),
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("от 300 000 ₽ за месяц, на руки", (300000, None, "RUB", CompensationPeriod.month)),
        (
            "250 000 – 350 000 ₽ за месяц, до вычета налогов",
            (250000, 350000, "RUB", CompensationPeriod.month),
        ),
        ("до 4 000 $ за месяц", (None, 4000, "USD", CompensationPeriod.month)),
        ("от 25 € в час", (25, None, "EUR", CompensationPeriod.hour)),
        ("1 200 000 ₸", (1200000, 1200000, "KZT", CompensationPeriod.month)),
        ("от 150 000 до 200 000 руб. на руки", (150000, 200000, "RUB", CompensationPeriod.month)),
        ("Опыт 3–6 лет", None),
        ("20 августа", None),
        ("Northwind Commerce", None),
    ],
)
def test_parse_salary_line(line: str, expected: tuple[Any, ...] | None) -> None:
    comp = hh_parsers.parse_salary_line(line)
    if expected is None:
        assert comp is None
    else:
        assert comp is not None
        assert (comp.min, comp.max, comp.currency, comp.period) == expected
        assert comp.raw == line and comp.type == "salary"


def test_parse_applications_text_from_negotiations_page() -> None:
    text = _text("paste_negotiations.txt")
    obs = hh_parsers.parse_applications(text, now=NOW)
    assert [o.job_title for o in obs] == [
        "Senior Data Engineer (dbt, ClickHouse)",
        "Analytics Engineer",
        "Data Platform Lead",
        "Data Engineer",
    ]
    assert [o.company for o in obs] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
        "Orbit Fintech",
    ]
    assert [o.status for o in obs] == [
        ApplicationStatus.invited,
        ApplicationStatus.viewed,
        ApplicationStatus.rejected,
        ApplicationStatus.applied,  # «Не просмотрен» must not read as «Просмотрен»
    ]
    assert [o.status_raw for o in obs] == [
        "Приглашение",
        "Отклик · Просмотрен",
        "Отказ",
        "Отклик · Не просмотрен",
    ]
    assert [o.updated_at_platform for o in obs] == [
        datetime(2026, 8, 22, tzinfo=UTC),
        datetime(2026, 8, 21, tzinfo=UTC),
        datetime(2026, 8, 15, tzinfo=UTC),
        datetime(2026, 8, 12, tzinfo=UTC),
    ]
    assert obs[3].applied_at == datetime(2026, 8, 12, tzinfo=UTC) and obs[0].applied_at is None
    assert obs[0].platform == Platform.hh
    assert obs[0].raw_payload == {
        "lines": [
            "Senior Data Engineer (dbt, ClickHouse)",
            "Northwind Commerce",
            "Москва",
            "Приглашение",
            "22 августа",
        ]
    }
    assert len(Connector().parse_applications_text(text)) == 4


def test_ru_list_dates_without_year_roll_back_to_previous_year() -> None:
    assert hh_parsers.parse_list_date("20 августа", now=NOW) == datetime(2026, 8, 20, tzinfo=UTC)
    assert hh_parsers.parse_list_date("3 сентября", now=NOW) == datetime(2025, 9, 3, tzinfo=UTC)
    assert hh_parsers.parse_list_date("12.08.2026", now=NOW) == datetime(2026, 8, 12, tzinfo=UTC)
    assert hh_parsers.parse_list_date("Northwind Commerce", now=NOW) is None
    assert hh_parsers.parse_list_date("Опыт 3–6 лет", now=NOW) is None


def test_paste_parsers_fall_back_to_generic_shapes() -> None:
    c = Connector()
    profile = c.parse_profile_text("Dana Kovalenko\nSkills\nPython · SQL\n")
    assert profile.headline == "Dana Kovalenko" and profile.skills == ["Python", "SQL"]
    jobs = c.parse_jobs_text(
        "Data Engineer at Northwind Commerce\nRemote · Full-time\nhttps://example.com/jobs/1\n\n"
        "Analytics Engineer — Lumen Analytics\nWarsaw, Poland (Hybrid)\n"
    )
    assert [(j.title, j.company) for j in jobs] == [
        ("Data Engineer", "Northwind Commerce"),
        ("Analytics Engineer", "Lumen Analytics"),
    ]
    obs = c.parse_applications_text(
        "Data Engineer at Northwind Commerce\nApplied on Aug 12, 2026\nApplication viewed\n"
    )
    assert obs[0].status == ApplicationStatus.viewed and obs[0].company == "Northwind Commerce"


def test_resume_raw_payload_has_no_personal_identifiers() -> None:
    import json

    from careeros.modules.platform.connectors.hh.mapping import RESUME_PII_KEYS, public_resume

    fixture = Path(__file__).parent / "fixtures" / "hh" / "resume.json"
    resume = json.loads(fixture.read_text())
    resume["contact"] = [{"type": {"id": "cell"}, "value": {"formatted": "+7 000"}}]
    cleaned = public_resume(resume)
    assert not RESUME_PII_KEYS & set(cleaned)
    assert "title" in cleaned and "skill_set" in cleaned
