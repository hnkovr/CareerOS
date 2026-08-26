"""Workflow runner (ADR-017): persists a run, executes steps in order, pauses at approval gates
by creating a Suggestion, resumes on the owner's decision. Everything a step writes goes through
the owning module's service; the runner itself only touches ``workflow_run``."""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.service import AIService
from careeros.modules.ai.suggestions import IllegalTransition, get_suggestion, transition
from careeros.modules.pipeline.service import due_follow_ups
from careeros.modules.vault.service import Vault
from careeros.modules.workflows.engine import (
    DEFINITIONS,
    StepFailed,
    WorkflowCtx,
    WorkflowDefinition,
)
from careeros.modules.workflows.enums import TERMINAL_RUN_STATES, RunState, StepStatus, WorkflowKind
from careeros.modules.workflows.models import WorkflowRun
from careeros.modules.workflows.schemas import (
    DecisionRequest,
    StartRequest,
    StepInfo,
    StepOut,
    WorkflowDefinitionOut,
    WorkflowRunOut,
)

log = get_logger(__name__)


class WorkflowError(Exception):
    pass


class WorkflowNotFound(WorkflowError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class WorkflowService:
    def __init__(
        self,
        settings: Settings,
        vault: Vault,
        ai: AIService,
        *,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> None:
        self.settings = settings
        self.vault = vault
        self.ai = ai
        self.session = session
        self.user_id = user_id

    # ------------------------------------------------------------------ read
    @staticmethod
    def definitions() -> list[WorkflowDefinitionOut]:
        return [
            WorkflowDefinitionOut(
                kind=d.kind,
                title=d.title,
                description=d.description,
                target_type=d.target_type,
                steps=[
                    StepInfo(name=s.name, kind=s.kind, description=s.description) for s in d.steps
                ],
            )
            for d in DEFINITIONS.values()
        ]

    async def list(
        self, *, state: RunState | None = None, kind: WorkflowKind | None = None, limit: int = 50
    ) -> list[WorkflowRunOut]:
        stmt = select(WorkflowRun).order_by(WorkflowRun.created_at.desc()).limit(limit)
        if state:
            stmt = stmt.where(WorkflowRun.state == str(state))
        if kind:
            stmt = stmt.where(WorkflowRun.kind == str(kind))
        return [self._out(r) for r in (await self.session.scalars(stmt)).all()]

    async def get(self, run_id: uuid.UUID) -> WorkflowRunOut:
        return self._out(await self._row(run_id))

    # ------------------------------------------------------------------ lifecycle
    async def start(self, req: StartRequest) -> WorkflowRunOut:
        definition = DEFINITIONS[req.kind]
        run = WorkflowRun(
            user_id=self.user_id,
            kind=str(req.kind),
            target_type=definition.target_type,
            target_ref=str(req.target_id),
            state=str(RunState.running),
            current_step=0,
            steps=[
                {"name": s.name, "kind": s.kind, "status": str(StepStatus.pending)}
                for s in definition.steps
            ],
            context={},
            options=dict(req.options),
        )
        self.session.add(run)
        await self.session.commit()
        log.info("workflow.started", run=str(run.id), kind=run.kind, target=run.target_ref)
        return await self._advance(run)

    async def decide(self, run_id: uuid.UUID, req: DecisionRequest) -> WorkflowRunOut:
        run = await self._row(run_id)
        if run.state != str(RunState.waiting_approval) or run.suggestion_id is None:
            raise WorkflowError(f"run is not waiting for approval (state: {run.state})")
        sid = run.suggestion_id
        rec = dict(run.steps[run.current_step])
        if req.decision == "reject":
            await self._ensure_suggestion(sid, "rejected", note=req.note)
            rec.update(
                status=str(StepStatus.rejected), decided="rejected", finished_at=_now().isoformat()
            )
            self._set_step(run, rec)
            run.state = str(RunState.cancelled)
            run.error = req.note or "rejected by the owner"
            run.finished_at = _now()
            await self.session.commit()
            log.info("workflow.rejected", run=str(run.id))
            return self._out(run)
        await self._ensure_suggestion(sid, "approved", note=req.note)
        rec.update(status=str(StepStatus.done), decided="approved", finished_at=_now().isoformat())
        self._set_step(run, rec)
        run.state = str(RunState.running)
        run.current_step += 1
        run.suggestion_id = None
        await self.session.commit()
        out = await self._advance(run)
        if out.state == RunState.completed:
            await self._ensure_suggestion(sid, "executed")
        return out

    async def cancel(self, run_id: uuid.UUID, *, note: str | None = None) -> WorkflowRunOut:
        run = await self._row(run_id)
        if run.state in {str(s) for s in TERMINAL_RUN_STATES}:
            raise WorkflowError(f"run already finished ({run.state})")
        if run.suggestion_id is not None:
            await self._ensure_suggestion(run.suggestion_id, "rejected", note=note or "cancelled")
            rec = dict(run.steps[run.current_step])
            rec.update(status=str(StepStatus.rejected), decided="cancelled")
            self._set_step(run, rec)
        run.state = str(RunState.cancelled)
        run.error = note or "cancelled by the owner"
        run.finished_at = _now()
        await self.session.commit()
        return self._out(run)

    async def sweep_follow_ups(self, *, limit: int = 20) -> list[WorkflowRunOut]:
        """Start a ``follow_up`` run for every application whose follow-up is due or overdue and
        has no active run yet. Each run stops at its gate — the sweep never sends anything; it
        only queues drafts for the owner's approval (the daily sweep from the brief)."""
        due = await due_follow_ups(self.session, within_hours=0, limit=limit)
        if not due:
            return []
        active = set(
            (
                await self.session.scalars(
                    select(WorkflowRun.target_ref).where(
                        WorkflowRun.kind == str(WorkflowKind.follow_up),
                        WorkflowRun.state.in_(
                            [str(RunState.running), str(RunState.waiting_approval)]
                        ),
                    )
                )
            ).all()
        )
        started: list[WorkflowRunOut] = []
        for item in due:
            aid = str(item["application_id"])
            if aid in active:
                continue
            started.append(
                await self.start(
                    StartRequest(kind=WorkflowKind.follow_up, target_id=uuid.UUID(aid))
                )
            )
        log.info("workflow.sweep", due=len(due), started=len(started))
        return started

    # ------------------------------------------------------------------ engine
    async def _advance(self, run: WorkflowRun) -> WorkflowRunOut:
        definition = DEFINITIONS[WorkflowKind(run.kind)]
        while run.current_step < len(definition.steps):
            step = definition.steps[run.current_step]
            rec = dict(run.steps[run.current_step])
            if rec.get("status") == str(StepStatus.waiting):
                break
            rec.update(status=str(StepStatus.running), started_at=_now().isoformat())
            self._set_step(run, rec)
            await self.session.commit()
            ctx = WorkflowCtx(
                settings=self.settings,
                vault=self.vault,
                ai=self.ai,
                session=self.session,
                user_id=self.user_id,
                run_id=run.id,
                target_ref=run.target_ref,
                context=dict(run.context),
                options=dict(run.options),
            )
            try:
                result = await step.run(ctx)
            except StepFailed as exc:
                return await self._fail(run, rec, str(exc))
            except Exception as exc:  # a step bug or a downstream error — the run fails, cleanly
                log.exception("workflow.step_crashed", run=str(run.id), step=step.name)
                return await self._fail(run, rec, f"{type(exc).__name__}: {exc}")
            run.context = {**run.context, **result.context}
            rec.update(summary=result.summary, output=result.output, finished_at=_now().isoformat())
            if step.kind == "approval" and result.proposal is not None:
                sid = await self.ai.record_suggestion(
                    target_type="workflow",
                    target_ref=str(run.id),
                    title=result.proposal.title,
                    payload={
                        **result.proposal.payload,
                        "workflow_run_id": str(run.id),
                        "workflow_kind": run.kind,
                        "step": step.name,
                    },
                    ai_run_id=result.proposal.ai_run_id,
                )
                rec.update(status=str(StepStatus.waiting), suggestion_id=str(sid) if sid else None)
                self._set_step(run, rec)
                run.suggestion_id = sid
                run.state = str(RunState.waiting_approval)
                await self.session.commit()
                log.info("workflow.waiting_approval", run=str(run.id), step=step.name)
                return self._out(run)
            rec["status"] = str(StepStatus.skipped if result.skipped else StepStatus.done)
            self._set_step(run, rec)
            run.current_step += 1
            await self.session.commit()
        if run.current_step >= len(definition.steps):
            run.state = str(RunState.completed)
            run.finished_at = _now()
            await self.session.commit()
            log.info("workflow.completed", run=str(run.id))
        return self._out(run)

    async def _fail(self, run: WorkflowRun, rec: dict[str, Any], error: str) -> WorkflowRunOut:
        rec.update(status=str(StepStatus.failed), error=error, finished_at=_now().isoformat())
        self._set_step(run, rec)
        run.state = str(RunState.failed)
        run.error = error
        run.finished_at = _now()
        await self.session.commit()
        log.warning("workflow.failed", run=str(run.id), error=error)
        return self._out(run)

    async def _ensure_suggestion(
        self, suggestion_id: uuid.UUID, wanted: str, *, note: str | None = None
    ) -> None:
        """Move the gate's Suggestion to ``wanted`` unless the owner already did it elsewhere."""
        current = await get_suggestion(self.session, suggestion_id)
        if current.state == wanted:
            return
        try:
            await transition(self.session, suggestion_id, wanted, note=note)
        except IllegalTransition as exc:
            raise WorkflowError(f"suggestion {suggestion_id}: {exc}") from exc

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _set_step(run: WorkflowRun, rec: dict[str, Any]) -> None:
        # Copy, never share: a step dict that is also referenced by the column's committed state
        # would make a later reassignment compare equal and silently skip the UPDATE.
        steps = [copy.deepcopy(item) for item in run.steps]
        steps[run.current_step] = copy.deepcopy(rec)
        run.steps = steps
        flag_modified(run, "steps")

    async def _row(self, run_id: uuid.UUID) -> WorkflowRun:
        row = await self.session.get(WorkflowRun, run_id)
        if row is None:
            raise WorkflowNotFound(str(run_id))
        return row

    @staticmethod
    def _out(run: WorkflowRun) -> WorkflowRunOut:
        definition: WorkflowDefinition = DEFINITIONS[WorkflowKind(run.kind)]
        steps = []
        for i, rec in enumerate(run.steps):
            spec = definition.steps[i] if i < len(definition.steps) else None
            steps.append(
                StepOut(
                    name=rec.get("name", spec.name if spec else f"step {i}"),
                    kind=rec.get("kind", spec.kind if spec else "auto"),
                    description=spec.description if spec else "",
                    status=StepStatus(rec.get("status", "pending")),
                    summary=rec.get("summary"),
                    output=rec.get("output"),
                    suggestion_id=uuid.UUID(rec["suggestion_id"])
                    if rec.get("suggestion_id")
                    else None,
                    decided=rec.get("decided"),
                    error=rec.get("error"),
                    started_at=datetime.fromisoformat(rec["started_at"])
                    if rec.get("started_at")
                    else None,
                    finished_at=datetime.fromisoformat(rec["finished_at"])
                    if rec.get("finished_at")
                    else None,
                )
            )
        return WorkflowRunOut(
            id=run.id,
            kind=WorkflowKind(run.kind),
            title=definition.title,
            target_type=run.target_type,
            target_ref=run.target_ref,
            state=RunState(run.state),
            current_step=run.current_step,
            steps=steps,
            context=dict(run.context),
            suggestion_id=run.suggestion_id,
            error=run.error,
            created_at=run.created_at,
            updated_at=run.updated_at,
            finished_at=run.finished_at,
        )
