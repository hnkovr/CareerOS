"""Per-platform update checklist (brief §20 'Generate update checklist', §29 'suggested updates').

Deterministic composition over the latest audit's open findings, open drift findings for the
platform, and channel rules. Copy-ready text comes from the audit's AI suggestions when present,
otherwise from canonical positioning/profile trimmed to the channel limits — never invented here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from careeros.modules.profiles.drift import DriftOut, list_drift
from careeros.modules.profiles.enums import Severity
from careeros.modules.profiles.models import ProfileAudit, ProfileSnapshot
from careeros.modules.vault import schema as s
from careeros.modules.vault.enums import Platform

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "nice": 3}


class ChecklistItem(BaseModel):
    order: int
    severity: Severity
    origin: str  # audit | drift
    category: str
    action: str
    why: str | None = None
    current: str | None = None
    suggested: str | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    ref_id: uuid.UUID | None = Field(default=None, description="audit finding / drift finding id")


class CopyReady(BaseModel):
    headline: str | None = None
    headline_limit: int | None = None
    about: str | None = None
    about_limit: int | None = None
    source: str = "vault"  # ai | vault


class ChecklistOut(BaseModel):
    platform: Platform
    generated_at: datetime
    snapshot_id: uuid.UUID | None
    audit_id: uuid.UUID | None
    health_score: int | None
    items: list[ChecklistItem]
    copy_ready: CopyReady
    notes: list[str] = Field(default_factory=list)


def _trim(text: str, limit: int | None) -> str:
    if not limit or len(text) <= limit:
        return text
    cut = text[: limit - 1]
    return (cut[: cut.rfind(" ")] if " " in cut else cut).rstrip(",;:—- ") + "…"


def compose_checklist(
    data: s.VaultData,
    platform: Platform,
    *,
    audit: ProfileAudit | None,
    drift: list[DriftOut],
    snapshot_id: uuid.UUID | None,
    now: datetime | None = None,
) -> ChecklistOut:
    now = now or datetime.now(UTC)
    channel = next((c for c in data.channels if c.platform == platform), None)
    positioning = data.by_id(data.positioning)[data.meta.default_positioning]
    items: list[ChecklistItem] = []
    notes: list[str] = []

    if audit is not None:
        for f in audit.findings:
            if f.resolution != "open":
                continue
            items.append(
                ChecklistItem(
                    order=0,
                    severity=Severity(f.severity),
                    origin="audit",
                    category=f.category,
                    action=f.problem,
                    why=f.why_it_matters,
                    suggested=f.suggested_change,
                    source_fact_ids=list(f.source_fact_ids or []),
                    ref_id=f.id,
                )
            )
    else:
        notes.append("no audit yet for the latest snapshot — run an audit for a fuller checklist")

    for d in drift:
        if d.resolution != "open" or platform not in (d.platform_a, d.platform_b):
            continue
        other = d.platform_b if d.platform_a == platform else d.platform_a
        mine, theirs = (
            (d.value_a, d.value_b) if d.platform_a == platform else (d.value_b, d.value_a)
        )
        items.append(
            ChecklistItem(
                order=0,
                severity=d.severity,
                origin="drift",
                category=f"drift:{d.field}",
                action=d.message,
                why=f"contradicts {other}",
                current=mine,
                suggested=theirs if other == "vault" else None,
                ref_id=d.id,
            )
        )

    items.sort(key=lambda i: (SEVERITY_RANK.get(str(i.severity), 9), i.origin, i.category))
    for n, item in enumerate(items, 1):
        item.order = n

    headline_limit = channel.limits.headline_chars if channel else None
    about_limit = channel.limits.about_chars if channel else None
    ai_headline = audit.headline_suggestion if audit else None
    ai_about = audit.about_suggestion if audit else None
    about_base = data.profile.summary_core
    if channel and channel.cta:
        about_base = f"{about_base}\n\n{channel.cta}"
    copy_ready = CopyReady(
        headline=_trim(ai_headline or positioning.headline, headline_limit),
        headline_limit=headline_limit,
        about=_trim(ai_about or about_base, about_limit),
        about_limit=about_limit,
        source="ai" if (ai_headline or ai_about) else "vault",
    )
    if channel is None:
        notes.append(f"no channel rules for {platform} in the vault — limits unknown")

    return ChecklistOut(
        platform=platform,
        generated_at=now,
        snapshot_id=snapshot_id,
        audit_id=audit.id if audit else None,
        health_score=audit.health_score if audit else None,
        items=items,
        copy_ready=copy_ready,
        notes=notes,
    )


async def checklist_for(
    session: AsyncSession, data: s.VaultData, platform: Platform
) -> ChecklistOut:
    snapshot = await session.scalar(
        select(ProfileSnapshot)
        .options(selectinload(ProfileSnapshot.audits).selectinload(ProfileAudit.findings))
        .where(ProfileSnapshot.platform == str(platform))
        .order_by(ProfileSnapshot.captured_at.desc())
        .limit(1)
    )
    audit = snapshot.audits[0] if snapshot and snapshot.audits else None
    drift = (await list_drift(session, open_only=True)).findings
    return compose_checklist(
        data, platform, audit=audit, drift=drift, snapshot_id=snapshot.id if snapshot else None
    )
