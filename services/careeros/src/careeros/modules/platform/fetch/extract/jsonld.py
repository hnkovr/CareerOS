"""schema.org ``JobPosting`` (JSON-LD) → ``JobPosting`` + per-field evidence (spec §5.4).

Deterministic and tolerant: ``@graph`` and lists are walked, descriptions may be markdown,
escaped HTML or HTML, salaries may be numbers / ``QuantitativeValue`` / lists. Nothing here
invents a value — a field the page does not state stays ``None``.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
)
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform.fetch.extract.text import html_to_text, looks_like_html
from careeros.modules.platform.schemas import FieldEvidence, JobPosting
from careeros.modules.vault.enums import Platform

SOURCE = "jsonld"
CONFIDENCE = 0.9

_LD_RE = re.compile(
    r"<script\b[^>]*type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_CDATA = re.compile(r"^\s*(?:<!--|<!\[CDATA\[)|(?:-->|\]\]>)\s*$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MAX_DEPTH = 8

_PERIOD = {
    "HOUR": CompensationPeriod.hour,
    "DAY": CompensationPeriod.day,
    "MONTH": CompensationPeriod.month,
    "YEAR": CompensationPeriod.year,
}
_EMPLOYMENT: dict[str, tuple[EmploymentType | None, ContractType | None]] = {
    "FULL_TIME": (EmploymentType.full_time, None),
    "PART_TIME": (EmploymentType.part_time, None),
    "CONTRACTOR": (None, ContractType.b2b),
    "TEMPORARY": (EmploymentType.project, None),
    "INTERN": (None, None),
    "VOLUNTEER": (None, None),
    "PER_DIEM": (None, None),
    "OTHER": (None, None),
}


def iter_jsonld(html: str) -> Iterator[Any]:
    """Every parseable ``application/ld+json`` block in ``html`` (unparseable ones are skipped)."""
    for m in _LD_RE.finditer(html or ""):
        raw = _CDATA.sub("", m.group(1)).strip()
        if not raw:
            continue
        try:
            yield json.loads(raw)
            continue
        except ValueError:
            pass
        try:
            yield json.loads(_CONTROL.sub("", raw))
        except ValueError:
            continue


def _is_jobposting(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    if isinstance(kind, str):
        return kind.rsplit("/", 1)[-1] == "JobPosting"
    if isinstance(kind, list):
        return any(isinstance(k, str) and k.rsplit("/", 1)[-1] == "JobPosting" for k in kind)
    return False


def _walk(node: Any, depth: int = 0) -> dict[str, Any] | None:
    if depth > _MAX_DEPTH:
        return None
    if isinstance(node, dict):
        if _is_jobposting(node):
            return node
        for key in ("@graph", "mainEntity", "itemListElement", "item", "hasPart"):
            found = _walk(node.get(key), depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(node, list):
        for item in node:
            found = _walk(item, depth + 1)
            if found is not None:
                return found
    return None


def find_jobposting(html_or_data: str | Any) -> dict[str, Any] | None:
    """First schema.org ``JobPosting`` node in an HTML page (or in already parsed JSON-LD)."""
    if isinstance(html_or_data, str):
        for data in iter_jsonld(html_or_data):
            found = _walk(data)
            if found is not None:
                return found
        return None
    return _walk(html_or_data)


# ------------------------------------------------------------------------------ field mappers


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("name", "@value", "value", "text"):
            got = _text(value.get(key))
            if got:
                return got
        return None
    if isinstance(value, list):
        parts = [p for p in (_text(v) for v in value) if p]
        return ", ".join(dict.fromkeys(parts)) or None
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return None


def _description(value: Any) -> str | None:
    raw = _text(value) if not isinstance(value, str) else value.strip()
    if not raw:
        return None
    if "&lt;" in raw or "&amp;" in raw:
        raw = html_lib.unescape(raw)
    return html_to_text(raw) if looks_like_html(raw) else raw


def _place(value: Any) -> str | None:
    if isinstance(value, list):
        parts = [p for p in (_place(v) for v in value) if p]
        return "; ".join(dict.fromkeys(parts)) or None
    if isinstance(value, dict):
        address = value.get("address")
        if isinstance(address, dict):
            bits = [
                _text(address.get(k))
                for k in ("addressLocality", "addressRegion", "addressCountry")
            ]
            joined = ", ".join(b for b in bits if b)
            if joined:
                return joined
        if isinstance(address, str) and address.strip():
            return address.strip()
        return _text(value.get("name")) or _text(address)
    return _text(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.,-]", "", value).replace(",", "")
        try:
            return float(cleaned) if cleaned else None
        except ValueError:
            return None
    return None


def _salary(value: Any) -> Compensation | None:
    if isinstance(value, list):
        for v in value:
            got = _salary(v)
            if got:
                return got
        return None
    if not isinstance(value, dict):
        return None
    currency = _text(value.get("currency")) or _text(value.get("priceCurrency"))
    inner = value.get("value")
    lo = hi = None
    unit = None
    if isinstance(inner, dict):
        lo = _number(inner.get("minValue"))
        hi = _number(inner.get("maxValue"))
        single = _number(inner.get("value"))
        if lo is None and hi is None and single is not None:
            lo = hi = single
        unit = _text(inner.get("unitText"))
    elif inner is not None:
        single = _number(inner)
        if single is not None:
            lo = hi = single
    if lo is None and hi is None:
        lo = _number(value.get("minValue"))
        hi = _number(value.get("maxValue"))
    unit = unit or _text(value.get("unitText"))
    if lo is None and hi is None:
        return None
    period = _PERIOD.get((unit or "").upper())
    return Compensation(
        min=lo,
        max=hi,
        currency=(currency or None) and currency.upper(),
        period=period,
        type="salary",
        raw=json.dumps(value, ensure_ascii=False, default=str)[:300],
    )


def _employment(value: Any) -> tuple[EmploymentType | None, ContractType | None]:
    values = value if isinstance(value, list) else [value]
    emp: EmploymentType | None = None
    contract: ContractType | None = None
    for v in values:
        key = (_text(v) or "").upper().replace("-", "_").replace(" ", "_")
        got = _EMPLOYMENT.get(key)
        if got is None:
            continue
        emp = emp or got[0]
        contract = contract or got[1]
    return emp, contract


def _datetime(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    candidate = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time())
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = html_to_text(value) if looks_like_html(value) else value
        parts = re.split(r"[\n;]+|,\s*(?=[^\d])", text)
        return [p.strip(" -•·*") for p in parts if p.strip(" -•·*")]
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if isinstance(v, str):
                out.extend(_list(v))
                continue
            got = _text(v)
            if got:
                out.append(got)
        return out
    if isinstance(value, dict):
        got = _text(value)
        return [got] if got else []
    return []


def _identifier(value: Any) -> str | None:
    if isinstance(value, list):
        for v in value:
            got = _identifier(v)
            if got:
                return got
        return None
    if isinstance(value, dict):
        return _text(value.get("value")) or _text(value.get("@id")) or _text(value.get("name"))
    return _text(value)


def _remote(data: dict[str, Any]) -> tuple[RemotePolicy, list[str]]:
    kind = (_text(data.get("jobLocationType")) or "").upper()
    if kind != "TELECOMMUTE":
        return RemotePolicy.unknown, []
    regions = _list(_place(data.get("applicantLocationRequirements")))
    return (RemotePolicy.remote_region if regions else RemotePolicy.remote_global), regions


def jobposting_to_posting(
    data: dict[str, Any],
    platform: Platform,
    url: str | None,
    *,
    fetched_at: datetime | None = None,
) -> JobPosting:
    """Map a ``JobPosting`` node. Raises ``ValueError`` when it has no title (not a job)."""
    title = _text(data.get("title")) or _text(data.get("name"))
    if not title:
        raise ValueError("JSON-LD JobPosting without title")
    company = _text(data.get("hiringOrganization"))
    location = _place(data.get("jobLocation"))
    remote_policy, regions = _remote(data)
    compensation = _salary(data.get("baseSalary")) or _salary(data.get("estimatedSalary"))
    employment_type, contract_type = _employment(data.get("employmentType"))
    posted = _datetime(data.get("datePosted"))
    valid_through = _datetime(data.get("validThrough"))
    skills = _list(data.get("skills"))
    qualifications = _list(data.get("qualifications")) + _list(data.get("experienceRequirements"))
    responsibilities = _list(data.get("responsibilities"))
    description = _description(data.get("description"))
    external_id = _identifier(data.get("identifier"))

    extraction = OpportunityExtraction(
        title=title,
        company=company,
        contract_type=contract_type,
        employment_type=employment_type,
        location=location,
        remote_policy=remote_policy,
        remote_regions=regions,
        compensation=compensation,
        requirements=qualifications,
        technologies=skills,
        responsibilities=responsibilities,
        deadline=valid_through.date() if valid_through else None,
    )
    evidence: list[FieldEvidence] = []
    observed: dict[str, Any] = {
        "title": title,
        "company": company,
        "location": location,
        "remote_policy": None if remote_policy == RemotePolicy.unknown else str(remote_policy),
        "remote_regions": regions or None,
        "compensation": compensation.model_dump(mode="json") if compensation else None,
        "employment_type": str(employment_type) if employment_type else None,
        "contract_type": str(contract_type) if contract_type else None,
        "technologies": skills or None,
        "requirements": qualifications or None,
        "responsibilities": responsibilities or None,
        "published_at": posted.isoformat() if posted else None,
        "expires_at": valid_through.isoformat() if valid_through else None,
        "description": description[:200] if description else None,
        "external_id": external_id,
    }
    for name, value in observed.items():
        if value is None:
            continue
        evidence.append(
            FieldEvidence(
                field=name,
                value=value,
                source=SOURCE,
                source_url=url,
                observed_at=fetched_at,
                confidence=CONFIDENCE,
            )
        )
    header = [p for p in (title, company, location) if p]
    raw_text = "\n".join(header) + ("\n\n" + description if description else "")
    return JobPosting(
        platform=platform,
        external_id=external_id,
        url=url,
        title=title[:300],
        company=company,
        location=location,
        posted_at=posted,
        published_at=posted,
        expires_at=valid_through,
        raw_text=raw_text,
        extraction=extraction,
        raw_payload={
            "jsonld": data,
            "direct_apply": data.get("directApply"),
            "jsonld_url": _text(data.get("url")),
        },
        field_evidence=evidence,
    )
