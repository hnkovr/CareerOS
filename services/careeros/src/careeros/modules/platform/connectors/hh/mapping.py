"""hh.ru JSON → platform DTOs. Pure functions; unknown → ``None``; raw payloads kept verbatim.

Field names follow the public OpenAPI spec (resume: ``skill_set[]`` strings, ``experience[]``,
``salary{amount,currency}``; vacancy: ``snippet``, ``salary{from,to,currency,gross}``,
``schedule``/``work_format``, ``employment``/``employment_form``; negotiation: ``state{id,name}``,
``vacancy{…}``, ``created_at``/``updated_at``).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any

from careeros.modules.opportunities.enums import CompensationPeriod, EmploymentType, RemotePolicy
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform import parsers
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.schemas import (
    AccountInfo,
    ApplicationObservationIn,
    JobPosting,
    ProfileRead,
)
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.profiles.schemas import SnapshotExperienceItem
from careeros.modules.vault.enums import Platform

# hh's ``currency`` dictionary keeps legacy codes for roubles; the rest is ISO already.
CURRENCY_TO_ISO = {"RUR": "RUB", "BYR": "BYN"}
ISO_TO_HH = {"RUB": "RUR", "BYN": "BYR"}

EMPLOYMENT_BY_ID = {
    "full": EmploymentType.full_time,
    "part": EmploymentType.part_time,
    "project": EmploymentType.project,
}
EMPLOYMENT_BY_FORM = {
    "FULL": EmploymentType.full_time,
    "PART": EmploymentType.part_time,
    "PROJECT": EmploymentType.project,
}
# negotiations_state dictionary → normalized status (``hidden`` and call states stay unknown)
STATE_STATUS = {
    "response": ApplicationStatus.applied,
    "invitation": ApplicationStatus.invited,
    "discard": ApplicationStatus.rejected,
}

_TAG = re.compile(r"<[^>]+>")
_BLOCK_TAG = re.compile(r"</?(?:p|div|br|li|ul|ol|h\d|tr|table)\b[^>]*>", re.IGNORECASE)
_WS = re.compile(r"[ \t ]+")


# Fields of GET /resumes/{id} that identify or contact the person; never persisted or echoed
# (mirrors the LinkedIn export importer's PII policy).
RESUME_PII_KEYS: frozenset[str] = frozenset(
    {
        "contact",
        "birth_date",
        "age",
        "gender",
        "first_name",
        "last_name",
        "middle_name",
        "photo",
        "citizenship",
        "work_ticket",
        "site",
        "hidden_fields",
    }
)


def public_resume(resume: dict[str, Any]) -> dict[str, Any]:
    """The resume payload without personal identifiers (kept fields drive the audit)."""
    return {k: v for k, v in resume.items() if k not in RESUME_PII_KEYS}


def to_iso_currency(code: Any) -> str | None:
    if not isinstance(code, str) or not code.strip():
        return None
    upper = code.strip().upper()
    return CURRENCY_TO_ISO.get(upper, upper)


def to_hh_currency(code: str | None) -> str | None:
    if not code:
        return None
    upper = code.strip().upper()
    return ISO_TO_HH.get(upper, upper)


def parse_ts(value: Any) -> datetime | None:
    """``2026-08-20T10:00:00+0300`` (hh) or ``2026-08-20`` → aware UTC datetime; else ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return parsers.parse_date(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def strip_tags(text: Any) -> str | None:
    """Drop ``<highlighttext>`` and any other markup, unescape entities, collapse whitespace."""
    if not isinstance(text, str):
        return None
    out = _WS.sub(" ", html.unescape(_TAG.sub("", text))).strip()
    return out or None


def html_to_text(markup: Any) -> str | None:
    """Vacancy ``description`` HTML → plain text, one line per paragraph / list item."""
    if not isinstance(markup, str):
        return None
    text = html.unescape(_TAG.sub("", _BLOCK_TAG.sub("\n", markup)))
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    out = "\n".join(ln for ln in lines if ln)
    return out or None


def _str(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    return str(value)


def _name(obj: Any) -> str | None:
    if isinstance(obj, dict):
        return strip_tags(obj.get("name"))
    return None


def _id(obj: Any) -> str | None:
    return _str(obj.get("id")) if isinstance(obj, dict) else None


def _ids(objs: Any) -> list[str]:
    if not isinstance(objs, list):
        return []
    return [i for i in (_id(o) for o in objs) if i]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _fmt(value: Any) -> str:
    num = _number(value)
    if num is None:
        return str(value)
    return f"{int(num):,}".replace(",", " ") if num.is_integer() else str(num)


def salary_text(salary: Any) -> str | None:
    """``{"from": 300000, "to": 400000, "currency": "RUR", "gross": false}`` → readable line."""
    if not isinstance(salary, dict):
        return None
    lo, hi = salary.get("from"), salary.get("to")
    if lo is None and hi is None:
        return None
    if lo is not None and hi is not None:
        core = f"{_fmt(lo)} – {_fmt(hi)}"
    elif lo is not None:
        core = f"от {_fmt(lo)}"
    else:
        core = f"до {_fmt(hi)}"
    currency = to_iso_currency(salary.get("currency")) or ""
    gross = salary.get("gross")
    suffix = " (до вычета налогов)" if gross is True else " (на руки)" if gross is False else ""
    return f"{core} {currency}".strip() + suffix


def compensation(salary: Any) -> Compensation | None:
    if not isinstance(salary, dict):
        return None
    lo, hi = _number(salary.get("from")), _number(salary.get("to"))
    if lo is None and hi is None:
        return None
    return Compensation(
        min=lo,
        max=hi,
        currency=to_iso_currency(salary.get("currency")),
        period=CompensationPeriod.month,
        type="salary",
        raw=salary_text(salary),
    )


def remote_policy(item: dict[str, Any]) -> RemotePolicy:
    """``schedule.id == "remote"`` (legacy) or ``work_format[].id`` (current dictionary)."""
    formats = {f.upper() for f in _ids(item.get("work_format"))}
    if _id(item.get("schedule")) == "remote" or "REMOTE" in formats:
        return RemotePolicy.remote_global
    if "HYBRID" in formats:
        return RemotePolicy.hybrid
    if formats and formats <= {"ON_SITE", "FIELD_WORK"}:
        return RemotePolicy.onsite
    return RemotePolicy.unknown


def employment_type(item: dict[str, Any]) -> EmploymentType | None:
    by_id = EMPLOYMENT_BY_ID.get(_id(item.get("employment")) or "")
    if by_id is not None:
        return by_id
    form = item.get("employment_form")
    form_id = _id(form) if isinstance(form, dict) else (_ids(form) or [None])[0]
    return EMPLOYMENT_BY_FORM.get((form_id or "").upper())


# ------------------------------------------------------------------------------ resume → profile


def resume_to_profile(
    resume: dict[str, Any], *, captured_at: datetime | None = None
) -> ProfileRead:
    skills: list[str] = []
    for raw_skill in resume.get("skill_set") or []:
        name = raw_skill if isinstance(raw_skill, str) else _name(raw_skill)
        if name and name not in skills:
            skills.append(name)

    experience: list[SnapshotExperienceItem] = []
    for exp in resume.get("experience") or []:
        if not isinstance(exp, dict):
            continue
        company = strip_tags(exp.get("company")) or _name(exp.get("employer"))
        position = strip_tags(exp.get("position"))
        if not company and not position:
            continue
        start, end = _str(exp.get("start")), _str(exp.get("end"))
        period = f"{start} – {end or 'now'}" if start else (f"– {end}" if end else None)
        experience.append(
            SnapshotExperienceItem(
                company=company or "",
                title=position,
                period=period,
                description=strip_tags(exp.get("description")),
            )
        )

    rates: dict[str, Any] | None = None
    salary = resume.get("salary")
    if isinstance(salary, dict) and salary.get("amount") is not None:
        rates = {"salary": salary["amount"], "currency": to_iso_currency(salary.get("currency"))}

    preferences: dict[str, Any] = {
        "schedules": _ids(resume.get("schedules")),
        "employments": _ids(resume.get("employments")),
        "area": _name(resume.get("area")),
    }
    if resume.get("work_format"):
        preferences["work_formats"] = _ids(resume.get("work_format"))
    if resume.get("employment_form"):
        preferences["employment_forms"] = _ids(resume.get("employment_form"))
    total = resume.get("total_experience")
    if isinstance(total, dict) and total.get("months") is not None:
        preferences["total_experience_months"] = total["months"]
    if resume.get("updated_at"):
        preferences["updated_at"] = resume["updated_at"]

    return ProfileRead(
        platform=Platform.hh,
        capture_method=CaptureMethod.api,
        external_id=_str(resume.get("id")),
        profile_url=_str(resume.get("alternate_url")),
        captured_at=captured_at,
        headline=strip_tags(resume.get("title")),
        about=strip_tags(resume.get("skills")),
        experience=experience,
        skills=skills,
        rates=rates,
        preferences=preferences,
        raw_payload=public_resume(resume),
    )


# ------------------------------------------------------------------------------ vacancy → job


def vacancy_to_job(
    item: dict[str, Any],
    *,
    detail: dict[str, Any] | None = None,
    detail_error: str | None = None,
) -> JobPosting:
    """Search item (+ optional ``GET /vacancies/{id}`` detail) → ``JobPosting``."""
    source = detail or item
    name = strip_tags(item.get("name")) or strip_tags(source.get("name")) or f"hh {item.get('id')}"
    employer = _name(item.get("employer")) or _name(source.get("employer"))
    area = _name(item.get("area")) or _name(source.get("area"))
    salary = item.get("salary") if isinstance(item.get("salary"), dict) else source.get("salary")
    raw_snippet = item.get("snippet")
    snippet: dict[str, Any] = raw_snippet if isinstance(raw_snippet, dict) else {}
    requirement = strip_tags(snippet.get("requirement"))
    responsibility = strip_tags(snippet.get("responsibility"))

    raw_lines = [
        f"{name} @ {employer}" if employer else name,
        area,
        salary_text(salary),
        requirement,
        responsibility,
    ]
    technologies: list[str] = []
    payload: dict[str, Any] = item
    if detail is not None:
        description = html_to_text(detail.get("description"))
        if description:
            raw_lines.extend(["", description])
        for raw_skill in detail.get("key_skills") or []:
            skill = _name(raw_skill) if isinstance(raw_skill, dict) else strip_tags(raw_skill)
            if skill and skill not in technologies:
                technologies.append(skill)
        if technologies:
            raw_lines.append("Ключевые навыки: " + ", ".join(technologies))
        payload = {**item, "detail": detail}
    elif detail_error:
        payload = {**item, "detail_error": detail_error}

    requirements: list[str] = []
    experience_name = _name(source.get("experience"))
    if experience_name:
        requirements.append(f"Опыт работы: {experience_name}")

    extraction = OpportunityExtraction(
        title=name,
        company=employer,
        location=area,
        remote_policy=remote_policy(source),
        compensation=compensation(salary),
        employment_type=employment_type(source),
        requirements=requirements,
        technologies=technologies,
        summary=" ".join(p for p in (requirement, responsibility) if p) or None,
    )
    return JobPosting(
        platform=Platform.hh,
        external_id=_str(item.get("id")),
        url=_str(item.get("alternate_url")),
        title=name[:300],
        company=employer,
        location=area,
        posted_at=parse_ts(item.get("published_at")),
        raw_text="\n".join(ln for ln in raw_lines if ln is not None),
        extraction=extraction,
        raw_payload=payload,
    )


# ------------------------------------------------------------------------------ negotiation → obs


def negotiation_to_observation(item: dict[str, Any]) -> ApplicationObservationIn:
    raw_state, raw_vacancy = item.get("state"), item.get("vacancy")
    state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
    vacancy: dict[str, Any] = raw_vacancy if isinstance(raw_vacancy, dict) else {}
    state_id = _id(state)
    state_name = _name(state) or state_id or ""
    status = STATE_STATUS.get(state_id or "") or parsers.normalize_status(state_name)
    if status == ApplicationStatus.applied and item.get("viewed_by_opponent") is True:
        status = ApplicationStatus.viewed
    return ApplicationObservationIn(
        platform=Platform.hh,
        external_id=_str(item.get("id")),
        job_title=strip_tags(vacancy.get("name")) or "",
        company=_name(vacancy.get("employer")),
        job_url=_str(vacancy.get("alternate_url")),
        status_raw=state_name,
        status=status,
        applied_at=parse_ts(item.get("created_at")),
        updated_at_platform=parse_ts(item.get("updated_at")),
        raw_payload=item,
    )


# ------------------------------------------------------------------------------ /me → account


def me_to_account(me: dict[str, Any]) -> AccountInfo:
    first = strip_tags(me.get("first_name")) or ""
    last = strip_tags(me.get("last_name")) or ""
    return AccountInfo(
        account_id=_str(me.get("id")),
        label=f"{first} {last}".strip() or None,
        raw={k: me.get(k) for k in ("id", "email", "auth_type", "is_applicant")},
    )
