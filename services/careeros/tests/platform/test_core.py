# ruff: noqa: E501
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from careeros.modules.platform import parsers
from careeros.modules.platform.base import BaseConnector, CapabilityUnavailable
from careeros.modules.platform.enums import (
    PLATFORMS,
    ApplicationStatus,
    CapabilityLevel,
    SyncKind,
    SyncMethod,
)
from careeros.modules.platform.registry import PlatformRegistry, UnknownPlatform, get_registry
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    ProfileRead,
)
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

# --------------------------------------------------------------------------- registry / matrix


def test_registry_default_has_all_platforms() -> None:
    reg = get_registry()
    assert reg.platforms() == list(PLATFORMS)
    assert len(reg.capabilities()) == 7
    assert reg.get("hh").platform == Platform.hh
    with pytest.raises(UnknownPlatform):
        reg.get("myspace")


def test_registry_verify_is_clean() -> None:
    assert get_registry().verify() == []


def test_registry_verify_flags_declared_but_unimplemented() -> None:
    class Broken(BaseConnector):
        platform = Platform.toptal
        capabilities = Capabilities(platform=Platform.toptal, jobs=[SyncMethod.api])

    problems = PlatformRegistry([Broken()]).verify()
    assert problems == ["toptal: declares jobs/api but no search_jobs()"]


def test_capabilities_levels_are_derived_from_methods() -> None:
    caps = Capabilities(
        platform=Platform.hh,
        profile=[SyncMethod.paste, SyncMethod.api],  # order normalised: api first
        jobs=[SyncMethod.export],
        applications=[],
    )
    assert caps.profile == [SyncMethod.api, SyncMethod.paste]
    assert caps.read_profile == CapabilityLevel.api
    assert caps.read_opportunities == CapabilityLevel.export
    assert caps.read_applications == CapabilityLevel.none
    assert caps.export_import == CapabilityLevel.export
    assert caps.manual_capture is True
    dumped = caps.model_dump(mode="json")
    assert dumped["read_profile"] == "api" and dumped["apply"] == "none"


async def test_base_connector_defaults_raise_capability_unavailable() -> None:
    class Paste(BaseConnector):
        platform = Platform.toptal
        capabilities = Capabilities(platform=Platform.toptal, profile=[SyncMethod.paste])

        def parse_profile_text(self, text: str) -> ProfileRead:
            return ProfileRead(platform=self.platform, headline=text)

    c = Paste()
    with pytest.raises(CapabilityUnavailable) as exc:
        await c.read_profile(None)  # type: ignore[arg-type]
    assert exc.value.available == [SyncMethod.paste] and exc.value.kind == SyncKind.profile
    assert c.parse_profile_text("Senior DE").headline == "Senior DE"


# --------------------------------------------------------------------------- DTO mapping


def test_profile_read_maps_to_snapshot() -> None:
    pr = ProfileRead(
        platform=Platform.hh,
        capture_method=CaptureMethod.api,
        external_id="r-1",
        profile_url="https://hh.ru/resume/r-1",
        headline="Senior Data Engineer",
        skills=["dbt", "ClickHouse"],
        rates={"salary": 500000, "currency": "RUR"},
    )
    snap = pr.to_snapshot()
    assert snap.platform == Platform.hh and snap.capture_method == CaptureMethod.api
    assert snap.preferences["profile_url"].endswith("r-1") and snap.skills == ["dbt", "ClickHouse"]


def test_job_posting_maps_to_ingest_request_with_platform_source() -> None:
    jp = JobPosting(
        platform=Platform.getmatch,
        title="Data Engineer",
        company="Northwind Commerce",
        url="https://getmatch.ru/vacancies/1",
        raw_text="Data Engineer at Northwind Commerce\nRemote, 300 000 ₽",
    )
    req = jp.to_ingest()
    assert req.source == "getmatch" and req.url == jp.url
    assert req.structured is not None and req.structured.title == "Data Engineer"
    assert req.structured.company == "Northwind Commerce" and req.text == jp.raw_text


def test_observation_content_hash_is_stable_and_case_insensitive() -> None:
    a = ApplicationObservationIn(
        platform=Platform.indeed, job_title="Data Engineer", company="Lumen Analytics"
    )
    b = ApplicationObservationIn(
        platform=Platform.indeed, job_title="data engineer ", company="lumen analytics"
    )
    c = ApplicationObservationIn(
        platform=Platform.indeed, job_title="Data Engineer", company="Other"
    )
    assert a.content_hash() == b.content_hash() != c.content_hash()


# --------------------------------------------------------------------------- shared parsers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Application viewed", ApplicationStatus.viewed),
        ("Viewed by employer", ApplicationStatus.viewed),
        ("Not selected by employer", ApplicationStatus.rejected),
        ("Not moving forward", ApplicationStatus.rejected),
        ("Interviewing", ApplicationStatus.interview),
        ("Application sent", ApplicationStatus.applied),
        ("Отклик отправлен", ApplicationStatus.applied),
        ("Приглашение", ApplicationStatus.invited),
        ("Отказ", ApplicationStatus.rejected),
        ("Собеседование", ApplicationStatus.interview),
        ("Withdrawn", ApplicationStatus.withdrawn),
        ("Offer received", ApplicationStatus.offer),
        ("banana", ApplicationStatus.unknown),
    ],
)
def test_normalize_status_en_ru(raw: str, expected: ApplicationStatus) -> None:
    assert parsers.normalize_status(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-12", datetime(2026, 8, 12, tzinfo=UTC)),
        ("2026-08-12T10:30:00", datetime(2026, 8, 12, 10, 30, tzinfo=UTC)),
        ("Aug 12, 2026", datetime(2026, 8, 12, tzinfo=UTC)),
        ("12 Aug 2026", datetime(2026, 8, 12, tzinfo=UTC)),
        ("12 августа 2026", datetime(2026, 8, 12, tzinfo=UTC)),
        ("12.08.2026", datetime(2026, 8, 12, tzinfo=UTC)),
        ("8/12/26, 10:15 AM", datetime(2026, 8, 12, tzinfo=UTC)),
        ("Applied on Aug 12, 2026", datetime(2026, 8, 12, tzinfo=UTC)),
        ("3 days ago", NOW.replace(day=22)),
        ("2 дня назад", NOW.replace(day=23)),
        ("yesterday", NOW.replace(day=24)),
        ("Posted 5 hours ago", NOW.replace(hour=7)),
        ("no date here", None),
    ],
)
def test_parse_date_absolute_and_relative(raw: str, expected: datetime | None) -> None:
    assert parsers.parse_date(raw, now=NOW) == expected


def test_guess_title_company_variants() -> None:
    assert parsers.guess_title_company("Data Engineer at Northwind Commerce") == (
        "Data Engineer",
        "Northwind Commerce",
    )
    assert parsers.guess_title_company("Data Engineer — Lumen Analytics") == (
        "Data Engineer",
        "Lumen Analytics",
    )
    assert parsers.guess_title_company("Data Engineer · Orbit Fintech") == (
        "Data Engineer",
        "Orbit Fintech",
    )
    assert parsers.guess_title_company("Data Engineer") == ("Data Engineer", None)


PROFILE_PASTE = """Dana Kovalenko
Senior Data Engineer | dbt, Dagster, ClickHouse
Tbilisi, Georgia

About
I build analytics platforms.
Twelve years in data.

Experience
Senior Data Engineer at Northwind Commerce
2023 – now
Lead Analytics Engineer at Lumen Analytics
2020 – 2023

Skills
Python · SQL · dbt · Dagster · ClickHouse

Education
Some University
"""

JOBS_PASTE = """Data Engineer at Northwind Commerce
Remote · Full-time
https://example.com/jobs/1
Apply

Analytics Engineer — Lumen Analytics
Warsaw, Poland (Hybrid)
Promoted
"""

APPS_PASTE = """Data Engineer at Northwind Commerce
Applied on Aug 12, 2026
Application viewed

Analytics Engineer — Lumen Analytics
Applied 3 days ago
Not selected by employer
"""


def test_generic_profile_parser() -> None:
    pr = parsers.generic_profile(PROFILE_PASTE, Platform.toptal)
    assert pr.headline == "Dana Kovalenko"
    assert pr.about == "I build analytics platforms. Twelve years in data."
    assert pr.skills == ["Python", "SQL", "dbt", "Dagster", "ClickHouse"]
    assert [e.company for e in pr.experience] == ["Northwind Commerce", "Lumen Analytics"]
    assert pr.experience[0].period == "2023 – now" and pr.raw_text == PROFILE_PASTE


def test_generic_jobs_parser() -> None:
    jobs = parsers.generic_jobs(JOBS_PASTE, Platform.wellfound)
    assert [j.title for j in jobs] == ["Data Engineer", "Analytics Engineer"]
    assert jobs[0].company == "Northwind Commerce" and jobs[0].url == "https://example.com/jobs/1"
    assert (
        jobs[0].location == "Remote · Full-time" and jobs[1].location == "Warsaw, Poland (Hybrid)"
    )
    assert jobs[0].raw_text.startswith("Data Engineer at Northwind")


def test_generic_applications_parser() -> None:
    obs = parsers.generic_applications(APPS_PASTE, Platform.indeed, now=NOW)
    assert [o.status for o in obs] == [ApplicationStatus.viewed, ApplicationStatus.rejected]
    assert obs[0].applied_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert obs[1].applied_at == NOW.replace(day=22) and obs[1].company == "Lumen Analytics"
    assert obs[0].status_raw == "Application viewed"
