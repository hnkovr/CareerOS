"""RocketHunt vacancy page → ``JobPosting``: JSON-LD first, the embedded RSC record second.

Two deterministic passes, no LLM and no invention:

1. the ``JobPosting`` JSON-LD block (title, company, description markdown, employment type,
   location, ``baseSalary``, dates, skills, ``qualifications``, the vacancy uuid);
2. the React-Server-Components state the page ships in ``self.__next_f.push([1, "…"])`` — the
   *vacancy record* only, located by its uuid, never the whole payload and never the page's
   i18n label dictionary (which repeats the same key names with translated values).

RocketHunt is an aggregator of Telegram job posts: its salary range is frequently its own
estimate (``salary_estimated`` / ``salary_analytics``), so the compensation carries
``aggregator_estimate`` evidence unless the vacancy text states the same figures itself
(ADR-016 §3). Contacts are a paid gate: ``contact*`` keys are never read, the "Show contacts"
button text never becomes content, and ``/api/`` is never called (ADR-015, robots).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from careeros.modules.opportunities.enums import FieldSource, RemotePolicy, Seniority
from careeros.modules.opportunities.schemas import OpportunityExtraction
from careeros.modules.platform.connectors.rockethunt import urls
from careeros.modules.platform.enums import SourceRelation
from careeros.modules.platform.fetch.extract.embedded import find_rsc_chunks, search_keys
from careeros.modules.platform.fetch.extract.jsonld import find_jobposting, jobposting_to_posting
from careeros.modules.platform.schemas import FieldEvidence, JobPosting
from careeros.modules.platform.sources import is_http_url
from careeros.modules.vault.enums import Platform

EMBEDDED_SOURCE = "embedded"
EMBEDDED_CONFIDENCE = 0.8
ESTIMATE_CONFIDENCE = 0.4
STATED_CONFIDENCE = 0.9
SUMMARY_MAX = 600

#: Vacancy-record keys we read. ``contact*`` is deliberately absent — see ``CONTACT_KEY``.
VACANCY_KEYS: tuple[str, ...] = (
    "grade",
    "english_level",
    "englishLevel",
    "work_formats",
    "workFormat",
    "is_remote",
    "relocations",
    "relocation",
    "experience_min_years",
    "experience_max_years",
    "industry",
    "specialization",
    "company_type",
    "companyType",
    "company_website",
    "companyWebsite",
    "employer_website",
    "salary_estimated",
    "salary_analytics",
    "avgSalary",
    "salary_period",
    "published_at",
    "updated_at",
    "archived",
    "expired",
    "status",
    "key_skills_en",
    "key_skills_ru",
    "language",
    "lang",
    "original",
    "original_url",
    "source_url",
    "apply_url",
    "applyOnSource",
    "showOriginal",
)

#: Any key matching this is a contact behind the paid gate — never read, never stored.
CONTACT_KEY = re.compile(r"contact|recruiter|telegram_user|phone|email", re.IGNORECASE)

#: Button / dialog labels of the contact gate: chrome, never content.
CONTACT_GATE = (
    "show contacts",
    "показать контакты",
    "vacancy contacts",
    "контакты вакансии",
    "recruiter contacts",
    "контакты рекрутера",
    "loading contacts...",
    "загрузка контактов...",
    "no contacts available for this vacancy",
    "reach out directly about this role",
)
_GATE_LINE = re.compile(
    r"^\s*(?:" + "|".join(re.escape(p) for p in CONTACT_GATE) + r")\s*[:.]?\s*$",
    re.IGNORECASE,
)
_GATE_INLINE = re.compile(
    r"(?:" + "|".join(re.escape(p) for p in CONTACT_GATE[:6]) + r")", re.IGNORECASE
)

_GRADES: dict[str, Seniority] = {
    "intern": Seniority.junior,
    "trainee": Seniority.junior,
    "junior": Seniority.junior,
    "middle": Seniority.mid,
    "mid": Seniority.mid,
    "senior": Seniority.senior,
    "lead": Seniority.lead,
    "teamlead": Seniority.lead,
    "head": Seniority.lead,
    "staff": Seniority.staff,
    "principal": Seniority.principal,
    "director": Seniority.principal,
    "clevel": Seniority.principal,
    "c-level": Seniority.principal,
}
_WORK_FORMATS: dict[str, RemotePolicy] = {
    "remote": RemotePolicy.remote_global,
    "удаленно": RemotePolicy.remote_global,
    "hybrid": RemotePolicy.hybrid,
    "гибрид": RemotePolicy.hybrid,
    "onsite": RemotePolicy.onsite,
    "office": RemotePolicy.onsite,
    "on-site": RemotePolicy.onsite,
    "офис": RemotePolicy.onsite,
}
_ENGLISH = re.compile(
    r"english\s*[:\-]?\s*(a1|a2|b1|b2|c1|c2|native|fluent)|английский\s*[:\-]?\s*"
    r"(a1|a2|b1|b2|c1|c2|свободный)",
    re.IGNORECASE,
)
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(?P<t>.+?)\s*#*\s*$")
_MD_BOLD_HEADING = re.compile(r"^\s{0,3}\*\*(?P<t>[^*]{2,80}?)\*\*\s*:?\s*$")
_MD_BULLET = re.compile(r"^\s{0,6}(?:[-*•·]|\d{1,2}[.)])\s+(?P<t>.+?)\s*$")
_REQUIREMENTS_HEADING = re.compile(
    r"requirement|qualification|expect|you have|skills|требован|ожида|навык", re.IGNORECASE
)
_RESPONSIBILITIES_HEADING = re.compile(
    r"responsibilit|task|what you|you will|обязанност|задач|предстоит", re.IGNORECASE
)
_MD_MARKUP = re.compile(r"[*_`]{1,3}")
_DIGIT_GROUP = re.compile(r"(?<=\d)[\s  .,'](?=\d)")
_STATUS_CLOSED = frozenset({"archived", "expired", "closed", "inactive", "removed"})


# ------------------------------------------------------------------------------ the contact gate


def strip_contact_gate(text: str) -> str:
    """Drop the contact-gate chrome from readable text (the gate is paid; we never unlock it)."""
    if not text:
        return text
    kept = [line for line in text.splitlines() if not _GATE_LINE.match(line)]
    return _GATE_INLINE.sub("", "\n".join(kept)).replace("\n\n\n", "\n\n").strip()


def gate_contacts(posting: JobPosting) -> JobPosting:
    """Apply the contact gate to any posting we produced, whatever path built it."""
    updates: dict[str, Any] = {}
    cleaned = strip_contact_gate(posting.raw_text)
    if cleaned != posting.raw_text:
        updates["raw_text"] = cleaned
    extraction = posting.extraction
    if extraction is not None and extraction.recruiter is not None:
        # Recruiter contacts on RocketHunt live behind the paid gate; anything we "found" in the
        # page chrome is a label, not a person. Openly written handles inside the vacancy text
        # stay in ``raw_text`` and are re-parsed at ingest.
        updates["extraction"] = extraction.model_copy(update={"recruiter": None})
    return posting.model_copy(update=updates) if updates else posting


# ------------------------------------------------------------------------------ embedded record


def _loads(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _balanced(text: str, start: int) -> str | None:
    """The JSON object literal starting at ``text[start]`` (string-aware brace matching)."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _enclosing_object(text: str, index: int, *, max_back: int = 80_000) -> dict[str, Any] | None:
    """The innermost JSON object literal that contains ``text[index]`` and parses."""
    limit = max(0, index - max_back)
    pos = index
    for _ in range(8):
        start = text.rfind("{", limit, pos)
        if start < 0:
            return None
        literal = _balanced(text, start)
        if literal is not None and start + len(literal) > index:
            data = _loads(literal)
            if isinstance(data, dict):
                return data
        pos = start
    return None


def find_vacancy_record(html: str, external_id: str | None) -> dict[str, Any] | None:
    """The vacancy object inside the RSC payload, found by its uuid (never the label dictionary).

    RocketHunt ships its i18n dictionary in the same payload, and it repeats the data key names
    (``"grade": "Grade"``, ``"englishLevel": "English Level"`` …). Anchoring on the uuid is what
    keeps a translated label out of an extracted field.
    """
    markers: list[re.Pattern[str]] = []
    if external_id:
        markers.append(
            re.compile(r'"(?:id|uuid)"\s*:\s*"' + re.escape(external_id) + r'"', re.IGNORECASE)
        )
    markers.append(re.compile(r'"key_skills_(?:en|ru)"\s*:'))
    for chunk in find_rsc_chunks(html):
        for marker in markers:
            for m in marker.finditer(chunk):
                record = _enclosing_object(chunk, m.start())
                if record is not None and _looks_like_vacancy(record, external_id):
                    return record
    return None


def _looks_like_vacancy(record: dict[str, Any], external_id: str | None) -> bool:
    if external_id and str(record.get("id") or record.get("uuid") or "").lower() == external_id:
        return True
    if external_id:
        return False
    return any(k in record for k in ("key_skills_en", "key_skills_ru", "employer_name"))


def read_embedded(record: dict[str, Any]) -> dict[str, Any]:
    """The declared vacancy keys of ``record`` — contact keys filtered out by construction."""
    found = search_keys(record, VACANCY_KEYS)
    return {k: v for k, v in found.items() if not CONTACT_KEY.search(k)}


# ------------------------------------------------------------------------------ small mappers


def _text(value: Any, *, locale: str = urls.CANONICAL_LOCALE) -> str | None:
    """A display string out of a scalar or a RocketHunt ``{name_en, name_ru}`` pair."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dict):
        for key in (f"name_{locale}", "name_en", "name_ru", "name", "title", "value"):
            got = value.get(key)
            if isinstance(got, str) and got.strip():
                return got.strip()
    return None


def _strings(value: Any, *, locale: str = urls.CANONICAL_LOCALE) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, list):
        return [s for s in (_text(v, locale=locale) for v in value) if s]
    got = _text(value, locale=locale)
    return [got] if got else []


def grade_to_seniority(value: Any) -> Seniority | None:
    key = (_text(value) or "").strip().lower().replace(" ", "").replace("_", "")
    return _GRADES.get(key)


def work_format_to_remote(embedded: dict[str, Any]) -> RemotePolicy | None:
    """``work_formats[].kind`` / ``workFormat`` / ``is_remote`` → a remote policy."""
    kinds: list[str] = []
    raw = embedded.get("work_formats", embedded.get("workFormat"))
    if isinstance(raw, list):
        for item in raw:
            kind = item.get("kind") if isinstance(item, dict) else item
            if isinstance(kind, str):
                kinds.append(kind)
    elif isinstance(raw, str):
        kinds.append(raw)
    for kind in kinds:
        policy = _WORK_FORMATS.get(kind.strip().lower())
        if policy is not None:
            return policy
    if embedded.get("is_remote") is True:
        return RemotePolicy.remote_global
    return None


def english_requirement(embedded: dict[str, Any], qualifications: list[str]) -> str | None:
    """``English: B2`` from the embedded level or from the JSON-LD ``qualifications`` string."""
    level = _text(embedded.get("english_level", embedded.get("englishLevel")))
    if level and len(level) <= 12:
        return f"English: {level.strip().upper()}"
    for item in qualifications:
        m = _ENGLISH.search(item)
        if m:
            return f"English: {(m.group(1) or m.group(2)).upper()}"
    return None


def experience_requirement(embedded: dict[str, Any]) -> str | None:
    lo, hi = embedded.get("experience_min_years"), embedded.get("experience_max_years")
    lo = lo if isinstance(lo, int | float) and not isinstance(lo, bool) else None
    hi = hi if isinstance(hi, int | float) and not isinstance(hi, bool) else None
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        return f"Experience: {lo:g}–{hi:g} years"
    if lo is not None:
        return f"Experience: {lo:g}+ years"
    return f"Experience: up to {hi:g} years"


def relocation_requirement(embedded: dict[str, Any], *, locale: str) -> str | None:
    raw = embedded.get("relocations", embedded.get("relocation"))
    if raw is True:
        return "Relocation: supported"
    countries = _strings(raw, locale=locale)
    return f"Relocation: {', '.join(countries)}" if countries else None


def is_closed(embedded: dict[str, Any]) -> bool:
    if embedded.get("archived") is True or embedded.get("expired") is True:
        return True
    status = (_text(embedded.get("status")) or "").strip().lower()
    return status in _STATUS_CLOSED


def original_url(embedded: dict[str, Any]) -> str | None:
    """A public link to the employer's own posting, when the page states one plainly."""
    for key in ("original_url", "original", "source_url", "apply_url"):
        found = _first_external_url(embedded.get(key))
        if found:
            return found
    return None


def _first_external_url(value: Any, depth: int = 0) -> str | None:
    if depth > 4 or value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if is_http_url(candidate) and not urls.is_rockethunt(candidate):
            return candidate
        return None
    if isinstance(value, dict):
        for key in ("url", "href", "link", "original_url", "source_url", "apply_url", "value"):
            got = _first_external_url(value.get(key), depth + 1)
            if got:
                return got
        return None
    if isinstance(value, list):
        for item in value:
            got = _first_external_url(item, depth + 1)
            if got:
                return got
    return None


def _dt(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


# ------------------------------------------------------------------------------ description


def markdown_sections(description: str) -> dict[str, list[str]]:
    """``{"summary": [...], "requirements": [...], "responsibilities": [...]}`` from markdown.

    RocketHunt keeps the vacancy body as markdown inside the JSON-LD description; its headings
    are either ``#``-style or a bold line (``**Requirements**``). Only bullets under a heading we
    recognise are lifted — free prose stays in ``raw_text`` and is re-parsed at ingest.
    """
    out: dict[str, list[str]] = {"summary": [], "requirements": [], "responsibilities": []}
    bucket: str | None = None
    seen_heading = False
    for line in (description or "").splitlines():
        heading = _MD_HEADING.match(line) or _MD_BOLD_HEADING.match(line)
        if heading:
            title = heading.group("t").strip()
            seen_heading = True
            if _REQUIREMENTS_HEADING.search(title):
                bucket = "requirements"
            elif _RESPONSIBILITIES_HEADING.search(title):
                bucket = "responsibilities"
            else:
                bucket = None
            continue
        bullet = _MD_BULLET.match(line)
        text = _MD_MARKUP.sub("", (bullet.group("t") if bullet else line)).strip()
        if not text:
            continue
        if bullet is not None and bucket is not None:
            out[bucket].append(text)
        elif bullet is None and seen_heading and not out["summary"] and bucket is None:
            out["summary"].append(text)
    return out


def salary_stated_in_text(text: str, *, values: list[float]) -> bool:
    """Does the vacancy text write every salary figure out itself (any digit grouping)?"""
    if not values:
        return False
    flat = _DIGIT_GROUP.sub("", text or "")
    return all(f"{value:.0f}" in flat for value in values)


# ------------------------------------------------------------------------------ the extractor


def extract_page(
    html: str,
    url: str | None,
    *,
    platform: Platform = Platform.rockethunt,
    external_id: str | None = None,
    locale: str = urls.CANONICAL_LOCALE,
    fetched_at: datetime | None = None,
) -> JobPosting | None:
    """Map one RocketHunt vacancy page; ``None`` when it carries no JSON-LD ``JobPosting``."""
    jsonld = find_jobposting(html or "")
    if jsonld is None:
        return None
    posting = jobposting_to_posting(jsonld, platform, url, fetched_at=fetched_at)
    ident = (posting.external_id or external_id or "").lower() or None
    record = find_vacancy_record(html or "", ident) or {}
    embedded = read_embedded(record) if record else {}
    return _merge(
        posting,
        embedded,
        record=record,
        url=url,
        locale=locale,
        fetched_at=fetched_at,
        jsonld=jsonld,
    )


def _merge(
    posting: JobPosting,
    embedded: dict[str, Any],
    *,
    record: dict[str, Any],
    url: str | None,
    locale: str,
    fetched_at: datetime | None,
    jsonld: dict[str, Any],
) -> JobPosting:
    extraction = posting.extraction or OpportunityExtraction()
    evidence = list(posting.field_evidence)
    raw_payload: dict[str, Any] = {"jsonld": jsonld, "embedded": _payload(embedded, locale=locale)}
    updates: dict[str, Any] = {}

    def note(field: str, value: Any, *, source: str = EMBEDDED_SOURCE, confidence: float) -> None:
        evidence.append(
            FieldEvidence(
                field=field,
                value=value,
                source=source,
                source_url=url,
                observed_at=fetched_at,
                confidence=confidence,
            )
        )

    # ---- seniority: the embedded grade, else the "Head, English: B2" qualifications string
    qualifications = list(extraction.requirements)
    seniority = grade_to_seniority(embedded.get("grade"))
    grade_label = _text(embedded.get("grade"))
    leftover: list[str] = []
    for item in qualifications:
        found = grade_to_seniority(item)
        if found is not None and seniority is None:
            seniority = found
            grade_label = grade_label or item.strip()
            continue
        if found is not None:
            continue
        leftover.append(item)
    if seniority is not None:
        note("seniority", str(seniority), confidence=EMBEDDED_CONFIDENCE)
    if grade_label:
        raw_payload["embedded"].setdefault("grade", grade_label)

    # ---- requirements: stated facts first, then the markdown "Requirements" bullets
    sections = markdown_sections(posting.raw_text)
    requirements: list[str] = []
    english = english_requirement(embedded, qualifications)
    if english:
        requirements.append(english)
        note("requirements.english", english, confidence=EMBEDDED_CONFIDENCE)
    experience = experience_requirement(embedded)
    if experience:
        requirements.append(experience)
        note("requirements.experience", experience, confidence=EMBEDDED_CONFIDENCE)
    relocation = relocation_requirement(embedded, locale=locale)
    if relocation:
        requirements.append(relocation)
        note("requirements.relocation", relocation, confidence=EMBEDDED_CONFIDENCE)
    requirements += [r for r in leftover if r not in requirements]
    requirements += [r for r in sections["requirements"] if r not in requirements]

    responsibilities = list(extraction.responsibilities) or sections["responsibilities"]
    summary = extraction.summary or (
        sections["summary"][0][:SUMMARY_MAX] if sections["summary"] else None
    )

    # ---- skills: JSON-LD skills, extended with the locale's key_skills
    key_skills = embedded.get("key_skills_ru" if locale == "ru" else "key_skills_en")
    technologies = list(extraction.technologies)
    seen = {t.casefold() for t in technologies}
    for skill in _strings(key_skills, locale=locale):
        if skill.casefold() not in seen:
            technologies.append(skill)
            seen.add(skill.casefold())

    # ---- remote policy
    remote = work_format_to_remote(embedded)
    if remote is not None:
        note("remote_policy", str(remote), confidence=EMBEDDED_CONFIDENCE)

    # ---- salary provenance (ADR-016 §3): aggregator estimate vs. a figure the text states
    compensation = extraction.compensation
    if compensation is not None and not compensation.is_empty():
        figures = [v for v in (compensation.min, compensation.max) if v is not None]
        stated = salary_stated_in_text(posting.raw_text, values=figures)
        analytics = embedded.get("salary_analytics")
        estimated = (
            embedded.get("salary_estimated") is True or embedded.get("avgSalary") is not None
        )
        if estimated and not stated:
            detail = _estimate_detail(analytics)
            compensation = compensation.model_copy(
                update={"raw": f"RocketHunt estimate ({detail}); {compensation.raw or ''}"[:300]}
            )
            raw_payload["salary_is_estimate"] = True
            note(
                "compensation",
                compensation.model_dump(mode="json"),
                source=str(FieldSource.aggregator_estimate),
                confidence=ESTIMATE_CONFIDENCE,
            )
        elif stated:
            note(
                "compensation",
                compensation.model_dump(mode="json"),
                source=str(FieldSource.board_page),
                confidence=STATED_CONFIDENCE,
            )

    # ---- the original posting behind the aggregated one (link only — never fetched here)
    original = original_url(embedded)
    if original:
        updates["original_url"] = original
        updates["relation"] = SourceRelation.aggregates
        note("original_url", original, confidence=EMBEDDED_CONFIDENCE)
    hint = _source_hint(record, locale=locale)
    if hint:
        raw_payload["source_hint"] = hint

    # ---- dates / lifecycle
    published = posting.published_at or _dt(embedded.get("published_at"))
    updated = _dt(embedded.get("updated_at"))
    if updated is not None:
        note("updated_at", updated.isoformat(), confidence=EMBEDDED_CONFIDENCE)
    if is_closed(embedded):
        raw_payload["closed"] = True
        note("status", "closed", confidence=EMBEDDED_CONFIDENCE)

    facets = {
        "industry": embedded.get("industry"),
        "specialization": embedded.get("specialization"),
        "company_type": embedded.get("company_type", embedded.get("companyType")),
    }
    for field, raw in facets.items():
        value = _text(raw, locale=locale)
        if value:
            note(field, value, confidence=EMBEDDED_CONFIDENCE)
    website = _first_external_url(
        embedded.get("company_website")
        or embedded.get("companyWebsite")
        or embedded.get("employer_website")
    )
    if website:
        note("company_website", website, confidence=EMBEDDED_CONFIDENCE)

    updates["extraction"] = extraction.model_copy(
        update={
            "seniority": seniority or extraction.seniority,
            "requirements": requirements,
            "responsibilities": responsibilities,
            "technologies": technologies,
            "summary": summary,
            "compensation": compensation,
            "remote_policy": remote or extraction.remote_policy,
            "recruiter": None,
        }
    )
    updates["field_evidence"] = evidence
    updates["raw_payload"] = raw_payload
    updates["published_at"] = published
    updates["posted_at"] = posting.posted_at or published
    return gate_contacts(posting.model_copy(update=updates))


def _estimate_detail(analytics: Any) -> str:
    if not isinstance(analytics, dict):
        return "salary_estimated"
    bits = [
        f"{key}={analytics[key]}"
        for key in ("source_type", "confidence", "salary_count")
        if analytics.get(key) is not None
    ]
    return ", ".join(bits) or "salary_estimated"


def _source_hint(record: dict[str, Any], *, locale: str) -> dict[str, Any]:
    """What the page says about where the vacancy came from (a Telegram channel, usually).

    Read from the record's **top level** only: ``source_type`` also names the basis of the salary
    estimate inside ``salary_analytics``, and the two must never be confused.
    """
    hint = {
        "source": _text(record.get("source"), locale=locale),
        "source_name": _text(record.get("source_name"), locale=locale),
        "source_type": _text(record.get("source_type"), locale=locale),
    }
    return hint if any(v is not None for v in hint.values()) else {}


def _payload(embedded: dict[str, Any], *, locale: str) -> dict[str, Any]:
    """The extracted keys only — never the whole RSC payload, never a contact key."""
    out: dict[str, Any] = {}
    for key, value in embedded.items():
        if CONTACT_KEY.search(key):
            continue
        if key == "salary_analytics" and isinstance(value, dict):
            out[key] = {
                k: value[k]
                for k in ("source_type", "confidence", "salary_count", "currency")
                if k in value
            }
            continue
        if key in ("industry", "specialization") and isinstance(value, dict):
            out[key] = _text(value, locale=locale)
            continue
        if key == "original" and not isinstance(value, str):
            out[key] = _first_external_url(value)
            continue
        if isinstance(value, dict | list) and len(json.dumps(value, default=str)) > 2000:
            continue
        out[key] = value
    return out
