"""JustJoin.it offer detail (candidate API JSON) → ``JobPosting`` (ADR-015 §6, ADR-016).

Pure mapping, no I/O. Everything the board keeps open — category, currency, contract type,
experience level, workplace type — stays an open string here: an unknown value is kept verbatim
in ``raw_payload["notes"]`` / ``raw_payload`` and never raises. The top-level key set of a
payload is fingerprinted (``schema_fingerprint``) so a schema change is visible in the artifact
and in ``careeros platform doctor justjoin``; ``REQUIRED_KEYS`` are the six keys the mapping
cannot work without — their absence is a drift warning, not an exception (only a missing
``title`` is fatal, because a posting without a title is not a posting).

The board publishes each salary range once per currency: the employer's own figures carry
``currencySource="original"`` and every other row is JustJoin's conversion (an aggregator
estimate, ADR-016 §3). The original row becomes ``compensation``; all rows are kept in
``raw_payload["salaries"]``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, time
from typing import Any

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    FieldSource,
    RemotePolicy,
    Seniority,
)
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform.fetch.extract.text import html_to_text
from careeros.modules.platform.schemas import FieldEvidence, JobPosting
from careeros.modules.vault.enums import Platform

#: Every field evidence of an API read carries the board's own structured record as authority.
SOURCE = str(FieldSource.board_api)
CONFIDENCE = 0.9

CANONICAL_URL = "https://justjoin.it/job-offer/{slug}"

#: Keys the mapping reads; missing ones are reported as schema drift.
REQUIRED_KEYS: tuple[str, ...] = (
    "title",
    "companyName",
    "body",
    "employmentTypes",
    "publishedAt",
    "slug",
)

#: Top-level keys of the reference detail payload (recorded 2026-08-26; see
#: ``docs/platform/justjoin.md``).
BASELINE_KEYS: tuple[str, ...] = (
    "appliedAt",
    "applyUrl",
    "bannerUrl",
    "body",
    "category",
    "city",
    "companyLogoUrl",
    "companyName",
    "companyProfileCoverPhotoUrl",
    "companyProfileShortDescription",
    "companyProfileSlug",
    "companySize",
    "companyUrl",
    "countryCode",
    "coverImage",
    "customConsent",
    "employmentTypes",
    "experienceLevel",
    "expiredAt",
    "futureConsent",
    "hybridWorkSchedule",
    "id",
    "informationClause",
    "isActive",
    "isOpenToHireUkrainians",
    "languages",
    "latitude",
    "locationId",
    "locations",
    "longitude",
    "niceToHaveSkills",
    "publishedAt",
    "requiredSkills",
    "slug",
    "street",
    "title",
    "url",
    "videoUrl",
    "workingTime",
    "workplaceType",
)

# ---- open vocabularies (unknown values are kept, never rejected)
REMOTE_POLICY: dict[str, RemotePolicy] = {
    "remote": RemotePolicy.remote_global,  # narrowed to remote_region when a country is stated
    "office": RemotePolicy.onsite,
    "hybrid": RemotePolicy.hybrid,
    "partly_remote": RemotePolicy.hybrid,
}
CONTRACT_TYPE: dict[str, ContractType] = {
    "b2b": ContractType.b2b,
    "permanent": ContractType.employment,
    "contract_of_employment": ContractType.employment,
    "mandate_contract": ContractType.freelance,  # umowa zlecenie — a civil-law contract
    "contract": ContractType.freelance,  # umowa o dzieło
    "freelance": ContractType.freelance,
}
EMPLOYMENT_TYPE: dict[str, EmploymentType] = {
    "full_time": EmploymentType.full_time,
    "part_time": EmploymentType.part_time,
}
SENIORITY: dict[str, Seniority] = {
    "junior": Seniority.junior,
    "mid": Seniority.mid,
    "senior": Seniority.senior,
    "c_level": Seniority.principal,
}
PERIOD: dict[str, CompensationPeriod] = {
    "hour": CompensationPeriod.hour,
    "day": CompensationPeriod.day,
    "month": CompensationPeriod.month,
    "year": CompensationPeriod.year,
}

_PARAGRAPH = re.compile(r"\n{2,}")
_MICROSECONDS = re.compile(r"\.(\d{6})\d+")
_SUMMARY_MIN = 40
_SUMMARY_MAX = 400


# ------------------------------------------------------------------------------ schema drift


def fingerprint_keys(keys: Iterable[str]) -> str:
    """Stable short hash of a *set* of keys (order-independent)."""
    joined = ",".join(sorted({str(k) for k in keys}))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def schema_fingerprint(payload: Mapping[str, Any]) -> str:
    """Fingerprint of one payload's top-level key set — compare with ``BASELINE_FINGERPRINT``."""
    return fingerprint_keys(payload.keys())


def missing_required(payload: Mapping[str, Any]) -> list[str]:
    """``REQUIRED_KEYS`` absent from (or empty in) ``payload``, in declaration order."""
    return [k for k in REQUIRED_KEYS if payload.get(k) in (None, "", [], {})]


BASELINE_FINGERPRINT = fingerprint_keys(BASELINE_KEYS)


def canonical_offer_url(slug: str) -> str:
    return CANONICAL_URL.format(slug=slug.strip("/"))


# ------------------------------------------------------------------------------ small helpers


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.-]", "", value)
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


def _dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, dict)]


def _unique(values: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys([v for v in values if v]))


def parse_datetime(value: Any) -> datetime | None:
    """ISO-8601 as JustJoin writes it: ``Z`` suffix, up to seven fractional digits, or a date."""
    raw = _clean_str(value)
    if raw is None:
        return None
    candidate = _MICROSECONDS.sub(r".\1", raw.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw[:10]), time.min)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def skill_names(value: Any) -> list[str]:
    """``[{"name": "Python", "level": 4}, …]`` → ``["Python", …]`` (levels stay in raw_payload)."""
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        name = _clean_str(item.get("name")) if isinstance(item, dict) else _clean_str(item)
        if name:
            out.append(name)
    return list(dict.fromkeys(out))


def language_requirement(value: Any) -> str | None:
    """``[{"code": "en", "level": "B2"}, …]`` → ``"Languages: EN B2, PL C1"``."""
    parts: list[str] = []
    for item in _dicts(value):
        code = _clean_str(item.get("code"))
        if not code:
            continue
        level = _clean_str(item.get("level"))
        parts.append(f"{code.upper()} {level}".strip())
    return "Languages: " + ", ".join(parts) if parts else None


# ------------------------------------------------------------------------------ salaries


def normalize_salary(entry: Mapping[str, Any]) -> Compensation | None:
    """One ``employmentTypes[]`` row → ``Compensation`` (``None`` when it states no range).

    ``unit`` is the unit the employer typed in; for an hourly offer JustJoin also stores the
    monthly equivalent in ``from``/``to`` and the typed figures in ``fromPerUnit``/``toPerUnit``
    (the site's own JSON-LD publishes the per-unit pair), so the per-unit pair wins there.
    """
    unit = (_clean_str(entry.get("unit")) or "month").lower()
    period = PERIOD.get(unit)
    low, high = _number(entry.get("from")), _number(entry.get("to"))
    per_low, per_high = _number(entry.get("fromPerUnit")), _number(entry.get("toPerUnit"))
    if period is CompensationPeriod.hour and (per_low is not None or per_high is not None):
        low, high = per_low, per_high
    if low is None and high is None:
        return None
    currency = _clean_str(entry.get("currency"))
    return Compensation(
        min=low,
        max=high,
        currency=currency.upper() if currency else None,
        period=period,
        type="rate" if period is CompensationPeriod.hour else "salary",
        raw=json.dumps(entry, ensure_ascii=False, sort_keys=True, default=str)[:300],
    )


def pick_salary(
    entries: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, Compensation | None]:
    """The employer's own row (``currencySource="original"``) when there is one, else the first."""
    priced = [(e, c) for e in entries if (c := normalize_salary(e)) is not None]
    if not priced:
        return None, None
    original = next(
        (
            pair
            for pair in priced
            if (_clean_str(pair[0].get("currencySource")) or "") == "original"
        ),
        None,
    )
    return original or priced[0]


def _salary_rows(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in entries:
        compensation = normalize_salary(entry)
        rows.append(
            {
                "type": _clean_str(entry.get("type")),
                "currency": _clean_str(entry.get("currency")),
                "currency_source": _clean_str(entry.get("currencySource")),
                "unit": _clean_str(entry.get("unit")),
                "gross": entry.get("gross"),
                "compensation": compensation.model_dump(mode="json") if compensation else None,
            }
        )
    return rows


# ------------------------------------------------------------------------------ the mapping


def _summary(body_text: str) -> str | None:
    for paragraph in _PARAGRAPH.split(body_text):
        cleaned = " ".join(paragraph.split())
        if len(cleaned) >= _SUMMARY_MIN:
            return cleaned[:_SUMMARY_MAX]
    return None


def offer_to_posting(
    payload: Mapping[str, Any],
    *,
    url: str | None = None,
    fetched_at: datetime | None = None,
) -> JobPosting:
    """Map one offer detail record. Raises ``ValueError`` when it carries no title."""
    title = _clean_str(payload.get("title"))
    if not title:
        raise ValueError("justjoin: offer detail without a title")
    notes: list[str] = []

    slug = _clean_str(payload.get("slug"))
    canonical = _clean_str(payload.get("url")) or (canonical_offer_url(slug) if slug else None)
    page_url = url or canonical
    company = _clean_str(payload.get("companyName"))

    locations = _dicts(payload.get("locations"))
    cities = _unique([_clean_str(loc.get("city")) for loc in locations]) or _unique(
        [_clean_str(payload.get("city"))]
    )
    country = _clean_str(payload.get("countryCode"))
    location = ", ".join([*cities, *([country] if country else [])]) or None

    workplace = (_clean_str(payload.get("workplaceType")) or "").lower()
    remote_policy = REMOTE_POLICY.get(workplace, RemotePolicy.unknown)
    remote_regions: list[str] = []
    if remote_policy is RemotePolicy.remote_global and country:
        remote_policy, remote_regions = RemotePolicy.remote_region, [country]
    if workplace and workplace not in REMOTE_POLICY:
        notes.append(f"unknown workplaceType: {workplace}")
    hybrid_schedule = _clean_str(payload.get("hybridWorkSchedule"))

    employment_types = _dicts(payload.get("employmentTypes"))
    declared = _unique([_clean_str(e.get("type")) for e in employment_types])
    contract_type = next(
        (CONTRACT_TYPE[t.lower()] for t in declared if t.lower() in CONTRACT_TYPE), None
    )
    unknown_contracts = [t for t in declared if t.lower() not in CONTRACT_TYPE]
    if unknown_contracts:
        notes.append("unknown employmentTypes.type: " + ", ".join(unknown_contracts))

    working_time = (_clean_str(payload.get("workingTime")) or "").lower()
    employment_type = EMPLOYMENT_TYPE.get(working_time)
    if working_time and employment_type is None:
        notes.append(f"unmapped workingTime: {working_time}")

    experience_level = (_clean_str(payload.get("experienceLevel")) or "").lower()
    seniority = SENIORITY.get(experience_level)
    if experience_level and seniority is None:
        notes.append(f"unknown experienceLevel: {experience_level}")

    chosen_salary, compensation = pick_salary(employment_types)
    technologies = skill_names(payload.get("requiredSkills"))
    preferred = skill_names(payload.get("niceToHaveSkills"))
    languages = language_requirement(payload.get("languages"))
    requirements = [r for r in (languages,) if r]
    if hybrid_schedule:
        requirements.append(f"Hybrid work schedule: {hybrid_schedule}")

    body_text = html_to_text(_clean_str(payload.get("body")) or "")
    summary = _summary(body_text)
    published_at = parse_datetime(payload.get("publishedAt"))
    expires_at = parse_datetime(payload.get("expiredAt"))
    apply_url = _clean_str(payload.get("applyUrl"))
    category = payload.get("category") if isinstance(payload.get("category"), dict) else None
    if payload.get("isActive") is False:
        notes.append("offer is not active (isActive=false)")
    drift = missing_required(payload)
    if drift:
        notes.append("schema drift — missing keys: " + ", ".join(drift))

    extraction = OpportunityExtraction(
        title=title,
        company=company,
        contract_type=contract_type,
        employment_type=employment_type,
        location=location,
        remote_policy=remote_policy,
        remote_regions=remote_regions,
        compensation=compensation,
        seniority=seniority,
        requirements=requirements,
        preferred=preferred,
        technologies=technologies,
        summary=summary,
        deadline=expires_at.date() if expires_at else None,
    )

    observed: dict[str, Any] = {
        "title": title,
        "company": company,
        "location": location,
        "remote_policy": None if remote_policy is RemotePolicy.unknown else str(remote_policy),
        "remote_regions": remote_regions or None,
        "compensation": compensation.model_dump(mode="json") if compensation else None,
        "contract_type": str(contract_type) if contract_type else None,
        "employment_type": str(employment_type) if employment_type else None,
        "seniority": str(seniority) if seniority else None,
        "technologies": technologies or None,
        "preferred": preferred or None,
        "requirements": requirements or None,
        "category": (_clean_str(category.get("key")) if category else None),
        "workplace_type": workplace or None,
        "published_at": published_at.isoformat() if published_at else None,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "description": summary,
        "apply_url": apply_url,
        "external_id": slug,
    }
    evidence = [
        FieldEvidence(
            field=name,
            value=value,
            source=SOURCE,
            source_url=page_url,
            observed_at=fetched_at,
            confidence=CONFIDENCE,
        )
        for name, value in observed.items()
        if value is not None
    ]

    header = [p for p in (title, company, location) if p]
    raw_text = "\n".join(header) + ("\n\n" + body_text if body_text else "")
    raw_payload: dict[str, Any] = {
        "api": dict(payload),
        "guid": _clean_str(payload.get("id")),
        "slug": slug,
        "schema_fingerprint": schema_fingerprint(payload),
        "company": {
            "name": company,
            "profile_slug": _clean_str(payload.get("companyProfileSlug")),
            "size": _clean_str(payload.get("companySize")),
            "url": _clean_str(payload.get("companyUrl")),
        },
        "category": category,
        "workplace_type": workplace or None,
        "working_time": working_time or None,
        "experience_level": experience_level or None,
        "hybrid_work_schedule": hybrid_schedule,
        "languages": payload.get("languages"),
        "required_skills": payload.get("requiredSkills"),
        "nice_to_have_skills": payload.get("niceToHaveSkills"),
        "salaries": _salary_rows(employment_types),
        "salary_gross": chosen_salary.get("gross") if chosen_salary else None,
        "apply_url": apply_url,
        "is_active": payload.get("isActive"),
        "notes": notes,
    }

    return JobPosting(
        platform=Platform.justjoin,
        external_id=slug,
        url=page_url,
        title=title[:300],
        company=company,
        location=location,
        posted_at=published_at,
        published_at=published_at,
        expires_at=expires_at,
        canonical_url=canonical,
        raw_text=raw_text,
        extraction=extraction,
        raw_payload=raw_payload,
        field_evidence=evidence,
    )
