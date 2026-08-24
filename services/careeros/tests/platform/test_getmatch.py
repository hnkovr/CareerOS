# ruff: noqa: E501
"""getmatch connector: paste parsers for «Мой профиль», «Вакансии» and «Отклики» (RU + EN)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    EmploymentType,
    RemotePolicy,
    Seniority,
)
from careeros.modules.platform.connectors.getmatch import parsers as gm
from careeros.modules.platform.connectors.getmatch.connector import Connector
from careeros.modules.platform.enums import ApplicationStatus, AuthKind, SyncMethod
from careeros.modules.platform.registry import PlatformRegistry
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "getmatch"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def connector() -> Connector:
    return Connector()


# --------------------------------------------------------------------------- capabilities


def test_capabilities_are_paste_only(connector: Connector) -> None:
    caps = connector.capabilities
    assert connector.platform == Platform.getmatch and caps.platform == Platform.getmatch
    assert caps.profile == [SyncMethod.paste]
    assert caps.jobs == [SyncMethod.paste]
    assert caps.applications == [SyncMethod.paste]
    assert caps.official_api is False and caps.auth == AuthKind.none
    assert caps.email_fallback is True and caps.manual_capture is True
    assert "No public API" in caps.notes and "Мой профиль" in caps.notes


def test_registry_verify_is_clean_for_getmatch(connector: Connector) -> None:
    assert PlatformRegistry([connector]).verify() == []


# --------------------------------------------------------------------------- profile («Мой профиль»)


def test_profile_ru_headline_location_about(connector: Connector) -> None:
    pr = connector.parse_profile_text(fixture("paste_profile_ru.txt"))
    assert pr.platform == Platform.getmatch and pr.capture_method == CaptureMethod.paste
    assert pr.headline == "Senior Data Engineer"
    assert pr.preferences["location"] == "Тбилиси, Грузия"
    assert pr.about == (
        "Строю аналитические платформы: dbt, Dagster, ClickHouse. "
        "12 лет в данных, последние 5 — руковожу командой data engineering."
    )
    assert pr.availability == "Открыта к предложениям"


def test_profile_ru_skills(connector: Connector) -> None:
    pr = connector.parse_profile_text(fixture("paste_profile_ru.txt"))
    assert pr.skills == ["Python", "SQL", "dbt", "ClickHouse", "Dagster", "Kafka", "Airflow"]


def test_profile_ru_experience_with_periods(connector: Connector) -> None:
    pr = connector.parse_profile_text(fixture("paste_profile_ru.txt"))
    assert [(e.title, e.company) for e in pr.experience] == [
        ("Senior Data Engineer", "Northwind Commerce"),
        ("Lead Analytics Engineer", "Lumen Analytics"),
        ("Data Engineer", "Orbit Fintech"),
    ]
    assert [e.period for e in pr.experience] == [
        "2023 — настоящее время",
        "2020 — 2023",
        "2017 — 2020",
    ]
    assert pr.experience[0].description is not None and pr.experience[0].description.startswith(
        "Платформа данных"
    )
    assert pr.experience[1].description == "Команда из 6 инженеров, миграция DWH на ClickHouse."
    assert pr.experience[2].description is None


def test_profile_ru_rates_and_preferences(connector: Connector) -> None:
    pr = connector.parse_profile_text(fixture("paste_profile_ru.txt"))
    assert pr.rates is not None
    assert pr.rates["salary_min"] == 400000 and pr.rates["currency"] == "RUB"
    assert "salary_max" not in pr.rates
    assert pr.preferences["english"] == "B2"
    assert pr.preferences["remote"] is True
    assert pr.preferences["relocation"] is True


def test_profile_ru_keeps_raw_text_and_name(connector: Connector) -> None:
    text = fixture("paste_profile_ru.txt")
    pr = connector.parse_profile_text(text)
    assert pr.raw_text == text
    assert pr.raw_payload is not None and pr.raw_payload["name"] == "Дана Коваленко"
    # navigation chrome never leaks into structured fields
    assert "Мой профиль" not in (pr.headline or "") and "Редактировать" not in pr.skills


PROFILE_MIXED = """Dana Kovalenko
Lead Data Engineer
Remote

Skills: Python · dbt · ClickHouse

Ожидания по зарплате: $6 000
English: C1
Удалённо: нет
Не готова к переезду
"""


def test_profile_usd_expectation_and_negative_flags(connector: Connector) -> None:
    pr = connector.parse_profile_text(PROFILE_MIXED)
    assert pr.headline == "Lead Data Engineer"
    assert pr.skills == ["Python", "dbt", "ClickHouse"]
    assert pr.rates == {"salary_min": 6000, "currency": "USD", "period": "month", "raw": "$6 000"}
    assert pr.preferences["english"] == "C1"
    assert pr.preferences["remote"] is False and pr.preferences["relocation"] is False
    assert pr.preferences["location"] == "Remote"


def test_profile_unknown_layout_falls_back_to_generic(connector: Connector) -> None:
    text = "Dana Kovalenko\nI build data platforms for a living.\nPing me on the platform.\n"
    pr = connector.parse_profile_text(text)
    assert pr.platform == Platform.getmatch and pr.headline == "Dana Kovalenko"
    assert pr.skills == [] and pr.experience == [] and pr.rates is None
    assert pr.raw_text == text


# --------------------------------------------------------------------------- vacancies («Вакансии»)


def test_vacancies_ru_titles_and_companies(connector: Connector) -> None:
    jobs = connector.parse_jobs_text(fixture("paste_vacancies_ru.txt"), now=NOW)
    assert [(j.title, j.company) for j in jobs] == [
        ("Senior Data Engineer", "Northwind Commerce"),
        ("Analytics Engineer", "Lumen Analytics"),
        ("Lead Data Platform Engineer", "Orbit Fintech"),
        ("Data Engineer (ClickHouse)", "Northwind Commerce"),
    ]
    assert all(j.platform == Platform.getmatch for j in jobs)
    assert all(j.extraction is not None and j.extraction.title == j.title for j in jobs)
    assert jobs[0].raw_text.startswith("Northwind Commerce\nSenior Data Engineer")
    assert "Откликнуться" in jobs[0].raw_text  # raw block kept verbatim


def test_vacancies_ru_compensation(connector: Connector) -> None:
    jobs = connector.parse_jobs_text(fixture("paste_vacancies_ru.txt"), now=NOW)
    comps = [j.extraction.compensation for j in jobs if j.extraction is not None]
    assert [(c.min, c.max, c.currency) if c else None for c in comps] == [
        (300000, None, "RUB"),
        (250000, 350000, "RUB"),  # thin-space thousand separators
        (4000, 6000, "USD"),
        (None, 5000, "EUR"),
    ]
    assert all(c is not None and c.period == CompensationPeriod.month for c in comps)
    assert all(c is not None and c.type == "salary" for c in comps)
    assert comps[0] is not None and comps[0].raw == "от 300 000 ₽"


def test_vacancies_ru_remote_policy_and_location(connector: Connector) -> None:
    jobs = connector.parse_jobs_text(fixture("paste_vacancies_ru.txt"), now=NOW)
    assert [j.extraction.remote_policy for j in jobs if j.extraction] == [
        RemotePolicy.remote_global,
        RemotePolicy.hybrid,
        RemotePolicy.remote_global,
        RemotePolicy.onsite,
    ]
    assert [j.location for j in jobs] == [None, "Москва", None, "Санкт-Петербург"]
    assert jobs[1].extraction is not None and jobs[1].extraction.location == "Москва"


def test_vacancies_ru_seniority_stack_employment(connector: Connector) -> None:
    jobs = connector.parse_jobs_text(fixture("paste_vacancies_ru.txt"), now=NOW)
    ex = [j.extraction for j in jobs if j.extraction is not None]
    assert [e.seniority for e in ex] == [
        Seniority.senior,
        Seniority.mid,
        Seniority.lead,
        Seniority.junior,
    ]
    assert ex[0].technologies == ["Python", "dbt", "ClickHouse", "Airflow"]
    assert ex[1].technologies == ["SQL", "dbt", "Dagster"]
    assert ex[2].technologies == [
        "Python",
        "Kubernetes",
        "Kafka",
        "PostgreSQL",
    ]  # unlabelled tag row
    assert ex[3].technologies == ["ClickHouse", "SQL"]
    assert ex[3].employment_type == EmploymentType.full_time and ex[0].employment_type is None


def test_vacancies_ru_posted_at_uses_fixed_now(connector: Connector) -> None:
    jobs = connector.parse_jobs_text(fixture("paste_vacancies_ru.txt"), now=NOW)
    assert [j.posted_at for j in jobs] == [
        NOW.replace(day=23),
        datetime(2026, 8, 12, tzinfo=UTC),
        NOW.replace(day=24),
        datetime(2026, 8, 20, tzinfo=UTC),
    ]


def test_vacancies_ru_skips_navigation_and_filters(connector: Connector) -> None:
    jobs = connector.parse_jobs_text(fixture("paste_vacancies_ru.txt"), now=NOW)
    assert len(jobs) == 4
    assert all("Найдено" not in j.title and j.title != "getmatch" for j in jobs)


def test_vacancies_en_variant(connector: Connector) -> None:
    jobs = connector.parse_jobs_text(fixture("paste_vacancies_en.txt"), now=NOW)
    assert [(j.title, j.company) for j in jobs] == [
        ("Senior Data Engineer", "Northwind Commerce"),
        ("Middle Backend Developer (Python)", "Orbit Fintech"),
        ("Analytics Engineer", "Lumen Analytics"),
    ]
    ex = [j.extraction for j in jobs if j.extraction is not None]
    assert len(ex) == 3
    comps = [e.compensation for e in ex]
    assert all(c is not None for c in comps)
    assert [(c.min, c.max, c.currency) for c in comps if c is not None] == [
        (300000, None, "RUB"),  # 'from 300 000 ₽'
        (3500, 5000, "USD"),  # '$3 500 – $5 000' with thin spaces
        (None, 5000, "EUR"),  # 'up to €5 000'
    ]
    assert [e.remote_policy for e in ex] == [
        RemotePolicy.remote_global,
        RemotePolicy.hybrid,
        RemotePolicy.onsite,
    ]
    assert [j.location for j in jobs] == [None, "Tbilisi", "Warsaw"]
    assert [e.seniority for e in ex] == [Seniority.senior, Seniority.mid, Seniority.junior]
    assert ex[1].technologies == ["Python", "FastAPI", "PostgreSQL"]
    assert [j.posted_at for j in jobs] == [
        NOW.replace(day=22),
        datetime(2026, 8, 20, tzinfo=UTC),
        NOW.replace(day=24),
    ]


def test_vacancies_unknown_layout_falls_back_to_generic(connector: Connector) -> None:
    text = (
        "Data Engineer at Northwind Commerce\nhttps://example.com/jobs/1\n\n"
        "Analytics Engineer — Lumen Analytics\nWarsaw, Poland\n"
    )
    jobs = connector.parse_jobs_text(text)
    assert [j.title for j in jobs] == ["Data Engineer", "Analytics Engineer"]
    assert jobs[0].company == "Northwind Commerce" and jobs[0].url == "https://example.com/jobs/1"
    assert all(j.extraction is None for j in jobs)  # generic parser: no structured extraction


def test_vacancy_url_gives_external_id(connector: Connector) -> None:
    text = (
        "Northwind Commerce\nSenior Data Engineer\nот 300 000 ₽\nУдалённо\n"
        "https://getmatch.ru/vacancies/48213\nОткликнуться\n"
    )
    (job,) = connector.parse_jobs_text(text)
    assert job.url == "https://getmatch.ru/vacancies/48213" and job.external_id == "48213"
    req = job.to_ingest()
    assert req.source == "getmatch" and req.structured is not None
    assert req.structured.compensation is not None and req.structured.compensation.currency == "RUB"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("от 300 000 ₽", (300000, None, "RUB", CompensationPeriod.month)),
        ("250 000 – 350 000 ₽", (250000, 350000, "RUB", CompensationPeriod.month)),
        ("$4 000 – $6 000", (4000, 6000, "USD", CompensationPeriod.month)),
        ("до 5 000 €", (None, 5000, "EUR", CompensationPeriod.month)),
        ("up to €5 000", (None, 5000, "EUR", CompensationPeriod.month)),
        ("from 300,000 ₽", (300000, None, "RUB", CompensationPeriod.month)),
        ("$4k – $6k", (4000, 6000, "USD", CompensationPeriod.month)),
        ("от 300 тыс. руб. на руки", (300000, None, "RUB", CompensationPeriod.month)),
        ("250 000 ₽", (250000, 250000, "RUB", CompensationPeriod.month)),
        ("$90 000 – $120 000 в год", (90000, 120000, "USD", CompensationPeriod.year)),
        ("$45 per hour", (45, 45, "USD", CompensationPeriod.hour)),
    ],
)
def test_parse_money(
    line: str, expected: tuple[float | None, float | None, str, CompensationPeriod]
) -> None:
    comp = gm.parse_money(line)
    assert comp is not None
    assert (comp.min, comp.max, comp.currency, comp.period) == expected
    assert comp.type == "salary" and comp.raw == line


@pytest.mark.parametrize(
    "line", ["Зарплата не указана", "Senior", "Опыт от 3 лет", "Опубликовано 2 дня назад"]
)
def test_parse_money_ignores_non_salary_lines(line: str) -> None:
    assert gm.parse_money(line) is None


def test_parse_date_day_month_without_year_uses_now_year() -> None:
    assert gm.parse_date("Опубликовано 12 августа", now=NOW) == datetime(2026, 8, 12, tzinfo=UTC)
    assert gm.parse_date("Posted 3 days ago", now=NOW) == NOW.replace(day=22)
    assert gm.parse_date("Опыт от 3 лет", now=NOW) is None  # a duration, not a date
    assert gm.parse_date("40 витрин", now=NOW) is None


# --------------------------------------------------------------------------- responses («Отклики»)


def test_responses_ru_statuses(connector: Connector) -> None:
    obs = connector.parse_applications_text(fixture("paste_responses_ru.txt"), now=NOW)
    assert [o.status for o in obs] == [
        ApplicationStatus.viewed,
        ApplicationStatus.applied,
        ApplicationStatus.invited,
        ApplicationStatus.rejected,
        ApplicationStatus.interview,
        ApplicationStatus.offer,
    ]
    assert [o.status_raw for o in obs][:3] == [
        "Статус: Просмотрен",
        "Статус: Отправлен",
        "Статус: Приглашение",
    ]


def test_responses_ru_titles_companies_and_raw(connector: Connector) -> None:
    text = fixture("paste_responses_ru.txt")
    obs = connector.parse_applications_text(text, now=NOW)
    assert [(o.job_title, o.company) for o in obs] == [
        ("Senior Data Engineer", "Northwind Commerce"),
        ("Analytics Engineer", "Lumen Analytics"),
        ("Lead Data Platform Engineer", "Orbit Fintech"),
        ("Data Engineer (ClickHouse)", "Northwind Commerce"),
        ("Backend Developer (Python)", "Orbit Fintech"),
        ("Head of Data", "Lumen Analytics"),
    ]
    assert all(o.platform == Platform.getmatch for o in obs)
    assert obs[0].raw_payload == {
        "lines": [
            "Senior Data Engineer",
            "Northwind Commerce",
            "Статус: Просмотрен",
            "12 августа 2026",
        ]
    }
    assert len(obs) == 6  # navigation and the «Все · Активные · Архив» tabs are not rows


def test_responses_ru_dates(connector: Connector) -> None:
    obs = connector.parse_applications_text(fixture("paste_responses_ru.txt"), now=NOW)
    assert [o.applied_at for o in obs] == [
        datetime(2026, 8, 12, tzinfo=UTC),
        NOW.replace(day=23),  # «2 дня назад» relative to the fixed now
        datetime(2026, 8, 10, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 8, 5, tzinfo=UTC),
        None,
    ]
    assert obs[5].updated_at_platform == datetime(2026, 8, 22, tzinfo=UTC)


RESPONSES_EN = """Responses

Senior Data Engineer · Northwind Commerce
Status: Sent
Aug 12, 2026

Analytics Engineer
Lumen Analytics
Status: Viewed
3 days ago

Lead Data Platform Engineer
Orbit Fintech
Status: Invited
Aug 10, 2026

Data Engineer
Northwind Commerce
Status: Interview
Aug 8, 2026

Backend Developer
Orbit Fintech
Status: Rejected
Aug 1, 2026

Head of Data
Lumen Analytics
Status: Offer
Aug 22, 2026
"""


def test_responses_en_variant(connector: Connector) -> None:
    obs = connector.parse_applications_text(RESPONSES_EN, now=NOW)
    assert [o.status for o in obs] == [
        ApplicationStatus.applied,
        ApplicationStatus.viewed,
        ApplicationStatus.invited,
        ApplicationStatus.interview,
        ApplicationStatus.rejected,
        ApplicationStatus.offer,
    ]
    assert (obs[0].job_title, obs[0].company) == ("Senior Data Engineer", "Northwind Commerce")
    assert obs[0].status_raw == "Status: Sent" and obs[1].applied_at == NOW.replace(day=22)


def test_responses_tab_separated_table_rows(connector: Connector) -> None:
    text = (
        "Вакансия\tКомпания\tСтатус\tДата\n"
        "Senior Data Engineer\tNorthwind Commerce\tПросмотрен\t12.08.2026\n"
        "Analytics Engineer\tLumen Analytics\tОтказ\tвчера\n"
    )
    obs = connector.parse_applications_text(text, now=NOW)
    assert [(o.job_title, o.company, o.status) for o in obs] == [
        ("Senior Data Engineer", "Northwind Commerce", ApplicationStatus.viewed),
        ("Analytics Engineer", "Lumen Analytics", ApplicationStatus.rejected),
    ]
    assert obs[0].applied_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert obs[1].applied_at == NOW.replace(day=24) and obs[1].status_raw == "Отказ"


def test_responses_unknown_layout_falls_back_to_generic(connector: Connector) -> None:
    text = "Data Engineer at Northwind Commerce\nsome free-form note\n"
    (o,) = connector.parse_applications_text(text)
    assert (o.job_title, o.company) == ("Data Engineer", "Northwind Commerce")
    assert o.status == ApplicationStatus.unknown and o.applied_at is None
    assert o.raw_payload == {
        "lines": ["Data Engineer at Northwind Commerce", "some free-form note"]
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Отправлен", ApplicationStatus.applied),
        ("Отклик отправлен", ApplicationStatus.applied),
        ("Просмотрен", ApplicationStatus.viewed),
        ("Приглашение", ApplicationStatus.invited),
        ("Интервью", ApplicationStatus.interview),
        ("Отказ", ApplicationStatus.rejected),
        ("Оффер", ApplicationStatus.offer),
        ("Отозван", ApplicationStatus.withdrawn),
        ("Sent", ApplicationStatus.applied),
        ("Viewed", ApplicationStatus.viewed),
        ("Invited", ApplicationStatus.invited),
        ("Interview", ApplicationStatus.interview),
        ("Rejected", ApplicationStatus.rejected),
        ("Offer", ApplicationStatus.offer),
        ("Статус: Просмотрен работодателем", ApplicationStatus.viewed),
        ("что-то неизвестное", ApplicationStatus.unknown),
    ],
)
def test_response_status_map(raw: str, expected: ApplicationStatus) -> None:
    assert gm.response_status(raw) == expected
