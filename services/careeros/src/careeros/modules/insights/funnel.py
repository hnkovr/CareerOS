"""Funnel analytics (brief §36) over the application pipeline."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.pipeline.enums import (
    APPLIED_STAGES,
    STAGES,
    TERMINAL_STAGES,
    PipelineKind,
    Stage,
)


class FunnelOut(BaseModel):
    applications_total: int
    active: int
    by_kind: dict[str, int]
    by_stage: dict[str, int]
    applied: int
    with_response: int
    response_rate: float | None
    interviews: int
    interview_rate: float | None
    offers: int
    offer_rate: float | None
    rejected: int
    median_days_to_first_response: float | None
    events_by_kind: dict[str, int]


INTERVIEW_STAGES = {
    Stage.recruiter_screen,
    Stage.technical,
    Stage.final,
    Stage.offer,
    Stage.discovery,
    Stage.negotiation,
    Stage.active,
    Stage.won,
}
OFFER_STAGES = {Stage.offer, Stage.won, Stage.active}


def compute_funnel(rows: list[dict[str, Any]]) -> FunnelOut:
    total = len(rows)
    by_kind = dict(Counter(r["kind"] for r in rows))
    by_stage: dict[str, int] = {str(st): 0 for kind in PipelineKind for st in STAGES[kind]}
    for r in rows:
        by_stage[r["stage"]] = by_stage.get(r["stage"], 0) + 1
    by_stage = {k: v for k, v in by_stage.items() if v}
    applied = [
        r
        for r in rows
        if r["applied_at"] is not None
        or Stage(r["stage"]) in APPLIED_STAGES
        or Stage(r["stage"]) in INTERVIEW_STAGES
    ]
    responded = [r for r in applied if any(e["kind"] == "message_received" for e in r["events"])]
    interviewed = [r for r in applied if r["interviews"] or Stage(r["stage"]) in INTERVIEW_STAGES]
    offers = [
        r
        for r in rows
        if Stage(r["stage"]) in OFFER_STAGES or any(e["kind"] == "offer" for e in r["events"])
    ]
    rejected = [r for r in rows if Stage(r["stage"]) in (Stage.rejected, Stage.lost)]
    days: list[float] = []
    for r in responded:
        start: datetime | None = r["applied_at"] or r["created_at"]
        first = min((e["at"] for e in r["events"] if e["kind"] == "message_received"), default=None)
        if start and first and first >= start:
            days.append((first - start).total_seconds() / 86400)
    events = Counter(e["kind"] for r in rows for e in r["events"])

    def rate(num: int, den: int) -> float | None:
        return round(num / den, 3) if den else None

    return FunnelOut(
        applications_total=total,
        active=sum(1 for r in rows if Stage(r["stage"]) not in TERMINAL_STAGES),
        by_kind=by_kind,
        by_stage=by_stage,
        applied=len(applied),
        with_response=len(responded),
        response_rate=rate(len(responded), len(applied)),
        interviews=len(interviewed),
        interview_rate=rate(len(interviewed), len(applied)),
        offers=len(offers),
        offer_rate=rate(len(offers), len(applied)),
        rejected=len(rejected),
        median_days_to_first_response=round(median(days), 1) if days else None,
        events_by_kind=dict(events),
    )


async def funnel_for(session: AsyncSession) -> FunnelOut:
    from careeros.modules.pipeline.service import funnel_rows

    return compute_funnel(await funnel_rows(session))
