"""LinkedIn connector: 'Download your data' archive importer + page-paste parsers (ADR-005).

No network. Exports are built inside the tests from the synthetic fixture CSVs (persona: Dana
Kovalenko; companies Northwind Commerce / Lumen Analytics / Orbit Fintech).
"""

from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from careeros.core.config import Settings
from careeros.modules.platform.base import CapabilityUnavailable, ConnectorContext, ParseError
from careeros.modules.platform.connectors.linkedin import export as li_export
from careeros.modules.platform.connectors.linkedin import parsers as li
from careeros.modules.platform.connectors.linkedin.connector import Connector
from careeros.modules.platform.enums import ApplicationStatus, AuthKind, SyncMethod
from careeros.modules.platform.registry import get_registry
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
CSV_FILES: tuple[str, ...] = (
    "Profile.csv",
    "Positions.csv",
    "Skills.csv",
    "Education.csv",
    "Certifications.csv",
    "Projects.csv",
    "Languages.csv",
    "Job Applications.csv",
    "Saved Jobs.csv",
)
JOB_URL_1 = "https://www.linkedin.com/jobs/view/4111111111/"
SAVED_URL_1 = "https://www.linkedin.com/jobs/view/4333333333/"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_export(
    tmp_path: Path,
    *,
    files: tuple[str, ...] = CSV_FILES,
    as_zip: bool = False,
    bom: bool = False,
    crlf: bool = False,
    subdir: str | None = None,
    overrides: dict[str, str] | None = None,
) -> Path:
    """Write fixture CSVs to ``tmp_path`` as a directory or a ``.zip`` (optionally nested)."""
    contents = {name: _read(name) for name in files}
    contents.update(overrides or {})

    def encode(text: str) -> bytes:
        if crlf:
            text = text.replace("\n", "\r\n")
        data = text.encode("utf-8")
        return b"\xef\xbb\xbf" + data if bom else data

    prefix = f"{subdir}/" if subdir else ""
    if as_zip:
        archive = tmp_path / "Basic_LinkedInDataExport_08-25-2026.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in contents.items():
                zf.writestr(prefix + name, encode(text))
        return archive
    root = tmp_path / "export"
    target = root / subdir if subdir else root
    target.mkdir(parents=True)
    for name, text in contents.items():
        (target / name).write_bytes(encode(text))
    return root


@pytest.fixture
def connector() -> Connector:
    return Connector()


# --------------------------------------------------------------------------- capabilities


def test_capabilities_declaration(connector: Connector) -> None:
    caps = connector.capabilities
    assert caps.platform == Platform.linkedin
    assert caps.profile == [SyncMethod.export, SyncMethod.paste]
    assert caps.jobs == [SyncMethod.export, SyncMethod.paste]
    assert caps.applications == [SyncMethod.export, SyncMethod.paste]
    assert caps.official_api is False and caps.auth == AuthKind.none
    assert caps.email_fallback is True and caps.manual_capture is True
    assert caps.read_profile == "export" and caps.export_import == "export"
    assert "Download your data" in caps.notes


def test_registry_serves_linkedin_connector_and_verifies_clean() -> None:
    reg = get_registry()
    assert isinstance(reg.get("linkedin"), Connector)
    assert reg.verify() == []


async def test_api_tier_is_not_declared_and_doctor_needs_no_tokens(
    connector: Connector, settings: Settings
) -> None:
    async with httpx.AsyncClient() as http:
        ctx = ConnectorContext(settings=settings, http=http, now=NOW)
        with pytest.raises(CapabilityUnavailable) as exc:
            await connector.read_profile(ctx)
        assert exc.value.available == [SyncMethod.export, SyncMethod.paste]
        checks = await connector.doctor(ctx)
    assert [c.name for c in checks] == ["capabilities"] and all(c.ok for c in checks)


# --------------------------------------------------------------------------- export: profile


def test_profile_export_from_directory(tmp_path: Path, connector: Connector) -> None:
    pr = connector.import_profile_export(make_export(tmp_path))
    assert pr.platform == Platform.linkedin and pr.capture_method == CaptureMethod.export
    assert pr.headline == "Senior Data Engineer | dbt, Dagster, ClickHouse"
    assert pr.about == (
        "I build analytics platforms that finance teams trust.\n"
        "Twelve years in data, the last five leading platform work."
    )
    assert [(e.company, e.title, e.period) for e in pr.experience] == [
        ("Northwind Commerce", "Senior Data Engineer", "Jan 2023 – now"),
        ("Lumen Analytics", "Lead Analytics Engineer", "Mar 2020 – Dec 2022"),
        ("Orbit Fintech", "Data Engineer", "Jun 2016 – Feb 2020"),
    ]
    assert pr.experience[0].description is not None
    assert "Cut pipeline latency" in pr.experience[0].description
    assert pr.experience[2].description is None
    assert pr.skills == ["Python", "SQL", "dbt", "Dagster", "ClickHouse"]
    assert pr.projects == [
        {
            "name": "Warehouse Cost Radar",
            "description": "Open-source ClickHouse cost dashboard.",
            "url": "https://github.com/example/cost-radar",
            "period": "May 2024 – now",
        }
    ]
    prefs = pr.preferences
    assert prefs["name"] == "Dana Kovalenko"
    assert prefs["industry"] == "Information Technology & Services"
    assert prefs["location"] == "Tbilisi, Georgia"
    assert prefs["websites"] == ["https://dana-kovalenko.example.dev"]
    assert prefs["languages"] == [
        {"name": "English", "proficiency": "Full professional proficiency"},
        {"name": "Ukrainian", "proficiency": "Native or bilingual proficiency"},
    ]
    assert prefs["certifications"] == [
        {
            "name": "Analytics Engineering Certification",
            "authority": "Example Certification Board",
            "url": "https://credentials.example.com/ae/123",
            "issued": "Mar 2024",
            "expires": None,
            "license_number": "AE-123",
        }
    ]
    assert prefs["education"] == [
        {
            "school": "Riverside Technical University",
            "degree": "Master of Science (MSc), Computer Science",
            "period": "2010 – 2015",
            "notes": None,
            "activities": "Programming club",
        }
    ]
    assert pr.external_id is None and pr.profile_url is None
    raw = pr.raw_payload
    assert raw is not None
    assert raw["profile"]["Headline"] == pr.headline and raw["profile"]["First Name"] == "Dana"
    for pii in ("Address", "Birth Date", "Zip Code", "Instant Messengers"):
        assert pii not in raw["profile"]
    assert raw["counts"] == {
        "positions": 3,
        "skills": 5,
        "education": 1,
        "certifications": 1,
        "projects": 1,
        "languages": 2,
    }
    assert raw["files"] == sorted(CSV_FILES)
    snap = pr.to_snapshot()
    assert (
        snap.capture_method == CaptureMethod.export and snap.preferences["name"] == "Dana Kovalenko"
    )


def test_profile_export_from_zip_with_nested_folder(tmp_path: Path, connector: Connector) -> None:
    archive = make_export(tmp_path, as_zip=True, subdir="Basic_LinkedInDataExport_08-25-2026")
    assert zipfile.is_zipfile(archive)
    pr = connector.import_profile_export(archive)
    assert pr.headline is not None and pr.headline.startswith("Senior Data Engineer")
    assert len(pr.experience) == 3 and pr.experience[0].period == "Jan 2023 – now"
    assert len(connector.import_jobs_export(archive)) == 2
    assert len(connector.import_applications_export(archive)) == 2


def test_export_tolerates_utf8_bom_and_crlf(tmp_path: Path, connector: Connector) -> None:
    pr = connector.import_profile_export(make_export(tmp_path, bom=True, crlf=True))
    assert pr.headline == "Senior Data Engineer | dbt, Dagster, ClickHouse"
    assert pr.experience[0].period == "Jan 2023 – now"
    assert pr.about is not None and "\r" not in pr.about and "Twelve years" in pr.about
    assert pr.skills == ["Python", "SQL", "dbt", "Dagster", "ClickHouse"]


def test_profile_export_tolerates_missing_optional_files(
    tmp_path: Path, connector: Connector
) -> None:
    pr = connector.import_profile_export(make_export(tmp_path, files=("Profile.csv",)))
    assert pr.headline is not None and pr.headline.startswith("Senior Data Engineer")
    assert pr.experience == [] and pr.skills == [] and pr.projects == []
    assert pr.preferences["education"] == [] and pr.preferences["languages"] == []
    assert pr.preferences["certifications"] == []
    assert pr.raw_payload is not None
    assert pr.raw_payload["counts"]["positions"] == 0 and pr.raw_payload["files"] == ["Profile.csv"]


def test_profile_export_requires_profile_csv(tmp_path: Path, connector: Connector) -> None:
    path = make_export(tmp_path, files=("Positions.csv", "Skills.csv"))
    with pytest.raises(ParseError, match=r"Profile\.csv not found in export"):
        connector.import_profile_export(path)


def test_export_rejects_paths_that_are_neither_dir_nor_zip(
    tmp_path: Path, connector: Connector
) -> None:
    stray = tmp_path / "notes.txt"
    stray.write_text("not an archive", encoding="utf-8")
    with pytest.raises(ParseError, match=r"directory or a \.zip"):
        connector.import_profile_export(stray)
    with pytest.raises(ParseError, match="not found"):
        li_export.open_export(tmp_path / "missing")


def test_export_reader_lists_files_and_looks_up_case_insensitively(tmp_path: Path) -> None:
    reader = li_export.open_export(make_export(tmp_path, files=("Profile.csv", "Skills.csv")))
    assert reader.files() == ["Profile.csv", "Skills.csv"]
    assert reader.has("skills.csv") and not reader.has("Positions.csv")
    assert reader.rows("Positions.csv") == []
    assert [r["Name"] for r in reader.rows("Skills.csv")] == [
        "Python",
        "SQL",
        "dbt",
        "Dagster",
        "ClickHouse",
    ]
    with pytest.raises(ParseError, match=r"Positions\.csv not found in export"):
        reader.rows("Positions.csv", required=True)


# --------------------------------------------------------------------------- export: applications


def test_applications_export_maps_rows_and_strips_pii(tmp_path: Path, connector: Connector) -> None:
    obs = connector.import_applications_export(make_export(tmp_path))
    assert [(o.job_title, o.company) for o in obs] == [
        ("Staff Data Engineer", "Northwind Commerce"),
        ("Analytics Engineering Lead", "Lumen Analytics"),
    ]
    first = obs[0]
    assert first.platform == Platform.linkedin
    assert first.external_id == first.job_url == JOB_URL_1
    assert first.status == ApplicationStatus.applied and first.status_raw == "applied"
    assert first.applied_at == datetime(2026, 8, 12, 10, 15, tzinfo=UTC)
    assert obs[1].applied_at == datetime(2026, 8, 3, 21, 2, tzinfo=UTC)
    raw = first.raw_payload
    assert raw is not None
    assert raw["Resume Name"] == "Dana_Kovalenko_CV.pdf"
    assert raw["Question And Answers"].startswith("Question: How many years")
    assert raw["Application Date"] == "8/12/26, 10:15 AM" and raw["source_file"].endswith(".csv")
    assert "Contact Email" not in raw and "Contact Phone Number" not in raw
    assert "dana@example.com" not in str(raw) and "+995" not in str(raw)
    assert obs[1].raw_payload is not None and obs[1].raw_payload["Question And Answers"] == ""
    assert first.content_hash() != obs[1].content_hash()


def test_applications_export_skips_notes_preamble(tmp_path: Path, connector: Connector) -> None:
    preamble = (
        "Notes:\n"
        '"This file lists the jobs you applied to on LinkedIn, most recent first.\n'
        'Applications submitted on company websites may be missing."\n'
        "\n"
    )
    path = make_export(
        tmp_path, overrides={"Job Applications.csv": preamble + _read("Job Applications.csv")}
    )
    obs = connector.import_applications_export(path)
    assert len(obs) == 2 and obs[0].job_title == "Staff Data Engineer"
    assert obs[0].applied_at == datetime(2026, 8, 12, 10, 15, tzinfo=UTC)


def test_applications_export_requires_job_applications_csv(
    tmp_path: Path, connector: Connector
) -> None:
    with pytest.raises(ParseError, match=r"Job Applications\.csv not found in export"):
        connector.import_applications_export(make_export(tmp_path, files=("Profile.csv",)))


# --------------------------------------------------------------------------- export: saved jobs


def test_jobs_export_maps_saved_jobs(tmp_path: Path, connector: Connector) -> None:
    jobs = connector.import_jobs_export(make_export(tmp_path))
    assert [(j.title, j.company) for j in jobs] == [
        ("Principal Data Engineer", "Orbit Fintech"),
        ("Analytics Platform Lead", "Lumen Analytics"),
    ]
    job = jobs[0]
    assert job.platform == Platform.linkedin
    assert job.external_id == job.url == SAVED_URL_1
    assert job.posted_at == datetime(2026, 8, 20, 8, 45, tzinfo=UTC)
    assert job.location is None and job.extraction is None
    assert (
        job.raw_text
        == f"Principal Data Engineer at Orbit Fintech\n{SAVED_URL_1}\nSaved on 8/20/26, 8:45 AM"
    )
    assert job.raw_payload is not None and job.raw_payload["Saved Date"] == "8/20/26, 8:45 AM"
    req = job.to_ingest()
    assert req.source == "linkedin" and req.url == SAVED_URL_1 and req.received_at == job.posted_at
    assert req.structured is not None and req.structured.company == "Orbit Fintech"


def test_jobs_export_requires_saved_jobs_csv(tmp_path: Path, connector: Connector) -> None:
    with pytest.raises(ParseError, match=r"Saved Jobs\.csv not found in export"):
        connector.import_jobs_export(make_export(tmp_path, files=("Profile.csv",)))


# --------------------------------------------------------------------------- paste: profile page


def test_parse_profile_page(connector: Connector) -> None:
    text = _read("profile_page.txt")
    pr = connector.parse_profile_text(text)
    assert pr.platform == Platform.linkedin and pr.capture_method == CaptureMethod.paste
    assert pr.raw_text == text
    assert pr.headline == "Senior Data Engineer | dbt, Dagster, ClickHouse"
    assert pr.preferences["name"] == "Dana Kovalenko"
    assert pr.preferences["location"] == "Tbilisi, Georgia"
    assert pr.about == (
        "I build analytics platforms that finance teams trust.\n"
        "Twelve years in data, the last five leading platform work."
    )
    assert [(e.title, e.company, e.period) for e in pr.experience] == [
        ("Senior Data Engineer", "Northwind Commerce", "Jan 2023 - Present"),
        ("Lead Analytics Engineer", "Lumen Analytics", "Jan 2022 - Dec 2022"),
        ("Analytics Engineer", "Lumen Analytics", "Mar 2020 - Dec 2021"),
        ("Data Engineer", "Orbit Fintech", "Jun 2016 - Feb 2020"),
    ]
    assert (
        pr.experience[0].description
        == "Built the analytics platform on dbt + Dagster + ClickHouse."
    )
    assert pr.experience[1].description is None and pr.experience[3].description is None
    assert pr.skills == ["dbt", "Dagster", "ClickHouse", "Python", "SQL", "Airflow"]
    assert pr.preferences["education"] == [
        {
            "school": "Riverside Technical University",
            "degree": "Master of Science (MSc), Computer Science",
            "period": "2010 - 2015",
        }
    ]
    assert pr.preferences["languages"] == [
        {"name": "English", "proficiency": "Full professional proficiency"},
        {"name": "Ukrainian", "proficiency": "Native or bilingual proficiency"},
    ]
    assert pr.raw_payload is not None and pr.raw_payload["sections"] == [
        "about",
        "skills",
        "experience",
        "education",
        "skills",
        "languages",
        "interests",
    ]


def test_parse_profile_text_falls_back_to_generic_layout(connector: Connector) -> None:
    text = "Senior Data Engineer\n\nAbout\nI build things.\n\nSkills\nPython, SQL\n"
    pr = connector.parse_profile_text(text)
    assert pr.headline == "Senior Data Engineer" and pr.about == "I build things."
    assert pr.skills == ["Python", "SQL"] and pr.raw_text == text


# --------------------------------------------------------------------------- paste: job search list


def test_parse_jobs_search_list(connector: Connector) -> None:
    text = _read("jobs_search.txt")
    jobs = connector.parse_jobs_text(text)
    assert [(j.title, j.company, j.location) for j in jobs] == [
        ("Staff Data Engineer", "Northwind Commerce", "Tbilisi, Georgia (Remote)"),
        ("Analytics Engineering Lead", "Lumen Analytics", "Warsaw, Poland (Hybrid)"),
        ("Principal Data Engineer", "Orbit Fintech", "Remote"),
    ]
    assert all(j.platform == Platform.linkedin and j.url is None for j in jobs)
    first = jobs[0]
    assert first.raw_text.startswith("Staff Data Engineer\nNorthwind Commerce\nTbilisi, Georgia")
    assert "$120K/yr - $150K/yr" in first.raw_text and "with verification" not in first.raw_text
    assert first.raw_payload is not None
    assert first.raw_payload["easy_apply"] is True and first.raw_payload["promoted"] is True
    assert first.raw_payload["applicants"] == 12 and first.raw_payload["posted"] is None
    assert jobs[1].raw_payload is not None and jobs[1].raw_payload["easy_apply"] is False
    assert jobs[1].raw_payload["posted"] == "2 days ago · Actively hiring"
    dated = li.parse_jobs(text, now=NOW)
    assert dated[0].posted_at is None
    assert dated[1].posted_at == NOW.replace(day=23) and dated[2].posted_at == NOW.replace(day=18)


def test_parse_jobs_text_blank_separated_blocks_fall_back_to_generic(
    connector: Connector,
) -> None:
    text = (
        "Data Engineer at Northwind Commerce\n"
        "https://www.linkedin.com/jobs/view/4555555555/\n"
        "\n"
        "Analytics Engineer — Lumen Analytics\n"
        "Warsaw, Poland (Hybrid)\n"
        "Promoted\n"
    )
    jobs = connector.parse_jobs_text(text)
    assert [(j.title, j.company) for j in jobs] == [
        ("Data Engineer", "Northwind Commerce"),
        ("Analytics Engineer", "Lumen Analytics"),
    ]
    assert jobs[0].url == "https://www.linkedin.com/jobs/view/4555555555/"
    assert jobs[1].location == "Warsaw, Poland (Hybrid)"


# --------------------------------------------------------------------------- paste: applied list


def test_parse_applied_list(connector: Connector) -> None:
    text = _read("applied_jobs.txt")
    obs = li.parse_applications(text, now=NOW)
    assert [(o.job_title, o.company) for o in obs] == [
        ("Staff Data Engineer", "Northwind Commerce"),
        ("Analytics Engineering Lead", "Lumen Analytics"),
        ("Principal Data Engineer", "Orbit Fintech"),
        ("Data Platform Lead", "Northwind Commerce"),
    ]
    assert [o.status for o in obs] == [
        ApplicationStatus.viewed,
        ApplicationStatus.viewed,
        ApplicationStatus.applied,
        ApplicationStatus.applied,
    ]
    assert [o.status_raw for o in obs] == [
        "Application viewed",
        "Resume downloaded",
        "Applied 1mo ago",
        "Applied 5h ago",
    ]
    assert obs[0].applied_at == NOW.replace(day=22)
    assert obs[1].applied_at == NOW.replace(day=11)
    assert obs[2].applied_at == NOW - timedelta(days=30)
    assert obs[3].applied_at == NOW.replace(hour=7)
    assert all(o.platform == Platform.linkedin and o.external_id is None for o in obs)
    payloads = [o.raw_payload for o in obs]
    assert all(p is not None for p in payloads)
    closed = obs[2].raw_payload
    assert closed is not None and closed["posting_closed"] is True
    assert closed["notes"] == ["No longer accepting applications"]
    first = obs[0].raw_payload
    assert first is not None and first["posting_closed"] is False and first["notes"] == []
    assert (
        first["location"] == "Tbilisi, Georgia (Remote)"
        and first["lines"][0] == "Staff Data Engineer"
    )
    last = obs[3].raw_payload
    assert last is not None and last["location"] is None
    assert connector.parse_applications_text(text)[0].job_title == "Staff Data Engineer"


def test_parse_applications_text_falls_back_to_generic(connector: Connector) -> None:
    text = (
        "Data Engineer at Northwind Commerce\n"
        "Applied on Aug 12, 2026\n"
        "Application viewed\n"
        "\n"
        "Analytics Engineer — Lumen Analytics\n"
        "Interview scheduled\n"
    )
    obs = li.parse_applications(text, now=NOW)
    assert [(o.job_title, o.company, o.status) for o in obs] == [
        ("Data Engineer", "Northwind Commerce", ApplicationStatus.viewed),
        ("Analytics Engineer", "Lumen Analytics", ApplicationStatus.interview),
    ]
    assert obs[0].applied_at == datetime(2026, 8, 12, tzinfo=UTC)
    assert obs[0].status_raw == "Application viewed"
    assert connector.parse_applications_text(text)[1].status == ApplicationStatus.interview


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("3d ago", "3 days ago"),
        ("2w ago", "2 weeks ago"),
        ("1mo ago", "1 month ago"),
        ("5h ago", "5 hours ago"),
        ("1yr ago", "1 year ago"),
        ("10m ago", "10 minutes ago"),
        ("Applied 3 days ago", "Applied 3 days ago"),
        ("Jan 2023 - Present", "Jan 2023 - Present"),
    ],
)
def test_expand_relative(raw: str, expected: str) -> None:
    assert li.expand_relative(raw) == expected
