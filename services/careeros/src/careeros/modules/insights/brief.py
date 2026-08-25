"""Daily career brief (brief §52): deterministic stats + ranked actions; AI narrative optional."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.logging import get_logger
from careeros.modules.ai.provider import AIError
from careeros.modules.ai.service import AIService
from careeros.modules.insights.notifications import NotificationKind, compute_notifications

log = get_logger(__name__)


class BriefAction(BaseModel):
    priority: int
    kind: str
    text: str
    url_path: str


class BriefStats(BaseModel):
    new_opportunities: int = 0
    best_opportunity: dict[str, Any] | None = None
    urgent_messages: int = 0
    follow_ups_due: int = 0
    follow_ups_overdue: int = 0
    interviews_soon: int = 0
    profiles_out_of_sync: int = 0
    pending_suggestions: int = 0
    applications_active: int = 0


class DailyBriefOutput(BaseModel):
    narrative: str = Field(min_length=20, max_length=1200)


class BriefOut(BaseModel):
    date: str
    greeting: str
    stats: BriefStats
    actions: list[BriefAction]
    narrative: str | None = None
    ai_run_id: uuid.UUID | None = None
    computed_at: datetime


def _greeting(now: datetime) -> str:
    h = now.hour
    return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"


async def compute_brief(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    ai: AIService | None = None,
    narrative: bool = False,
    now: datetime | None = None,
) -> BriefOut:
    from careeros.modules.opportunities.service import new_opportunity_stats
    from careeros.modules.pipeline.service import active_application_count
    from careeros.modules.profiles.drift import open_drift_count

    now = now or datetime.now(UTC)
    notes = await compute_notifications(session, user_id)
    new_count, best = await new_opportunity_stats(session)
    stats = BriefStats(
        new_opportunities=new_count,
        best_opportunity=best,
        urgent_messages=sum(1 for n in notes.items if n.kind == NotificationKind.urgent_message),
        follow_ups_due=sum(1 for n in notes.items if n.kind == NotificationKind.follow_up_due),
        follow_ups_overdue=sum(
            1 for n in notes.items if n.kind == NotificationKind.follow_up_overdue
        ),
        interviews_soon=sum(1 for n in notes.items if n.kind == NotificationKind.interview_soon),
        profiles_out_of_sync=await open_drift_count(session),
        pending_suggestions=next(
            (
                int(n.title.split(" ")[0])
                for n in notes.items
                if n.kind == NotificationKind.pending_suggestions
            ),
            0,
        ),
        applications_active=await active_application_count(session),
    )

    actions: list[BriefAction] = []
    for n in notes.items:
        if n.kind == NotificationKind.follow_up_overdue:
            actions.append(
                BriefAction(priority=1, kind="follow_up", text=n.title, url_path=n.url_path)
            )
        elif n.kind == NotificationKind.interview_soon:
            actions.append(
                BriefAction(
                    priority=1, kind="interview", text=f"Prepare — {n.title}", url_path=n.url_path
                )
            )
        elif n.kind == NotificationKind.urgent_message:
            actions.append(
                BriefAction(
                    priority=2, kind="reply", text=f"Reply — {n.title}", url_path=n.url_path
                )
            )
    if best and best["score"] >= 65:
        actions.append(
            BriefAction(
                priority=2,
                kind="apply",
                text=(
                    f"{best['recommendation'].replace('_', ' ').capitalize()}: "
                    f"{best['title']} ({best['score']}/100)"
                ),
                url_path=f"/opportunities/{best['id']}",
            )
        )
    for n in notes.items:
        if n.kind == NotificationKind.follow_up_due:
            actions.append(
                BriefAction(priority=3, kind="follow_up", text=n.title, url_path=n.url_path)
            )
    if stats.profiles_out_of_sync:
        actions.append(
            BriefAction(
                priority=3,
                kind="drift",
                text=(
                    f"Fix {stats.profiles_out_of_sync} profile drift finding"
                    f"{'s' if stats.profiles_out_of_sync != 1 else ''}"
                ),
                url_path="/profiles",
            )
        )
    if stats.pending_suggestions:
        actions.append(
            BriefAction(
                priority=4,
                kind="review",
                text=f"Review {stats.pending_suggestions} AI suggestion(s)",
                url_path="/suggestions",
            )
        )
    actions.sort(key=lambda a: a.priority)
    actions = actions[:8]

    text: str | None = None
    run_id: uuid.UUID | None = None
    if narrative and ai is not None:
        try:
            run = await ai.structured(
                "daily_brief",
                {
                    "date": now.date().isoformat(),
                    "stats": stats.model_dump(),
                    "actions": [a.text for a in actions],
                },
                DailyBriefOutput,
                entity_type="brief",
                entity_id=now.date().isoformat(),
            )
            text, run_id = run.data.narrative, run.run_id
        except AIError as exc:
            log.warning("brief.narrative_failed", error=str(exc))

    return BriefOut(
        date=now.date().isoformat(),
        greeting=_greeting(now.astimezone()),
        stats=stats,
        actions=actions,
        narrative=text,
        ai_run_id=run_id,
        computed_at=now,
    )
