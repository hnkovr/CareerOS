"""``/api/workflows`` — start, inspect and decide workflows with approval gates (ADR-017)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.service import VaultInvalid
from careeros.modules.workflows.enums import RunState, WorkflowKind
from careeros.modules.workflows.schemas import (
    DecisionRequest,
    StartRequest,
    WorkflowDefinitionOut,
    WorkflowRunOut,
)
from careeros.modules.workflows.service import WorkflowError, WorkflowNotFound, WorkflowService

router = APIRouter(prefix="/workflows", tags=["workflows"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> WorkflowService:
    settings = request.app.state.settings
    return WorkflowService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user.id),
        session=session,
        user_id=user.id,
    )


@router.get("/definitions", response_model=list[WorkflowDefinitionOut])
async def definitions(request: Request, user: CurrentUserDep) -> list[WorkflowDefinitionOut]:
    _ = request, user
    return WorkflowService.definitions()


@router.get("", response_model=list[WorkflowRunOut])
async def list_runs(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    state: RunState | None = None,
    kind: WorkflowKind | None = None,
    limit: int = 50,
) -> list[WorkflowRunOut]:
    return await _svc(request, user, session).list(state=state, kind=kind, limit=limit)


@router.post("", response_model=WorkflowRunOut, status_code=status.HTTP_201_CREATED)
async def start(
    req: StartRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> WorkflowRunOut:
    """Start a workflow; it runs until its first approval gate (or the end) and returns."""
    try:
        return await _svc(request, user, session).start(req)
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/{run_id}", response_model=WorkflowRunOut)
async def get_run(
    run_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> WorkflowRunOut:
    try:
        return await _svc(request, user, session).get(run_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow run not found") from exc


@router.post("/{run_id}/decision", response_model=WorkflowRunOut)
async def decide(
    run_id: uuid.UUID,
    req: DecisionRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> WorkflowRunOut:
    """Approve (the run continues) or reject (the run is cancelled) the pending gate."""
    try:
        return await _svc(request, user, session).decide(run_id, req)
    except WorkflowNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow run not found") from exc
    except WorkflowError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/{run_id}/cancel", response_model=WorkflowRunOut)
async def cancel(
    run_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> WorkflowRunOut:
    try:
        return await _svc(request, user, session).cancel(run_id)
    except WorkflowNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workflow run not found") from exc
    except WorkflowError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
