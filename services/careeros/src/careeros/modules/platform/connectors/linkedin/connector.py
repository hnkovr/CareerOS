"""LinkedIn: 'Download your data' archive importer + page-paste parsers. No API, no fetching.

ADR-005: only files the member downloaded themselves and text they copied are read. The archive
(Settings & Privacy → Data privacy → Get a copy of your data) arrives by e-mail as a ``.zip``;
``import_*_export`` accept that archive or the directory it was unpacked into.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from careeros.modules.platform import parsers as generic
from careeros.modules.platform.base import BaseConnector, ParseError
from careeros.modules.platform.connectors.linkedin import export as ex
from careeros.modules.platform.connectors.linkedin import parsers as li
from careeros.modules.platform.enums import ApplicationStatus, AuthKind, SyncMethod
from careeros.modules.platform.schemas import (
    ApplicationObservationIn,
    Capabilities,
    JobPosting,
    ProfileRead,
)
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

#: Profile.csv columns that never leave the file (not needed for positioning; sensitive).
PROFILE_PII_COLUMNS: frozenset[str] = frozenset(
    {"Address", "Birth Date", "Zip Code", "Instant Messengers"}
)
#: Job Applications.csv columns kept in ``raw_payload`` (contact e-mail / phone are dropped).
APPLICATION_COLUMNS: tuple[str, ...] = (
    "Application Date",
    "Company Name",
    "Job Title",
    "Job Url",
    "Resume Name",
    "Question And Answers",
)
_LI_DATETIME_FORMATS = ("%m/%d/%y, %I:%M %p", "%m/%d/%Y, %I:%M %p", "%m/%d/%y", "%m/%d/%Y")


class Connector(BaseConnector):
    platform = Platform.linkedin
    capabilities = Capabilities(
        platform=Platform.linkedin,
        profile=[SyncMethod.export, SyncMethod.paste],
        jobs=[SyncMethod.export, SyncMethod.paste],
        applications=[SyncMethod.export, SyncMethod.paste],
        official_api=False,
        email_fallback=True,
        auth=AuthKind.none,
        notes=(
            "No job/search API for normal apps; use the 'Download your data' archive "
            "(Settings → Data privacy → Get a copy of your data) or paste."
        ),
    )

    # ---- export tier -----------------------------------------------------------------------
    def import_profile_export(self, path: Path) -> ProfileRead:
        return _profile_from_export(ex.open_export(path))

    def import_jobs_export(self, path: Path) -> list[JobPosting]:
        """``Saved Jobs.csv`` → postings; the archive has no job search, only your saved jobs."""
        return _saved_jobs_from_export(ex.open_export(path))

    def import_applications_export(self, path: Path) -> list[ApplicationObservationIn]:
        return _applications_from_export(ex.open_export(path))

    # ---- paste tier ------------------------------------------------------------------------
    def parse_profile_text(self, text: str) -> ProfileRead:
        return li.parse_profile(text)

    def parse_jobs_text(self, text: str) -> list[JobPosting]:
        return li.parse_jobs(text)

    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]:
        return li.parse_applications(text)


# ------------------------------------------------------------------------------ export mapping


def _value(row: dict[str, str], key: str) -> str | None:
    v = row.get(key, "").strip()
    return v or None


def _range(start: str | None, end: str | None, *, ongoing: str = "now") -> str | None:
    """'Jan 2023' + '' → 'Jan 2023 – now'; both empty → None (nothing is invented)."""
    if start and end:
        return f"{start} – {end}"
    if start:
        return f"{start} – {ongoing}"
    return end


def _li_datetime(raw: str | None) -> datetime | None:
    """LinkedIn's '8/12/26, 10:15 AM' (kept to the minute, treated as UTC); parse_date fallback."""
    if not raw:
        return None
    for fmt in _LI_DATETIME_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return generic.parse_date(raw)


def _profile_from_export(reader: ex.ExportReader) -> ProfileRead:
    profiles = reader.rows(ex.PROFILE, required=True)
    if not profiles:
        raise ParseError(f"{ex.PROFILE} in {reader.path} has no data rows")
    row = profiles[0]
    positions = reader.rows(ex.POSITIONS)
    skills_rows = reader.rows(ex.SKILLS)
    education = reader.rows(ex.EDUCATION)
    certifications = reader.rows(ex.CERTIFICATIONS)
    projects = reader.rows(ex.PROJECTS)
    languages = reader.rows(ex.LANGUAGES)

    experience = [
        SnapshotExperienceItem(
            company=company,
            title=_value(p, "Title"),
            period=_range(_value(p, "Started On"), _value(p, "Finished On")),
            description=_value(p, "Description"),
        )
        for p in positions
        if (company := _value(p, "Company Name"))
    ]
    skills: list[str] = []
    for s in skills_rows:
        name = _value(s, "Name")
        if name and name.lower() not in {k.lower() for k in skills}:
            skills.append(name)
    name = " ".join(part for part in (_value(row, "First Name"), _value(row, "Last Name")) if part)
    websites = generic.find_urls(row.get("Websites", ""))
    preferences: dict[str, Any] = {
        "name": name or None,
        "industry": _value(row, "Industry"),
        "location": _value(row, "Geo Location"),
        "websites": websites,
        "languages": [
            {"name": lang, "proficiency": _value(lg, "Proficiency")}
            for lg in languages
            if (lang := _value(lg, "Name"))
        ],
        "certifications": [
            {
                "name": cert,
                "authority": _value(c, "Authority"),
                "url": _value(c, "Url"),
                "issued": _value(c, "Started On"),
                "expires": _value(c, "Finished On"),
                "license_number": _value(c, "License Number"),
            }
            for c in certifications
            if (cert := _value(c, "Name"))
        ],
        "education": [
            {
                "school": school,
                "degree": _value(e, "Degree Name"),
                "period": _range(_value(e, "Start Date"), _value(e, "End Date")),
                "notes": _value(e, "Notes"),
                "activities": _value(e, "Activities"),
            }
            for e in education
            if (school := _value(e, "School Name"))
        ],
    }
    return ProfileRead(
        platform=Platform.linkedin,
        capture_method=CaptureMethod.export,
        headline=_value(row, "Headline"),
        about=_value(row, "Summary"),
        experience=experience,
        skills=skills,
        projects=[
            {
                "name": title,
                "description": _value(p, "Description"),
                "url": _value(p, "Url"),
                "period": _range(_value(p, "Started On"), _value(p, "Finished On")),
            }
            for p in projects
            if (title := _value(p, "Title"))
        ],
        preferences=preferences,
        raw_payload={
            "profile": {k: v for k, v in row.items() if k not in PROFILE_PII_COLUMNS},
            "counts": {
                "positions": len(positions),
                "skills": len(skills_rows),
                "education": len(education),
                "certifications": len(certifications),
                "projects": len(projects),
                "languages": len(languages),
            },
            "files": reader.files(),
            "rows": {
                "positions": positions,
                "education": education,
                "certifications": certifications,
                "projects": projects,
                "languages": languages,
            },
        },
    )


def _applications_from_export(reader: ex.ExportReader) -> list[ApplicationObservationIn]:
    out: list[ApplicationObservationIn] = []
    for row in reader.rows(ex.JOB_APPLICATIONS, required=True):
        url = _value(row, "Job Url")
        title = _value(row, "Job Title") or url
        if not title:
            continue
        payload: dict[str, Any] = {k: row.get(k, "") for k in APPLICATION_COLUMNS}
        payload["source_file"] = ex.JOB_APPLICATIONS
        out.append(
            ApplicationObservationIn(
                platform=Platform.linkedin,
                external_id=url,
                job_title=title[:300],
                company=_value(row, "Company Name"),
                job_url=url,
                status_raw="applied",
                status=ApplicationStatus.applied,
                applied_at=_li_datetime(_value(row, "Application Date")),
                raw_payload=payload,
            )
        )
    return out


def _saved_jobs_from_export(reader: ex.ExportReader) -> list[JobPosting]:
    out: list[JobPosting] = []
    for row in reader.rows(ex.SAVED_JOBS, required=True):
        url = _value(row, "Job Url")
        title = _value(row, "Job Title") or url
        if not title:
            continue
        company = _value(row, "Company Name")
        saved = _value(row, "Saved Date")
        head = f"{title} at {company}" if company else title
        raw_lines = [head, url or "", f"Saved on {saved}" if saved else ""]
        out.append(
            JobPosting(
                platform=Platform.linkedin,
                external_id=url,
                url=url,
                title=title[:300],
                company=company,
                posted_at=_li_datetime(saved),
                raw_text="\n".join(ln for ln in raw_lines if ln),
                raw_payload={**row, "source_file": ex.SAVED_JOBS},
            )
        )
    return out
