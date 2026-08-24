# ruff: noqa: E501
"""Toptal connector: paste-only parsers (profile, portal jobs, applied jobs) + capabilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
)
from careeros.modules.platform import parsers
from careeros.modules.platform.base import CapabilityUnavailable
from careeros.modules.platform.connectors.toptal import parsers as toptal
from careeros.modules.platform.connectors.toptal.connector import Connector
from careeros.modules.platform.enums import (
    ApplicationStatus,
    AuthKind,
    CapabilityLevel,
    SyncKind,
    SyncMethod,
)
from careeros.modules.platform.registry import get_registry
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "toptal"
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
def applications_text() -> str:
    return _fixture("paste_applications.txt")


# --------------------------------------------------------------------------- connector / registry


def test_capabilities_are_paste_only_and_honest() -> None:
    caps = Connector.capabilities
    assert caps.platform == Platform.toptal
    assert caps.profile == [SyncMethod.paste]
    assert caps.jobs == [SyncMethod.paste]
    assert caps.applications == [SyncMethod.paste]
    assert caps.official_api is False and caps.auth == AuthKind.none
    assert caps.email_fallback is True and caps.manual_capture is True
    assert caps.read_profile == CapabilityLevel.manual
    assert caps.read_opportunities == CapabilityLevel.manual
    assert caps.read_applications == CapabilityLevel.manual
    assert caps.export_import == CapabilityLevel.none
    assert "never fetched" in caps.notes


def test_registry_serves_the_toptal_connector_and_verifies_clean() -> None:
    reg = get_registry()
    assert isinstance(reg.get("toptal"), Connector)
    assert reg.verify() == []


async def test_api_and_export_tiers_are_unavailable() -> None:
    c = Connector()
    with pytest.raises(CapabilityUnavailable) as exc:
        await c.read_profile(None)  # type: ignore[arg-type]
    assert exc.value.kind == SyncKind.profile and exc.value.available == [SyncMethod.paste]
    with pytest.raises(CapabilityUnavailable):
        c.import_jobs_export(Path("/nonexistent.csv"))
    with pytest.raises(CapabilityUnavailable):
        await c.application_statuses(None)  # type: ignore[arg-type]


def test_connector_delegates_to_toptal_parsers(
    profile_text: str, jobs_text: str, applications_text: str
) -> None:
    c = Connector()
    assert c.parse_profile_text(profile_text).headline == "Senior Data Platform Engineer"
    assert len(c.parse_jobs_text(jobs_text)) == 3
    assert len(c.parse_applications_text(applications_text)) == 5


# --------------------------------------------------------------------------- profile


def test_profile_header_meta(profile_text: str) -> None:
    pr = toptal.parse_profile(profile_text)
    assert pr.platform == Platform.toptal and pr.capture_method == CaptureMethod.paste
    assert pr.headline == "Senior Data Platform Engineer"
    assert pr.profile_url == "https://www.toptal.com/resume/dana-kovalenko"
    assert pr.preferences["name"] == "Dana Kovalenko"
    assert pr.preferences["location"] == "Tbilisi, Georgia"
    assert pr.preferences["member_since"] == "March 15, 2023"
    assert pr.availability == "Full-time"
    assert pr.rates is not None
    assert pr.rates["hourly"] == 90 and pr.rates["currency"] == "USD"
    assert pr.raw_text == profile_text


def test_profile_bio_and_expertise(profile_text: str) -> None:
    pr = toptal.parse_profile(profile_text)
    assert pr.about == (
        "Dana builds analytics platforms for e-commerce and fintech teams: warehouse design, dbt "
        "modelling and orchestration with Dagster. Twelve years in data engineering, the last five "
        "leading small platform teams that ship reliable, observable pipelines."
    )
    assert pr.skills == [
        "Python",
        "SQL",
        "dbt",
        "Dagster",
        "ClickHouse",
        "Snowflake",
        "Kafka",
        "AWS",
        "Terraform",
        "Data Modeling",
    ]


def test_profile_work_experience_blocks(profile_text: str) -> None:
    pr = toptal.parse_profile(profile_text)
    assert [e.company for e in pr.experience] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
    ]
    assert [e.title for e in pr.experience] == [
        "Senior Data Platform Engineer",
        "Lead Analytics Engineer",
        "Data Engineer",
    ]
    assert [e.period for e in pr.experience] == [
        "2023 - PRESENT",
        "Jan 2020 - Mar 2023",
        "Jun 2016 - Dec 2019",
    ]
    first = pr.experience[0].description
    assert first is not None
    assert first.splitlines() == [
        "Rebuilt the analytics warehouse on ClickHouse; p95 dashboard latency went from 40 s to 2 s.",
        "Introduced dbt and Dagster: 300+ models with freshness SLAs met on 99% of days.",
        "Technologies: Python, dbt, Dagster, ClickHouse, Kafka",
    ]
    assert pr.experience[2].description == (
        "Built ingestion for payment events (Kafka to Snowflake) handling 50M events per day.\n"
        "Technologies: Python, Kafka, Snowflake, Airflow"
    )


def test_profile_portfolio_education_languages(profile_text: str) -> None:
    pr = toptal.parse_profile(profile_text)
    assert pr.portfolio == [
        {
            "name": "Realtime Fraud Signals Pipeline",
            "description": "Kafka to Flink to ClickHouse pipeline that flags anomalous payments within seconds.",
            "url": "https://example.com/dana/fraud-signals",
        },
        {
            "name": "Warehouse Cost Observability",
            "description": "dbt and Dagster metadata turned into cost dashboards; found the 35% Snowflake saving.",
        },
    ]
    assert pr.preferences["education"] == [
        {
            "degree": "Master's Degree in Computer Science",
            "institution": "Kyiv Polytechnic Institute",
            "period": "2010 - 2016",
        }
    ]
    # page chrome after the last section ("Hire Dana", "Share this profile") must not leak in
    assert pr.preferences["languages"] == ["English: Fluent", "Ukrainian: Native"]


ALT_PROFILE = """Dana Kovalenko
Data Engineer
Location: Tbilisi, Georgia
Member since 2023

Dana is a data engineer who builds warehouses and pipelines for e-commerce and fintech teams.

Skills
Languages
Python, SQL
Tools
dbt, Dagster
Work Experience
Data Engineer
2019 - PRESENT
Orbit Fintech
Built ingestion pipelines for payment events.
Experience
Fraud Signals (Development)
Streaming anomaly detection for payments.
Availability
Part-time
"""


def test_profile_alternative_layout_portal_editor() -> None:
    """Unlabelled lead paragraph, categorised Skills, title/period/company order, bare Experience."""
    pr = toptal.parse_profile(ALT_PROFILE)
    assert pr.headline == "Data Engineer"
    assert pr.preferences["location"] == "Tbilisi, Georgia"
    assert pr.preferences["member_since"] == "2023"
    assert pr.about == (
        "Dana is a data engineer who builds warehouses and pipelines for e-commerce and fintech teams."
    )
    assert pr.skills == ["Python", "SQL", "dbt", "Dagster"]
    assert len(pr.experience) == 1
    item = pr.experience[0]
    assert (item.title, item.company, item.period) == (
        "Data Engineer",
        "Orbit Fintech",
        "2019 - PRESENT",
    )
    assert item.description == "Built ingestion pipelines for payment events."
    assert pr.portfolio == [
        {
            "name": "Fraud Signals (Development)",
            "description": "Streaming anomaly detection for payments.",
        }
    ]
    assert pr.availability == "Part-time"
    assert pr.rates is None


def test_profile_unknown_layout_falls_back_to_generic() -> None:
    text = "Dana Kovalenko\nSome free-form line\nAnother line without any Toptal markers\n"
    pr = toptal.parse_profile(text)
    assert pr == parsers.generic_profile(text, Platform.toptal)
    assert pr.headline == "Dana Kovalenko" and pr.raw_text == text


def test_profile_empty_text_is_empty_profile() -> None:
    pr = toptal.parse_profile("\n \n")
    assert pr.platform == Platform.toptal and pr.headline is None and pr.skills == []


# --------------------------------------------------------------------------- jobs


def test_jobs_titles_clients_and_industries(jobs_text: str) -> None:
    jobs = toptal.parse_jobs(jobs_text, now=NOW)
    assert [j.title for j in jobs] == [
        "Senior Data Platform Engineer",
        "Analytics Warehouse Migration Lead",
        "Fractional Data Engineer",
    ]
    assert [j.company for j in jobs] == ["Northwind Commerce", "Lumen Analytics", "Orbit Fintech"]
    assert [(j.raw_payload or {}).get("industry") for j in jobs] == [
        "E-commerce",
        "Marketing Analytics",
        "Fintech",
    ]
    for job in jobs:
        assert job.platform == Platform.toptal and job.extraction is not None
        assert job.extraction.title == job.title and job.extraction.company == job.company
        assert job.extraction.contract_type == ContractType.freelance
        assert job.extraction.summary is None


def test_jobs_hourly_range_remote_us_hours(jobs_text: str) -> None:
    job = toptal.parse_jobs(jobs_text, now=NOW)[0]
    ex = job.extraction
    assert ex is not None and ex.compensation is not None
    comp = ex.compensation
    assert (comp.min, comp.max, comp.currency) == (70, 90, "USD")
    assert comp.period == CompensationPeriod.hour and comp.type == "rate"
    assert comp.raw == "Rate: $70 - $90/hr"
    assert ex.remote_policy == RemotePolicy.remote_region
    assert ex.remote_regions == ["US"] and ex.timezone_range == "US hours"
    assert job.location == "Remote — US hours" and ex.location == "Remote — US hours"
    assert ex.employment_type == EmploymentType.full_time
    assert ex.technologies == ["Python", "dbt", "Snowflake", "Dagster"]
    assert job.posted_at == NOW - timedelta(days=2)
    assert job.raw_payload == {
        "industry": "E-commerce",
        "engagement": "Full-time",
        "duration": "6 months",
        "posted": "Posted 2 days ago",
    }
    assert job.raw_text.startswith(
        "Senior Data Platform Engineer\nNorthwind Commerce · E-commerce\n"
    )
    assert job.raw_text.endswith("Skills: Python, dbt, Snowflake, Dagster\nApply")


def test_jobs_fixed_budget_on_site(jobs_text: str) -> None:
    job = toptal.parse_jobs(jobs_text, now=NOW)[1]
    ex = job.extraction
    assert ex is not None and ex.compensation is not None
    comp = ex.compensation
    assert (comp.min, comp.max, comp.currency) == (20000, None, "USD")
    assert comp.period == CompensationPeriod.project and comp.type == "rate"
    assert comp.raw == "Budget: $20,000"
    assert ex.remote_policy == RemotePolicy.onsite
    assert ex.remote_regions == [] and ex.timezone_range is None
    assert job.location == "Berlin" and ex.location == "Berlin"
    assert ex.employment_type == EmploymentType.full_time
    assert job.posted_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert (job.raw_payload or {})["duration"] == "3 months"


def test_jobs_part_time_ongoing_remote(jobs_text: str) -> None:
    job = toptal.parse_jobs(jobs_text, now=NOW)[2]
    ex = job.extraction
    assert ex is not None and ex.compensation is not None
    assert ex.employment_type == EmploymentType.part_time
    assert (job.raw_payload or {})["hours_per_week"] == 20
    assert (job.raw_payload or {})["engagement"] == "Part-time (20 hrs/week)"
    assert ex.remote_policy == RemotePolicy.remote_global
    assert ex.remote_regions == [] and ex.timezone_range is None
    assert job.location == "Remote"
    assert (ex.compensation.min, ex.compensation.max) == (85, None)
    assert ex.compensation.period == CompensationPeriod.hour
    assert (job.raw_payload or {})["duration"] == "Ongoing"
    assert job.posted_at == NOW - timedelta(days=1)
    assert ex.technologies == ["Python", "Kafka", "ClickHouse"]


def test_jobs_cards_without_blank_lines_split_on_apply(jobs_text: str) -> None:
    dense = "\n".join(ln for ln in jobs_text.splitlines() if ln.strip())
    jobs = toptal.parse_jobs(dense, now=NOW)
    assert [j.title for j in jobs] == [
        "Senior Data Platform Engineer",
        "Analytics Warehouse Migration Lead",
        "Fractional Data Engineer",
    ]
    assert [j.company for j in jobs] == ["Northwind Commerce", "Lumen Analytics", "Orbit Fintech"]


@pytest.mark.parametrize(
    ("line", "policy", "regions", "tz", "location"),
    [
        ("Remote", RemotePolicy.remote_global, [], None, "Remote"),
        ("Remote — worldwide", RemotePolicy.remote_global, [], None, "Remote — worldwide"),
        (
            "Remote (EU time zones)",
            RemotePolicy.remote_region,
            ["EU"],
            "EU time zones",
            "Remote (EU time zones)",
        ),
        (
            "Remote, PST overlap",
            RemotePolicy.remote_region,
            ["US"],
            "PST overlap",
            "Remote, PST overlap",
        ),
        (
            "Remote — 4h overlap with CET",
            RemotePolicy.remote_region,
            ["EU"],
            "4h overlap with CET",
            "Remote — 4h overlap with CET",
        ),
        ("Hybrid, Warsaw", RemotePolicy.hybrid, [], None, "Warsaw"),
        ("Berlin, Germany (On-site)", RemotePolicy.onsite, [], None, "Berlin, Germany"),
    ],
)
def test_jobs_location_line_variants(
    line: str, policy: RemotePolicy, regions: list[str], tz: str | None, location: str
) -> None:
    text = f"Data Engineer\nNorthwind Commerce · E-commerce\nEngagement: Full-time\n{line}\nApply\n"
    job = toptal.parse_jobs(text, now=NOW)[0]
    assert job.extraction is not None
    assert job.extraction.remote_policy == policy
    assert job.extraction.remote_regions == regions
    assert job.extraction.timezone_range == tz
    assert job.location == location


@pytest.mark.parametrize(
    ("line", "lo", "hi", "currency", "period"),
    [
        ("Rate: $70 - $90/hr", 70, 90, "USD", CompensationPeriod.hour),
        ("Rate: $70–90 per hour", 70, 90, "USD", CompensationPeriod.hour),
        ("Hourly rate: USD 85/hour", 85, None, "USD", CompensationPeriod.hour),
        ("Rate: €60/hr", 60, None, "EUR", CompensationPeriod.hour),
        ("Budget: $20,000", 20000, None, "USD", CompensationPeriod.project),
        ("Fixed price: $15k", 15000, None, "USD", CompensationPeriod.project),
        ("Rate: $8,000/month", 8000, None, "USD", CompensationPeriod.month),
        ("Rate: 70 - 90/hr", 70, 90, None, CompensationPeriod.hour),
    ],
)
def test_jobs_compensation_variants(
    line: str, lo: float, hi: float | None, currency: str | None, period: CompensationPeriod
) -> None:
    text = f"Data Engineer\nNorthwind Commerce · E-commerce\n{line}\nApply\n"
    job = toptal.parse_jobs(text, now=NOW)[0]
    assert job.extraction is not None and job.extraction.compensation is not None
    comp = job.extraction.compensation
    assert (comp.min, comp.max, comp.currency, comp.period) == (lo, hi, currency, period)
    assert comp.type == "rate" and comp.raw == line


def test_jobs_maps_to_ingest_request(jobs_text: str) -> None:
    req = toptal.parse_jobs(jobs_text, now=NOW)[0].to_ingest()
    assert req.source == "toptal" and req.structured is not None
    assert req.structured.company == "Northwind Commerce"
    assert req.structured.compensation is not None and req.structured.compensation.max == 90
    assert req.received_at == NOW - timedelta(days=2)
    assert req.text is not None and "Rate: $70 - $90/hr" in req.text


def test_jobs_unrecognised_cards_are_skipped_when_others_parse() -> None:
    text = (
        "Jobs\n\nSenior Data Engineer\nNorthwind Commerce · E-commerce\nRate: $80/hr\nApply\n\n"
        "Showing 1 of 1 jobs\n"
    )
    jobs = toptal.parse_jobs(text, now=NOW)
    assert [j.title for j in jobs] == ["Senior Data Engineer"]


def test_jobs_unknown_layout_falls_back_to_generic() -> None:
    text = (
        "Data Engineer at Northwind Commerce\nSome description without portal markers.\n\n"
        "Analytics Engineer — Lumen Analytics\nAnother description.\n"
    )
    jobs = toptal.parse_jobs(text, now=NOW)
    assert jobs == parsers.generic_jobs(text, Platform.toptal)
    assert [j.title for j in jobs] == ["Data Engineer", "Analytics Engineer"]
    assert [j.company for j in jobs] == ["Northwind Commerce", "Lumen Analytics"]
    assert all(j.extraction is None for j in jobs)


def test_jobs_respects_limit(jobs_text: str) -> None:
    assert len(toptal.parse_jobs(jobs_text, now=NOW, limit=2)) == 2


# --------------------------------------------------------------------------- applications


def test_applications_rows_and_statuses(applications_text: str) -> None:
    obs = toptal.parse_applications(applications_text, now=NOW)
    assert [o.job_title for o in obs] == [
        "Senior Data Platform Engineer",
        "Analytics Warehouse Migration Lead",
        "Fractional Data Engineer",
        "Data Quality Engineer",
        "Streaming Platform Engineer",
    ]
    assert [o.company for o in obs] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Orbit Fintech",
        "Northwind Commerce",
        "Lumen Analytics",
    ]
    assert [o.status for o in obs] == [
        ApplicationStatus.interview,
        ApplicationStatus.offer,
        ApplicationStatus.applied,
        ApplicationStatus.rejected,
        ApplicationStatus.viewed,
    ]
    assert [o.status_raw for o in obs] == [
        "Stage: Interviewing",
        "Stage: Matched",
        "Stage: Applied",
        "Stage: Declined",
        "Stage: Under review",
    ]
    assert all(o.platform == Platform.toptal for o in obs)


def test_applications_dates_absolute_and_relative(applications_text: str) -> None:
    obs = toptal.parse_applications(applications_text, now=NOW)
    assert [o.applied_at for o in obs] == [
        datetime(2026, 8, 12, tzinfo=UTC),
        datetime(2026, 8, 5, tzinfo=UTC),
        NOW - timedelta(days=3),
        datetime(2026, 7, 28, tzinfo=UTC),
        datetime(2026, 7, 20, tzinfo=UTC),
    ]
    assert [o.updated_at_platform for o in obs] == [
        None,
        None,
        None,
        None,
        datetime(2026, 8, 1, tzinfo=UTC),
    ]


def test_applications_raw_payload_keeps_the_row_verbatim(applications_text: str) -> None:
    obs = toptal.parse_applications(applications_text, now=NOW)
    assert obs[0].raw_payload == {
        "lines": [
            "Senior Data Platform Engineer",
            "Northwind Commerce",
            "Applied Aug 12, 2026",
            "Stage: Interviewing",
            "View job",
        ]
    }
    # the "Withdraw application" button on an active row is chrome, not a status
    assert obs[2].status == ApplicationStatus.applied
    assert obs[2].raw_payload == {
        "lines": [
            "Fractional Data Engineer",
            "Orbit Fintech",
            "Applied 3 days ago",
            "Stage: Applied",
            "Withdraw application",
        ]
    }


def test_applications_rows_without_blank_lines(applications_text: str) -> None:
    dense = "\n".join(ln for ln in applications_text.splitlines() if ln.strip())
    obs = toptal.parse_applications(dense, now=NOW)
    assert len(obs) == 5
    assert [o.company for o in obs][:2] == ["Northwind Commerce", "Lumen Analytics"]
    assert obs[4].status == ApplicationStatus.viewed


def test_applications_single_line_rows() -> None:
    text = (
        "Fractional Data Engineer · Orbit Fintech · Applied Aug 12, 2026 · Stage: Withdrawn\n"
        "Data Quality Engineer · Northwind Commerce · Applied yesterday · Stage: Closed\n"
    )
    obs = toptal.parse_applications(text, now=NOW)
    assert [(o.job_title, o.company) for o in obs] == [
        ("Fractional Data Engineer", "Orbit Fintech"),
        ("Data Quality Engineer", "Northwind Commerce"),
    ]
    assert [o.status for o in obs] == [ApplicationStatus.withdrawn, ApplicationStatus.rejected]
    assert [o.applied_at for o in obs] == [
        datetime(2026, 8, 12, tzinfo=UTC),
        NOW - timedelta(days=1),
    ]
    assert obs[0].raw_payload == {
        "lines": [
            "Fractional Data Engineer · Orbit Fintech · Applied Aug 12, 2026 · Stage: Withdrawn"
        ]
    }


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("Stage: Applied", ApplicationStatus.applied),
        ("Stage: Under review", ApplicationStatus.viewed),
        ("Stage: Interviewing", ApplicationStatus.interview),
        ("Stage: Matched", ApplicationStatus.offer),
        ("Stage: Declined", ApplicationStatus.rejected),
        ("Stage: Closed", ApplicationStatus.rejected),
        ("Stage: Withdrawn", ApplicationStatus.withdrawn),
        ("Status: Interview scheduled", ApplicationStatus.interview),
        ("Stage: Something brand new", ApplicationStatus.unknown),
    ],
)
def test_applications_stage_map(stage: str, expected: ApplicationStatus) -> None:
    obs = toptal.parse_applications(f"Data Engineer\nNorthwind Commerce\n{stage}\n", now=NOW)
    assert len(obs) == 1
    assert obs[0].status == expected and obs[0].status_raw == stage
    assert obs[0].company == "Northwind Commerce"


def test_applications_unknown_layout_falls_back_to_generic() -> None:
    text = (
        "Data Engineer at Northwind Commerce\nApplication viewed\n\n"
        "Analytics Engineer — Lumen Analytics\nNot selected by employer\n"
    )
    obs = toptal.parse_applications(text, now=NOW)
    assert obs == parsers.generic_applications(text, Platform.toptal, now=NOW)
    assert [o.status for o in obs] == [ApplicationStatus.viewed, ApplicationStatus.rejected]
