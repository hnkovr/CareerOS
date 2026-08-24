"""``/api/pipeline`` — applications Kanban, timeline, interviews, follow-ups."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.pipeline.enums import PipelineKind
from careeros.modules.pipeline.schemas import (
    ApplicationCreate,
    ApplicationDetail,
    ApplicationUpdate,
    BoardOut,
    EventIn,
    FollowUpOut,
    InterviewIn,
    InterviewUpdate,
)
from careeros.modules.pipeline.service import (
    ApplicationNotFound,
    InvalidStage,
    PipelineError,
    PipelineService,
)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> PipelineService:
    _ = request
    return PipelineService(session, user.id)


@router.get("/board", response_model=BoardOut)
async def board(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    kind: PipelineKind = PipelineKind.employment,
) -> BoardOut:
    return await _svc(request, user, session).board(kind)


@router.get("/follow-ups", response_model=list[FollowUpOut])
async def follow_ups(
    request: Request, user: CurrentUserDep, session: SessionDep, within_days: int = 7
) -> list[FollowUpOut]:
    return await _svc(request, user, session).follow_ups(within_days=within_days)


@router.post("/applications", response_model=ApplicationDetail, status_code=status.HTTP_201_CREATED)
async def create_application(
    req: ApplicationCreate, request: Request, user: CurrentUserDep, session: SessionDep
) -> ApplicationDetail:
    try:
        return await _svc(request, user, session).create(req)
    except ApplicationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PipelineError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/applications/{application_id}", response_model=ApplicationDetail)
async def get_application(
    application_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> ApplicationDetail:
    try:
        return await _svc(request, user, session).get(application_id)
    except ApplicationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found") from exc


@router.patch("/applications/{application_id}", response_model=ApplicationDetail)
async def update_application(
    application_id: uuid.UUID,
    req: ApplicationUpdate,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> ApplicationDetail:
    try:
        return await _svc(request, user, session).update(application_id, req)
    except ApplicationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found") from exc
    except InvalidStage as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/applications/{application_id}/events", response_model=ApplicationDetail)
async def add_event(
    application_id: uuid.UUID,
    req: EventIn,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> ApplicationDetail:
    try:
        return await _svc(request, user, session).add_event(application_id, req)
    except ApplicationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found") from exc


@router.post("/applications/{application_id}/interviews", response_model=ApplicationDetail)
async def add_interview(
    application_id: uuid.UUID,
    req: InterviewIn,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> ApplicationDetail:
    try:
        return await _svc(request, user, session).add_interview(application_id, req)
    except ApplicationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "application not found") from exc


@router.patch(
    "/applications/{application_id}/interviews/{interview_id}", response_model=ApplicationDetail
)
async def update_interview(
    application_id: uuid.UUID,
    interview_id: uuid.UUID,
    req: InterviewUpdate,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> ApplicationDetail:
    try:
        return await _svc(request, user, session).update_interview(
            application_id, interview_id, req
        )
    except ApplicationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
