"""Upwork GraphQL payloads → platform DTOs. Pure functions; unknown → ``None``; raw kept."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    RemotePolicy,
)
from careeros.modules.opportunities.schemas import Compensation, OpportunityExtraction
from careeros.modules.platform.enums import ApplicationStatus
from careeros.modules.platform.parsers import normalize_status, parse_date
from careeros.modules.platform.schemas import (
    AccountInfo,
    ApplicationObservationIn,
    JobPosting,
    ProfileRead,
)
from careeros.modules.profiles.enums import CaptureMethod
from careeros.modules.vault.enums import Platform

PLATFORM = Platform.upwork
SITE = "https://www.upwork.com"

# Upwork ``VendorProposalStatusName`` values (Accepted / Activated / Offered / Hired / Declined /
# Withdrawn / Archived / Pending) plus the spellings other surfaces use. Anything else falls back
# to the shared ``normalize_status`` keyword rules.
PROPOSAL_STATUS: dict[str, ApplicationStatus] = {
    "ACCEPTED": ApplicationStatus.applied,
    "ACTIVE": ApplicationStatus.applied,
    "SUBMITTED": ApplicationStatus.applied,
    "PENDING": ApplicationStatus.applied,
    "CREATED": ApplicationStatus.applied,
    "VIEWED": ApplicationStatus.viewed,
    # created by accepting a client's invitation → the interview room is open
    "ACTIVATED": ApplicationStatus.interview,
    "INTERVIEW": ApplicationStatus.interview,
    "INTERVIEWING": ApplicationStatus.interview,
    "SHORTLISTED": ApplicationStatus.interview,
    "OFFERED": ApplicationStatus.offer,
    "OFFER": ApplicationStatus.offer,
    "OFFER_SENT": ApplicationStatus.offer,
    "HIRED": ApplicationStatus.offer,
    "DECLINED": ApplicationStatus.rejected,
    "REJECTED": ApplicationStatus.rejected,
    "ARCHIVED": ApplicationStatus.rejected,
    "CLOSED": ApplicationStatus.rejected,
    "WITHDRAWN": ApplicationStatus.withdrawn,
}

# ``FreelancerProfileAvailabilityCapacity`` → the label Upwork shows; other values stay verbatim.
CAPACITY_LABELS: dict[str, str | None] = {
    "fullTime": "More than 30 hrs/week",
    "partTime": "Less than 30 hrs/week",
    "none": None,
}

_STATUS_KEY = re.compile(r"[\s-]+")


# ------------------------------------------------------------------------------ small helpers


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _get(value: Any, *path: str) -> Any:
    cur = value
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip()
    return s or None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().lstrip("$").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _dt(value: Any) -> datetime | None:
    text = _text(value)
    return parse_date(text) if text else None


def _with_tilde(key: str) -> str:
    key = key.strip()
    return key if key.startswith("~") else f"~{key}"


def job_url(ciphertext: str) -> str:
    return f"{SITE}/jobs/{_with_tilde(ciphertext)}"


def profile_url(profile_key: str) -> str:
    return f"{SITE}/freelancers/{_with_tilde(profile_key)}"


def _skill_names(value: Any) -> list[str]:
    """Skill lists come as ``[{name, prettyName}]`` or a connection ``{edges: [{node: …}]}``."""
    items: list[Any]
    if isinstance(value, dict):
        raw = value.get("edges") or value.get("nodes") or []
        items = raw if isinstance(raw, list) else []
    elif isinstance(value, list):
        items = value
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        node: Any = item.get("node", item) if isinstance(item, dict) else item
        if isinstance(node, dict) and isinstance(node.get("skill"), dict):
            node = node["skill"]
        name: str | None = None
        if isinstance(node, dict):
            name = (
                _text(node.get("prettyName"))
                or _text(node.get("preferredLabel"))
                or _text(node.get("name"))
            )
        elif isinstance(node, str):
            name = _text(node)
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


# ------------------------------------------------------------------------------ account / profile


def map_account(user: dict[str, Any]) -> AccountInfo:
    return AccountInfo(
        account_id=_text(user.get("nid")) or _text(user.get("id")),
        label=_text(user.get("name")),
        raw={"id": user.get("id"), "email": user.get("email")},
    )


def _availability(value: Any) -> str | None:
    av = _obj(value)
    if not av:
        return None
    parts: list[str] = []
    name = _text(av.get("name"))
    capacity = _text(av.get("capacity"))
    label = CAPACITY_LABELS.get(capacity, capacity) if capacity else None
    for part in (name, label):
        if part and part not in parts:
            parts.append(part)
    since = _text(av.get("availabilityDateTime"))
    if since:
        parts.append(f"available from {since[:10]}")
    return " · ".join(parts) or None


def _portfolio(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    out: list[dict[str, Any]] = []
    for item in items:
        title = _text(_obj(item).get("title"))
        if title:
            out.append({"name": title, "url": _text(_obj(item).get("projectUrl"))})
    return out


def map_profile(user: dict[str, Any], *, captured_at: datetime | None = None) -> ProfileRead:
    """``user { …, freelancerProfile { … } }`` → ``ProfileRead`` (client-only accounts → sparse)."""
    profile = _obj(user.get("freelancerProfile"))
    personal = _obj(profile.get("personalData"))
    charge = _obj(personal.get("chargeRate"))
    hourly = _float(charge.get("rawValue"))
    if hourly is None:
        hourly = _float(charge.get("displayValue"))
    rates: dict[str, Any] | None = None
    if hourly is not None:
        rates = {
            "hourly": hourly,
            "currency": _text(charge.get("currency")) or "USD",
            "raw": _text(charge.get("displayValue")) or f"{hourly:g}",
        }
    url = _text(personal.get("profileUrl"))
    if url and url.startswith("/"):
        url = SITE + url
    elif url and not url.startswith("http"):
        url = None
    key = _text(user.get("ciphertext"))
    if url is None and key:
        url = profile_url(key)
    return ProfileRead(
        platform=PLATFORM,
        capture_method=CaptureMethod.api,
        external_id=_text(user.get("id")) or _text(user.get("nid")),
        profile_url=url,
        captured_at=captured_at,
        headline=_text(personal.get("title")),
        about=_text(personal.get("description")),
        skills=_skill_names(profile.get("skills")),
        portfolio=_portfolio(profile.get("project")),
        rates=rates,
        availability=_availability(profile.get("availability")),
        raw_payload=user,
    )


# ------------------------------------------------------------------------------ jobs


def _job_compensation(node: dict[str, Any]) -> tuple[Compensation | None, str | None]:
    """Hourly range (period=hour) or fixed budget (period=project); ``(None, None)`` if unstated."""
    lo, hi = _float(node.get("hourlyBudgetMin")), _float(node.get("hourlyBudgetMax"))
    job_type = str(node.get("type") or "").upper()
    if lo is not None or hi is not None or job_type == "HOURLY":
        raw: str | None = None
        if lo is not None and hi is not None:
            raw = f"${lo:,.2f}-${hi:,.2f}/hr"
        elif lo is not None or hi is not None:
            raw = f"${(lo if lo is not None else hi):,.2f}/hr"
        comp = Compensation(
            min=lo,
            max=hi,
            currency="USD" if raw else None,
            period=CompensationPeriod.hour,
            type="rate",
            raw=raw,
        )
        return comp, raw
    amount = _obj(node.get("amount"))
    value = _float(amount.get("rawValue"))
    if value is not None and value > 0:
        currency = _text(amount.get("currency")) or "USD"
        display = _text(amount.get("displayValue")) or f"{value:,.2f} {currency}"
        raw = f"{display} fixed"
        comp = Compensation(
            min=value,
            max=value,
            currency=currency,
            period=CompensationPeriod.project,
            type=None,
            raw=raw,
        )
        return comp, raw
    return None, None


def map_job(node: dict[str, Any]) -> JobPosting:
    """Search-result node → ``JobPosting``; the client is anonymous in search (``company=None``)."""
    title = _text(node.get("title")) or f"Upwork job {node.get('id') or '?'}"
    description = _text(node.get("description")) or ""
    skills = _skill_names(node.get("skills"))
    ciphertext = _text(node.get("ciphertext"))
    country = _text(_get(node, "client", "location", "country"))
    posted = _dt(node.get("createdDateTime")) or _dt(node.get("publishedDateTime"))
    compensation, budget = _job_compensation(node)
    job_type = str(node.get("type") or "").upper()

    lines = [title]
    if description:
        lines.append(description)
    if skills:
        lines.append("Skills: " + ", ".join(skills))
    if budget:
        lines.append("Budget: " + budget)

    extraction = OpportunityExtraction(
        title=title,
        contract_type=ContractType.freelance,
        employment_type=EmploymentType.project if job_type == "FIXED" else None,
        location=country,
        remote_policy=RemotePolicy.remote_global,
        compensation=compensation,
        technologies=skills,
        summary=description[:600] or None,
    )
    return JobPosting(
        platform=PLATFORM,
        external_id=_text(node.get("id")),
        url=job_url(ciphertext) if ciphertext else None,
        title=title,
        company=None,
        location=country,
        posted_at=posted,
        raw_text="\n".join(lines),
        extraction=extraction,
        raw_payload=node,
    )


# ------------------------------------------------------------------------------ proposals


def proposal_status(raw: str, *, viewed: bool = False) -> ApplicationStatus:
    """Upwork status name → normalized status; ``viewedByClient`` upgrades *applied* to *viewed*."""
    key = _STATUS_KEY.sub("_", (raw or "").strip().upper())
    status = PROPOSAL_STATUS.get(key) or normalize_status(raw)
    if status == ApplicationStatus.applied and viewed:
        return ApplicationStatus.viewed
    return status


def map_proposal(node: dict[str, Any]) -> ApplicationObservationIn:
    job = _obj(node.get("marketplaceJobPosting"))
    external_id = _text(node.get("id"))
    title = _text(_get(job, "content", "title")) or f"Upwork proposal {external_id or '?'}"
    ciphertext = _text(job.get("ciphertext")) or _text(_get(job, "content", "ciphertext"))
    status_raw = _text(_get(node, "status", "status")) or ""
    audit = _obj(node.get("auditDetails"))
    return ApplicationObservationIn(
        platform=PLATFORM,
        external_id=external_id,
        job_title=title,
        company=_text(_get(job, "clientCompanyPublic", "name")),
        job_url=job_url(ciphertext) if ciphertext else None,
        status_raw=status_raw,
        status=proposal_status(status_raw, viewed=node.get("viewedByClient") is True),
        applied_at=_dt(audit.get("createdDateTime")),
        updated_at_platform=_dt(audit.get("modifiedDateTime")),
        raw_payload=node,
    )
