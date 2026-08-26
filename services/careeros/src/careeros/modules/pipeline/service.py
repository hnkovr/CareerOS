"""Pipeline service. Stage moves are validated per kind and always leave a timeline event."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from careeros.core.logging import get_logger
from careeros.modules.opportunities.models import Opportunity
from careeros.modules.pipeline.enums import (
    APPLIED_STAGES,
    STAGES,
    TERMINAL_STAGES,
    EventKind,
    PipelineKind,
    Stage,
)
from careeros.modules.pipeline.models import Application, ApplicationEvent, Interview
from careeros.modules.pipeline.schemas import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationOut,
    ApplicationUpdate,
    BoardColumn,
    BoardOut,
    EventIn,
    EventOut,
    FollowUpOut,
    InterviewIn,
    InterviewOut,
    InterviewUpdate,
)

log = get_logger(__name__)

DEFAULT_FOLLOW_UP_DAYS = 5
FREELANCE_CONTRACTS = {"freelance"}


class PipelineError(Exception):
    pass


class ApplicationNotFound(PipelineError):
    pass


class InvalidStage(PipelineError):
    pass


class PipelineService:
    def __init__(self, session: AsyncSession, user_id: uuid.UUID) -> None:
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------------ create / update
    async def create(self, req: ApplicationCreate) -> ApplicationDetail:
        opp = await self.session.get(Opportunity, req.opportunity_id)
        if opp is None:
            raise ApplicationNotFound(f"opportunity {req.opportunity_id} not found")
        existing = await self.session.scalar(
            select(Application).where(Application.opportunity_id == req.opportunity_id)
        )
        if existing is not None:
            raise PipelineError(f"application already exists for this opportunity ({existing.id})")

        kind = req.kind or (
            PipelineKind.freelance
            if (opp.contract_type in FREELANCE_CONTRACTS or opp.source == "upwork")
            else PipelineKind.employment
        )
        stage = req.stage or STAGES[kind][0]
        self._validate_stage(kind, stage)

        app = Application(
            user_id=self.user_id,
            opportunity_id=opp.id,
            kind=str(kind),
            stage=str(stage),
            cv_artifact_id=req.cv_artifact_id,
            notes=req.notes,
        )
        now = datetime.now(UTC)
        app.events.append(
            ApplicationEvent(
                kind=str(EventKind.discovered),
                at=now,
                title=f"Added to {kind} pipeline ({stage})",
                meta={"source": opp.source},
            )
        )
        self.session.add(app)
        await self.session.commit()
        log.info(
            "pipeline.created", application=str(app.id), opportunity=str(opp.id), kind=str(kind)
        )
        return await self.get(app.id)

    async def update(self, application_id: uuid.UUID, req: ApplicationUpdate) -> ApplicationDetail:
        app = await self._row(application_id)
        now = datetime.now(UTC)

        if req.stage is not None and str(req.stage) != app.stage:
            kind = PipelineKind(app.kind)
            self._validate_stage(kind, req.stage)
            old = app.stage
            app.stage = str(req.stage)
            app.events.append(
                ApplicationEvent(
                    kind=str(EventKind.stage_change),
                    at=now,
                    title=f"{old} → {req.stage}",
                    meta={"from": old, "to": str(req.stage)},
                )
            )
            if req.stage in APPLIED_STAGES and app.applied_at is None:
                app.applied_at = now
                if app.next_follow_up_at is None:
                    app.next_follow_up_at = now + timedelta(days=DEFAULT_FOLLOW_UP_DAYS)
                await self._sync_opportunity_status(app.opportunity_id, "applied")
            if req.stage in TERMINAL_STAGES:
                app.closed_at = now
                app.next_follow_up_at = None
                opp_status = "archived" if req.stage in (Stage.archived, Stage.won) else "ignored"
                if req.stage == Stage.rejected or req.stage == Stage.lost:
                    opp_status = "archived"
                await self._sync_opportunity_status(app.opportunity_id, opp_status)
            else:
                app.closed_at = None

        if req.cv_artifact_id is not None:
            app.cv_artifact_id = req.cv_artifact_id
        if req.recruiter_contact_id is not None:
            app.recruiter_contact_id = req.recruiter_contact_id
        if req.clear_follow_up:
            app.next_follow_up_at = None
        elif req.next_follow_up_at is not None:
            app.next_follow_up_at = req.next_follow_up_at
        if req.notes is not None:
            app.notes = req.notes

        await self.session.commit()
        return await self.get(application_id)

    async def add_event(self, application_id: uuid.UUID, req: EventIn) -> ApplicationDetail:
        app = await self._row(application_id)
        app.events.append(
            ApplicationEvent(
                kind=str(req.kind),
                at=req.at or datetime.now(UTC),
                title=req.title,
                body=req.body,
                meta=dict(req.meta),
            )
        )
        if req.kind == EventKind.message_sent and app.next_follow_up_at is None:
            app.next_follow_up_at = datetime.now(UTC) + timedelta(days=DEFAULT_FOLLOW_UP_DAYS)
        await self.session.commit()
        return await self.get(application_id)

    async def add_interview(self, application_id: uuid.UUID, req: InterviewIn) -> ApplicationDetail:
        app = await self._row(application_id)
        app.interviews.append(
            Interview(
                kind=str(req.kind),
                scheduled_at=req.scheduled_at,
                interviewer_contact_id=req.interviewer_contact_id,
                notes=req.notes,
            )
        )
        app.events.append(
            ApplicationEvent(
                kind=str(EventKind.interview_scheduled),
                at=datetime.now(UTC),
                title=f"Interview scheduled: {req.kind}",
                meta={"scheduled_at": req.scheduled_at.isoformat() if req.scheduled_at else None},
            )
        )
        await self.session.commit()
        return await self.get(application_id)

    async def update_interview(
        self, application_id: uuid.UUID, interview_id: uuid.UUID, req: InterviewUpdate
    ) -> ApplicationDetail:
        app = await self._row(application_id)
        interview = next((i for i in app.interviews if i.id == interview_id), None)
        if interview is None:
            raise ApplicationNotFound(f"interview {interview_id} not found")
        if req.scheduled_at is not None:
            interview.scheduled_at = req.scheduled_at
        if req.notes is not None:
            interview.notes = req.notes
        if req.outcome is not None and str(req.outcome) != interview.outcome:
            interview.outcome = str(req.outcome)
            app.events.append(
                ApplicationEvent(
                    kind=str(EventKind.interview_done),
                    at=datetime.now(UTC),
                    title=f"Interview {interview.kind}: {req.outcome}",
                )
            )
        await self.session.commit()
        return await self.get(application_id)

    # ------------------------------------------------------------------ read
    async def get(self, application_id: uuid.UUID) -> ApplicationDetail:
        app = await self._row(application_id)
        opp = await self.session.get(
            Opportunity,
            app.opportunity_id,
            options=[selectinload(Opportunity.scores)],
            populate_existing=True,
        )
        base = self._to_out(app, opp)
        return ApplicationDetail(
            **base.model_dump(),
            events=[
                EventOut(
                    id=e.id,
                    kind=EventKind(e.kind),
                    at=e.at,
                    title=e.title,
                    body=e.body,
                    meta=dict(e.meta),
                )
                for e in app.events
            ],
            interviews=[
                InterviewOut(
                    id=i.id,
                    kind=i.kind,  # type: ignore[arg-type]
                    scheduled_at=i.scheduled_at,
                    interviewer_contact_id=i.interviewer_contact_id,
                    outcome=i.outcome,  # type: ignore[arg-type]
                    notes=i.notes,
                )
                for i in app.interviews
            ],
        )

    async def board(self, kind: PipelineKind) -> BoardOut:
        stmt = (
            select(Application, Opportunity)
            .join(Opportunity, Application.opportunity_id == Opportunity.id)
            .options(selectinload(Opportunity.scores))
            .where(Application.kind == str(kind))
            .order_by(Application.updated_at.desc())
        )
        rows = (await self.session.execute(stmt)).all()
        by_stage: dict[Stage, list[ApplicationOut]] = {s: [] for s in STAGES[kind]}
        for app, opp in rows:
            by_stage.setdefault(Stage(app.stage), []).append(self._to_out(app, opp))
        return BoardOut(
            kind=kind,
            stages=STAGES[kind],
            columns=[BoardColumn(stage=s, applications=by_stage[s]) for s in STAGES[kind]],
        )

    async def follow_ups(self, *, within_days: int = 7) -> list[FollowUpOut]:
        horizon = datetime.now(UTC) + timedelta(days=within_days)
        stmt = (
            select(Application, Opportunity)
            .join(Opportunity, Application.opportunity_id == Opportunity.id)
            .options(selectinload(Opportunity.scores))
            .where(
                Application.next_follow_up_at.is_not(None), Application.next_follow_up_at <= horizon
            )
            .order_by(Application.next_follow_up_at)
        )
        rows = (await self.session.execute(stmt)).all()
        now = datetime.now(UTC)
        return [
            FollowUpOut(
                application=self._to_out(app, opp),
                due_at=app.next_follow_up_at,
                overdue=app.next_follow_up_at < now,
            )  # type: ignore[arg-type]
            for app, opp in rows
        ]

    # ------------------------------------------------------------------ internals
    async def _row(self, application_id: uuid.UUID) -> Application:
        app = await self.session.get(
            Application,
            application_id,
            options=[selectinload(Application.events), selectinload(Application.interviews)],
            populate_existing=True,
        )
        if app is None:
            raise ApplicationNotFound(str(application_id))
        return app

    @staticmethod
    def _validate_stage(kind: PipelineKind, stage: Stage) -> None:
        if stage not in STAGES[kind]:
            allowed = ", ".join(str(s) for s in STAGES[kind])
            raise InvalidStage(
                f"stage '{stage}' is not part of the {kind} pipeline (allowed: {allowed})"
            )

    async def _sync_opportunity_status(self, opportunity_id: uuid.UUID, status: str) -> None:
        opp = await self.session.get(Opportunity, opportunity_id)
        if opp is not None and opp.status != status:
            opp.status = status

    @staticmethod
    def _to_out(app: Application, opp: Opportunity | None) -> ApplicationOut:
        score = opp.scores[0].overall if opp and opp.scores else None
        return ApplicationOut(
            id=app.id,
            opportunity_id=app.opportunity_id,
            opportunity_title=opp.title if opp else "?",
            company_name=opp.company_name if opp else None,
            kind=PipelineKind(app.kind),
            stage=Stage(app.stage),
            cv_artifact_id=app.cv_artifact_id,
            recruiter_contact_id=app.recruiter_contact_id,
            applied_at=app.applied_at,
            next_follow_up_at=app.next_follow_up_at,
            closed_at=app.closed_at,
            notes=app.notes,
            score_overall=score,
            created_at=app.created_at,
            updated_at=app.updated_at,
        )


async def active_application_count(session: AsyncSession) -> int:
    """Service-level read for other modules: applications not in a terminal stage."""
    from sqlalchemy import func as _func

    terminal = [str(s) for s in TERMINAL_STAGES]
    return (
        await session.scalar(
            select(_func.count()).select_from(Application).where(Application.stage.not_in(terminal))
        )
        or 0
    )


async def funnel_rows(session: AsyncSession) -> list[dict[str, Any]]:
    """Service-level read for insights: one row per application with its event kinds/dates."""
    from sqlalchemy.orm import selectinload as _selectinload

    rows = (
        await session.scalars(
            select(Application).options(
                _selectinload(Application.events), _selectinload(Application.interviews)
            )
        )
    ).all()
    return [
        {
            "id": str(app.id),
            "kind": app.kind,
            "stage": app.stage,
            "applied_at": app.applied_at,
            "closed_at": app.closed_at,
            "created_at": app.created_at,
            "events": [{"kind": e.kind, "at": e.at} for e in app.events],
            "interviews": [{"kind": i.kind, "outcome": i.outcome} for i in app.interviews],
        }
        for app in rows
    ]


async def due_follow_ups(
    session: AsyncSession, *, within_hours: int = 24, limit: int = 10
) -> list[dict[str, Any]]:
    """Service-level read for insights: follow-ups due within the horizon (overdue included)."""
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Application, Opportunity.title)
            .join(Opportunity, Application.opportunity_id == Opportunity.id)
            .where(
                Application.next_follow_up_at.is_not(None),
                Application.next_follow_up_at <= now + timedelta(hours=within_hours),
            )
            .order_by(Application.next_follow_up_at)
            .limit(limit)
        )
    ).all()
    return [
        {"application_id": str(app.id), "title": title, "due_at": app.next_follow_up_at}
        for app, title in rows
    ]


async def upcoming_interviews(
    session: AsyncSession, *, within_hours: int = 48, grace_hours: int = 2, limit: int = 10
) -> list[dict[str, Any]]:
    """Service-level read for insights: pending interviews, ``grace_hours`` ago → the horizon."""
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(Interview, Opportunity.title)
            .join(Application, Interview.application_id == Application.id)
            .join(Opportunity, Application.opportunity_id == Opportunity.id)
            .where(
                Interview.outcome == "pending",
                Interview.scheduled_at.is_not(None),
                Interview.scheduled_at >= now - timedelta(hours=grace_hours),
                Interview.scheduled_at <= now + timedelta(hours=within_hours),
            )
            .order_by(Interview.scheduled_at)
            .limit(limit)
        )
    ).all()
    return [
        {
            "application_id": str(interview.application_id),
            "kind": interview.kind,
            "scheduled_at": interview.scheduled_at,
            "title": title,
        }
        for interview, title in rows
    ]


async def application_summaries(
    session: AsyncSession, *, opportunity_id: uuid.UUID | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Service-level read for assistants: compact application rows, most recently updated first."""
    stmt = (
        select(Application, Opportunity.title, Opportunity.company_name)
        .join(Opportunity, Application.opportunity_id == Opportunity.id)
        .order_by(Application.updated_at.desc())
        .limit(limit)
    )
    if opportunity_id is not None:
        stmt = stmt.where(Application.opportunity_id == opportunity_id)
    return [
        {
            "id": str(app.id),
            "opportunity_id": str(app.opportunity_id),
            "title": title,
            "company": company,
            "kind": app.kind,
            "stage": app.stage,
            "applied_at": app.applied_at,
            "next_follow_up_at": app.next_follow_up_at,
            "updated_at": app.updated_at,
        }
        for app, title, company in (await session.execute(stmt)).all()
    ]
