"""Workflow definitions (ADR-017, brief §53): ordered steps, each *automatic* or an *approval
gate*. An approval step produces a Proposal → Suggestion and the run pauses in
``waiting_approval`` until the owner decides; the steps after it are the only place a workflow
writes operational state (an application, a timeline event) — never an external send.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.config import Settings
from careeros.core.logging import get_logger
from careeros.modules.ai.provider import AIError
from careeros.modules.ai.service import AIService
from careeros.modules.cv.provenance import numbers_in
from careeros.modules.cv.schemas import GenerateCVRequest
from careeros.modules.cv.service import CVService
from careeros.modules.opportunities.service import OpportunityService
from careeros.modules.pipeline.enums import TERMINAL_STAGES, EventKind, PipelineKind, Stage
from careeros.modules.pipeline.schemas import ApplicationCreate, ApplicationUpdate, EventIn
from careeros.modules.pipeline.service import PipelineService, application_summaries
from careeros.modules.vault import schema as s
from careeros.modules.vault.service import Vault
from careeros.modules.workflows.enums import WorkflowKind
from careeros.modules.workflows.schemas import FollowUpDraft

log = get_logger(__name__)

FOLLOW_UP_DAYS = 5
DEFAULT_FORMATS = ["pdf", "md", "json"]


class StepFailed(Exception):
    """A step that cannot proceed — the run fails with this message, no retry."""


@dataclass
class Proposal:
    title: str
    payload: dict[str, Any]
    ai_run_id: uuid.UUID | None = None


@dataclass
class StepResult:
    summary: str
    output: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    proposal: Proposal | None = None
    skipped: bool = False


@dataclass
class WorkflowCtx:
    settings: Settings
    vault: Vault
    ai: AIService
    session: AsyncSession
    user_id: uuid.UUID
    run_id: uuid.UUID
    target_ref: str
    context: dict[str, Any]
    options: dict[str, Any]

    def opportunities(self) -> OpportunityService:
        return OpportunityService(
            self.settings, self.vault, self.ai, session=self.session, user_id=self.user_id
        )

    def pipeline(self) -> PipelineService:
        return PipelineService(self.session, self.user_id)

    def cv(self) -> CVService:
        return CVService(
            self.settings, self.vault, self.ai, session=self.session, user_id=self.user_id
        )

    def ai_available(self) -> bool:
        try:
            return bool(self.ai.providers.get().info().configured)
        except Exception:  # no default provider at all
            return False

    @property
    def use_ai(self) -> bool:
        return bool(self.options.get("use_ai", True)) and self.ai_available()

    def positioning(self) -> s.Positioning:
        data = self.vault.require()
        return data.by_id(data.positioning)[data.meta.default_positioning]


StepFn = Callable[[WorkflowCtx], Awaitable[StepResult]]


@dataclass(frozen=True)
class Step:
    name: str
    kind: Literal["auto", "approval"]
    description: str
    run: StepFn


@dataclass(frozen=True)
class WorkflowDefinition:
    kind: WorkflowKind
    title: str
    description: str
    target_type: str
    steps: tuple[Step, ...]


# ----------------------------------------------------------------------------- apply


async def _analyze(ctx: WorkflowCtx) -> StepResult:
    oid = uuid.UUID(ctx.target_ref)
    if await application_summaries(ctx.session, opportunity_id=oid, limit=1):
        raise StepFailed("this opportunity is already in the pipeline")
    svc = ctx.opportunities()
    detail = await svc.get(oid)
    if detail.score is None:
        detail = await svc.rescore(oid)
    analysis = detail.analysis
    if analysis is not None:
        note = "existing analysis"
    elif ctx.use_ai:
        try:
            detail = await svc.analyze(oid)
            analysis = detail.analysis
            note = "AI analysis"
        except AIError as exc:
            note = f"AI analysis unavailable ({exc}); deterministic score only"
    else:
        note = "AI off — deterministic score only"
    score = detail.score.overall if detail.score else None
    return StepResult(
        summary=f"{detail.title} @ {detail.company_name or 'unknown company'} — "
        f"score {score if score is not None else '?'}/100 ({note})",
        output={"score": score, "verdict": analysis.verdict if analysis else None},
        context={
            "opportunity_id": str(oid),
            "title": detail.title,
            "company": detail.company_name,
            "score": score,
            "recommendation": str(detail.score.recommendation) if detail.score else None,
            "recommended_cv_variant": analysis.recommended_cv_variant if analysis else None,
            "suggested_response": analysis.suggested_response if analysis else None,
        },
    )


async def _select_cv(ctx: WorkflowCtx) -> StepResult:
    data = ctx.vault.require()
    variants = data.by_id(data.cv_variants)
    wanted = ctx.context.get("recommended_cv_variant")
    chosen = wanted if wanted in variants else data.meta.default_cv_variant
    why = "recommended by the analysis" if chosen == wanted else "default variant"
    return StepResult(summary=f"CV variant: {chosen} ({why})", context={"variant_id": chosen})


async def _generate_cv(ctx: WorkflowCtx) -> StepResult:
    formats = list(ctx.options.get("formats") or DEFAULT_FORMATS)
    req = GenerateCVRequest(
        variant_id=ctx.context["variant_id"],
        opportunity_id=uuid.UUID(ctx.target_ref),
        use_ai=ctx.use_ai,
        formats=formats,  # type: ignore[arg-type]
    )
    try:
        art = await ctx.cv().generate(req)
    except AIError as exc:
        log.warning("workflow.cv_ai_fallback", error=str(exc))
        art = await ctx.cv().generate(req.model_copy(update={"use_ai": False}))
    return StepResult(
        summary=f"CV generated: {art.variant_id} "
        f"({'AI-tailored' if art.ai_used else 'deterministic'})",
        output={"artifact_id": str(art.id), "status": art.status, "ai_used": art.ai_used},
        context={"cv_artifact_id": str(art.id)},
    )


def _default_apply_message(data: s.VaultData, context: dict[str, Any]) -> str:
    positioning = data.by_id(data.positioning)[data.meta.default_positioning]
    company = context.get("company") or "your team"
    return (
        f"Hello,\n\nI'm applying for the {context.get('title', 'position')} role at {company}.\n\n"
        f"{positioning.summary}\n\n"
        f"My CV ({context.get('variant_id', 'core')}) is attached. I'd be glad to walk you through "
        "how my experience maps to what you need.\n\n"
        f"Best regards,\n{data.profile.name}"
    )


async def _draft_message(ctx: WorkflowCtx) -> StepResult:
    data = ctx.vault.require()
    message = ctx.context.get("suggested_response") or _default_apply_message(data, ctx.context)
    source = "from the AI analysis" if ctx.context.get("suggested_response") else "vault template"
    title = f"Apply: {ctx.context.get('title')} @ {ctx.context.get('company') or 'unknown company'}"
    return StepResult(
        summary=f"Application package ready for your approval (message {source})",
        proposal=Proposal(
            title=title,
            payload={
                "kind": "apply",
                "opportunity_id": ctx.context.get("opportunity_id"),
                "cv_artifact_id": ctx.context.get("cv_artifact_id"),
                "variant_id": ctx.context.get("variant_id"),
                "score": ctx.context.get("score"),
                "message": message,
            },
        ),
        context={"message": message},
    )


async def _create_application(ctx: WorkflowCtx) -> StepResult:
    pipe = ctx.pipeline()
    cv_id = ctx.context.get("cv_artifact_id")
    app = await pipe.create(
        ApplicationCreate(
            opportunity_id=uuid.UUID(ctx.context["opportunity_id"]),
            cv_artifact_id=uuid.UUID(cv_id) if cv_id else None,
            notes="created by the apply workflow — package approved, sending is yours",
        )
    )
    stage = Stage.proposal if app.kind == PipelineKind.freelance else Stage.preparing
    app = await pipe.update(app.id, ApplicationUpdate(stage=stage))
    app = await pipe.add_event(
        app.id,
        EventIn(
            kind=EventKind.note,
            title="Application package approved — send it yourself, then move to applied",
            body=ctx.context.get("message"),
            meta={"workflow_run": str(ctx.run_id), "cv_artifact_id": cv_id},
        ),
    )
    return StepResult(
        summary=f"Application created in the {app.kind} pipeline at '{stage}'",
        output={"application_id": str(app.id), "stage": str(stage)},
        context={"application_id": str(app.id)},
    )


# ----------------------------------------------------------------------------- follow-up


async def _review(ctx: WorkflowCtx) -> StepResult:
    app = await ctx.pipeline().get(uuid.UUID(ctx.target_ref))
    if app.stage in TERMINAL_STAGES:
        raise StepFailed(f"application is closed ({app.stage})")
    since = app.applied_at or app.created_at
    days = max(0, (datetime.now(UTC) - since).days)
    last = app.events[0] if app.events else None
    return StepResult(
        summary=f"{app.opportunity_title} @ {app.company_name or 'unknown company'} — "
        f"stage {app.stage}, {days} day(s) since {'applying' if app.applied_at else 'creation'}",
        context={
            "application_id": str(app.id),
            "opportunity_id": str(app.opportunity_id),
            "title": app.opportunity_title,
            "company": app.company_name,
            "stage": str(app.stage),
            "days": days,
            "last_event": f"{last.kind}: {last.title}" if last else None,
        },
    )


def _default_follow_up(data: s.VaultData, context: dict[str, Any]) -> FollowUpDraft:
    title = context.get("title", "the role")
    company = context.get("company") or "your team"
    return FollowUpDraft(
        subject=f"Following up — {title}",
        message=(
            f"Hello,\n\nI wanted to follow up on my application for {title} at {company}. "
            "I remain very interested and would welcome any update on the next steps.\n\n"
            "If it helps, I'm happy to share more detail on any part of my background.\n\n"
            f"Best regards,\n{data.profile.name}"
        ),
    )


async def _draft_follow_up(ctx: WorkflowCtx) -> StepResult:
    data = ctx.vault.require()
    inputs = {
        "positioning": ctx.positioning().model_dump(mode="json"),
        "application": {
            k: ctx.context.get(k) for k in ("title", "company", "stage", "days", "last_event")
        },
    }
    draft: FollowUpDraft | None = None
    run_id: uuid.UUID | None = None
    source = "vault template"
    if ctx.use_ai:
        try:
            run = await ctx.ai.structured(
                "follow_up_message",
                inputs,
                FollowUpDraft,
                entity_type="application",
                entity_id=ctx.context["application_id"],
            )
            allowed = set(numbers_in(json.dumps(inputs, default=str)))
            foreign = sorted(set(numbers_in(run.data.subject + " " + run.data.message)) - allowed)
            if foreign:
                log.warning("workflow.follow_up_guarded", foreign=foreign, run_id=str(run.run_id))
                source = (
                    "vault template — AI draft rejected "
                    f"(numbers not in inputs: {', '.join(foreign)})"
                )
            else:
                draft, run_id, source = run.data, run.run_id, "AI draft"
        except AIError as exc:
            source = f"vault template — AI unavailable ({exc})"
    if draft is None:
        draft = _default_follow_up(data, ctx.context)
    return StepResult(
        summary=f"Follow-up ready for your approval ({source})",
        proposal=Proposal(
            title=f"Follow up: {ctx.context.get('title')} @ "
            f"{ctx.context.get('company') or 'unknown company'}",
            payload={
                "kind": "follow_up",
                "application_id": ctx.context["application_id"],
                "subject": draft.subject,
                "message": draft.message,
            },
            ai_run_id=run_id,
        ),
        context={"subject": draft.subject, "message": draft.message},
    )


async def _record_follow_up(ctx: WorkflowCtx) -> StepResult:
    pipe = ctx.pipeline()
    aid = uuid.UUID(ctx.context["application_id"])
    await pipe.add_event(
        aid,
        EventIn(
            kind=EventKind.follow_up,
            title=f"Follow-up approved — send it yourself: {ctx.context['subject']}",
            body=ctx.context.get("message"),
            meta={"workflow_run": str(ctx.run_id)},
        ),
    )
    next_at = datetime.now(UTC) + timedelta(days=FOLLOW_UP_DAYS)
    app = await pipe.update(aid, ApplicationUpdate(next_follow_up_at=next_at))
    return StepResult(
        summary=f"Follow-up recorded; next one due {next_at:%Y-%m-%d}",
        output={
            "next_follow_up_at": app.next_follow_up_at.isoformat()
            if app.next_follow_up_at
            else None
        },
    )


DEFINITIONS: dict[WorkflowKind, WorkflowDefinition] = {
    WorkflowKind.apply: WorkflowDefinition(
        kind=WorkflowKind.apply,
        title="Apply to an opportunity",
        description="Score and analyse the posting, pick and generate the CV variant, draft the "
        "message, then WAIT for your approval before the application enters the pipeline. "
        "Sending stays yours.",
        target_type="opportunity",
        steps=(
            Step(
                "analyze", "auto", "Score (and AI-analyse, if configured) the opportunity", _analyze
            ),
            Step("select_cv", "auto", "Choose the CV variant the analysis recommends", _select_cv),
            Step("generate_cv", "auto", "Generate the tailored CV artifact", _generate_cv),
            Step(
                "draft_message",
                "approval",
                "Draft the application message and wait for approval",
                _draft_message,
            ),
            Step(
                "create_application",
                "auto",
                "Create the application in the pipeline (preparing/proposal) with the package",
                _create_application,
            ),
        ),
    ),
    WorkflowKind.follow_up: WorkflowDefinition(
        kind=WorkflowKind.follow_up,
        title="Follow up on an application",
        description="Review the application's state, draft a follow-up (AI if configured, "
        "guarded), WAIT for your approval, then record it and schedule the next one.",
        target_type="application",
        steps=(
            Step("review", "auto", "Read the application's stage and timeline", _review),
            Step(
                "draft_follow_up",
                "approval",
                "Draft the follow-up message and wait for approval",
                _draft_follow_up,
            ),
            Step(
                "record_follow_up",
                "auto",
                "Record the approved follow-up and schedule the next one",
                _record_follow_up,
            ),
        ),
    ),
}
