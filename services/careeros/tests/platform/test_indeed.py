"""Indeed connector: paste-only parsers for the profile page, search results and 'My jobs'."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from careeros.modules.opportunities.enums import CompensationPeriod, EmploymentType, RemotePolicy
from careeros.modules.platform import parsers as shared
from careeros.modules.platform.connectors.indeed import parsers as indeed
from careeros.modules.platform.connectors.indeed.connector import Connector
from careeros.modules.platform.enums import ApplicationStatus, AuthKind, SyncMethod
from careeros.modules.platform.registry import get_registry
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "indeed"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def profile_text() -> str:
    return _fixture("paste_profile.txt")


@pytest.fixture(scope="module")
def jobs_text() -> str:
    return _fixture("paste_jobs.txt")


@pytest.fixture(scope="module")
def applied_text() -> str:
    return _fixture("paste_applied.txt")


# --------------------------------------------------------------------------- capabilities


def test_capabilities_are_paste_only_without_auth() -> None:
    caps = Connector.capabilities
    assert caps.platform == Platform.indeed
    assert caps.profile == [SyncMethod.paste]
    assert caps.jobs == [SyncMethod.paste]
    assert caps.applications == [SyncMethod.paste]
    assert caps.official_api is False and caps.auth == AuthKind.none
    assert caps.email_fallback is True and caps.manual_capture is True
    assert "Publisher API discontinued" in caps.notes and "never fetched" in caps.notes


def test_registry_serves_this_connector_and_verifies_clean() -> None:
    reg = get_registry()
    assert isinstance(reg.get("indeed"), Connector)
    assert reg.verify() == []


def test_connector_methods_delegate_to_parsers(
    profile_text: str, jobs_text: str, applied_text: str
) -> None:
    c = Connector()
    assert c.parse_profile_text(profile_text).headline == "Senior Data Engineer"
    assert [j.title for j in c.parse_jobs_text(jobs_text)] == [
        j.title for j in indeed.parse_jobs(jobs_text)
    ]
    assert [o.status for o in c.parse_applications_text(applied_text)] == [
        o.status for o in indeed.parse_applications(applied_text)
    ]


# --------------------------------------------------------------------------- profile


def test_profile_header_summary_and_preferences(profile_text: str) -> None:
    pr = indeed.parse_profile(profile_text)
    assert pr.platform == Platform.indeed and pr.capture_method == CaptureMethod.paste
    assert pr.headline == "Senior Data Engineer"
    assert pr.preferences["location"] == "Tbilisi, Georgia"
    assert pr.preferences["willing_to_relocate"] == "Warsaw, Poland - Berlin, Germany"
    assert pr.about is not None
    assert pr.about.startswith("Senior data engineer with twelve years")
    assert "keep them aligned." in pr.about
    assert pr.raw_text == profile_text
    assert pr.raw_payload is not None and pr.raw_payload["name"] == "Dana Kovalenko"


def test_profile_experience_blocks(profile_text: str) -> None:
    pr = indeed.parse_profile(profile_text)
    assert [e.company for e in pr.experience] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
    ]
    assert [e.title for e in pr.experience] == [
        "Senior Data Engineer",
        "Lead Analytics Engineer",
        "Data Engineer",
    ]
    assert [e.period for e in pr.experience] == [
        "January 2023 – Present",
        "March 2020 – December 2022",
        "June 2016 – February 2020",
    ]
    first = pr.experience[0]
    assert first.description is not None
    assert first.description.startswith("Own the Dagster + dbt platform")
    assert "TTL policies." in first.description
    assert pr.raw_payload is not None
    assert [x["location"] for x in pr.raw_payload["experience"]] == [
        "Remote",
        "Warsaw, Poland",
        "Tbilisi, Georgia",
    ]


def test_profile_skills_have_years_stripped(profile_text: str) -> None:
    pr = indeed.parse_profile(profile_text)
    assert pr.skills == [
        "Python",
        "SQL",
        "dbt",
        "Dagster",
        "ClickHouse",
        "Apache Airflow",
        "Kafka",
        "Docker",
        "Terraform",
        "Data modelling",
    ]


def test_profile_other_sections_are_kept_in_raw_payload(profile_text: str) -> None:
    pr = indeed.parse_profile(profile_text)
    assert pr.raw_payload is not None
    assert pr.raw_payload["education"][0] == "Master's degree in Computer Science"
    assert pr.raw_payload["certifications"] == [
        "Analytics Engineering Certificate",
        "May 2024 to Present",
    ]
    assert pr.raw_payload["assessments"][0] == "Data analysis: Expert"
    assert pr.raw_payload["links"] == [
        "https://github.com/dana-kovalenko",
        "https://www.linkedin.com/in/dana-kovalenko",
    ]
    assert pr.portfolio == [] and pr.projects == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Python (10+ years)", "Python"),
        ("SQL (12 years)", "SQL"),
        ("Airflow (1 year)", "Airflow"),
        ("Terraform (Less than 1 year)", "Terraform"),
        ("Data modelling", "Data modelling"),
        ("C++ (3 years)", "C++"),
    ],
)
def test_strip_years(raw: str, expected: str) -> None:
    assert indeed.strip_years(raw) == expected


def test_profile_without_headers_falls_back_to_generic() -> None:
    text = "Dana Kovalenko\nSenior Data Engineer\nTbilisi, Georgia\n"
    assert indeed.parse_profile(text) == shared.generic_profile(text, Platform.indeed)


def test_profile_generic_headers_are_understood_too() -> None:
    text = "Dana Kovalenko\nSenior DE\n\nAbout\nI build platforms.\n\nSkills\nPython · SQL\n"
    pr = indeed.parse_profile(text)
    assert pr.headline == "Senior DE" and pr.about == "I build platforms."
    assert pr.skills == ["Python", "SQL"] and "location" not in pr.preferences


# --------------------------------------------------------------------------- jobs


def test_jobs_cards_titles_companies_locations(jobs_text: str) -> None:
    jobs = indeed.parse_jobs(jobs_text, now=NOW)
    assert [j.title for j in jobs] == [
        "Senior Data Engineer",
        "Analytics Engineer (Contract)",
        "Data Platform Engineer",
        "Staff Data Engineer",
    ]
    assert [j.company for j in jobs] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
        "Northwind Commerce",
    ]
    assert [j.location for j in jobs] == [
        "Remote",
        "Warsaw, Poland",
        "Tbilisi",
        "Hybrid work in Warsaw, Poland",
    ]
    assert all(j.platform == Platform.indeed for j in jobs)


def test_jobs_compensation_yearly_hourly_from_and_up_to(jobs_text: str) -> None:
    jobs = indeed.parse_jobs(jobs_text, now=NOW)
    comps = [j.extraction.compensation for j in jobs if j.extraction is not None]
    assert len(comps) == 4 and all(c is not None for c in comps)
    yearly, hourly, from_, up_to = comps
    assert yearly is not None and hourly is not None
    assert from_ is not None and up_to is not None
    assert (yearly.min, yearly.max, yearly.currency) == (120000, 150000, "USD")
    assert yearly.period == CompensationPeriod.year and yearly.type == "salary"
    assert yearly.raw == "$120,000 - $150,000 a year"
    assert (hourly.min, hourly.max, hourly.currency) == (60, 80, "USD")
    assert hourly.period == CompensationPeriod.hour and hourly.type == "rate"
    assert (from_.min, from_.max) == (90000, None) and from_.period == CompensationPeriod.year
    assert (up_to.min, up_to.max, up_to.currency) == (None, 95000, "EUR")


def test_jobs_remote_policy_and_employment_type(jobs_text: str) -> None:
    jobs = indeed.parse_jobs(jobs_text, now=NOW)
    ex = [j.extraction for j in jobs]
    assert all(e is not None for e in ex)
    policies = [e.remote_policy for e in ex if e is not None]
    assert policies == [
        RemotePolicy.remote_global,
        RemotePolicy.unknown,
        RemotePolicy.unknown,
        RemotePolicy.hybrid,
    ]
    types = [e.employment_type for e in ex if e is not None]
    assert types == [
        EmploymentType.full_time,
        EmploymentType.part_time,
        EmploymentType.full_time,
        EmploymentType.full_time,
    ]


def test_jobs_posted_at_uses_fixed_now(jobs_text: str) -> None:
    jobs = indeed.parse_jobs(jobs_text, now=NOW)
    assert [j.posted_at for j in jobs] == [
        NOW - timedelta(days=3),
        NOW,
        NOW - timedelta(days=2),
        None,  # "Posted 30+ days ago" is a floor, not a date
    ]


def test_jobs_summary_badges_and_raw_block(jobs_text: str) -> None:
    jobs = indeed.parse_jobs(jobs_text, now=NOW)
    first = jobs[0]
    assert first.extraction is not None
    assert first.extraction.summary == (
        "Own the Dagster + dbt platform for our analytics teams; ClickHouse experience required."
    )
    assert first.extraction.title == "Senior Data Engineer"
    assert first.extraction.company == "Northwind Commerce"
    assert first.raw_text.startswith("Senior Data Engineer\nNorthwind Commerce\n4.1\nRemote")
    assert first.raw_text.endswith("Posted 3 days ago")
    assert first.raw_payload is not None
    assert first.raw_payload["badges"] == ["Easily apply", "Hiring multiple candidates"]
    assert first.raw_payload["posted"] == "Posted 3 days ago"
    assert jobs[3].raw_payload is not None
    assert jobs[3].raw_payload["badges"] == ["Urgently hiring", "Typically responds within 1 day"]


def test_jobs_header_alert_and_pagination_blocks_are_skipped(jobs_text: str) -> None:
    jobs = indeed.parse_jobs(jobs_text, now=NOW)
    assert len(jobs) == 4
    titles = {j.title for j in jobs}
    assert "data engineer jobs in Remote" not in titles
    assert "Get new jobs for this search by email" not in titles
    assert "Previous" not in titles


def test_jobs_map_to_ingest_requests_with_indeed_source(jobs_text: str) -> None:
    req = indeed.parse_jobs(jobs_text, now=NOW)[1].to_ingest()
    assert req.source == "indeed" and req.structured is not None
    assert req.structured.compensation is not None
    assert req.structured.compensation.period == CompensationPeriod.hour
    assert req.received_at == NOW


def test_jobs_unknown_layout_falls_back_to_generic() -> None:
    text = (
        "Data Engineer at Northwind Commerce\nhttps://example.com/jobs/1\n\n"
        "Analytics Engineer — Lumen Analytics\nWarsaw, Poland (Hybrid)\n"
    )
    jobs = indeed.parse_jobs(text, now=NOW)
    generic = shared.generic_jobs(text, Platform.indeed)
    assert [(j.title, j.company) for j in jobs] == [(j.title, j.company) for j in generic]
    assert jobs[0].url == "https://example.com/jobs/1"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("$120,000 - $150,000 a year", (120000, 150000, "USD", "year", "salary")),
        ("$60 - $80 an hour", (60, 80, "USD", "hour", "rate")),
        ("From $90,000 a year", (90000, None, "USD", "year", "salary")),
        ("Up to €95,000 a year", (None, 95000, "EUR", "year", "salary")),
        ("£450 a day", (450, 450, "GBP", "day", "rate")),
        ("$5,000 a month", (5000, 5000, "USD", "month", "salary")),
        ("Estimated $110K – $140K a year", (110000, 140000, "USD", "year", "salary")),
        ("$1,200 a week", (1200, 1200, "USD", None, "salary")),
    ],
)
def test_parse_pay_variants(
    line: str, expected: tuple[float | None, float | None, str, str | None, str]
) -> None:
    comp = indeed.parse_pay(line)
    assert comp is not None and comp.raw == line
    period = comp.period.value if comp.period is not None else None
    assert (comp.min, comp.max, comp.currency, period, comp.type) == expected


@pytest.mark.parametrize("line", ["Full-time", "Posted 3 days ago", "Remote", "$ per year"])
def test_parse_pay_rejects_non_pay_lines(line: str) -> None:
    assert indeed.parse_pay(line) is None


# --------------------------------------------------------------------------- applications


def test_applications_rows_titles_and_companies(applied_text: str) -> None:
    obs = indeed.parse_applications(applied_text, now=NOW)
    assert [o.job_title for o in obs] == [
        "Senior Data Engineer",
        "Analytics Engineer (Contract)",
        "Data Platform Engineer",
        "Staff Data Engineer",
        "Data Engineer",
        "Backend Data Engineer",
    ]
    assert [o.company for o in obs] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
    ]
    assert all(o.platform == Platform.indeed for o in obs)


def test_applications_statuses_and_raw_lines(applied_text: str) -> None:
    obs = indeed.parse_applications(applied_text, now=NOW)
    assert [o.status for o in obs] == [
        ApplicationStatus.applied,
        ApplicationStatus.viewed,
        ApplicationStatus.interview,
        ApplicationStatus.rejected,
        ApplicationStatus.offer,
        ApplicationStatus.withdrawn,
    ]
    assert [o.status_raw for o in obs] == [
        "Application submitted",
        "Viewed by employer",
        "Interviewing",
        "Not selected by employer",
        "Hired",
        "Application withdrawn",
    ]


def test_applications_dates_absolute_and_relative(applied_text: str) -> None:
    obs = indeed.parse_applications(applied_text, now=NOW)
    assert obs[0].applied_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert obs[1].applied_at == datetime(2026, 8, 5, tzinfo=UTC)
    assert obs[2].applied_at == NOW - timedelta(days=3)
    assert obs[3].applied_at == datetime(2026, 7, 28, tzinfo=UTC)
    assert obs[5].applied_at == datetime(2026, 7, 3, tzinfo=UTC)


def test_applications_notes_and_raw_payload(applied_text: str) -> None:
    obs = indeed.parse_applications(applied_text, now=NOW)
    expired = obs[3]
    assert expired.raw_payload is not None
    assert expired.raw_payload["notes"] == ["Job expired"]
    assert expired.status == ApplicationStatus.rejected  # note never changes the status
    assert expired.raw_payload["lines"][0] == "Staff Data Engineer"
    assert expired.raw_payload["location"] == "Warsaw, Poland"
    assert obs[0].raw_payload is not None
    assert obs[0].raw_payload["notes"] == ["Applied on Indeed"]
    assert obs[5].raw_payload is not None
    assert obs[5].raw_payload["notes"] == ["Applied on company site"]
    assert "Withdraw application" in obs[5].raw_payload["lines"]  # button kept verbatim


def test_applications_tab_bar_is_not_a_row(applied_text: str) -> None:
    obs = indeed.parse_applications(applied_text, now=NOW)
    assert len(obs) == 6 and all(o.job_title != "My jobs" for o in obs)


def test_applications_unknown_layout_falls_back_to_generic() -> None:
    text = (
        "Data Engineer at Northwind Commerce\nApplication viewed\n\n"
        "Analytics Engineer — Lumen Analytics\nNot moving forward\n"
    )
    obs = indeed.parse_applications(text, now=NOW)
    generic = shared.generic_applications(text, Platform.indeed, now=NOW)
    assert [(o.job_title, o.company, o.status) for o in obs] == [
        (o.job_title, o.company, o.status) for o in generic
    ]
    assert obs[0].company == "Northwind Commerce"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Applied", ApplicationStatus.applied),
        ("Application submitted", ApplicationStatus.applied),
        ("Viewed by employer", ApplicationStatus.viewed),
        ("Interviewing", ApplicationStatus.interview),
        ("Not selected by employer", ApplicationStatus.rejected),
        ("Hired", ApplicationStatus.offer),
        ("Application withdrawn", ApplicationStatus.withdrawn),
        ("Job expired", None),
        ("Withdraw application", None),
    ],
)
def test_map_status_explicit_indeed_wording(line: str, expected: ApplicationStatus | None) -> None:
    assert indeed.map_status(line) == expected
