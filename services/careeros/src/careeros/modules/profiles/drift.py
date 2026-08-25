"""Drift detection (brief §12): the same fact told differently across platforms, or differently
from the vault. Deterministic; findings persist so the owner can resolve or dismiss them.

Public entry points (service-level, safe to import from other modules): ``recompute_drift``,
``list_drift``, ``set_drift_resolution``, ``open_drift_count``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.cv.keywords import (
    contains_keyword,
    extract_known_tech,
    normalize,
    tech_vocabulary,
)
from careeros.modules.profiles.enums import PROFILE_PLATFORMS, Severity
from careeros.modules.profiles.models import DriftFinding, ProfileSnapshot
from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import ItemStatus, SkillTier

VAULT = "vault"
_YEARS_RE = re.compile(r"(\d{1,2})\+?\s+years", re.IGNORECASE)
RATE_DRIFT_PCT = 0.2


class DriftOut(BaseModel):
    id: uuid.UUID
    key: str
    field: str
    platform_a: str
    platform_b: str
    value_a: str
    value_b: str
    severity: Severity
    message: str
    resolution: str
    created_at: datetime


class DriftSummary(BaseModel):
    open: int
    by_field: dict[str, int]
    findings: list[DriftOut]


@dataclass(frozen=True)
class Draft:
    field: str
    platform_a: str
    platform_b: str
    value_a: str
    value_b: str
    severity: Severity
    message: str

    @property
    def key(self) -> str:
        raw = "|".join([self.field, self.platform_a, self.platform_b, self.value_a, self.value_b])
        return hashlib.sha1(raw.encode()).hexdigest()[:20]


@dataclass
class Snap:
    platform: str
    headline: str
    about: str
    text: str
    skills: list[str]
    rates: dict[str, Any]


def _snap(row: ProfileSnapshot) -> Snap:
    parts = [row.headline or "", row.about or "", row.raw_text or ""]
    parts += [
        f"{e.get('company', '')} {e.get('title', '')} {e.get('description', '')}"
        for e in (row.experience or [])
        if isinstance(e, dict)
    ]
    return Snap(
        platform=row.platform,
        headline=row.headline or "",
        about=row.about or "",
        text=" ".join(parts),
        skills=[str(x) for x in (row.skills or [])],
        rates=dict(row.rates or {}),
    )


def _years(text: str) -> int | None:
    found = [int(m) for m in _YEARS_RE.findall(text)]
    return max(found) if found else None


def _rate(rates: dict[str, Any]) -> float | None:
    for key in ("hourly", "hourly_rate", "rate", "per_hour"):
        value = rates.get(key)
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            digits = re.sub(r"[^\d.]", "", value)
            if digits:
                try:
                    return float(digits)
                except ValueError:
                    pass
    return None


def detect_drift(
    data: s.VaultData, snaps: list[Snap], *, now: datetime | None = None
) -> list[Draft]:
    """Pure comparison. ``snaps`` = latest snapshot per platform."""
    now = now or datetime.now(UTC)
    drafts: list[Draft] = []
    vocab = tech_vocabulary(data)
    vault_years = now.year - data.profile.years_experience_since
    vault_skill_names = {sk.name.lower() for sk in data.skills} | {
        a.lower() for sk in data.skills for a in sk.aliases
    }
    first_priority = [
        sk.name
        for sk in data.skills
        if sk.tier == SkillTier.first_priority and sk.status != ItemStatus.retired
    ]
    current = next((e for e in data.experience if e.end is None), None)
    target_hourly = data.scoring.compensation.target_hourly if data.scoring else None

    # --- years of experience: each platform vs vault, then pairwise
    claims: dict[str, int] = {}
    for sn in snaps:
        y = _years(f"{sn.headline} {sn.about}")
        if y is None:
            continue
        claims[sn.platform] = y
        if abs(y - vault_years) > 1:
            drafts.append(
                Draft(
                    "years_experience",
                    sn.platform,
                    VAULT,
                    f"{y} years",
                    f"~{vault_years} years",
                    Severity.high,
                    f"{sn.platform} claims {y} years; the vault implies ~{vault_years}",
                )
            )
    platforms = sorted(claims)
    for i, a in enumerate(platforms):
        for b in platforms[i + 1 :]:
            if abs(claims[a] - claims[b]) > 1:
                drafts.append(
                    Draft(
                        "years_experience",
                        a,
                        b,
                        f"{claims[a]} years",
                        f"{claims[b]} years",
                        Severity.high,
                        f"{a} says {claims[a]} years, {b} says {claims[b]}",
                    )
                )

    # --- headline technologies: unknown to vault; first-priority present here, absent there
    headline_tech: dict[str, set[str]] = {}
    for sn in snaps:
        techs = set(extract_known_tech(sn.headline, vocab))
        headline_tech[sn.platform] = techs
        unknown = [t for t in techs if t.lower() not in vault_skill_names]
        for t in unknown:
            drafts.append(
                Draft(
                    "headline_technology",
                    sn.platform,
                    VAULT,
                    t,
                    "not in vault skills",
                    Severity.medium,
                    f"{sn.platform} headline mentions {t}, which the vault does not claim",
                )
            )
        if "snowflake" not in vault_skill_names and contains_keyword(
            normalize(sn.headline), "snowflake"
        ):
            pass  # covered by unknown-tech rule; explicit example from the brief
    for tech in first_priority:
        present = [p for p, techs in headline_tech.items() if tech in techs]
        absent = [p for p in headline_tech if tech not in headline_tech[p]]
        if present and absent:
            for p in absent:
                drafts.append(
                    Draft(
                        "headline_technology",
                        p,
                        present[0],
                        f"{tech} missing",
                        f"{tech} present",
                        Severity.medium,
                        f"{tech} is in the {present[0]} headline but not on {p}",
                    )
                )

    # --- rates across freelance platforms and vs targets
    rates = {sn.platform: _rate(sn.rates) for sn in snaps if _rate(sn.rates) is not None}
    rate_platforms = sorted(rates)
    for i, a in enumerate(rate_platforms):
        ra = rates[a]
        assert ra is not None
        if target_hourly and abs(ra - target_hourly) / target_hourly > RATE_DRIFT_PCT:
            drafts.append(
                Draft(
                    "rate",
                    a,
                    VAULT,
                    f"{ra:g}/h",
                    f"target {target_hourly}/h",
                    Severity.medium,
                    f"{a} rate {ra:g}/h is far from the scoring target {target_hourly}/h",
                )
            )
        for b in rate_platforms[i + 1 :]:
            rb = rates[b]
            assert rb is not None
            if abs(ra - rb) / max(ra, rb) > RATE_DRIFT_PCT:
                drafts.append(
                    Draft(
                        "rate",
                        a,
                        b,
                        f"{ra:g}/h",
                        f"{rb:g}/h",
                        Severity.high,
                        f"{a} rate {ra:g}/h vs {b} rate {rb:g}/h",
                    )
                )

    # --- current employer present here, missing there
    if current is not None:
        name = current.company_name.lower()
        has = {sn.platform: name in sn.text.lower() for sn in snaps if sn.text.strip()}
        present = [p for p, ok in has.items() if ok]
        for p, ok in has.items():
            if not ok:
                ref = present[0] if present else VAULT
                drafts.append(
                    Draft(
                        "current_employer",
                        p,
                        ref,
                        "missing",
                        current.company_name,
                        Severity.high,
                        f"{p} does not show the current role at {current.company_name}",
                    )
                )

    # --- location
    city = data.profile.location.city.lower()
    loc = {sn.platform: city in sn.text.lower() for sn in snaps if sn.about or sn.headline}
    present = [p for p, ok in loc.items() if ok]
    for p, ok in loc.items():
        if not ok and present:
            drafts.append(
                Draft(
                    "location",
                    p,
                    present[0],
                    "not stated",
                    data.profile.location.city,
                    Severity.nice,
                    f"{present[0]} states {data.profile.location.city}; {p} does not",
                )
            )

    # --- first-priority skills listed on one platform, absent on another
    listed: dict[str, set[str]] = {
        sn.platform: {x.lower() for x in sn.skills} for sn in snaps if sn.skills
    }
    for tech in first_priority:
        have = [p for p, sk in listed.items() if tech.lower() in sk]
        lack = [p for p in listed if tech.lower() not in listed[p]]
        if have and lack:
            for p in lack:
                drafts.append(
                    Draft(
                        "skills",
                        p,
                        have[0],
                        f"{tech} not listed",
                        f"{tech} listed",
                        Severity.nice,
                        f"skill {tech} is listed on {have[0]} but not on {p}",
                    )
                )

    seen: set[str] = set()
    unique: list[Draft] = []
    for d in drafts:
        if d.key not in seen:
            seen.add(d.key)
            unique.append(d)
    return unique


async def latest_snapshots(session: AsyncSession) -> list[ProfileSnapshot]:
    out: list[ProfileSnapshot] = []
    for platform in PROFILE_PLATFORMS:
        row = await session.scalar(
            select(ProfileSnapshot)
            .where(ProfileSnapshot.platform == str(platform))
            .order_by(ProfileSnapshot.captured_at.desc())
            .limit(1)
        )
        if row is not None:
            out.append(row)
    return out


async def recompute_drift(
    session: AsyncSession, user_id: uuid.UUID, data: s.VaultData
) -> DriftSummary:
    snaps = [_snap(r) for r in await latest_snapshots(session)]
    drafts = detect_drift(data, snaps)
    decided = {
        row.key: row.resolution
        for row in (
            await session.scalars(select(DriftFinding).where(DriftFinding.resolution != "open"))
        ).all()
    }
    await session.execute(delete(DriftFinding).where(DriftFinding.resolution == "open"))
    for d in drafts:
        if d.key in decided:
            continue  # dismissed/resolved earlier and unchanged since → stays decided
        session.add(
            DriftFinding(
                user_id=user_id,
                key=d.key,
                field=d.field,
                platform_a=d.platform_a,
                platform_b=d.platform_b,
                value_a=d.value_a[:300],
                value_b=d.value_b[:300],
                severity=str(d.severity),
                message=d.message[:500],
            )
        )
    await session.commit()
    return await list_drift(session)


async def list_drift(session: AsyncSession, *, open_only: bool = False) -> DriftSummary:
    stmt = select(DriftFinding).order_by(DriftFinding.severity, DriftFinding.field)
    if open_only:
        stmt = stmt.where(DriftFinding.resolution == "open")
    rows = (await session.scalars(stmt)).all()
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "nice": 3}
    rows = sorted(
        rows, key=lambda r: (r.resolution != "open", severity_rank.get(r.severity, 9), r.field)
    )
    findings = [
        DriftOut(
            id=r.id,
            key=r.key,
            field=r.field,
            platform_a=r.platform_a,
            platform_b=r.platform_b,
            value_a=r.value_a,
            value_b=r.value_b,
            severity=Severity(r.severity),
            message=r.message,
            resolution=r.resolution,
            created_at=r.created_at,
        )
        for r in rows
    ]
    open_rows = [f for f in findings if f.resolution == "open"]
    by_field: dict[str, int] = {}
    for f in open_rows:
        by_field[f.field] = by_field.get(f.field, 0) + 1
    return DriftSummary(open=len(open_rows), by_field=by_field, findings=findings)


async def set_drift_resolution(
    session: AsyncSession, finding_id: uuid.UUID, resolution: str
) -> DriftOut | None:
    row = await session.get(DriftFinding, finding_id)
    if row is None:
        return None
    row.resolution = resolution
    await session.commit()
    return DriftOut(
        id=row.id,
        key=row.key,
        field=row.field,
        platform_a=row.platform_a,
        platform_b=row.platform_b,
        value_a=row.value_a,
        value_b=row.value_b,
        severity=Severity(row.severity),
        message=row.message,
        resolution=row.resolution,
        created_at=row.created_at,
    )


async def open_drift_count(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(DriftFinding).where(DriftFinding.resolution == "open")
        )
        or 0
    )
