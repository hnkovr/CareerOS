"""Computed notifications: read-only aggregation over pipeline, opportunities, inbox, AI and
profiles — through each module's service layer only (invariant 7; enforced by import-linter)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.ai.service import pending_suggestion_count
from careeros.modules.inbox.service import unread_urgent_messages
from careeros.modules.opportunities.service import top_new_opportunities
from careeros.modules.pipeline.service import due_follow_ups, upcoming_interviews
from careeros.modules.profiles.service import open_drift_count


class NotificationKind(StrEnum):
    follow_up_overdue = "follow_up_overdue"
    follow_up_due = "follow_up_due"
    interview_soon = "interview_soon"
    high_score_opportunity = "high_score_opportunity"
    urgent_message = "urgent_message"
    pending_suggestions = "pending_suggestions"
    profile_drift = "profile_drift"


class Notification(BaseModel):
    kind: NotificationKind
    title: str
    detail: str | None = None
    url_path: str
    at: datetime | None = None
    severity: str = "normal"  # high | normal


class NotificationsOut(BaseModel):
    count: int
    high: int
    items: list[Notification]
    computed_at: datetime


INTERVIEW_HORIZON_H = 48
FOLLOW_UP_HORIZON_H = 24
HIGH_SCORE = 80


async def compute_notifications(session: AsyncSession, user_id: uuid.UUID) -> NotificationsOut:
    now = datetime.now(UTC)
    items: list[Notification] = []

    for f in await due_follow_ups(session, within_hours=FOLLOW_UP_HORIZON_H, limit=10):
        overdue = f["due_at"] < now
        items.append(
            Notification(
                kind=NotificationKind.follow_up_overdue
                if overdue
                else NotificationKind.follow_up_due,
                title=f"Follow up: {f['title']}",
                detail="overdue" if overdue else "due within a day",
                url_path=f"/pipeline/{f['application_id']}",
                at=f["due_at"],
                severity="high" if overdue else "normal",
            )
        )

    for iv in await upcoming_interviews(session, within_hours=INTERVIEW_HORIZON_H, limit=10):
        items.append(
            Notification(
                kind=NotificationKind.interview_soon,
                title=f"Interview ({str(iv['kind']).replace('_', ' ')}): {iv['title']}",
                detail=None,
                url_path=f"/pipeline/{iv['application_id']}",
                at=iv["scheduled_at"],
                severity="high",
            )
        )

    for opp in await top_new_opportunities(session, min_score=HIGH_SCORE, limit=5):
        items.append(
            Notification(
                kind=NotificationKind.high_score_opportunity,
                title=f"{opp['overall']}/100: {opp['title']}",
                detail=opp["company"],
                url_path=f"/opportunities/{opp['id']}",
                at=opp["received_at"],
                severity="high" if opp["overall"] >= 85 else "normal",
            )
        )

    for msg in await unread_urgent_messages(session, limit=5):
        items.append(
            Notification(
                kind=NotificationKind.urgent_message,
                title=f"Urgent: {msg['subject'] or '(no subject)'}",
                detail=msg["from_email"],
                url_path="/inbox",
                at=msg["received_at"],
                severity="high",
            )
        )

    pending = await pending_suggestion_count(session)
    if pending:
        items.append(
            Notification(
                kind=NotificationKind.pending_suggestions,
                title=f"{pending} AI suggestion{'s' if pending != 1 else ''} awaiting review",
                url_path="/suggestions",
                severity="normal",
            )
        )

    drift_open = await open_drift_count(session)
    if drift_open:
        items.append(
            Notification(
                kind=NotificationKind.profile_drift,
                title=f"Profiles out of sync: {drift_open}",
                url_path="/profiles",
                severity="normal",
            )
        )

    severity_rank = {"high": 0, "normal": 1}
    items.sort(key=lambda n: (severity_rank[n.severity], n.at or now))
    return NotificationsOut(
        count=len(items),
        high=sum(1 for n in items if n.severity == "high"),
        items=items,
        computed_at=now,
    )
