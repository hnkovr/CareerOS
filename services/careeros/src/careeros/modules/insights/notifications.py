"""Computed notifications: read-only aggregation over pipeline, opportunities, inbox, AI."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


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
    from careeros.modules.ai.models import Suggestion
    from careeros.modules.inbox.models import Message
    from careeros.modules.opportunities.models import Opportunity, OpportunityScore
    from careeros.modules.pipeline.models import Application, Interview

    now = datetime.now(UTC)
    items: list[Notification] = []

    # follow-ups (join opportunity for the title)
    rows = (
        await session.execute(
            select(Application, Opportunity.title)
            .join(Opportunity, Application.opportunity_id == Opportunity.id)
            .where(
                Application.next_follow_up_at.is_not(None),
                Application.next_follow_up_at <= now + timedelta(hours=FOLLOW_UP_HORIZON_H),
            )
            .order_by(Application.next_follow_up_at)
            .limit(10)
        )
    ).all()
    for app, title in rows:
        overdue = app.next_follow_up_at < now
        items.append(
            Notification(
                kind=NotificationKind.follow_up_overdue
                if overdue
                else NotificationKind.follow_up_due,
                title=f"Follow up: {title}",
                detail="overdue" if overdue else "due within a day",
                url_path=f"/pipeline/{app.id}",
                at=app.next_follow_up_at,
                severity="high" if overdue else "normal",
            )
        )

    # interviews in the next 48h
    rows = (
        await session.execute(
            select(Interview, Opportunity.title)
            .join(Application, Interview.application_id == Application.id)
            .join(Opportunity, Application.opportunity_id == Opportunity.id)
            .where(
                Interview.outcome == "pending",
                Interview.scheduled_at.is_not(None),
                Interview.scheduled_at >= now - timedelta(hours=2),
                Interview.scheduled_at <= now + timedelta(hours=INTERVIEW_HORIZON_H),
            )
            .order_by(Interview.scheduled_at)
            .limit(10)
        )
    ).all()
    for interview, title in rows:
        items.append(
            Notification(
                kind=NotificationKind.interview_soon,
                title=f"Interview ({interview.kind.replace('_', ' ')}): {title}",
                detail=None,
                url_path=f"/pipeline/{interview.application_id}",
                at=interview.scheduled_at,
                severity="high",
            )
        )

    # new high-score opportunities
    latest_score = (
        select(
            OpportunityScore.opportunity_id,
            func.max(OpportunityScore.computed_at).label("latest"),
        )
        .group_by(OpportunityScore.opportunity_id)
        .subquery()
    )
    rows = (
        await session.execute(
            select(Opportunity, OpportunityScore.overall)
            .join(OpportunityScore, OpportunityScore.opportunity_id == Opportunity.id)
            .join(
                latest_score,
                (latest_score.c.opportunity_id == OpportunityScore.opportunity_id)
                & (latest_score.c.latest == OpportunityScore.computed_at),
            )
            .where(Opportunity.status == "new", OpportunityScore.overall >= HIGH_SCORE)
            .order_by(OpportunityScore.overall.desc())
            .limit(5)
        )
    ).all()
    for opp, overall in rows:
        items.append(
            Notification(
                kind=NotificationKind.high_score_opportunity,
                title=f"{overall}/100: {opp.title}",
                detail=opp.company_name,
                url_path=f"/opportunities/{opp.id}",
                at=opp.received_at,
                severity="high" if overall >= 85 else "normal",
            )
        )

    # unread urgent messages
    rows = (
        await session.scalars(
            select(Message)
            .where(Message.read_at.is_(None), Message.urgency == "high")
            .order_by(Message.received_at.desc())
            .limit(5)
        )
    ).all()
    for msg in rows:
        items.append(
            Notification(
                kind=NotificationKind.urgent_message,
                title=f"Urgent: {msg.subject or '(no subject)'}",
                detail=msg.from_email,
                url_path="/inbox",
                at=msg.received_at,
                severity="high",
            )
        )

    # pending suggestions (one aggregate line)
    pending = (
        await session.scalar(
            select(func.count()).select_from(Suggestion).where(Suggestion.state == "suggested")
        )
        or 0
    )
    if pending:
        items.append(
            Notification(
                kind=NotificationKind.pending_suggestions,
                title=f"{pending} AI suggestion{'s' if pending != 1 else ''} awaiting review",
                url_path="/suggestions",
                severity="normal",
            )
        )

    from careeros.modules.profiles.drift import open_drift_count

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
