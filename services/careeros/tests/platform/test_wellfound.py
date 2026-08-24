# ruff: noqa: E501
"""Wellfound connector: paste-only parsers over realistic page copies (synthetic persona)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from careeros.modules.opportunities.enums import CompensationPeriod, RemotePolicy
from careeros.modules.platform.connectors.wellfound import parsers as wf
from careeros.modules.platform.connectors.wellfound.connector import Connector
from careeros.modules.platform.enums import (
    ApplicationStatus,
    AuthKind,
    CapabilityLevel,
    SyncMethod,
)
from careeros.modules.platform.registry import PlatformRegistry, get_registry
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "wellfound"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- capabilities


def test_capabilities_are_paste_only_and_registry_is_clean() -> None:
    caps = Connector.capabilities
    assert caps.platform == Platform.wellfound
    assert caps.profile == caps.jobs == caps.applications == [SyncMethod.paste]
    assert (
        caps.read_profile
        == caps.read_opportunities
        == caps.read_applications
        == CapabilityLevel.manual
    )
    assert caps.official_api is False and caps.email_fallback is True and caps.auth == AuthKind.none
    assert caps.manual_capture is True and caps.export_import == CapabilityLevel.none
    assert "never fetched" in caps.notes
    assert PlatformRegistry([Connector()]).verify() == []
    assert get_registry().get("wellfound").capabilities == caps


# --------------------------------------------------------------------------- profile


def test_profile_header_and_sections_in_mixed_order() -> None:
    pr = wf.parse_profile(fixture("paste_profile.txt"))
    assert pr.platform == Platform.wellfound and pr.capture_method == CaptureMethod.paste
    assert pr.headline == "Senior Data Engineer · Tbilisi, Georgia"
    assert pr.raw_payload is not None and pr.raw_payload["name"] == "Dana Kovalenko"
    assert pr.availability == "Ready to interview"
    assert pr.preferences["location"] == "Tbilisi, Georgia"
    # Skills came before About, Education before Work experience: header-driven, order-free.
    assert pr.skills == [
        "Python",
        "SQL",
        "dbt",
        "Dagster",
        "ClickHouse",
        "Airflow",
        "Kafka",
        "Terraform",
    ]
    assert pr.about is not None
    assert pr.about.startswith("I build analytics platforms for e-commerce")
    assert pr.about.endswith("four of them leading platform teams.")
    assert "Edit profile" not in pr.about and "Wellfound" not in (pr.headline or "")


def test_profile_experience_entries_with_periods_and_descriptions() -> None:
    pr = wf.parse_profile(fixture("paste_profile.txt"))
    assert [(e.company, e.title) for e in pr.experience] == [
        ("Northwind Commerce", "Senior Data Engineer"),
        ("Lumen Analytics", "Lead Analytics Engineer"),
        ("Orbit Fintech", "Data Engineer"),
    ]
    assert [e.period for e in pr.experience] == [
        "Jan 2023 – Present",
        "Mar 2020 – Dec 2022",
        "Jun 2016 – Feb 2020",
    ]
    first = pr.experience[0].description
    assert (
        first is not None and first.startswith("Built the lakehouse") and first.endswith("40 min.")
    )
    assert (
        pr.experience[1].description
        == "Owned the metrics layer for 40+ dashboards; introduced dbt tests and CI."
    )
    assert pr.experience[2].description is None


def test_profile_preferences() -> None:
    pr = wf.parse_profile(fixture("paste_profile.txt"))
    assert pr.preferences["remote"] is True
    assert pr.preferences["roles"] == ["Full-time", "Contract"]
    assert pr.preferences["desired_salary"] == "$120k+"
    assert pr.preferences["locations"] == ["Remote", "Tbilisi", "Warsaw"]


def test_profile_education_in_raw_payload_and_raw_text_verbatim() -> None:
    text = fixture("paste_profile.txt")
    pr = wf.parse_profile(text)
    assert pr.raw_text == text
    assert pr.raw_payload is not None
    assert pr.raw_payload["education"] == [
        "Kyiv Polytechnic Institute",
        "MSc, Computer Science",
        "2008 – 2013",
    ]
    # Nothing invented: no rates, no projects, no portfolio.
    assert pr.rates is None and pr.projects == [] and pr.portfolio == []


def test_profile_without_name_line_and_unknown_preferences() -> None:
    pr = wf.parse_profile("Senior Data Engineer\n\nAbout\nHello there.\n")
    assert pr.headline == "Senior Data Engineer" and pr.about == "Hello there."
    assert pr.raw_payload is None and pr.availability is None
    assert pr.preferences == {"remote": None, "roles": [], "desired_salary": None}


def test_profile_company_first_experience_layout_and_profile_url() -> None:
    text = (
        "https://wellfound.com/u/dana-kovalenko\n"
        "Dana Kovalenko\n"
        "Data Engineer\n\n"
        "Experience\n"
        "Orbit Fintech\n"
        "Data Engineer\n"
        "2016 – 2020\n"
    )
    pr = wf.parse_profile(text)
    assert pr.profile_url == "https://wellfound.com/u/dana-kovalenko"
    assert pr.external_id == "dana-kovalenko" and pr.headline == "Data Engineer"
    assert [(e.company, e.title, e.period) for e in pr.experience] == [
        ("Orbit Fintech", "Data Engineer", "2016 – 2020")
    ]


# --------------------------------------------------------------------------- jobs


def test_jobs_cards_titles_companies_and_multi_role_cards() -> None:
    jobs = wf.parse_jobs(fixture("paste_jobs.txt"), now=NOW)
    assert [j.title for j in jobs] == [
        "Senior Data Engineer",
        "Analytics Engineer",
        "Data Platform Lead",
        "Staff Data Engineer",
    ]
    assert [j.company for j in jobs] == [
        "Northwind Commerce",
        "Lumen Analytics",
        "Lumen Analytics",
        "Orbit Fintech",
    ]
    assert all(j.platform == Platform.wellfound for j in jobs)
    assert [j.location for j in jobs] == [
        "Remote • US",
        "Tbilisi, Georgia • Hybrid",
        "Remote",
        "Berlin, Germany • In office",
    ]
    for j in jobs:
        assert j.extraction is not None
        assert j.extraction.title == j.title and j.extraction.company == j.company
        assert j.extraction.location == j.location and j.extraction.summary is None


def test_jobs_remote_policy_from_location_line() -> None:
    jobs = wf.parse_jobs(fixture("paste_jobs.txt"), now=NOW)
    policies = [
        (j.extraction.remote_policy, j.extraction.remote_regions) for j in jobs if j.extraction
    ]
    assert policies == [
        (RemotePolicy.remote_region, ["US"]),
        (RemotePolicy.hybrid, []),
        (RemotePolicy.remote_global, []),
        (RemotePolicy.onsite, []),
    ]


def test_jobs_compensation_salary_ranges_currency_and_equity() -> None:
    jobs = wf.parse_jobs(fixture("paste_jobs.txt"), now=NOW)
    comp = jobs[0].extraction.compensation if jobs[0].extraction else None
    assert comp is not None
    assert (comp.min, comp.max, comp.currency) == (140000, 170000, "USD")
    assert comp.period == CompensationPeriod.year and comp.type == "salary"
    assert comp.raw == "$140k – $170k • 0.05% – 0.1%"
    assert jobs[0].raw_payload is not None
    assert jobs[0].raw_payload["equity"] == "0.05% – 0.1%"
    assert jobs[0].raw_payload["company_size"] == "51-200 employees"

    hybrid = jobs[1].extraction.compensation if jobs[1].extraction else None
    assert hybrid is not None and (hybrid.min, hybrid.max, hybrid.currency) == (60000, 85000, "USD")
    assert jobs[1].raw_payload is not None and "equity" not in jobs[1].raw_payload

    euro = jobs[2].extraction.compensation if jobs[2].extraction else None
    assert euro is not None and (euro.min, euro.max, euro.currency) == (90000, 120000, "EUR")

    assert jobs[3].extraction is not None and jobs[3].extraction.compensation is None


def test_jobs_posted_at_uses_injected_now() -> None:
    jobs = wf.parse_jobs(fixture("paste_jobs.txt"), now=NOW)
    assert [j.posted_at for j in jobs] == [
        NOW - timedelta(days=3),
        NOW,
        NOW - timedelta(days=7),
        NOW - timedelta(days=1),
    ]


def test_jobs_raw_text_keeps_card_header_per_role() -> None:
    jobs = wf.parse_jobs(fixture("paste_jobs.txt"), now=NOW)
    lead = jobs[2].raw_text
    assert lead.startswith("Lumen Analytics\n11-50 employees\n")
    assert "Data Platform Lead\nRemote\n€90k – €120k • 0.2% – 0.5%\nPosted 1 week ago" in lead
    assert "Analytics Engineer" not in lead
    assert jobs[0].raw_text.startswith("Northwind Commerce · 51-200 employees\n")
    assert jobs[0].raw_text.endswith("Posted 3 days ago\nApply\nSave")


def test_jobs_limit_and_connector_delegation() -> None:
    text = fixture("paste_jobs.txt")
    assert [j.title for j in wf.parse_jobs(text, limit=2)] == [
        "Senior Data Engineer",
        "Analytics Engineer",
    ]
    assert [j.title for j in Connector().parse_jobs_text(text)] == [
        j.title for j in wf.parse_jobs(text)
    ]


def test_jobs_single_job_page() -> None:
    text = (
        "Senior Data Engineer\n"
        "Northwind Commerce\n"
        "$140k – $170k • 0.05% – 0.1%\n"
        "Remote • US\n"
        "Actively Hiring\n"
        "Apply now\n"
        "Save\n"
        "Job Location\n"
        "Remote • US\n"
        "Visa Sponsorship\n"
        "Not Available\n"
        "Remote Work Policy\n"
        "Remote only\n"
        "Hires remotely in\n"
        "United States\n"
        "Skills\n"
        "Python\n"
        "dbt\n"
        "ClickHouse\n"
        "About the job\n"
        "We are looking for a senior data engineer to own the lakehouse.\n"
    )
    jobs = wf.parse_jobs(text, now=NOW)
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.title, job.company, job.location) == (
        "Senior Data Engineer",
        "Northwind Commerce",
        "Remote • US",
    )
    assert job.raw_text == text.strip()
    assert job.extraction is not None
    assert job.extraction.compensation is not None and job.extraction.compensation.max == 170000
    assert job.extraction.remote_policy == RemotePolicy.remote_region
    assert job.extraction.remote_regions == ["US"]
    assert job.extraction.technologies == ["Python", "dbt", "ClickHouse"]
    assert job.extraction.summary is None


def test_jobs_unknown_layout_falls_back_to_generic_parser() -> None:
    text = (
        "Data Engineer at Northwind Commerce\n"
        "Contractor friendly, async team\n"
        "https://example.com/jobs/1\n"
        "\n"
        "Analytics Engineer — Lumen Analytics\n"
        "Warsaw office, relocation package\n"
    )
    jobs = wf.parse_jobs(text, now=NOW)
    assert [(j.title, j.company) for j in jobs] == [
        ("Data Engineer", "Northwind Commerce"),
        ("Analytics Engineer", "Lumen Analytics"),
    ]
    assert jobs[0].url == "https://example.com/jobs/1" and jobs[0].extraction is None
    assert wf.parse_jobs("") == [] and wf.parse_jobs("Apply\nSave\n") == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("$120k", (120000, 120000, "USD", CompensationPeriod.year, "salary")),
        ("$120K – $150K", (120000, 150000, "USD", CompensationPeriod.year, "salary")),
        ("$120,000 – $150,000", (120000, 150000, "USD", CompensationPeriod.year, "salary")),
        ("$120k - $150k • 0.1% – 0.5%", (120000, 150000, "USD", CompensationPeriod.year, "salary")),
        ("€80k – €100k", (80000, 100000, "EUR", CompensationPeriod.year, "salary")),
        ("£70k+", (70000, None, "GBP", CompensationPeriod.year, "salary")),
        ("Up to $150k", (None, 150000, "USD", CompensationPeriod.year, "salary")),
        ("$50 – $70 / hr", (50, 70, "USD", CompensationPeriod.hour, "rate")),
        ("$8k – $10k / month", (8000, 10000, "USD", CompensationPeriod.month, "salary")),
        ("120k – 150k USD", (120000, 150000, "USD", CompensationPeriod.year, "salary")),
        ("C$120k – C$140k", (120000, 140000, "CAD", CompensationPeriod.year, "salary")),
        ("$1.2M", (1200000, 1200000, "USD", CompensationPeriod.year, "salary")),
    ],
)
def test_parse_money(
    line: str, expected: tuple[float | None, float | None, str, CompensationPeriod, str]
) -> None:
    comp = wf.parse_money(line)
    assert comp is not None
    assert (comp.min, comp.max, comp.currency, comp.period, comp.type) == expected
    assert comp.raw == line


@pytest.mark.parametrize(
    "line",
    [
        "No salary",
        "0.1% – 0.5%",
        "Equity only",
        "Series B",
        "51-200 employees",
        "",
        "Posted 3 days ago",
    ],
)
def test_parse_money_rejects_non_salary_lines(line: str) -> None:
    assert wf.parse_money(line) is None


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Remote • US", (RemotePolicy.remote_region, ["US"])),
        ("Remote • United States", (RemotePolicy.remote_region, ["US"])),
        ("Remote • Europe", (RemotePolicy.remote_region, ["EU"])),
        ("Remote (Worldwide)", (RemotePolicy.remote_global, [])),
        ("Remote", (RemotePolicy.remote_global, [])),
        ("New York City • Remote", (RemotePolicy.remote_region, ["New York City"])),
        ("Tbilisi, Georgia • Hybrid", (RemotePolicy.hybrid, [])),
        ("Berlin, Germany • In office", (RemotePolicy.onsite, [])),
        ("Berlin, Germany", (RemotePolicy.unknown, [])),
        ("Onsite or remote", (RemotePolicy.remote_global, [])),
    ],
)
def test_remote_policy_from_location_line(
    line: str, expected: tuple[RemotePolicy, list[str]]
) -> None:
    assert wf.remote_policy(line) == expected


# --------------------------------------------------------------------------- applications


def test_applications_rows_statuses_and_dates() -> None:
    obs = wf.parse_applications(fixture("paste_applications.txt"), now=NOW)
    assert [(o.company, o.job_title) for o in obs] == [
        ("Northwind Commerce", "Senior Data Engineer"),
        ("Lumen Analytics", "Analytics Engineer"),
        ("Orbit Fintech", "Staff Data Engineer"),
        ("Northwind Commerce", "Data Platform Lead"),
        ("Lumen Analytics", "Head of Data"),
    ]
    assert [o.status for o in obs] == [
        ApplicationStatus.applied,
        ApplicationStatus.viewed,
        ApplicationStatus.interview,
        ApplicationStatus.rejected,
        ApplicationStatus.offer,
    ]
    assert [o.status_raw for o in obs] == [
        "Application sent",
        "Viewed",
        "Interviewing",
        "Not moving forward",
        "Hired",
    ]
    assert [o.applied_at for o in obs] == [
        datetime(2026, 8, 12, tzinfo=UTC),
        NOW - timedelta(days=3),
        datetime(2026, 7, 30, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC),
        datetime(2026, 6, 15, tzinfo=UTC),
    ]
    assert all(o.platform == Platform.wellfound for o in obs)
    assert obs[0].raw_payload == {
        "lines": [
            "Northwind Commerce",
            "Senior Data Engineer",
            "Remote • US",
            "Applied Aug 12, 2026",
            "Application sent",
        ]
    }
    assert obs[3].raw_payload == {
        "lines": [
            "Northwind Commerce",
            "Data Platform Lead",
            "Applied Jul 2, 2026",
            "Not moving forward",
        ]
    }


def test_applications_rows_without_blank_lines_and_via_connector() -> None:
    text = fixture("paste_applications.txt")
    dense = "\n".join(ln for ln in text.splitlines() if ln.strip())
    assert [(o.job_title, o.status) for o in wf.parse_applications(dense, now=NOW)] == [
        (o.job_title, o.status) for o in wf.parse_applications(text, now=NOW)
    ]
    via_connector = Connector().parse_applications_text(text)
    assert [o.status_raw for o in via_connector] == [
        "Application sent",
        "Viewed",
        "Interviewing",
        "Not moving forward",
        "Hired",
    ]
    assert via_connector[1].applied_at is not None  # relative date resolved against the wall clock


def test_applications_row_with_only_applied_line_and_unknown_status_wording() -> None:
    text = "Orbit Fintech\nData Engineer\nApplied Jul 1, 2026\n\nLumen Analytics\nHead of Data\nApplied Jun 1, 2026\nIn review\n"
    obs = wf.parse_applications(text, now=NOW)
    assert (obs[0].status, obs[0].status_raw) == (ApplicationStatus.applied, "Applied Jul 1, 2026")
    assert (obs[1].status, obs[1].status_raw) == (ApplicationStatus.unknown, "In review")
    assert obs[1].applied_at == datetime(2026, 6, 1, tzinfo=UTC)


def test_applications_unknown_layout_falls_back_to_generic_parser() -> None:
    text = "Data Engineer at Northwind Commerce\nsome note from the recruiter\nthey rejected me by email\n"
    obs = wf.parse_applications(text, now=NOW)
    assert len(obs) == 1
    assert (obs[0].job_title, obs[0].company, obs[0].status) == (
        "Data Engineer",
        "Northwind Commerce",
        ApplicationStatus.rejected,
    )
    assert wf.parse_applications("") == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Application sent", ApplicationStatus.applied),
        ("Viewed", ApplicationStatus.viewed),
        ("Interviewing", ApplicationStatus.interview),
        ("Not moving forward", ApplicationStatus.rejected),
        ("Hired", ApplicationStatus.offer),
        ("Withdrawn", ApplicationStatus.withdrawn),
        ("Status: Interviewing", ApplicationStatus.interview),
        ("In review", ApplicationStatus.unknown),
    ],
)
def test_status_line_mapping(line: str, expected: ApplicationStatus) -> None:
    assert wf.is_status_line(line)
    assert wf.map_status(line) == expected
