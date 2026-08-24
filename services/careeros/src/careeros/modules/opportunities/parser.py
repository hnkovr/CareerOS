"""Deterministic heuristics that turn pasted text into an ``OpportunityExtraction``.

Good enough for triage on its own; AI extraction (``opportunity_extract`` prompt) fills gaps when
enabled. Raw text is never modified or discarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from careeros.modules.cv.keywords import extract_known_tech, normalize
from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
    Seniority,
)
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction, Recruiter

_SECTION_HEADERS = {
    "requirements": (
        "requirements",
        "must have",
        "must-have",
        "what you bring",
        "you have",
        "qualifications",
        "required skills",
        "what we expect",
    ),
    "preferred": ("nice to have", "nice-to-have", "preferred", "bonus", "plus", "good to have"),
    "responsibilities": (
        "responsibilities",
        "what you'll do",
        "what you will do",
        "you will",
        "the role",
        "your mission",
    ),
}

_REMOTE_GLOBAL = (
    "remote worldwide",
    "remote (worldwide)",
    "fully remote",
    "100% remote",
    "remote-first",
    "remote first",
    "work from anywhere",
    "anywhere in the world",
    "remote, anywhere",
    "remote – anywhere",
    "remote - anywhere",
    "global remote",
)
_REMOTE_REGION_HINTS = {
    "US": (
        "us only",
        "u.s. only",
        "united states only",
        "must be located in the us",
        "us-based",
        "us based",
        "within the us",
        "us citizens",
        "authorized to work in the us",
        "remote (us)",
        "remote - us",
        "remote us",
        "usa only",
    ),
    "EU": (
        "eu only",
        "europe only",
        "within the eu",
        "eu-based",
        "eu based",
        "european union",
        "remote (eu)",
        "remote - eu",
        "remote eu",
        "emea",
        "european time",
        "cet",
        "cest",
    ),
    "UK": ("uk only", "uk-based", "uk based"),
    "PL": (
        "poland",
        "polska",
        "warsaw",
        "kraków",
        "krakow",
        "wrocław",
        "wroclaw",
        "gdańsk",
        "gdansk",
        "poznań",
        "poznan",
    ),
    "GE": ("georgia", "tbilisi", "batumi"),
    "LATAM": ("latam", "latin america"),
    "APAC": ("apac", "asia pacific"),
}
_HYBRID = ("hybrid",)
_ONSITE = (
    "on-site",
    "onsite",
    "on site",
    "in-office",
    "in office",
    "relocation required",
    "relocate to",
)

_B2B = (
    "b2b",
    "contractor",
    "contract basis",
    "independent contractor",
    "1099",
    "umowa b2b",
    "consulting agreement",
)
_FREELANCE = (
    "freelance",
    "hourly",
    "fixed-price",
    "fixed price",
    "upwork",
    "gig",
    "short-term project",
    "project-based",
)
_C2H = ("contract-to-hire", "contract to hire", "temp to perm")
_PART_TIME = ("part-time", "part time", "20 hours", "hours per week")

_SENIORITY = (
    (Seniority.principal, ("principal",)),
    (Seniority.staff, ("staff engineer", "staff data")),
    (Seniority.lead, ("lead ", "tech lead", "team lead", "head of")),
    (Seniority.senior, ("senior", "sr.", "sr ")),
    (Seniority.junior, ("junior", "jr.", "entry level", "entry-level", "intern")),
    (Seniority.mid, ("mid-level", "mid level", "middle", "intermediate")),
)

_RED_FLAGS = (
    ("unpaid", "unpaid"),
    ("equity only", "equity-only compensation"),
    ("equity-only", "equity-only compensation"),
    ("rockstar", "buzzword-heavy posting"),
    ("ninja", "buzzword-heavy posting"),
    ("urgent requirement", "agency-style urgent requirement"),
    ("immediate joiner", "agency-style posting"),
    ("rate: doe", "compensation hidden (DOE)"),
    ("competitive salary", "compensation not disclosed"),
    ("unlimited pto", "watch for overwork culture signals"),
    ("fast-paced", "possible overwork signal"),
    ("wear many hats", "scope creep risk"),
)

_MONEY_RE = re.compile(
    r"(?P<cur>\$|€|£|usd|eur|gbp|pln|chf)?\s?(?P<min>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<kmin>k)?"
    r"(?:\s?(?:-|–|—|to)\s?(?P<cur2>\$|€|£)?\s?(?P<max>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<kmax>k)?)?"
    r"\s?(?P<cur3>usd|eur|gbp|pln|chf)?\s?(?P<per>/\s?(?:hr|hour|h|year|yr|annum|month|mo|day)|per\s(?:hour|year|annum|month|day)|an?\s(?:hour|year)|hourly|annually|p\.a\.)?",
    re.IGNORECASE,
)
_CURRENCY = {
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "pln": "PLN",
    "chf": "CHF",
}
_PERIOD = (
    (CompensationPeriod.hour, ("hr", "hour", "/h", "hourly")),
    (CompensationPeriod.day, ("day",)),
    (CompensationPeriod.month, ("month", "mo")),
    (CompensationPeriod.year, ("year", "yr", "annum", "annually", "p.a.")),
)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TITLE_PATTERNS = (
    re.compile(
        r"^(?:job title|title|role|position)\s*[:\-–]\s*(.+)$", re.IGNORECASE | re.MULTILINE
    ),
    re.compile(
        r"^(?:hiring|we are hiring|we're hiring)\s*[:\-–]?\s*(?:an?\s+)?(.+)$",
        re.IGNORECASE | re.MULTILINE,
    ),
)
_COMPANY_PATTERNS = (
    re.compile(r"^(?:company|employer|client)\s*[:\-–]\s*(.+)$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\b(?:at|@)\s+([A-Z][\w&.-]+(?:\s+[A-Z][\w&.-]+){0,3})\b"),
    re.compile(
        r"^([A-Z][\w&.-]+(?:\s+[A-Z][\w&.-]+){0,3})\s+is\s+(?:hiring|looking for)",
        re.IGNORECASE | re.MULTILINE,
    ),
)
_ROLE_WORDS = (
    "engineer",
    "developer",
    "architect",
    "analyst",
    "consultant",
    "lead",
    "manager",
    "scientist",
    "specialist",
)


@dataclass
class ParseResult:
    extraction: OpportunityExtraction
    confidence: float
    parser: str = "heuristic-v1"


def _num(value: str, k: str | None) -> float:
    n = float(value.replace(",", ""))
    return n * 1000 if k else n


def parse_compensation(text: str) -> Compensation | None:
    best: Compensation | None = None
    for m in _MONEY_RE.finditer(text):
        cur = m.group("cur") or m.group("cur2") or m.group("cur3")
        per = m.group("per")
        if not cur and not per and not (m.group("kmin") or m.group("kmax")):
            continue
        try:
            lo = _num(
                m.group("min"), m.group("kmin") or (m.group("kmax") if m.group("max") else None)
            )
            hi = _num(m.group("max"), m.group("kmax")) if m.group("max") else None
        except ValueError:
            continue
        if lo < 10 and not per:
            continue
        period = None
        if per:
            per_l = per.lower()
            for p, hints in _PERIOD:
                if any(h in per_l for h in hints):
                    period = p
                    break
        if period is None:
            period = (
                CompensationPeriod.hour
                if lo < 500
                else CompensationPeriod.year
                if lo > 20000
                else CompensationPeriod.month
            )
        if period == CompensationPeriod.year and lo < 1000:
            lo, hi = lo * 1000, hi * 1000 if hi else None
        prefix = text[max(0, m.start() - 12) : m.start()].lower()
        if hi is None and ("up to" in prefix or "max" in prefix):
            lo, hi = None, lo
        comp = Compensation(
            min=lo,
            max=hi,
            currency=_CURRENCY.get(cur.lower()) if cur else None,
            period=period,
            type="rate"
            if period in (CompensationPeriod.hour, CompensationPeriod.day)
            else "salary",
            raw=m.group(0).strip(),
        )
        if (
            best is None
            or (best.currency is None and comp.currency)
            or (best.max is None and comp.max)
        ):
            best = comp
    return best


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"requirements": [], "preferred": [], "responsibilities": []}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip().strip("*").strip()
        if not line:
            continue
        lowered = line.lower().rstrip(":")
        header = None
        if len(lowered) <= 60:
            for key, hints in _SECTION_HEADERS.items():
                if any(lowered == h or lowered.startswith(h) for h in hints):
                    header = key
                    break
        if header:
            current = header
            continue
        if current and (line.startswith(("-", "•", "·", "*", "–")) or re.match(r"^\d+[.)]", line)):
            out[current].append(line.lstrip("-•·*– ").strip())
        elif current and len(out[current]) == 0 and len(line) < 160:
            out[current].append(line)
    return out


def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(1).strip().rstrip(".,;")
    return None


def _guess_title(text: str) -> str | None:
    explicit = _first_match(_TITLE_PATTERNS, text)
    if explicit:
        return explicit[:150]
    for line in text.splitlines()[:8]:
        cand = line.strip().strip("#*").strip()
        if 6 <= len(cand) <= 120 and any(w in cand.lower() for w in _ROLE_WORDS):
            return cand
    return None


def parse_text(text: str, vocab: dict[str, str], *, url: str | None = None) -> ParseResult:
    norm = normalize(text)
    lowered = text.lower()
    ex = OpportunityExtraction()
    signals = 0

    ex.title = _guess_title(text)
    signals += ex.title is not None
    ex.company = _first_match(_COMPANY_PATTERNS, text)
    if ex.company and any(w in ex.company.lower() for w in ("least", "the", "a ", "our")):
        ex.company = None
    signals += ex.company is not None

    if any(h in lowered for h in _REMOTE_GLOBAL):
        ex.remote_policy = RemotePolicy.remote_global
    regions = [
        code for code, hints in _REMOTE_REGION_HINTS.items() if any(h in lowered for h in hints)
    ]
    if any(h in lowered for h in _ONSITE):
        ex.remote_policy = RemotePolicy.onsite
    elif any(h in lowered for h in _HYBRID):
        ex.remote_policy = RemotePolicy.hybrid
    elif "remote" in lowered and ex.remote_policy == RemotePolicy.unknown:
        ex.remote_policy = RemotePolicy.remote_region if regions else RemotePolicy.remote_global
    ex.remote_regions = regions
    if ex.remote_policy != RemotePolicy.unknown:
        signals += 1

    if any(h in lowered for h in _C2H):
        ex.contract_type = ContractType.contract_to_hire
    elif any(h in lowered for h in _B2B):
        ex.contract_type = ContractType.b2b
    elif any(h in lowered for h in _FREELANCE):
        ex.contract_type = ContractType.freelance
    elif any(h in lowered for h in ("full-time", "full time", "permanent", "employee")):
        ex.contract_type = ContractType.employment
    if ex.contract_type is not None:
        signals += 1
    if any(h in lowered for h in _PART_TIME):
        ex.employment_type = EmploymentType.part_time
    elif ex.contract_type == ContractType.freelance:
        ex.employment_type = EmploymentType.project
    elif ex.contract_type is not None:
        ex.employment_type = EmploymentType.full_time

    for sen, hints in _SENIORITY:
        if any(h in lowered for h in hints):
            ex.seniority = sen
            break
    signals += ex.seniority is not None

    ex.compensation = parse_compensation(text)
    signals += ex.compensation is not None

    tz = re.search(r"\b(utc|gmt|cet|cest|est|pst|pt|et)\s?([+-]\s?\d{1,2})?\b", lowered)
    if tz:
        ex.timezone_range = tz.group(0).upper()
    overlap = re.search(r"(\d)\s?(?:hours?|hrs?)\s+(?:of\s+)?overlap", lowered)
    if overlap:
        ex.timezone_range = (
            ex.timezone_range + "; " if ex.timezone_range else ""
        ) + f"{overlap.group(1)}h overlap"

    sections = _sections(text)
    ex.requirements = sections["requirements"][:15]
    ex.preferred = sections["preferred"][:10]
    ex.responsibilities = sections["responsibilities"][:10]
    signals += bool(ex.requirements)

    ex.technologies = extract_known_tech(text, vocab)
    signals += bool(ex.technologies)

    email = _EMAIL_RE.search(text)
    if email:
        ex.recruiter = Recruiter(email=email.group(0))

    ex.red_flags = sorted({label for needle, label in _RED_FLAGS if needle in lowered})
    if not ex.company:
        ex.red_flags.append("company not identified")
    if ex.remote_policy == RemotePolicy.unknown:
        ex.red_flags.append("remote policy unclear")
    if ex.compensation is None:
        ex.red_flags.append("compensation not stated")

    first_para = next(
        (p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) > 60), text[:300]
    )
    ex.summary = re.sub(r"\s+", " ", first_para)[:400]
    if url and not ex.title:
        ex.title = url
    confidence = round(min(1.0, signals / 8), 2)
    _ = norm
    return ParseResult(ex, confidence)


def merge_extractions(
    base: OpportunityExtraction, override: OpportunityExtraction
) -> OpportunityExtraction:
    """AI (override) fills gaps in heuristics (base); lists are unioned."""
    merged = base.model_copy(deep=True)
    for field_name in (
        "title",
        "company",
        "contract_type",
        "employment_type",
        "location",
        "timezone_range",
        "seniority",
        "deadline",
        "summary",
        "recruiter",
    ):
        if getattr(merged, field_name) in (None, "") and getattr(override, field_name) not in (
            None,
            "",
        ):
            setattr(merged, field_name, getattr(override, field_name))
    if (
        merged.remote_policy == RemotePolicy.unknown
        and override.remote_policy != RemotePolicy.unknown
    ):
        merged.remote_policy = override.remote_policy
    if (merged.compensation is None or merged.compensation.is_empty()) and override.compensation:
        merged.compensation = override.compensation
    for list_name in (
        "remote_regions",
        "requirements",
        "preferred",
        "technologies",
        "responsibilities",
        "red_flags",
    ):
        seen: dict[str, None] = {}
        for item in [*getattr(merged, list_name), *getattr(override, list_name)]:
            seen.setdefault(item.strip(), None)
        setattr(merged, list_name, [k for k in seen if k])
    return merged
