"""Upwork page-text parsers: profile page, "Find Work" feed, "My Proposals" page.

Heuristics on top of the shared ``careeros.modules.platform.parsers`` helpers. Nothing is
invented: unknown → ``None``, the pasted text is kept verbatim, and text in an unrecognised
shape falls back to the generic parsers.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
    Seniority,
)
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform import parsers as shared
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.upwork

# ------------------------------------------------------------------------------ profile page

_RATE = re.compile(r"\$\s?(?P<v>\d[\d,]*(?:\.\d+)?)\s*/\s*hr\b", re.IGNORECASE)
_BADGE = re.compile(
    r"^(top rated(?: plus)?|rising talent|expert[- ]vetted|\d+%\s*job success.*|job success.*)$",
    re.IGNORECASE,
)
_LOCAL_TIME = re.compile(r"local time|\b\d{1,2}:\d{2}\s*(?:am|pm)\b", re.IGNORECASE)
_AVAIL = re.compile(
    r"(hrs?\s*/\s*week|hours per week|hours/week|available now|as needed|"
    r"open to (?:offers|contract to hire)|not available)",
    re.IGNORECASE,
)
_PROFILE_HEADER = re.compile(
    r"^(?P<name>overview|about|profile overview|skills|portfolio|work history|"
    r"employment history|education|languages|availability|hours per week|certifications|"
    r"licenses|testimonials|project catalog|consultations|categories|linked accounts|"
    r"other experiences|video introduction|verifications|associated with|agencies|"
    r"specialized profiles|browse similar freelancers|reviews|projects)\b"
    r"\s*(?:\(\d+\))?\s*(?::\s*(?P<inline>.*))?$",
    re.IGNORECASE,
)
_SECTION_OF_HEADER = {
    "overview": "about",
    "about": "about",
    "profile overview": "about",
    "skills": "skills",
    "portfolio": "portfolio",
    "work history": "work_history",
    "employment history": "experience",
    "availability": "availability",
    "hours per week": "availability",
}
_NOISE = re.compile(
    r"^(see more|show more|see all|show all|view more|read more|more|less)\b.*$|^\d+$",
    re.IGNORECASE,
)
_RATING = re.compile(r"^(rating is|\d(?:\.\d+)?\s*(?:of|out of)\s*5|★|\d+ stars?)", re.IGNORECASE)
_QUOTE = re.compile(r"^[\"“„«']")


def _headline(lines: list[str], rate_idx: int | None) -> tuple[str | None, int]:
    """Upwork renders the title right above ``$NN.NN/hr``; without a rate, right above the first
    section header. Badges / local-time lines in between are skipped."""
    if rate_idx is not None:
        stop = rate_idx
    else:
        stop = next((i for i, ln in enumerate(lines) if _PROFILE_HEADER.match(ln)), len(lines))
    for i in range(stop - 1, -1, -1):
        ln = lines[i]
        if _BADGE.match(ln) or _LOCAL_TIME.search(ln) or _AVAIL.search(ln) or _NOISE.match(ln):
            continue
        return ln[:300], i
    return (lines[0][:300], 0) if lines else (None, -1)


def parse_profile(text: str) -> ProfileRead:
    """Profile page → ``ProfileRead``; text without a rate or Upwork section headers → generic."""
    lines = shared.split_lines(text)
    rate_idx = next((i for i, ln in enumerate(lines) if _RATE.search(ln)), None)
    if rate_idx is None and not any(_PROFILE_HEADER.match(ln) for ln in lines):
        return shared.generic_profile(text, PLATFORM)

    rates: dict[str, Any] | None = None
    if rate_idx is not None:
        m = _RATE.search(lines[rate_idx])
        assert m is not None
        rates = {
            "hourly": float(m.group("v").replace(",", "")),
            "currency": "USD",
            "raw": m.group(0),
        }
    headline, headline_idx = _headline(lines, rate_idx)
    start = (rate_idx if rate_idx is not None else headline_idx) + 1

    about: list[str] = []
    skills: list[str] = []
    portfolio: list[dict[str, Any]] = []
    projects: list[dict[str, Any]] = []
    experience: list[SnapshotExperienceItem] = []
    availability: list[str] = []
    section = "top"

    def feed(target: str, line: str) -> None:
        if target == "about" or (target == "top" and rate_idx is not None):
            about.append(line)
        elif target == "skills":
            known = {s.lower() for s in skills}
            skills.extend(s for s in shared.split_skills(line) if s.lower() not in known)
        elif target == "portfolio":
            if not (shared.looks_like_period(line) or _RATING.match(line) or _QUOTE.match(line)):
                portfolio.append({"name": line})
        elif target == "work_history":
            if shared.looks_like_period(line):
                if projects and "period" not in projects[-1]:
                    projects[-1]["period"] = line
            elif not (_RATING.match(line) or _QUOTE.match(line)):
                projects.append({"name": line})
        elif target == "experience":
            title, company = shared.guess_title_company(line)
            if company and not shared.looks_like_period(line):
                experience.append(SnapshotExperienceItem(company=company, title=title))
            elif experience and experience[-1].period is None and shared.looks_like_period(line):
                experience[-1].period = line
            elif experience and experience[-1].description is None:
                experience[-1].description = line
        elif target == "availability":
            availability.append(line)

    for line in lines[start:]:
        m = _PROFILE_HEADER.match(line)
        if m:
            section = _SECTION_OF_HEADER.get(m.group("name").lower(), "other")
            inline = (m.group("inline") or "").strip()
            if inline:
                feed(section, inline)
            continue
        if _AVAIL.search(line):
            if line not in availability:
                availability.append(line)
            continue
        if _NOISE.match(line) or _BADGE.match(line) or _LOCAL_TIME.search(line):
            continue
        feed(section, line)

    return ProfileRead(
        platform=PLATFORM,
        headline=headline,
        about=" ".join(about) or None,
        skills=skills,
        experience=experience,
        projects=projects,
        portfolio=portfolio,
        rates=rates,
        availability=" · ".join(availability)[:300] or None,
        raw_text=text,
    )


# ------------------------------------------------------------------------------ "Find Work" feed

_POSTED = re.compile(r"^posted\b", re.IGNORECASE)
_META = re.compile(r"^(?P<kind>hourly|fixed[- ]price)\b", re.IGNORECASE)
_MONEY = re.compile(r"\$\s?(?P<v>\d[\d,]*(?:\.\d+)?)")
_RANGE = re.compile(r"\$\s?(?P<lo>\d[\d,]*(?:\.\d+)?)\s*[-–]\s*\$\s?(?P<hi>\d[\d,]*(?:\.\d+)?)")
_BUDGET = re.compile(r"est\.?\s*budget:?\s*\$\s?(?P<v>\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_LEVEL = re.compile(r"\b(?P<level>expert|intermediate|entry[- ]level)\b", re.IGNORECASE)
_HOURS_FULL = re.compile(r"(30\+|more than 30)\s*hrs?\s*/\s*week", re.IGNORECASE)
_HOURS_PART = re.compile(r"less than 30\s*hrs?\s*/\s*week", re.IGNORECASE)
_PAYMENT = re.compile(r"^payment\s+(?P<un>un)?verified\b", re.IGNORECASE)
_SPENT = re.compile(r"^(?P<amt>\$\s?[\d.,]+[kKmM]?\+?)\s+spent\b", re.IGNORECASE)
_PROPOSALS_LINE = re.compile(r"^proposals?:\s*(?P<n>.+)$", re.IGNORECASE)
_SKILLS_INLINE = re.compile(r"^skills?:\s*(?P<inline>.+)$", re.IGNORECASE)
_CARD_NOISE = re.compile(
    r"^(apply now|apply|save job|save|saved|share|report|see more|show more|less|more|"
    r"\d+ (?:applicants?|proposals?))$",
    re.IGNORECASE,
)
_SENIORITY = {
    "expert": Seniority.senior,
    "intermediate": Seniority.mid,
    "entry level": Seniority.junior,
    "entry-level": Seniority.junior,
}


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _looks_like_skill(line: str) -> bool:
    return (
        len(line) <= 40
        and len(line.split()) <= 4
        and not line.endswith((".", "!", "?", ":"))
        and ", " not in line
        and ": " not in line
    )


def _looks_like_location(line: str) -> bool:
    return len(line) <= 40 and not any(ch.isdigit() for ch in line) and "http" not in line


def _cards(text: str) -> list[list[str]]:
    """Cards = paragraph blocks; without blank lines, each ``Posted …`` line starts a card.
    A block with neither a ``Posted`` nor a budget line continues the previous card."""
    blocks = shared.blocks(text)
    if len(blocks) >= 2:
        cards: list[list[str]] = []
        for block in blocks:
            if cards and not any(_POSTED.match(ln) or _META.match(ln) for ln in block):
                cards[-1].extend(block)
            else:
                cards.append(list(block))
        return cards
    lines = shared.split_lines(text)
    starts = [i for i, ln in enumerate(lines) if _POSTED.match(ln)]
    if len(starts) >= 2:
        bounds = [*starts, len(lines)]
        return [lines[bounds[i] : bounds[i + 1]] for i in range(len(starts))]
    return [lines] if lines else []


def _meta(meta: str) -> tuple[Compensation, EmploymentType | None, Seniority | None]:
    """'Hourly: $60.00-$90.00 - Expert - Est. time: …' / 'Fixed-price - … - Est. budget: $N'."""
    m = _META.match(meta)
    assert m is not None
    level = _LEVEL.search(meta)
    seniority = _SENIORITY.get(level.group("level").lower()) if level else None
    if m.group("kind").lower().startswith("hourly"):
        lo: float | None = None
        hi: float | None = None
        if r := _RANGE.search(meta):
            lo, hi = _num(r.group("lo")), _num(r.group("hi"))
        elif s := _MONEY.search(meta):
            lo = hi = _num(s.group("v"))
        comp = Compensation(
            min=lo,
            max=hi,
            currency="USD" if lo is not None else None,
            period=CompensationPeriod.hour,
            type="rate",
            raw=meta,
        )
        employment: EmploymentType | None = None
        if _HOURS_FULL.search(meta):
            employment = EmploymentType.full_time
        elif _HOURS_PART.search(meta):
            employment = EmploymentType.part_time
        return comp, employment, seniority
    value: float | None = None
    if b := _BUDGET.search(meta):
        value = _num(b.group("v"))
    elif s := _MONEY.search(meta):
        value = _num(s.group("v"))
    comp = Compensation(
        min=value,
        max=value,
        currency="USD" if value is not None else None,
        period=CompensationPeriod.project,
        type=None,
        raw=meta,
    )
    return comp, EmploymentType.project, seniority


def _parse_card(lines: list[str], now: datetime) -> JobPosting | None:
    meta_idx = next((i for i, ln in enumerate(lines) if _META.match(ln)), None)
    posted_line = next((ln for ln in lines if _POSTED.match(ln)), None)
    if meta_idx is None and posted_line is None:
        return None
    posted_at = shared.parse_date(posted_line, now=now) if posted_line else None

    candidates = list(reversed(lines[:meta_idx])) if meta_idx is not None else lines
    title: str | None = None
    title_idx = -1
    for ln in candidates:
        if _POSTED.match(ln) or _CARD_NOISE.match(ln) or ln.startswith("http"):
            continue
        title, title_idx = ln, lines.index(ln)
        break
    if not title:
        return None

    compensation: Compensation | None = None
    employment: EmploymentType | None = None
    seniority: Seniority | None = None
    meta_line: str | None = None
    if meta_idx is not None:
        meta_line = lines[meta_idx]
        compensation, employment, seniority = _meta(meta_line)

    description: list[str] = []
    skills: list[str] = []
    payment_verified: bool | None = None
    spent: str | None = None
    proposals: str | None = None
    location: str | None = None
    client_meta = False
    rest_start = (meta_idx if meta_idx is not None else title_idx) + 1
    for ln in lines[rest_start:]:
        if ln.startswith("http") or _POSTED.match(ln):
            continue
        if m := _PAYMENT.match(ln):
            payment_verified = m.group("un") is None
            client_meta = True
            continue
        if m := _SPENT.match(ln):
            spent = m.group("amt")
            client_meta = True
            continue
        if m := _PROPOSALS_LINE.match(ln):
            proposals = m.group("n").strip()
            continue
        if m := _SKILLS_INLINE.match(ln):
            skills.extend(s for s in shared.split_skills(m.group("inline")) if s not in skills)
            continue
        if _CARD_NOISE.match(ln) or _RATING.match(ln):
            continue
        if client_meta:
            if location is None and _looks_like_location(ln):
                location = ln
            continue
        if description and _looks_like_skill(ln):
            if ln not in skills:
                skills.append(ln)
        else:
            description.append(ln)

    summary = " ".join(description)
    urls = shared.find_urls("\n".join(lines))
    extraction = OpportunityExtraction(
        title=title[:300],
        contract_type=ContractType.freelance,
        employment_type=employment,
        location=location,
        remote_policy=RemotePolicy.remote_global,
        compensation=compensation,
        seniority=seniority,
        technologies=skills[:20],
        summary=summary[:600] or None,
        red_flags=["payment unverified"] if payment_verified is False else [],
    )
    return JobPosting(
        platform=PLATFORM,
        title=title[:300],
        company=None,
        location=location,
        url=urls[0] if urls else None,
        posted_at=posted_at,
        raw_text="\n".join(lines),
        extraction=extraction,
        raw_payload={
            "lines": lines,
            "posted": posted_line,
            "meta": meta_line,
            "payment_verified": payment_verified,
            "client_spent": spent,
            "proposals": proposals,
        },
    )


def parse_jobs(text: str, *, now: datetime | None = None, limit: int = 100) -> list[JobPosting]:
    """ "Find Work" / job feed paste → postings; no recognisable card → generic parser."""
    now = now or datetime.now(UTC)
    out: list[JobPosting] = []
    for card in _cards(text):
        job = _parse_card(card, now)
        if job is not None:
            out.append(job)
        if len(out) >= limit:
            break
    return out or shared.generic_jobs(text, PLATFORM, limit=limit)


# ------------------------------------------------------------------------------ "My Proposals"

_SECTION = re.compile(
    r"^(?P<name>offers?|invitations?(?: to interview)?|interviews?|active proposals?|"
    r"submitted proposals?|referrals?|archived proposals?|archived)\s*(?:\(\d+\))?\s*:?$",
    re.IGNORECASE,
)
_DETAIL = re.compile(
    r"^(?:initiated|submitted|applied|viewed|declined|withdrawn|offer|hired|interview|"
    r"archived|received|sent|accepted|rejected|closed|not selected|client:|status:)|"
    r"\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_SEP = re.compile(r"\s+[·•|]\s+|\s+/\s+")
_RANK: dict[ApplicationStatus, int] = {
    ApplicationStatus.unknown: 0,
    ApplicationStatus.applied: 1,
    ApplicationStatus.viewed: 2,
    ApplicationStatus.invited: 3,
    ApplicationStatus.interview: 4,
    ApplicationStatus.offer: 5,
    ApplicationStatus.rejected: 6,
    ApplicationStatus.withdrawn: 6,
}


def _section_status(name: str) -> ApplicationStatus:
    n = name.lower()
    if n.startswith("offer"):
        return ApplicationStatus.offer
    if n.startswith("invitation"):
        return ApplicationStatus.invited
    if n.startswith(("interview", "active")):
        # "Active proposals" = the client has responded and an interview room is open
        return ApplicationStatus.interview
    if n.startswith("submitted"):
        return ApplicationStatus.applied
    if n.startswith("archived"):
        return ApplicationStatus.rejected
    return ApplicationStatus.unknown  # referrals and anything unrecognised


class _Row:
    __slots__ = ("company", "details", "lines", "title")

    def __init__(self, title: str, line: str) -> None:
        self.title = title
        self.company: str | None = None
        self.details: list[str] = []
        self.lines: list[str] = [line]

    def absorb(self, parts: list[str], line: str) -> None:
        self.lines.append(line)
        for part in parts:
            if _DETAIL.search(part) or self.company is not None:
                self.details.append(part)
            else:
                self.company = part


def _observation(row: _Row, section: str, now: datetime) -> ApplicationObservationIn:
    default = _section_status(section)
    row_status, row_raw = shared.best_status(row.details)
    if row_status != ApplicationStatus.unknown and _RANK[row_status] >= _RANK[default]:
        status, status_raw = row_status, row_raw
    else:
        status, status_raw = default, section
    applied_at = next(
        (d for d in (shared.parse_date(p, now=now) for p in row.details) if d is not None), None
    )
    return ApplicationObservationIn(
        platform=PLATFORM,
        job_title=row.title[:300],
        company=row.company,
        status_raw=status_raw,
        status=status,
        applied_at=applied_at,
        raw_payload={"section": section, "lines": row.lines},
    )


def parse_proposals(text: str, *, now: datetime | None = None) -> list[ApplicationObservationIn]:
    """ "My Proposals" paste → observations; without section headers → generic parser."""
    now = now or datetime.now(UTC)
    lines = shared.split_lines(text)
    if not any(_SECTION.match(ln) for ln in lines):
        return shared.generic_applications(text, PLATFORM, now=now)

    out: list[ApplicationObservationIn] = []
    section: str | None = None
    row: _Row | None = None

    def flush() -> None:
        nonlocal row
        if row is not None and section is not None:
            out.append(_observation(row, section, now))
        row = None

    for line in lines:
        if m := _SECTION.match(line):
            flush()
            section = m.group("name")
            continue
        if section is None:
            continue  # page chrome before the first section
        parts = [p.strip() for p in _SEP.split(line) if p.strip()]
        if len(parts) > 1:
            head, tail = parts[0], parts[1:]
            if row is not None and not row.details and not _DETAIL.search(head):
                row.absorb(parts, line)  # "Client · Initiated <date>" under a title line
            elif row is not None and _DETAIL.search(head):
                row.absorb(parts, line)  # "Initiated <date> · Viewed by client"
            else:
                flush()
                row = _Row(head, line)
                row.absorb(tail, line)
                row.lines = [line]
            continue
        if _DETAIL.search(line):
            if row is not None:
                row.details.append(line)
                row.lines.append(line)
            continue
        short = len(line) <= 40 and len(line.split()) <= 4
        if row is not None and not row.details and row.company is None and short:
            row.company = line
            row.lines.append(line)
            continue
        flush()
        row = _Row(line, line)
    flush()
    return out
