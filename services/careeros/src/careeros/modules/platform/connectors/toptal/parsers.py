"""Toptal paste parsers: public talent profile, talent-portal job cards, applied-jobs rows.

Toptal has no API or export and the site is never fetched (ADR-005): every function here works
on text the user copied from a page (select all → copy). Rules: nothing is invented — unknown →
``None``; ``raw_text`` / ``raw_payload`` keep the pasted lines verbatim; a layout with no Toptal
markers at all falls back to the shared generic parsers.

Layout assumptions (documented in docs/platform/toptal.md):
* profile — ``Name / Title / Based in <city> | Location: <city> / Member since … /
  Availability: … / $90/hr`` header, then sections ``Bio|About``, ``Expertise|Skills`` (Toptal's
  categorised ``Languages / Frameworks / Tools …`` sub-headers are skipped), ``Work Experience``
  (``Title / Company / 2023 - PRESENT`` or ``Title / 2023 - PRESENT / Company`` + bullets +
  ``Technologies: …``), ``Portfolio|Project Highlights|Experience`` (name + description [+ url]),
  ``Education``, ``Languages``. An unlabelled paragraph before the first section is the bio.
* jobs — one card per paragraph, or cards ending with ``Apply`` when the copy has no blank
  lines: ``Title / Client · Industry / Engagement: … / Remote — US hours | On-site, Berlin /
  Duration: … / Rate: $70 - $90/hr | Budget: $20,000 / Posted … / Skills: …``.
* applications — rows ``Title / Client / Applied <date> / Stage: <stage>`` (multi-line) or
  ``Title · Client · Applied <date> · Stage: <stage>`` (single line).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
)
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform import parsers
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import ApplicationObservationIn, JobPosting, ProfileRead
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.toptal

# ------------------------------------------------------------------------------ shared bits

_BULLET = re.compile(r"^[-•–—*·▪●]\s+")
_URL_LINE = re.compile(r"^https?://\S+$", re.IGNORECASE)
_TOPTAL_URL = re.compile(r"https?://(?:www\.)?toptal\.com/[^\s<>\"')\]]+", re.IGNORECASE)
_SEP = re.compile(r"\s*[·•|]\s*")

# Page chrome that a select-all copy drags along (nav, buttons, badges).
_NAV_NOISE = re.compile(
    r"^(toptal|menu|home|log ?in|sign ?up|hire talent|hire freelancers|hire (me|now)|hire \w+|"
    r"apply as a freelancer|share( this profile)?|download (pdf|resume|cv)|contact( me)?|message|"
    r"view (portfolio|profile)|verified|top talent|available now)$",
    re.IGNORECASE,
)

_CURRENCY: dict[str, str] = {
    "$": "USD",
    "€": "EUR",
    "£": "GBP",
    "₽": "RUB",
    "usd": "USD",
    "eur": "EUR",
    "gbp": "GBP",
    "rub": "RUB",
}
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_RANGE = re.compile(
    rf"(?P<cur>[$€£₽]|usd|eur|gbp|rub)?\s*(?P<min>{_NUM})\s*(?P<kmin>k\b)?"
    rf"(?:\s*(?:-|–|—|to)\s*(?P<cur2>[$€£₽]|usd|eur|gbp|rub)?\s*(?P<max>{_NUM})\s*(?P<kmax>k\b)?)?"
    r"(?:\s*(?P<cur3>usd|eur|gbp|rub)\b)?",
    re.IGNORECASE,
)
_PER_HOUR = re.compile(r"/\s*(?:hr|hour|h)\b|per hour|an hour|hourly", re.IGNORECASE)
_PER_DAY = re.compile(r"/\s*day\b|per day|daily", re.IGNORECASE)
_PER_MONTH = re.compile(r"/\s*(?:mo|month)\b|per month|monthly", re.IGNORECASE)
_PER_YEAR = re.compile(r"/\s*(?:yr|year)\b|per year|annual|yearly", re.IGNORECASE)
_FIXED = re.compile(r"budget|fixed|project|total", re.IGNORECASE)
_MONEY_MARK = re.compile(r"[$€£₽]\s?\d|\b(?:usd|eur|gbp|rub)\s?\d", re.IGNORECASE)


def _number(value: str, k: str | None) -> float:
    n = float(value.replace(",", ""))
    return n * 1000 if k else n


def _pay_period(line: str, *, fixed_hint: bool) -> CompensationPeriod | None:
    if _PER_HOUR.search(line):
        return CompensationPeriod.hour
    if _PER_DAY.search(line):
        return CompensationPeriod.day
    if _PER_MONTH.search(line):
        return CompensationPeriod.month
    if _PER_YEAR.search(line):
        return CompensationPeriod.year
    if fixed_hint or _FIXED.search(line):
        return CompensationPeriod.project
    return None


def parse_pay(line: str, *, fixed_hint: bool = False) -> Compensation | None:
    """'Rate: $70 - $90/hr' / 'Budget: $20,000' / '€60/hr' → ``Compensation`` (type=rate)."""
    m = _RANGE.search(line)
    if not m:
        return None
    kmin = m.group("kmin") or (m.group("kmax") if m.group("max") else None)
    lo = _number(m.group("min"), kmin)
    hi = _number(m.group("max"), m.group("kmax")) if m.group("max") else None
    cur = m.group("cur") or m.group("cur2") or m.group("cur3")
    return Compensation(
        min=lo,
        max=hi,
        currency=_CURRENCY.get(cur.lower()) if cur else None,
        period=_pay_period(line, fixed_hint=fixed_hint),
        type="rate",
        raw=line,
    )


_HOURLY = re.compile(
    rf"(?P<cur>[$€£₽]|usd|eur|gbp|rub)\s?(?P<amt>{_NUM})\s*(?P<k>k\b)?"
    r"\s*(?:/\s*(?:hr|hour|h)\b|per hour|an hour|hourly)",
    re.IGNORECASE,
)


def _hourly_rate(line: str) -> dict[str, Any] | None:
    """'$90/hr' / 'Rate: USD 90 per hour' → ``{"hourly": 90, "currency": "USD", "raw": line}``."""
    m = _HOURLY.search(line)
    if not m:
        return None
    return {
        "hourly": _number(m.group("amt"), m.group("k")),
        "currency": _CURRENCY.get(m.group("cur").lower()),
        "raw": line,
    }


def _is_prose(line: str) -> bool:
    """Sentence-like line: long, or punctuated and clearly not a name/title."""
    return (
        len(line) > 80
        or bool(_BULLET.match(line))
        or (line.endswith((".", "!", "?", ";")) and " " in line and len(line) > 30)
    )


def _strip_bullet(line: str) -> str:
    return _BULLET.sub("", line, count=1).strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = it.lower()
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


# ------------------------------------------------------------------------------ profile

_H_ABOUT = re.compile(r"^(about|about me|bio|summary|overview|profile summary):?$", re.IGNORECASE)
_H_SKILLS = re.compile(
    r"^(expertise|skills|top skills|core skills|technical skills|skills (?:&|and) expertise)"
    r"\s*(?::\s*(?P<inline>.+))?$",
    re.IGNORECASE,
)
_H_WORK = re.compile(
    r"^(work experience|employment( history)?|professional experience|work history|career):?$",
    re.IGNORECASE,
)
_H_EXPERIENCE = re.compile(r"^experience:?$", re.IGNORECASE)
_H_PORTFOLIO = re.compile(
    r"^(portfolio|project highlights|projects|selected projects|highlights|notable projects):?$",
    re.IGNORECASE,
)
_H_EDUCATION = re.compile(r"^(education|academic background):?$", re.IGNORECASE)
_H_LANGUAGES = re.compile(r"^languages:?$", re.IGNORECASE)
_H_CERTS = re.compile(
    r"^(certifications?|certificates|licenses(?: (?:&|and) certifications)?):?$", re.IGNORECASE
)
_H_AVAILABILITY = re.compile(r"^(availability|available for):?$", re.IGNORECASE)
_H_RATE = re.compile(r"^(rate|hourly rate|rates):?$", re.IGNORECASE)
_H_SKIP = re.compile(
    r"^(contact|recommendations|testimonials|reviews|industry expertise|interests|hobbies|"
    r"references|awards|publications|talks|volunteering|work preferences):?$",
    re.IGNORECASE,
)
# Sub-headers of Toptal's categorised "Skills" section — never skills themselves.
_SKILL_SUBHEADERS = re.compile(
    r"^(languages|frameworks|libraries(?:/apis)?|tools|paradigms|platforms|storage|other|"
    r"databases|cloud|methodologies|industry expertise|technologies):?$",
    re.IGNORECASE,
)

_LOCATION = re.compile(r"^(?:location|based in|located in|lives in)\s*:?\s*(?P<v>.+)$", re.I)
_CITY_COUNTRY = re.compile(r"^[A-Z][A-Za-z .'\-]{1,40},\s*[A-Z][A-Za-z .'\-]{1,40}$")
_AVAILABILITY = re.compile(r"^(?:availability|available for|available)\s*[:—–-]\s*(?P<v>.+)$", re.I)
_MEMBER_SINCE = re.compile(r"^(?:toptal )?member since\s*:?\s*(?P<v>.+)$", re.IGNORECASE)
_TECH_LINE = re.compile(r"^(?:technologies|tech stack|tools|stack)\s*:", re.IGNORECASE)


def _profile_header(line: str, section: str | None, has_work_header: bool) -> str | None:
    """Section name a line opens, or ``None``. Context-sensitive for Toptal's ambiguous labels."""
    if section == "skills" and _SKILL_SUBHEADERS.match(line):
        return None  # categorised skills: "Languages" here means programming languages
    if _H_ABOUT.match(line):
        return "about"
    if _H_SKILLS.match(line):
        return "skills"
    if _H_WORK.match(line):
        return "work"
    if _H_EXPERIENCE.match(line):
        # Toptal: "Work Experience" = jobs, bare "Experience" = project highlights
        return "portfolio" if has_work_header else "work"
    if _H_PORTFOLIO.match(line):
        return "portfolio"
    if _H_EDUCATION.match(line):
        return "education"
    if _H_LANGUAGES.match(line):
        return "languages"
    if _H_CERTS.match(line):
        return "certifications"
    if _H_AVAILABILITY.match(line):
        return "availability"
    if _H_RATE.match(line):
        return "rate"
    if _H_SKIP.match(line):
        return "skip"
    return None


@dataclass(slots=True)
class _Job:
    title: str | None = None
    company: str | None = None
    period: str | None = None
    lines: list[str] = field(default_factory=list)

    def to_item(self) -> SnapshotExperienceItem:
        title, company = self.title, self.company
        if company is None and title:
            t, c = parsers.guess_title_company(title)
            if c:
                title, company = t, c
        return SnapshotExperienceItem(
            company=company or "",
            title=title,
            period=self.period,
            description="\n".join(self.lines) or None,
        )


def _parse_work(lines: list[str]) -> list[SnapshotExperienceItem]:
    """Entries = ``Title / Company / period`` or ``Title / period / Company`` + bullets/prose."""
    jobs: list[_Job] = []
    pending: list[str] = []
    cur: _Job | None = None
    for line in lines:
        if _BULLET.match(line) or _TECH_LINE.match(line) or _is_prose(line):
            if cur is None:
                cur = _Job(title=pending.pop(0) if pending else None)
                jobs.append(cur)
                if pending:
                    cur.company, pending = pending[0], pending[1:]
            cur.lines.append(_strip_bullet(line))
            continue
        if parsers.looks_like_period(line):
            if cur is not None and cur.period is None and not pending and not cur.lines:
                cur.period = line  # Title / period / Company
                continue
            if len(pending) >= 2:
                if cur is not None:
                    cur.lines.extend(pending[:-2])  # stray trailing lines of the previous entry
                cur = _Job(title=pending[-2], company=pending[-1], period=line)
            else:
                cur = _Job(title=pending[-1] if pending else None, period=line)
            jobs.append(cur)
            pending = []
            continue
        if _URL_LINE.match(line):
            continue
        if cur is not None and cur.company is None and not cur.lines and not pending:
            cur.company = line  # Title / period / Company
            continue
        pending.append(line)
    if pending:
        title = pending[0]
        company = pending[1] if len(pending) > 1 else None
        jobs.append(_Job(title=title, company=company, lines=pending[2:]))
    return [j.to_item() for j in jobs if j.title or j.company]


def _parse_items(lines: list[str]) -> list[dict[str, Any]]:
    """Portfolio / project highlights: short line = name, prose = description, URL = url."""
    items: list[dict[str, Any]] = []
    for line in lines:
        urls = parsers.find_urls(line)
        if urls and (_URL_LINE.match(line) or len(line) <= 120):
            if items:
                items[-1].setdefault("url", urls[0])
            continue
        if items and (_is_prose(line) or _BULLET.match(line)):
            desc = items[-1].get("description")
            piece = _strip_bullet(line)
            items[-1]["description"] = f"{desc} {piece}" if desc else piece
            continue
        items.append({"name": line})
    return items


def _parse_education(lines: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    pending_period: str | None = None
    for line in lines:
        if parsers.looks_like_period(line):
            if cur:
                cur["period"] = line
                items.append(cur)
                cur = {}
            else:
                pending_period = line
            continue
        if pending_period and not cur:
            cur["period"], pending_period = pending_period, None
        if "degree" not in cur:
            cur["degree"] = line
        elif "institution" not in cur:
            cur["institution"] = line
        else:
            cur["details"] = f"{cur['details']} {line}" if "details" in cur else line
    if cur:
        items.append(cur)
    return items


def parse_profile(text: str) -> ProfileRead:
    """Public talent profile / portal profile editor text → ``ProfileRead``.

    Falls back to ``parsers.generic_profile`` when neither a Toptal header line nor a section
    header is recognised.
    """
    lines = [ln for ln in parsers.split_lines(text) if not _NAV_NOISE.match(ln)]
    has_work_header = any(_H_WORK.match(ln) for ln in lines)
    name: str | None = None
    headline: str | None = None
    location: str | None = None
    availability: str | None = None
    member_since: str | None = None
    rates: dict[str, Any] | None = None
    lead: list[str] = []

    i = 0
    while i < len(lines) and _profile_header(lines[i], None, has_work_header) is None:
        line = lines[i]
        i += 1
        if _URL_LINE.match(line):
            continue
        if m := _LOCATION.match(line):
            location = m.group("v").strip()
        elif m := _AVAILABILITY.match(line):
            availability = m.group("v").strip()
        elif m := _MEMBER_SINCE.match(line):
            member_since = m.group("v").strip()
        elif len(line) <= 40 and (rate := _hourly_rate(line)):
            rates = rate
        elif name is None and not _is_prose(line):
            name = line
        elif headline is None and not _is_prose(line):
            headline = line
        elif location is None and _CITY_COUNTRY.match(line):
            location = line
        elif _is_prose(line) or lead:
            lead.append(line)  # unlabelled bio paragraph under the header

    about_lines: list[str] = []
    skills: list[str] = []
    work: list[str] = []
    portfolio_lines: list[str] = []
    education_lines: list[str] = []
    languages: list[str] = []
    certifications: list[str] = []
    section: str | None = None
    sections_seen = 0
    for line in lines[i:]:
        header = _profile_header(line, section, has_work_header)
        if header is not None:
            section = header
            sections_seen += 1
            if header == "skills" and (m := _H_SKILLS.match(line)) and m.group("inline"):
                skills.extend(parsers.split_skills(m.group("inline")))
            continue
        if section == "about":
            about_lines.append(line)
        elif section == "skills":
            if not _SKILL_SUBHEADERS.match(line):
                skills.extend(parsers.split_skills(line))
        elif section == "work":
            work.append(line)
        elif section == "portfolio":
            portfolio_lines.append(line)
        elif section == "education":
            education_lines.append(line)
        elif section == "languages":
            languages.append(line)
        elif section == "certifications":
            certifications.append(line)
        elif section == "availability":
            if availability is None:
                availability = line
        elif section == "rate" and rates is None:
            rates = _hourly_rate(line)

    recognised = sections_seen > 0 or any((location, availability, member_since, rates))
    if not recognised:
        return parsers.generic_profile(text, PLATFORM)

    preferences: dict[str, Any] = {}
    for key, value in (
        ("name", name),
        ("location", location),
        ("member_since", member_since),
        ("education", _parse_education(education_lines)),
        ("languages", languages),
        ("certifications", certifications),
    ):
        if value:
            preferences[key] = value
    url = _TOPTAL_URL.search(text)
    return ProfileRead(
        platform=PLATFORM,
        capture_method=CaptureMethod.paste,
        profile_url=url.group(0).rstrip(".,;") if url else None,
        headline=headline,
        about=" ".join(about_lines or lead) or None,
        experience=_parse_work(work),
        skills=_dedupe(skills),
        portfolio=_parse_items(portfolio_lines),
        rates=rates,
        availability=availability,
        preferences=preferences,
        raw_text=text,
    )


# ------------------------------------------------------------------------------ jobs

_CARD_END = re.compile(
    r"^(apply|apply now|applied|view (job|details)|not interested|save|saved|share)$",
    re.IGNORECASE,
)
_PAGE_NOISE = re.compile(
    r"^(jobs|all jobs|my jobs|recommended( for you)?|new|featured|hot|urgent|open jobs|"
    r"filters?|sort by.*|\d+ (open )?jobs?( match.*)?|showing .*|load more)$",
    re.IGNORECASE,
)
_CLIENT = re.compile(r"^(?P<client>[^·•|]+?)\s*[·•|]\s*(?P<industry>[^·•|]+)$")
_CLIENT_KV = re.compile(r"^client\s*:\s*(?P<v>.+)$", re.IGNORECASE)
_INDUSTRY_KV = re.compile(r"^industry\s*:\s*(?P<v>.+)$", re.IGNORECASE)
_ENGAGEMENT = re.compile(r"^(?:engagement|commitment|engagement type)\s*:\s*(?P<v>.+)$", re.I)
_BARE_ENGAGEMENT = re.compile(r"^(full[- ]?time|part[- ]?time|hourly)\b.*$", re.IGNORECASE)
_HOURS_WEEK = re.compile(r"(\d+)\s*(?:hrs?|hours)\s*(?:/|per|a)\s*(?:week|wk)", re.IGNORECASE)
_DURATION = re.compile(r"^(?:duration|length|term)\s*:\s*(?P<v>.+)$", re.IGNORECASE)
_POSTED = re.compile(r"^(?:posted|published|opened|added|listed)\b", re.IGNORECASE)
_SKILLS_KV = re.compile(
    r"^(?:skills|technologies|tech stack|required skills|must have|stack)\s*:\s*(?P<v>.*)$",
    re.IGNORECASE,
)
_PAY_KV = re.compile(
    r"^(?P<key>rate|hourly rate|budget|fixed price|fixed budget|compensation|pay|price)"
    r"\s*:\s*(?P<v>.+)$",
    re.IGNORECASE,
)

_REMOTE = re.compile(r"^remote(?:\s*(?:[—–\-,:(]|only)\s*(?P<q>.+?)\)?)?$", re.IGNORECASE)
_ONSITE = re.compile(
    r"^(?:on-?site|in[- ]office|in[- ]person)(?:\s*(?:[—–\-,:]|in)\s*(?P<loc>.+))?$",
    re.IGNORECASE,
)
_HYBRID = re.compile(r"^hybrid(?:\s*(?:[—–\-,:]|in)\s*(?P<loc>.+))?$", re.IGNORECASE)
_LOC_SUFFIX = re.compile(
    r"^(?P<loc>.+?)\s*[(\-—–,]\s*(?P<mode>remote|on-?site|hybrid)\)?$", re.IGNORECASE
)
_GLOBAL_Q = re.compile(r"\b(worldwide|anywhere|global|any time ?zone)\b", re.IGNORECASE)
_REGION_TOKENS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "US",
        re.compile(
            r"\b(us|usa|u\.s\.|united states|est|edt|pst|pdt|cst|cdt|mst|mdt|et|pt|"
            r"eastern time|pacific time|central time|mountain time)\b",
            re.IGNORECASE,
        ),
    ),
    ("CA", re.compile(r"\b(canada|canadian)\b", re.IGNORECASE)),
    ("UK", re.compile(r"\b(uk|united kingdom|britain|british|gmt|bst)\b", re.IGNORECASE)),
    ("EU", re.compile(r"\b(eu|europe|european|cet|cest|eet)\b", re.IGNORECASE)),
    ("LATAM", re.compile(r"\b(latam|latin america)\b", re.IGNORECASE)),
    ("APAC", re.compile(r"\b(apac|asia|asia[- ]pacific)\b", re.IGNORECASE)),
    ("AU", re.compile(r"\b(australia|aest|aedt)\b", re.IGNORECASE)),
    ("IN", re.compile(r"\b(india|ist)\b", re.IGNORECASE)),
)


def _regions(qualifier: str) -> list[str]:
    return [code for code, rx in _REGION_TOKENS if rx.search(qualifier)]


@dataclass(slots=True)
class _Loc:
    policy: RemotePolicy
    location: str
    regions: list[str] = field(default_factory=list)
    timezone: str | None = None


def parse_location_line(line: str) -> _Loc | None:
    """'Remote' / 'Remote — US hours' / 'On-site, Berlin' / 'Hybrid, Warsaw' / 'City (Remote)'."""
    if m := _REMOTE.match(line):
        q = (m.group("q") or "").strip(" ()")
        if not q or _GLOBAL_Q.search(q):
            return _Loc(RemotePolicy.remote_global, line)
        return _Loc(RemotePolicy.remote_region, line, _regions(q), q)
    if m := _ONSITE.match(line):
        return _Loc(RemotePolicy.onsite, (m.group("loc") or line).strip())
    if m := _HYBRID.match(line):
        return _Loc(RemotePolicy.hybrid, (m.group("loc") or line).strip())
    if m := _LOC_SUFFIX.match(line):
        mode = m.group("mode").lower()
        policy = (
            RemotePolicy.remote_global
            if mode == "remote"
            else RemotePolicy.hybrid
            if mode == "hybrid"
            else RemotePolicy.onsite
        )
        return _Loc(policy, m.group("loc").strip())
    return None


def _employment(engagement: str) -> EmploymentType | None:
    low = engagement.lower()
    if re.search(r"full[- ]?time", low):
        return EmploymentType.full_time
    if re.search(r"part[- ]?time", low):
        return EmploymentType.part_time
    return None


def _cards(text: str) -> list[list[str]]:
    """Paragraph blocks, additionally cut after card-terminating buttons ('Apply', …)."""
    out: list[list[str]] = []
    for block in parsers.blocks(text):
        cur: list[str] = []
        for line in block:
            cur.append(line)
            if _CARD_END.match(line):
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
    return out


def _parse_card(lines: list[str], *, now: datetime | None) -> JobPosting | None:
    content = [ln for ln in lines if not (_PAGE_NOISE.match(ln) or _CARD_END.match(ln))]
    if not content:
        return None
    title = content[0]
    client: str | None = None
    industry: str | None = None
    engagement: str | None = None
    duration: str | None = None
    posted_raw: str | None = None
    posted_at: datetime | None = None
    comp: Compensation | None = None
    loc: _Loc | None = None
    technologies: list[str] = []
    rest: list[str] = []
    want_skills = False
    for line in content[1:]:
        if want_skills:
            want_skills = False
            if not _URL_LINE.match(line):
                technologies.extend(parsers.split_skills(line))
                continue
        if _URL_LINE.match(line):
            continue
        if m := _ENGAGEMENT.match(line):
            engagement = m.group("v").strip()
        elif m := _DURATION.match(line):
            duration = m.group("v").strip()
        elif _POSTED.match(line):
            posted_raw = line
            posted_at = parsers.parse_date(line, now=now)
        elif m := _SKILLS_KV.match(line):
            inline = m.group("v").strip()
            if inline:
                technologies.extend(parsers.split_skills(inline))
            else:
                want_skills = True
        elif m := _PAY_KV.match(line):
            fixed = m.group("key").lower() in {"budget", "fixed price", "fixed budget", "price"}
            comp = parse_pay(line, fixed_hint=fixed) or comp
        elif m := _CLIENT_KV.match(line):
            client = m.group("v").strip()
        elif m := _INDUSTRY_KV.match(line):
            industry = m.group("v").strip()
        elif found := parse_location_line(line):
            loc = found
        elif _MONEY_MARK.search(line) and len(line) <= 60:
            comp = parse_pay(line) or comp
        elif len(line) <= 40 and _BARE_ENGAGEMENT.match(line):
            engagement = line
        else:
            rest.append(line)
    if rest:
        if m := _CLIENT.match(rest[0]):
            client = client or m.group("client").strip()
            industry = industry or m.group("industry").strip()
        elif client is None and len(rest[0]) <= 60 and not _is_prose(rest[0]):
            client = rest[0]
    if client is None:
        t, c = parsers.guess_title_company(title)
        if c and t:
            title, client = t, c
    recognised = any(
        v is not None for v in (engagement, duration, posted_raw, comp, industry, loc)
    ) or bool(technologies)
    if not recognised:
        return None

    hours = _HOURS_WEEK.search(engagement) if engagement else None
    payload: dict[str, Any] = {}
    for key, value in (
        ("industry", industry),
        ("engagement", engagement),
        ("hours_per_week", int(hours.group(1)) if hours else None),
        ("duration", duration),
        ("posted", posted_raw),
    ):
        if value is not None:
            payload[key] = value
    urls = parsers.find_urls("\n".join(lines))
    extraction = OpportunityExtraction(
        title=title[:300],
        company=client,
        contract_type=ContractType.freelance,
        employment_type=_employment(engagement) if engagement else None,
        location=loc.location if loc else None,
        remote_policy=loc.policy if loc else RemotePolicy.unknown,
        remote_regions=list(loc.regions) if loc else [],
        timezone_range=loc.timezone if loc else None,
        compensation=comp,
        technologies=_dedupe(technologies),
        summary=None,
    )
    return JobPosting(
        platform=PLATFORM,
        url=urls[0] if urls else None,
        title=title[:300],
        company=client,
        location=loc.location if loc else None,
        posted_at=posted_at,
        raw_text="\n".join(lines),
        extraction=extraction,
        raw_payload=payload or None,
    )


def parse_jobs(text: str, *, now: datetime | None = None, limit: int = 100) -> list[JobPosting]:
    """Talent-portal 'Jobs' list → one ``JobPosting`` per recognised card.

    Cards with no portal marker (engagement, duration, rate/budget, posted, skills, client ·
    industry, location line) are page chrome and are skipped; when *no* card is recognised the
    text is handed to ``parsers.generic_jobs``. ``now`` anchors relative dates ('Posted 2 days
    ago').
    """
    out: list[JobPosting] = []
    for card in _cards(text):
        job = _parse_card(card, now=now)
        if job is not None:
            out.append(job)
            if len(out) >= limit:
                break
    if not out:
        return parsers.generic_jobs(text, PLATFORM, limit=limit)
    return out


# ------------------------------------------------------------------------------ applications

_APP_NOISE = re.compile(
    r"^(my applications|applications|(active|archived|past|all|open)(\s*\(\d+\))?|"
    r"view( job| details)?|withdraw( application)?|message( client)?|\d+ applications?|"
    r"showing .*|load more)$",
    re.IGNORECASE,
)
_STAGE = re.compile(r"^(?:stage|status)\s*:\s*(?P<v>.+)$", re.IGNORECASE)
_APPLIED = re.compile(r"^(?:applied|submitted|sent)\b", re.IGNORECASE)
_UPDATED = re.compile(r"^(?:updated|last (?:updated|activity)|status changed)\b", re.IGNORECASE)

# Toptal's stage wording → normalized status; anything else goes through the shared rules.
STAGE_STATUS: dict[str, ApplicationStatus] = {
    "applied": ApplicationStatus.applied,
    "under review": ApplicationStatus.viewed,
    "in review": ApplicationStatus.viewed,
    "interviewing": ApplicationStatus.interview,
    "matched": ApplicationStatus.offer,
    "declined": ApplicationStatus.rejected,
    "closed": ApplicationStatus.rejected,
    "withdrawn": ApplicationStatus.withdrawn,
}


def _is_app_field(line: str) -> bool:
    return bool(_STAGE.match(line) or _APPLIED.match(line) or _UPDATED.match(line))


def _is_app_noise(line: str) -> bool:
    """Chrome line, including tab strips such as 'Active (4) · Archived (1)'."""
    parts = [p for p in _SEP.split(line) if p]
    return bool(parts) and all(_APP_NOISE.match(p) for p in parts)


def _is_inline_row(line: str) -> bool:
    return len(_SEP.split(line)) >= 3 and any(_is_app_field(p) for p in _SEP.split(line))


def _rows(text: str) -> list[tuple[list[str], list[str]]]:
    """(verbatim row lines, parse lines) — rows split on blank lines, single-line '·' rows, or
    the first non-field line after a row already carrying a stage/applied line."""
    out: list[tuple[list[str], list[str]]] = []
    for block in parsers.blocks(text):
        cur: list[str] = []
        closed = False  # current row already has its stage / applied line
        for line in block:
            if _is_inline_row(line):
                if cur:
                    out.append((cur, cur))
                    cur, closed = [], False
                out.append(([line], [p for p in _SEP.split(line) if p]))
                continue
            if closed and not (_is_app_field(line) or _is_app_noise(line)):
                out.append((cur, cur))
                cur, closed = [], False
            cur.append(line)
            closed = closed or _is_app_field(line)
        if cur:
            out.append((cur, cur))
    return out


def _stage_status(value: str) -> ApplicationStatus:
    key = value.strip().lower()
    if key in STAGE_STATUS:
        return STAGE_STATUS[key]
    return parsers.normalize_status(key)


def _parse_row(
    raw_lines: list[str], lines: list[str], *, now: datetime | None
) -> ApplicationObservationIn | None:
    content = [ln for ln in lines if not _is_app_noise(ln)]
    stage_line: str | None = None
    status_value = ""
    applied_at: datetime | None = None
    updated_at: datetime | None = None
    before: list[str] = []  # free lines preceding the first field line: title, client
    after: list[str] = []
    seen_field = False
    for line in content:
        if _URL_LINE.match(line):
            continue
        if (m := _STAGE.match(line)) and stage_line is None:
            stage_line = line
            status_value = m.group("v")
            seen_field = True
        elif _APPLIED.match(line):
            applied_at = applied_at or parsers.parse_date(line, now=now)
            seen_field = True
        elif _UPDATED.match(line):
            updated_at = updated_at or parsers.parse_date(line, now=now)
            seen_field = True
        elif seen_field:
            after.append(line)
        else:
            before.append(line)
    if stage_line is None and applied_at is None:
        return None
    head = before[-2:] if before else after[:1]
    if not head:
        return None
    title = head[0]
    company = head[1] if len(head) > 1 and len(head[1]) <= 60 and not _is_prose(head[1]) else None
    rest = [*before, *after]
    if company is None:
        t, c = parsers.guess_title_company(title)
        if c and t:
            title, company = t, c
    if stage_line is not None:
        status, status_raw = _stage_status(status_value), stage_line
    else:
        status, status_raw = parsers.best_status(rest[1:])
    urls = parsers.find_urls("\n".join(raw_lines))
    return ApplicationObservationIn(
        platform=PLATFORM,
        job_title=title[:300],
        company=company,
        job_url=urls[0] if urls else None,
        status_raw=status_raw,
        status=status,
        applied_at=applied_at,
        updated_at_platform=updated_at,
        raw_payload={"lines": list(raw_lines)},
    )


def parse_applications(text: str, *, now: datetime | None = None) -> list[ApplicationObservationIn]:
    """'My Applications' list → one observation per row carrying a stage or applied line.

    Rows without either marker are page chrome and are skipped; when no row is recognised the
    text goes to ``parsers.generic_applications``. ``now`` anchors relative dates.
    """
    out: list[ApplicationObservationIn] = []
    for raw_lines, lines in _rows(text):
        obs = _parse_row(raw_lines, lines, now=now)
        if obs is not None:
            out.append(obs)
    if not out:
        return parsers.generic_applications(text, PLATFORM, now=now)
    return out
