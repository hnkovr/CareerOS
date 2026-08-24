"""Indeed paste parsers (ADR-005: the site is never fetched; the user copies page text).

Layout assumptions — "select all → copy" of the rendered page, blank lines separate cards/rows:

* Profile / Resume page: ``Name / Headline / Location / contacts`` preamble, then sections headed
  ``Summary``, ``Work experience``, ``Education``, ``Skills``, ``Certifications and licenses``,
  ``Assessments``, ``Links`` … Work-experience entries are ``Title / Company - Location /
  <Month YYYY> to <Month YYYY|Present> / description lines``. Skills are one per line or
  comma-separated, optionally suffixed with ``(N years)``.
* Search results: one card per block — ``Title / Company / [rating] / Location / [pay] /
  [job type] / [badges] / snippet / Posted N days ago``. A block counts as a card only when it
  carries at least one Indeed signal (pay, job type, posted line or badge).
* "My jobs → Applied": one row per block — ``Title / Company / Location / Applied on <date> |
  Applied N days ago / status chip / notes``. A block counts as a row only when it carries an
  ``Applied …`` line or a known status chip.

Unknown shapes fall back to the shared generic parsers. Values are never invented: unknown →
``None``; every DTO keeps the verbatim block in ``raw_text`` / ``raw_payload["lines"]``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from careeros.modules.opportunities.enums import CompensationPeriod, EmploymentType, RemotePolicy
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform import parsers as shared
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.indeed

# ------------------------------------------------------------------------------ profile

_SECTION_ALIASES: dict[str, str] = {
    "summary": "summary",
    "professional summary": "summary",
    "about": "summary",
    "work experience": "experience",
    "experience": "experience",
    "employment": "experience",
    "education": "education",
    "skills": "skills",
    "certifications": "certifications",
    "certifications and licenses": "certifications",
    "certifications & licenses": "certifications",
    "licenses and certifications": "certifications",
    "licenses & certifications": "certifications",
    "assessments": "assessments",
    "links": "links",
    "additional information": "additional_information",
    "languages": "languages",
    "awards": "awards",
    "groups": "groups",
    "publications": "publications",
    "patents": "patents",
    "military service": "military_service",
    "volunteering": "volunteering",
    "volunteer work": "volunteering",
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"^\+?[\d\s().\-/]{7,}$")
_RELOCATE = re.compile(r"^willing to relocate(?:\s+to)?\s*:\s*(?P<val>.+)$", re.IGNORECASE)
_AUTHORIZED = re.compile(r"^authorized to work\b.*$", re.IGNORECASE)
_JOB_WORDS = re.compile(
    r"engineer|developer|manager|lead|analyst|architect|scientist|head|director|consultant|"
    r"specialist|designer|administrator|devops|sre|cto|ceo|founder|intern|freelanc|officer",
    re.IGNORECASE,
)
_PERIOD = re.compile(
    r"^(?:[A-Za-z]+\s+)?\d{4}\s+(?:to|-|–|—)\s+(?:present|current|(?:[A-Za-z]+\s+)?\d{4})$",
    re.IGNORECASE,
)
_PERIOD_SEP = re.compile(r"\s+(?:to|-|—)\s+", re.IGNORECASE)
_COMPANY_SEP = re.compile(r"\s+[-–—]\s+")
_YEARS = re.compile(
    r"\s*\((?:[^()]*\b(?:years?|yrs?)\b[^()]*|less than [^()]*)\)\s*$", re.IGNORECASE
)
_SKILL_SEP = re.compile(r"[,;·•|]")


def _section(line: str) -> str | None:
    return _SECTION_ALIASES.get(line.rstrip(":").strip().lower())


def _split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    """Preamble (before the first header) + canonical section → its lines (repeats merge)."""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        name = _section(line)
        if name is not None:
            current = name
            sections.setdefault(name, [])
            continue
        if current is None:
            preamble.append(line)
        else:
            sections[current].append(line)
    return preamble, sections


def _is_contact(line: str) -> bool:
    return bool(_EMAIL.search(line) or _PHONE.match(line) or shared.find_urls(line))


def _looks_like_location(line: str) -> bool:
    if len(line) > 50 or re.search(r"[|·•@/\d]", line) or _JOB_WORDS.search(line):
        return False
    parts = [p.strip() for p in line.split(",")]
    return 1 <= len(parts) <= 3 and all(0 < len(p.split()) <= 3 for p in parts)


def _preamble(lines: list[str]) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    """→ (name, headline, location, extras{contacts, willing_to_relocate, work_authorization})."""
    extras: dict[str, Any] = {}
    contacts: list[str] = []
    candidates: list[str] = []
    name = lines[0] if lines else None
    for line in lines[1:]:
        if m := _RELOCATE.match(line):
            extras["willing_to_relocate"] = m.group("val").strip()
        elif _AUTHORIZED.match(line):
            extras["work_authorization"] = line
        elif _is_contact(line):
            contacts.append(line)
        else:
            candidates.append(line)
    if contacts:
        extras["contacts"] = contacts
    headline: str | None = None
    location: str | None = None
    if len(candidates) >= 2:
        headline = candidates[0]
        location = candidates[1] if _looks_like_location(candidates[1]) else None
    elif len(candidates) == 1:
        only = candidates[0]
        if "," in only and _looks_like_location(only):
            location = only
        else:
            headline = only
    return name, headline, location, extras


def _is_period(line: str) -> bool:
    return bool(_PERIOD.match(line))


def _dash_period(line: str) -> str:
    return _PERIOD_SEP.sub(" – ", line)


def _split_company(line: str) -> tuple[str, str | None]:
    parts = _COMPANY_SEP.split(line, maxsplit=1)
    return parts[0].strip(), (parts[1].strip() or None) if len(parts) == 2 else None


def _experience(lines: list[str]) -> tuple[list[SnapshotExperienceItem], list[dict[str, Any]]]:
    """``Title / Company - Location / Period / description…`` entries (title may be absent)."""
    items: list[SnapshotExperienceItem] = []
    extras: list[dict[str, Any]] = []
    desc: list[str] = []

    def flush() -> None:
        if items and desc:
            items[-1].description = "\n".join(desc)
        desc.clear()

    def start(title: str | None, company_line: str, period: str) -> None:
        flush()
        company, location = _split_company(company_line)
        items.append(
            SnapshotExperienceItem(company=company, title=title, period=_dash_period(period))
        )
        extras.append(
            {
                "title": title,
                "company": company,
                "location": location,
                "period": _dash_period(period),
            }
        )

    i, n = 0, len(lines)
    while i < n:
        if i + 2 < n and _is_period(lines[i + 2]) and not _is_period(lines[i + 1]):
            start(lines[i], lines[i + 1], lines[i + 2])
            i += 3
        elif i + 1 < n and _is_period(lines[i + 1]):
            start(None, lines[i], lines[i + 1])
            i += 2
        else:
            if items:
                desc.append(lines[i])
            i += 1
    flush()
    return items, extras


def strip_years(skill: str) -> str:
    """'Python (10+ years)' → 'Python'; 'Terraform (Less than 1 year)' → 'Terraform'."""
    return _YEARS.sub("", skill).strip()


def _skills(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        parts = shared.split_skills(line) if _SKILL_SEP.search(line) else [line]
        for part in parts:
            skill = strip_years(part)
            if skill and skill.lower() not in seen and len(skill) <= 60:
                seen.add(skill.lower())
                out.append(skill)
    return out


def parse_profile(text: str) -> ProfileRead:
    """Indeed Profile / Resume page text → ``ProfileRead`` (generic parser for unknown shapes)."""
    lines = shared.split_lines(text)
    preamble, sections = _split_sections(lines)
    if not sections:
        return shared.generic_profile(text, PLATFORM)
    name, headline, location, extras = _preamble(preamble)
    experience, experience_extras = _experience(sections.get("experience", []))
    preferences: dict[str, Any] = {}
    if location:
        preferences["location"] = location
    for key in ("willing_to_relocate", "work_authorization"):
        if key in extras:
            preferences[key] = extras[key]
    payload: dict[str, Any] = {"name": name}
    if "contacts" in extras:
        payload["contacts"] = extras["contacts"]
    if experience_extras:
        payload["experience"] = experience_extras
    for key, section_lines in sections.items():
        if key not in {"summary", "experience", "skills"} and section_lines:
            payload[key] = list(section_lines)
    return ProfileRead(
        platform=PLATFORM,
        capture_method=CaptureMethod.paste,
        headline=headline,
        about="\n".join(sections.get("summary", [])) or None,
        experience=experience,
        skills=_skills(sections.get("skills", [])),
        preferences=preferences,
        raw_text=text,
        raw_payload=payload,
    )


# ------------------------------------------------------------------------------ jobs

_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}
_PAY_PERIOD: dict[str, CompensationPeriod | None] = {
    "year": CompensationPeriod.year,
    "month": CompensationPeriod.month,
    "day": CompensationPeriod.day,
    "hour": CompensationPeriod.hour,
    "week": None,
}
_PAY = re.compile(
    r"^(?P<prefix>from|up to|estimated|est\.)?\s*"
    r"(?P<cur>[$€£])\s?(?P<a>\d[\d,]*(?:\.\d+)?)\s?(?P<ka>k)?"
    r"(?:\s*(?:-|–|—|to)\s*[$€£]?\s?(?P<b>\d[\d,]*(?:\.\d+)?)\s?(?P<kb>k)?)?"
    r"\s+(?:a|an|per)\s+(?P<per>year|month|week|day|hour)$",
    re.IGNORECASE,
)
_JOB_TYPE = re.compile(
    r"^(?P<t>full-time|part-time|contract|temporary|internship|permanent|freelance|seasonal|"
    r"apprenticeship|fixed term|per diem|temp-to-hire)(?:\s*[,+/].*)?$",
    re.IGNORECASE,
)
_EMPLOYMENT: dict[str, EmploymentType] = {
    "full-time": EmploymentType.full_time,
    "part-time": EmploymentType.part_time,
}
_POSTED = re.compile(r"^(?:posted\s*)?(?:posted|just posted|active|employer active)\b", re.I)
_BADGE = re.compile(
    r"^(?:easily apply|hiring multiple candidates|urgently hiring|responsive employer|"
    r"typically responds within .+|hiring ongoing|new|sponsored|encouraged to apply.*|"
    r"fair chance.*)$",
    re.IGNORECASE,
)
_WORKPLACE = re.compile(
    r"^(?:remote|hybrid work|hybrid remote|temporarily remote|in person|on-site)$", re.IGNORECASE
)
_JOB_NOISE = re.compile(
    r"^(?:apply|apply now|apply on company site|easy apply|save|saved|save this job|hide job|"
    r"report job|share|promoted|\d[\d,]* jobs|\d(?:\.\d)?|previous|next|page \d+.*|"
    r"sort by:.*|view all .*|salary search:.*|see popular questions.*|job post details|"
    r"\d+ applicants?)$",
    re.IGNORECASE,
)
_JOB_TITLE_SUFFIX = re.compile(r"\s+-\s+job post$", re.IGNORECASE)
_RATING_SUFFIX = re.compile(r"\s+\d\.\d$")
_SENTENCE_END = re.compile(r"[.!?;]$|^[•…-]")


def _amount(raw: str, thousands: str | None) -> float:
    return float(raw.replace(",", "")) * (1000 if thousands else 1)


def parse_pay(line: str) -> Compensation | None:
    """'$120,000 - $150,000 a year' / '$60 - $80 an hour' / 'From $90,000 a year' → Compensation."""
    m = _PAY.match(line.strip())
    if m is None:
        return None
    prefix = (m.group("prefix") or "").lower()
    a = _amount(m.group("a"), m.group("ka"))
    b = _amount(m.group("b"), m.group("kb")) if m.group("b") else None
    if b is not None:
        lo, hi = a, b
    elif prefix == "from":
        lo, hi = a, None
    elif prefix == "up to":
        lo, hi = None, a
    else:
        lo = hi = a
    per = m.group("per").lower()
    return Compensation(
        min=lo,
        max=hi,
        currency=_CURRENCY[m.group("cur")],
        period=_PAY_PERIOD[per],
        type="rate" if per in {"hour", "day"} else "salary",
        raw=line.strip(),
    )


def _posted_at(line: str, now: datetime) -> datetime | None:
    low = line.lower()
    if "+" in low:  # "Posted 30+ days ago" is a floor, not a date
        return None
    if "just posted" in low or "today" in low:
        return now
    return shared.parse_date(line, now=now)


def _remote_policy(*texts: str | None) -> RemotePolicy:
    joined = " ".join(t for t in texts if t).lower()
    if "hybrid" in joined:
        return RemotePolicy.hybrid
    if re.search(r"\bremote\b", joined):
        return RemotePolicy.remote_global
    return RemotePolicy.unknown


def _short_plain(line: str) -> bool:
    return len(line) <= 60 and not _SENTENCE_END.search(line)


def _job_card(block: list[str], now: datetime) -> JobPosting | None:
    lines = [ln for ln in block if not _JOB_NOISE.match(ln)]
    if len(lines) < 2:
        return None
    title = _JOB_TITLE_SUFFIX.sub("", lines[0])
    company: str | None = None
    location: str | None = None
    workplace: str | None = None
    compensation: Compensation | None = None
    employment_type: EmploymentType | None = None
    job_type: str | None = None
    posted: str | None = None
    badges: list[str] = []
    snippet: list[str] = []
    signal = False
    for i, ln in enumerate(lines[1:], start=1):
        if compensation is None and (pay := parse_pay(ln)) is not None:
            compensation, signal = pay, True
        elif job_type is None and (m := _JOB_TYPE.match(ln)):
            job_type, signal = ln, True
            employment_type = _EMPLOYMENT.get(m.group("t").lower())
        elif posted is None and _POSTED.match(ln):
            posted, signal = ln, True
        elif _BADGE.match(ln):
            badges.append(ln)
            signal = True
        elif _WORKPLACE.match(ln):
            workplace = ln
            if location is None:
                location = ln
        elif company is None and i == 1:
            company = _RATING_SUFFIX.sub("", ln)
        elif location is None and not snippet and _short_plain(ln):
            location = ln
        else:
            snippet.append(ln)
    if not signal:
        return None
    urls = shared.find_urls("\n".join(block))
    extraction = OpportunityExtraction(
        title=title,
        company=company,
        location=location,
        remote_policy=_remote_policy(location, workplace),
        employment_type=employment_type,
        compensation=compensation,
        summary=" ".join(snippet) or None,
    )
    return JobPosting(
        platform=PLATFORM,
        title=title[:300],
        company=company,
        location=location,
        posted_at=_posted_at(posted, now) if posted else None,
        url=urls[0] if urls else None,
        raw_text="\n".join(block),
        extraction=extraction,
        raw_payload={"badges": badges, "posted": posted, "job_type": job_type},
    )


def parse_jobs(text: str, *, now: datetime | None = None, limit: int = 100) -> list[JobPosting]:
    """Indeed search-results page text → one ``JobPosting`` per card (generic fallback)."""
    now = now or datetime.now(UTC)
    out: list[JobPosting] = []
    for block in shared.blocks(text):
        card = _job_card(block, now)
        if card is not None:
            out.append(card)
            if len(out) >= limit:
                break
    return out or shared.generic_jobs(text, PLATFORM, limit=limit)


# ------------------------------------------------------------------------------ applications

STATUS_MAP: dict[str, ApplicationStatus] = {
    "applied": ApplicationStatus.applied,
    "application submitted": ApplicationStatus.applied,
    "submitted": ApplicationStatus.applied,
    "viewed by employer": ApplicationStatus.viewed,
    "interviewing": ApplicationStatus.interview,
    "interview scheduled": ApplicationStatus.interview,
    "not selected by employer": ApplicationStatus.rejected,
    "not selected": ApplicationStatus.rejected,
    "hired": ApplicationStatus.offer,
    "offer received": ApplicationStatus.offer,
    "application withdrawn": ApplicationStatus.withdrawn,
    "withdrawn": ApplicationStatus.withdrawn,
}
_APP_NOISE = re.compile(
    r"^(?:update status|archive|not interested|view job|job details|report job|see similar jobs|"
    r"withdraw application|save|share|more actions|\.\.\.|…)$",
    re.IGNORECASE,
)
_TAB = re.compile(r"^(?:my jobs|saved|applied|interviews|archived)(?:\s+\d+)?$", re.IGNORECASE)
_APPLIED_LINE = re.compile(r"^applied\b", re.IGNORECASE)
_APP_NOTE = re.compile(
    r"^(?:job expired|this job has expired.*|job closed|job is no longer available.*|"
    r"no longer accepting applications.*|applied on indeed|applied on (?:company|employer) site)$",
    re.IGNORECASE,
)


def map_status(line: str) -> ApplicationStatus | None:
    """Exact Indeed status chip → normalized status; ``None`` when the line is not a chip."""
    return STATUS_MAP.get(re.sub(r"[.!:]+$", "", line.strip()).lower())


def _application_row(block: list[str], now: datetime) -> ApplicationObservationIn | None:
    lines = [ln for ln in block if not _APP_NOISE.match(ln)]
    while lines and _TAB.match(lines[0]):
        lines.pop(0)
    if len(lines) < 2:
        return None
    title = lines[0]
    company: str | None = None
    location: str | None = None
    applied_at: datetime | None = None
    status: ApplicationStatus | None = None
    status_raw = ""
    notes: list[str] = []
    signal = False
    for i, ln in enumerate(lines[1:], start=1):
        if _APP_NOTE.match(ln):
            notes.append(ln)
            signal = True
        elif _APPLIED_LINE.match(ln) and (d := shared.parse_date(ln, now=now)) is not None:
            signal = True
            if applied_at is None:
                applied_at = d
        elif (st := map_status(ln)) is not None:
            signal = True
            if status is None:
                status, status_raw = st, ln
        elif company is None and i == 1:
            company = ln
        elif location is None and i == 2 and _short_plain(ln):
            location = ln
    if not signal:
        return None
    if status is None:
        status, status_raw = shared.best_status([ln for ln in lines[1:] if not _APP_NOTE.match(ln)])
    urls = shared.find_urls("\n".join(block))
    return ApplicationObservationIn(
        platform=PLATFORM,
        job_title=title[:300],
        company=company,
        job_url=urls[0] if urls else None,
        status_raw=status_raw,
        status=status,
        applied_at=applied_at,
        raw_payload={"lines": block, "location": location, "notes": notes},
    )


def parse_applications(text: str, *, now: datetime | None = None) -> list[ApplicationObservationIn]:
    """'My jobs → Applied' page text → one observation per row (generic fallback)."""
    now = now or datetime.now(UTC)
    out: list[ApplicationObservationIn] = []
    for block in shared.blocks(text):
        row = _application_row(block, now)
        if row is not None:
            out.append(row)
    return out or shared.generic_applications(text, PLATFORM, now=now)
