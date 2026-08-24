"""Wellfound paste parsers: profile page, jobs list / job page, "Applied" tab.

Pure functions over text the user copied from wellfound.com (ADR-005: the site is never fetched).
Nothing is invented: unknown → ``None``; ``raw_text`` / ``raw_payload`` keep the pasted lines
verbatim. Layout assumptions are documented in ``docs/platform/wellfound.md``; unknown shapes fall
back to the shared generic parsers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from careeros.modules.opportunities.enums import CompensationPeriod, EmploymentType, RemotePolicy
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform import parsers as shared
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.wellfound

# ------------------------------------------------------------------------------ line vocabulary

# Wellfound joins inline facts with "•" (cards) or "·" (profile header).
_SEP = re.compile(r"\s*[•·|]\s*")
_LIST_SEP = re.compile(r"\s*[,;/•·|]\s*")

# Site chrome that a select-all copy drags along; matched as whole lines, case-insensitively.
_CHROME = frozenset(
    {
        "wellfound",
        "jobs",
        "companies",
        "messages",
        "applications",
        "discover",
        "for job seekers",
        "for recruiters",
        "log in",
        "log out",
        "sign up",
        "edit profile",
        "share",
        "share profile",
        "report",
        "message",
        "settings",
        "saved",
        "hide",
        "hidden",
        "view profile",
        "public profile",
        "resume / cv",
        "download resume",
    }
)
_ACTION = re.compile(
    r"^(?:apply|apply now|easy apply|save|saved|unsave|hide|hidden|share|report|view job|"
    r"view details)$",
    re.IGNORECASE,
)
_BADGE = re.compile(
    r"^(?:actively hiring|top \d+% of responders|highly responsive|growing fast|recently funded|"
    r"remote[- ]first|fully remote company|be an early applicant|recruiter recently active|new|"
    r"featured|promoted|verified|hiring now|actively recruiting|series [a-e]|seed|pre-seed|"
    r"bootstrapped|showing \d+.*|\d+ (?:open )?jobs?|see all(?: \d+)? jobs)$",
    re.IGNORECASE,
)
_SIZE_CORE = r"\d[\d,.]*(?:\s*[-–—]\s*\d[\d,.]*|\+)?\s+employees"
_SIZE = re.compile(rf"^(?P<size>{_SIZE_CORE})$", re.IGNORECASE)
_COMPANY_SIZE = re.compile(rf"^(?P<company>.+?)\s*[•·|]\s*(?P<size>{_SIZE_CORE})$", re.IGNORECASE)
_URL_LINE = re.compile(r"^https?://\S+$")
_JOB_URL = re.compile(r"https?://(?:www\.)?wellfound\.com/jobs/(?P<id>\d+)\S*")
_PROFILE_URL = re.compile(r"https?://(?:www\.)?wellfound\.com/u/(?P<handle>[\w.-]+)")

_ROLE_WORDS = re.compile(
    r"\b(?:engineer(?:ing)?|developer|programmer|architect|analyst|scientist|manager|lead|head|"
    r"director|vp|vice president|chief|cto|ceo|coo|cfo|cpo|officer|founder|co-?founder|designer|"
    r"researcher|specialist|consultant|intern(?:ship)?|associate|coordinator|recruiter|marketer|"
    r"executive|sdr|bdr|devops|sre|qa|tester|writer|editor|admin(?:istrator)?|principal|staff|"
    r"senior|junior|full[- ]?stack|front[- ]?end|back[- ]?end|generalist|partner|advisor|"
    r"evangelist|advocate|dba|accountant|controller|counsel|technician|assistant|strategist|"
    r"president|contractor|freelancer|trainer|scrum master)\b",
    re.IGNORECASE,
)


def _is_chrome(line: str) -> bool:
    return line.lower().rstrip(":") in _CHROME


def _has_role_word(line: str) -> bool:
    return _ROLE_WORDS.search(line) is not None


def _split_list(value: str) -> list[str]:
    return [p.strip() for p in _LIST_SEP.split(value) if p.strip()]


# ------------------------------------------------------------------------------ money

_CURRENCY_BY_SYMBOL: dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₽": "RUB",
    "₴": "UAH",
    "¥": "JPY",
    "₹": "INR",
    "₾": "GEL",
    "C$": "CAD",
    "CA$": "CAD",
    "A$": "AUD",
    "AU$": "AUD",
    "NZ$": "NZD",
    "S$": "SGD",
    "HK$": "HKD",
}
_CURRENCY_CODES = ("USD", "EUR", "GBP", "CAD", "AUD", "NZD", "SGD", "HKD", "CHF", "PLN", "UAH")
_CURRENCY_CODES += ("RUB", "INR", "JPY", "GEL")
_CODE = re.compile(r"\b(" + "|".join(_CURRENCY_CODES) + r")\b")
_MONEY = re.compile(
    r"(?P<pre>(?:US|CA|AU|NZ|HK|C|A|S)?\$|€|£|₽|₴|¥|₹|₾)?\s?"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<mult>[kKmM]\b)?(?P<pct>\s?%)?"
)
_NO_SALARY = re.compile(
    r"^(?:no salary(?: listed)?|salary not (?:listed|disclosed)|equity only|"
    r"compensation not listed|competitive salary)$",
    re.IGNORECASE,
)
_EQUITY = re.compile(r"\d+(?:\.\d+)?%(?:\s*[–—-]\s*\d+(?:\.\d+)?%)?")
_EQUITY_ONLY = re.compile(r"^\d+(?:\.\d+)?%(?:\s*[–—-]\s*\d+(?:\.\d+)?%)?(?:\s+equity)?$")
_PER_HOUR = re.compile(r"/\s*(?:hr|hour)\b|\bper hour\b|\bhourly\b|\ban hour\b", re.IGNORECASE)
_PER_DAY = re.compile(r"/\s*day\b|\bper day\b|\bdaily\b", re.IGNORECASE)
_PER_MONTH = re.compile(r"/\s*(?:mo|month)\b|\bper month\b|\bmonthly\b", re.IGNORECASE)
_UP_TO = re.compile(r"\b(?:up to|max(?:imum)?|до)\b", re.IGNORECASE)


def parse_money(line: str) -> Compensation | None:
    """'$120k – $150k • 0.1% – 0.5%', '€80k', '£70k+', 'Up to $150k', '$50 – $70 / hr' → figures.

    Percent tokens (equity) are ignored; the whole line is kept verbatim in ``raw``.
    ``None`` when the line carries no salary figure.
    """
    text = line.strip()
    if not text or _NO_SALARY.match(text) or _EQUITY_ONLY.match(text):
        return None
    tokens: list[tuple[float, str | None, int, int]] = []  # (value, currency, start, end)
    code = _CODE.search(text)
    for m in _MONEY.finditer(text):
        if m.group("pct") or not (m.group("pre") or m.group("mult")):
            continue
        value = float(m.group("num").replace(",", ""))
        mult = (m.group("mult") or "").lower()
        value *= 1_000 if mult == "k" else 1_000_000 if mult == "m" else 1
        currency = _CURRENCY_BY_SYMBOL.get(m.group("pre") or "")
        tokens.append((value, currency, m.start(), m.end()))
    if not tokens:
        return None
    currency = code.group(1) if code else next((c for _, c, _, _ in tokens if c), None)
    lo: float | None
    hi: float | None
    if len(tokens) >= 2:
        lo, hi = tokens[0][0], tokens[1][0]
        if hi < lo:
            lo, hi = hi, lo
    else:
        value, _, start, end = tokens[0]
        if text[end:].lstrip().startswith("+"):
            lo, hi = value, None
        elif _UP_TO.search(text[:start]):
            lo, hi = None, value
        else:
            lo = hi = value
    if _PER_HOUR.search(text):
        period, kind = CompensationPeriod.hour, "rate"
    elif _PER_DAY.search(text):
        period, kind = CompensationPeriod.day, "rate"
    elif _PER_MONTH.search(text):
        period, kind = CompensationPeriod.month, "salary"
    else:
        period, kind = CompensationPeriod.year, "salary"
    return Compensation(min=lo, max=hi, currency=currency, period=period, type=kind, raw=text)


def _is_comp_line(line: str) -> bool:
    return bool(_NO_SALARY.match(line) or _EQUITY_ONLY.match(line) or parse_money(line))


def _equity(line: str | None) -> str | None:
    if not line:
        return None
    m = _EQUITY.search(line)
    return m.group(0) if m else None


# ------------------------------------------------------------------------------ location

_REMOTE_TAG = re.compile(r"\bremote\b", re.IGNORECASE)
_HYBRID_TAG = re.compile(r"\bhybrid\b", re.IGNORECASE)
_ONSITE_TAG = re.compile(r"\b(?:in[- ]office|on[- ]?site|office only)\b", re.IGNORECASE)
_REMOTE_FILLER = re.compile(
    r"\b(?:only|ok|okay|friendly|work|possible|or|onsite|on-site|in office|first|position|job|in|"
    r"within|from)\b",
    re.IGNORECASE,
)
_CITY_COUNTRY = re.compile(r"^[A-Z][A-Za-z .'’-]{1,40},\s*[A-Z][A-Za-z .'’-]{1,40}$")
_GLOBAL_HINTS = frozenset({"worldwide", "anywhere", "global", "globally", "everywhere"})
_REGION_CODES: dict[str, str] = {
    "us": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "united states": "US",
    "united states of america": "US",
    "eu": "EU",
    "europe": "EU",
    "european union": "EU",
    "emea": "EMEA",
    "uk": "UK",
    "united kingdom": "UK",
    "canada": "CA",
    "poland": "PL",
    "georgia": "GE",
    "germany": "DE",
    "ukraine": "UA",
    "latam": "LATAM",
    "latin america": "LATAM",
    "apac": "APAC",
    "asia": "APAC",
    "americas": "AMERICAS",
}
_EMPLOYMENT: tuple[tuple[str, EmploymentType], ...] = (
    ("full-time", EmploymentType.full_time),
    ("full time", EmploymentType.full_time),
    ("part-time", EmploymentType.part_time),
    ("part time", EmploymentType.part_time),
)


def _region_code(text: str) -> str:
    return _REGION_CODES.get(text.strip().lower(), text.strip())


def remote_policy(line: str) -> tuple[RemotePolicy, list[str]]:
    """Location line → (policy, regions).

    'Remote' → remote_global; 'Remote • US' → remote_region ['US'] (the card names a region, so
    worldwide eligibility is not assumed); 'Tbilisi, Georgia • Hybrid' → hybrid;
    'Berlin, Germany • In office' → onsite; a bare city → unknown.
    """
    parts = [p for p in _SEP.split(line.strip()) if p]
    has_remote = any(_REMOTE_TAG.search(p) for p in parts)
    if any(_HYBRID_TAG.search(p) for p in parts):
        return RemotePolicy.hybrid, []
    if any(_ONSITE_TAG.search(p) for p in parts) and not has_remote:
        return RemotePolicy.onsite, []
    if not has_remote:
        return RemotePolicy.unknown, []
    regions: list[str] = []
    for part in parts:
        if _REMOTE_TAG.search(part):
            inner = _REMOTE_FILLER.sub(" ", _REMOTE_TAG.sub(" ", part))
            candidate = inner.strip(" ()[]-–—,:/").strip()
        elif _ONSITE_TAG.search(part):
            continue
        else:
            candidate = part.strip()
        if not candidate:
            continue
        if candidate.lower() in _GLOBAL_HINTS:
            return RemotePolicy.remote_global, []
        regions.append(_region_code(candidate))
    return (RemotePolicy.remote_region, regions) if regions else (RemotePolicy.remote_global, [])


def _is_location_line(line: str) -> bool:
    if _REMOTE_TAG.search(line) or _HYBRID_TAG.search(line) or _ONSITE_TAG.search(line):
        return True
    first = _SEP.split(line.strip())[0]
    return bool(_CITY_COUNTRY.match(first))


def _employment_type(*lines: str | None) -> EmploymentType | None:
    for line in lines:
        low = (line or "").lower()
        for needle, kind in _EMPLOYMENT:
            if needle in low:
                return kind
    return None


def _looks_like_place(line: str) -> bool:
    words = line.split()
    return (
        0 < len(words) <= 4
        and len(line) <= 40
        and not any(ch.isdigit() for ch in line)
        and not _has_role_word(line)
    )


# ------------------------------------------------------------------------------ posted

_POSTED = re.compile(
    r"^(?:re)?posted\b.*$|^\d+\s*(?:d|h|w|m|mo|min|hr|hrs|days?|hours?|weeks?|months?)\s+ago$|"
    r"^(?:today|yesterday|just now)$",
    re.IGNORECASE,
)
_POSTED_PREFIX = re.compile(r"^(?:re)?posted\s*(?:on\s+)?", re.IGNORECASE)
_SHORT_REL = re.compile(r"^(\d+)\s*(mo|d|h|w|m)\s+ago$", re.IGNORECASE)
_SHORT_UNITS = {"d": "days", "h": "hours", "w": "weeks", "m": "months", "mo": "months"}


def parse_posted(line: str, *, now: datetime | None = None) -> datetime | None:
    """'Posted 3 days ago', 'Reposted today', '2w ago', 'Posted Aug 12, 2026' → aware datetime."""
    text = _POSTED_PREFIX.sub("", line.strip()).strip()
    if m := _SHORT_REL.match(text):
        text = f"{m.group(1)} {_SHORT_UNITS[m.group(2).lower()]} ago"
    return shared.parse_date(text, now=now)


# ------------------------------------------------------------------------------ line classes

_TEXT, _ACT, _BDG, _SZ, _COMP, _PST, _LOC, _URL, _CHR = (
    "text",
    "action",
    "badge",
    "size",
    "comp",
    "posted",
    "location",
    "url",
    "chrome",
)


def _classify(line: str) -> str:
    if _ACTION.match(line):
        return _ACT
    if _is_chrome(line):
        return _CHR
    if _BADGE.match(line):
        return _BDG
    if _SIZE.match(line) or _COMPANY_SIZE.match(line):
        return _SZ
    if _is_comp_line(line):
        return _COMP
    if _POSTED.match(line):
        return _PST
    if _URL_LINE.match(line):
        return _URL
    if _is_location_line(line):
        return _LOC
    return _TEXT


# ------------------------------------------------------------------------------ jobs: cards


@dataclass(slots=True)
class _Role:
    title: str
    start: int
    end: int
    company: str | None
    company_size: str | None
    header: tuple[int, int] | None
    location: str | None = None
    comp_line: str | None = None
    posted: str | None = None
    url: str | None = None
    closed: bool = False


@dataclass(slots=True)
class _Card:
    lines: list[str]
    company: str | None = None
    company_size: str | None = None
    company_idx: int | None = None
    first_title_idx: int | None = None
    run: list[int] = field(default_factory=list)
    cur: _Role | None = None
    roles: list[_Role] = field(default_factory=list)


def _open_role(card: _Card, kind: str, end: int) -> None:
    """Resolve the pending run of plain lines into (company header, title[, bare location])."""
    rest = card.run
    card.run = []
    lines = card.lines
    if card.company is None:
        if len(rest) >= 2:
            card.company, card.company_idx = lines[rest[0]], rest[0]
            rest = rest[1:]
        elif kind in (_LOC, _ACT) and not _has_role_word(lines[rest[0]]):
            card.company, card.company_idx = lines[rest[0]], rest[0]
            return
    title_idx, loc_idx = rest[-1], None
    if (
        kind != _LOC
        and len(rest) >= 2
        and _has_role_word(lines[rest[-2]])
        and _looks_like_place(lines[rest[-1]])
    ):
        title_idx, loc_idx = rest[-2], rest[-1]
    title, company = lines[title_idx], card.company
    if company is None:
        guessed_title, guessed_company = shared.guess_title_company(title)
        if guessed_title and guessed_company:
            title, company = guessed_title, guessed_company
    if card.first_title_idx is None:
        card.first_title_idx = title_idx
    header = None
    if card.company_idx is not None and card.first_title_idx is not None:
        header = (card.company_idx, card.first_title_idx)
    role = _Role(
        title=title,
        start=title_idx,
        end=end,
        company=company,
        company_size=card.company_size,
        header=header,
        location=lines[loc_idx] if loc_idx is not None else None,
    )
    card.cur = role
    card.roles.append(role)


def _walk_card(lines: list[str]) -> list[_Role]:
    card = _Card(lines=lines)
    for idx, line in enumerate(lines):
        kind = _classify(line)
        if kind in (_BDG, _CHR):
            if card.cur is not None and not card.cur.closed:
                card.cur.end = idx + 1
            continue
        if kind == _TEXT:
            card.run.append(idx)
            continue
        if kind == _SZ:
            card.cur = None
            if m := _COMPANY_SIZE.match(line):
                card.company, card.company_size = m.group("company").strip(), m.group("size")
                card.company_idx, card.first_title_idx = idx, None
            elif card.run:
                card.company, card.company_idx = lines[card.run[0]], card.run[0]
                card.company_size, card.first_title_idx = line, None
            else:
                card.company_size = card.company_size or line
            card.run = []
            continue
        if kind == _URL:
            if card.cur is not None and not card.cur.closed:
                card.cur.url = card.cur.url or line
                card.cur.end = idx + 1
            continue
        # terminators: location / compensation / posted / apply-save
        if card.run:
            _open_role(card, kind, idx)
        cur = card.cur
        if cur is None:
            continue
        if kind == _ACT:
            cur.end, cur.closed = idx + 1, True
            continue
        if cur.closed:
            continue
        cur.end = idx + 1
        if kind == _LOC and cur.location is None:
            cur.location = line
        elif kind == _COMP and cur.comp_line is None:
            cur.comp_line = line
        elif kind == _PST and cur.posted is None:
            cur.posted = line
    return card.roles


def _posting(lines: list[str], role: _Role, *, now: datetime | None) -> JobPosting:
    header = lines[role.header[0] : role.header[1]] if role.header else []
    raw_lines = header + lines[role.start : role.end]
    policy, regions = remote_policy(role.location) if role.location else (RemotePolicy.unknown, [])
    comp = parse_money(role.comp_line) if role.comp_line else None
    extraction = OpportunityExtraction(
        title=role.title,
        company=role.company,
        location=role.location,
        remote_policy=policy,
        remote_regions=regions,
        compensation=comp,
        employment_type=_employment_type(role.location),
    )
    payload = {
        "company_size": role.company_size,
        "equity": _equity(role.comp_line),
        "posted": role.posted,
    }
    job_url = _JOB_URL.search(role.url) if role.url else None
    return JobPosting(
        platform=PLATFORM,
        external_id=job_url.group("id") if job_url else None,
        url=role.url,
        title=role.title[:300],
        company=role.company,
        location=role.location,
        posted_at=parse_posted(role.posted, now=now) if role.posted else None,
        raw_text="\n".join(raw_lines),
        extraction=extraction,
        raw_payload={k: v for k, v in payload.items() if v} or None,
    )


# ------------------------------------------------------------------------------ jobs: job page

_PAGE_SECTION = re.compile(
    r"^(job location|visa sponsorship|remote work policy|hires remotely in|relocation|skills|"
    r"about the job|job description|about the company|hiring contact|job type|role type|"
    r"employment type|experience|compensation|salary)\s*:?$",
    re.IGNORECASE,
)
_PAGE_MARKERS = frozenset(
    {
        "job location",
        "visa sponsorship",
        "remote work policy",
        "hires remotely in",
        "relocation",
        "about the job",
        "job description",
        "hiring contact",
    }
)


def _is_job_page(lines: list[str]) -> bool:
    markers = {m.group(1).lower() for ln in lines if (m := _PAGE_SECTION.match(ln))}
    return len(markers & _PAGE_MARKERS) >= 2


def _parse_job_page(text: str, lines: list[str], *, now: datetime | None) -> list[JobPosting]:
    header: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines:
        if m := _PAGE_SECTION.match(line):
            current = sections.setdefault(m.group(1).lower(), [])
            continue
        (header if current is None else current).append(line)
    kinds = [(ln, _classify(ln)) for ln in header]
    texts = [ln for ln, k in kinds if k == _TEXT]
    if not texts:
        return []
    title = texts[0]
    company = texts[1] if len(texts) > 1 else None
    comp_line = next((ln for ln, k in kinds if k == _COMP), None)
    posted = next((ln for ln, k in kinds if k == _PST), None)
    location = next((ln for ln, k in kinds if k == _LOC), None)
    if location is None:
        location = next(iter(sections.get("job location", [])), None)
    policy, regions = remote_policy(location) if location else (RemotePolicy.unknown, [])
    wp = " ".join(sections.get("remote work policy", [])).lower()
    if wp:
        if "hybrid" in wp:
            policy, regions = RemotePolicy.hybrid, []
        elif "remote" in wp and policy in (RemotePolicy.unknown, RemotePolicy.onsite):
            policy = RemotePolicy.remote_global
        elif "remote" not in wp and ("office" in wp or "onsite" in wp or "on-site" in wp):
            policy, regions = RemotePolicy.onsite, []
    hires_in = [
        _region_code(p) for ln in sections.get("hires remotely in", []) for p in _split_list(ln)
    ]
    if hires_in and policy in (RemotePolicy.remote_global, RemotePolicy.remote_region):
        if any(r.lower() in _GLOBAL_HINTS for r in hires_in):
            policy, regions = RemotePolicy.remote_global, []
        else:
            policy, regions = RemotePolicy.remote_region, hires_in
    technologies = [s for ln in sections.get("skills", []) for s in shared.split_skills(ln)]
    job_type = (
        sections.get("job type") or sections.get("role type") or sections.get("employment type")
    )
    comp = parse_money(comp_line) if comp_line else None
    extraction = OpportunityExtraction(
        title=title,
        company=company,
        location=location,
        remote_policy=policy,
        remote_regions=regions,
        compensation=comp,
        employment_type=_employment_type(location, *(job_type or [])),
        technologies=technologies,
    )
    job_url = _JOB_URL.search(text)
    payload: dict[str, Any] = {"sections": sections}
    if equity := _equity(comp_line):
        payload["equity"] = equity
    return [
        JobPosting(
            platform=PLATFORM,
            external_id=job_url.group("id") if job_url else None,
            url=job_url.group(0) if job_url else None,
            title=title[:300],
            company=company,
            location=location,
            posted_at=parse_posted(posted, now=now) if posted else None,
            raw_text=text.strip(),
            extraction=extraction,
            raw_payload=payload,
        )
    ]


def parse_jobs(text: str, *, now: datetime | None = None, limit: int = 100) -> list[JobPosting]:
    """wellfound.com/jobs cards (blank-line separated; several roles per company card are fine)
    or a single job page. Unknown layouts fall back to ``shared.generic_jobs``."""
    if not text.strip():
        return []
    all_lines = [ln for ln in shared.split_lines(text) if not _is_chrome(ln)]
    if _is_job_page(all_lines):
        return _parse_job_page(text, all_lines, now=now)[:limit]
    out: list[JobPosting] = []
    for block in shared.blocks(text):
        lines = [ln for ln in block if not _is_chrome(ln)]
        for role in _walk_card(lines):
            out.append(_posting(lines, role, now=now))
            if len(out) >= limit:
                return out
    if out:
        return out
    return shared.generic_jobs(text, PLATFORM, limit=limit)


# ------------------------------------------------------------------------------ applications

_APPLIED = re.compile(r"^applied\s+(?:on\s+)?\S.*$", re.IGNORECASE)
_APPLIED_PREFIX = re.compile(r"^applied\s+(?:on\s+)?", re.IGNORECASE)
_STATUS_LINE = re.compile(
    r"^(?:status\s*:\s*)?(?:application (?:sent|submitted|viewed|received|withdrawn)|applied|"
    r"submitted|viewed(?: by (?:company|employer|recruiter))?|seen|interviewing|"
    r"interview(?: scheduled| requested)?|not moving forward|rejected|declined|closed|archived|"
    r"hired|offer(?: received| extended)?|withdrawn|in review|under review|no response|pending|"
    r"shortlisted|matched)$",
    re.IGNORECASE,
)
_STATUS_PREFIX = re.compile(r"^status\s*:\s*", re.IGNORECASE)
_STATUS_MAP: dict[str, ApplicationStatus] = {
    "application sent": ApplicationStatus.applied,
    "application submitted": ApplicationStatus.applied,
    "application received": ApplicationStatus.applied,
    "applied": ApplicationStatus.applied,
    "submitted": ApplicationStatus.applied,
    "viewed": ApplicationStatus.viewed,
    "application viewed": ApplicationStatus.viewed,
    "viewed by company": ApplicationStatus.viewed,
    "viewed by employer": ApplicationStatus.viewed,
    "viewed by recruiter": ApplicationStatus.viewed,
    "seen": ApplicationStatus.viewed,
    "interviewing": ApplicationStatus.interview,
    "interview": ApplicationStatus.interview,
    "interview scheduled": ApplicationStatus.interview,
    "interview requested": ApplicationStatus.invited,
    "not moving forward": ApplicationStatus.rejected,
    "rejected": ApplicationStatus.rejected,
    "declined": ApplicationStatus.rejected,
    "closed": ApplicationStatus.rejected,
    "hired": ApplicationStatus.offer,
    "offer": ApplicationStatus.offer,
    "offer received": ApplicationStatus.offer,
    "offer extended": ApplicationStatus.offer,
    "withdrawn": ApplicationStatus.withdrawn,
    "application withdrawn": ApplicationStatus.withdrawn,
}


def is_status_line(line: str) -> bool:
    return _STATUS_LINE.match(line.strip()) is not None


def map_status(line: str) -> ApplicationStatus:
    """Wellfound wording first ('Application sent', 'Not moving forward', 'Hired' …), then the
    shared multilingual rules; unrecognised wording stays ``unknown``."""
    key = _STATUS_PREFIX.sub("", line.strip()).strip().lower()
    return _STATUS_MAP.get(key) or shared.normalize_status(key)


def _is_app_extra(line: str) -> bool:
    kind = _classify(line)
    return kind in (_LOC, _COMP, _SZ, _BDG, _URL) or _employment_type(line) is not None


def _observation(
    row: list[str], name_idx: list[int], anchors: list[str], *, now: datetime | None
) -> ApplicationObservationIn | None:
    names = name_idx[-2:]
    if not names:
        return None
    if len(names) == 2:
        company, title = row[names[0]], row[names[1]]
    else:
        title, company = shared.guess_title_company(row[names[0]])
        if not title:
            return None
    lines = row[names[0] :]
    status_line = next((a for a in anchors if is_status_line(a)), None)
    applied_line = next((a for a in anchors if _APPLIED.match(a)), None)
    status_raw = status_line or applied_line or ""
    status = map_status(status_raw) if status_raw else ApplicationStatus.unknown
    applied_at = None
    if applied_line:
        applied_at = shared.parse_date(_APPLIED_PREFIX.sub("", applied_line), now=now)
    urls = shared.find_urls("\n".join(lines))
    return ApplicationObservationIn(
        platform=PLATFORM,
        job_title=title[:300],
        company=company,
        job_url=urls[0] if urls else None,
        status_raw=status_raw,
        status=status,
        applied_at=applied_at,
        raw_payload={"lines": lines},
    )


def parse_applications(text: str, *, now: datetime | None = None) -> list[ApplicationObservationIn]:
    """ "Applied" tab rows: Company / Title / [location, comp] / 'Applied <date|N days ago>' /
    status line. Rows are delimited by the 'Applied …' / status anchors, so blank lines are
    optional. Without any anchor the shared generic parser is used."""
    out: list[ApplicationObservationIn] = []
    row: list[str] = []
    names: list[int] = []
    anchors: list[str] = []

    def flush() -> None:
        nonlocal row, names, anchors
        if anchors and (obs := _observation(row, names, anchors, now=now)):
            out.append(obs)
        row, names, anchors = [], [], []

    seen_anchor = False
    for line in shared.split_lines(text):
        if _is_chrome(line):
            continue
        if _APPLIED.match(line) or is_status_line(line):
            seen_anchor = True
            anchors.append(line)
            row.append(line)
            continue
        if anchors:
            flush()
        if not _is_app_extra(line):
            names.append(len(row))
        row.append(line)
    flush()
    if not seen_anchor:
        return shared.generic_applications(text, PLATFORM, now=now)
    return out


# ------------------------------------------------------------------------------ profile

_H_ABOUT = re.compile(r"^(?:about|about me|bio|summary)\s*:?$", re.IGNORECASE)
_H_SKILLS = re.compile(
    r"^(?:skills|expertise|top skills)\s*(?::\s*(?P<inline>.+))?$", re.IGNORECASE
)
_H_EXPERIENCE = re.compile(
    r"^(?:work experience|experience|employment|work history)\s*:?$", re.IGNORECASE
)
_H_EDUCATION = re.compile(r"^education\s*:?$", re.IGNORECASE)
_H_PREFS = re.compile(
    r"^(?:what i.m looking for|looking for|job preferences|preferences|job search preferences)"
    r"\s*:?$",
    re.IGNORECASE,
)
_H_OTHER = re.compile(
    r"^(?:achievements|projects|portfolio|links|social|contact|culture|values|languages|"
    r"certifications|recommendations|interests|profile|overview)\s*:?$",
    re.IGNORECASE,
)
_P_REMOTE = re.compile(
    r"^(?:open to remote(?: work)?|remote(?: work)?\s*:\s*(?:yes|open|ok|only)|remote only|"
    r"remote ok|willing to work remotely|open to working remotely)$",
    re.IGNORECASE,
)
_P_ROLES = re.compile(
    r"^(?:looking for|desired roles?|roles?|job types?|work type|type of work|open to|"
    r"interested in)\s*:\s*(?P<v>.+)$",
    re.IGNORECASE,
)
_P_SALARY = re.compile(
    r"^(?:desired salary|salary expectations?|expected salary|salary|desired compensation|"
    r"compensation)\s*:\s*(?P<v>.+)$",
    re.IGNORECASE,
)
_P_LOCATIONS = re.compile(
    r"^(?:preferred locations?|desired locations?|locations)\s*:\s*(?P<v>.+)$", re.IGNORECASE
)
_P_LOCATION = re.compile(r"^location\s*:\s*(?P<v>.+)$", re.IGNORECASE)
_AVAILABILITY = re.compile(
    r"^(?:ready to interview|open to offers|closed to offers|actively looking|"
    r"open to new opportunities|not looking|available (?:now|immediately|from .+)|"
    r"availability\s*:\s*.+)$",
    re.IGNORECASE,
)
_TITLE_AT = re.compile(r"^(?P<title>.+?)\s+(?:at|@)\s+(?P<company>.+)$", re.IGNORECASE)
_NAME = re.compile(r"^[^\W\d_][\w'’.-]*(?:\s+[^\W\d_][\w'’.-]*){1,3}$")


def _looks_like_name(line: str) -> bool:
    return bool(
        _NAME.match(line)
        and all(w[0].isupper() for w in line.split())
        and not _has_role_word(line)
        and not _SEP.search(line)
        and "," not in line
    )


def _apply_pref(line: str, prefs: dict[str, Any]) -> bool:
    if _P_REMOTE.match(line):
        prefs["remote"] = True
    elif m := _P_ROLES.match(line):
        prefs["roles"] = [*prefs["roles"], *_split_list(m.group("v"))]
    elif m := _P_SALARY.match(line):
        prefs["desired_salary"] = prefs["desired_salary"] or m.group("v").strip()
    elif m := _P_LOCATIONS.match(line):
        prefs["locations"] = _split_list(m.group("v"))
    elif m := _P_LOCATION.match(line):
        prefs["location"] = m.group("v").strip()
    else:
        return False
    return True


class _Experience:
    """'Title at Company' / period / description lines, or Company / Title / period (chips)."""

    def __init__(self) -> None:
        self.items: list[SnapshotExperienceItem] = []
        self.loose: list[str] = []

    def _flush_description(self, lines: list[str]) -> None:
        if lines and self.items and self.items[-1].description is None:
            self.items[-1].description = "\n".join(lines)

    def feed(self, line: str) -> None:
        if shared.looks_like_period(line):
            if self.items and self.items[-1].period is None:
                self._flush_description(self.loose)
                self.items[-1].period = line
            elif self.loose:
                pair = self.loose[-2:]
                self._flush_description(self.loose[:-2])
                company, title = (pair[0], pair[1]) if len(pair) == 2 else (pair[0], None)
                self.items.append(SnapshotExperienceItem(company=company, title=title, period=line))
            self.loose = []
            return
        if m := _TITLE_AT.match(line):
            self._flush_description(self.loose)
            self.loose = []
            self.items.append(
                SnapshotExperienceItem(company=m.group("company").strip(), title=m.group("title"))
            )
            return
        self.loose.append(line)

    def finish(self) -> list[SnapshotExperienceItem]:
        self._flush_description(self.loose)
        self.loose = []
        return self.items


def parse_profile(text: str) -> ProfileRead:
    """wellfound.com/u/<handle> (or the edit-profile page): name / headline / about / skills /
    work experience / education / preferences, in any order."""
    prefs: dict[str, Any] = {"remote": None, "roles": [], "desired_salary": None}
    url_match = _PROFILE_URL.search(text)
    lines = [
        ln for ln in shared.split_lines(text) if not _is_chrome(ln) and not _URL_LINE.match(ln)
    ]
    name: str | None = None
    headline: str | None = None
    availability: str | None = None
    about: list[str] = []
    skills: list[str] = []
    education: list[str] = []
    experience = _Experience()
    section = "header"
    for line in lines:
        if _H_ABOUT.match(line):
            section = "about"
            continue
        if m := _H_SKILLS.match(line):
            section = "skills"
            if m.group("inline"):
                skills.extend(shared.split_skills(m.group("inline")))
            continue
        if _H_EXPERIENCE.match(line):
            section = "experience"
            continue
        if _H_EDUCATION.match(line):
            section = "education"
            continue
        if _H_PREFS.match(line) or _H_OTHER.match(line):
            section = "other"
            continue
        if _apply_pref(line, prefs):
            continue
        if _AVAILABILITY.match(line):
            availability = availability or line
            continue
        if section == "header":
            if name is None and headline is None and _looks_like_name(line):
                name = line
            elif headline is None:
                headline = line
            elif _CITY_COUNTRY.match(line):
                prefs.setdefault("location", line)
        elif section == "about":
            about.append(line)
        elif section == "skills":
            skills.extend(shared.split_skills(line))
        elif section == "education":
            education.append(line)
        elif section == "experience":
            experience.feed(line)
    if headline and "location" not in prefs:
        tail = _SEP.split(headline)[-1]
        if tail != headline and _CITY_COUNTRY.match(tail):
            prefs["location"] = tail
    payload: dict[str, Any] = {}
    if name:
        payload["name"] = name
    if education:
        payload["education"] = education
    return ProfileRead(
        platform=PLATFORM,
        capture_method=CaptureMethod.paste,
        external_id=url_match.group("handle") if url_match else None,
        profile_url=url_match.group(0) if url_match else None,
        headline=headline[:300] if headline else None,
        about=" ".join(about) or None,
        skills=skills,
        experience=experience.finish(),
        availability=availability,
        preferences=prefs,
        raw_text=text,
        raw_payload=payload or None,
    )
