"""``/api/ai`` — providers, prompts, run ledger, external bundles (Mode B), dev packets (Mode C)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.prompts import PromptNotFound, PromptRenderError
from careeros.modules.ai.schemas import (
    AIRunOut,
    BundleOut,
    BundleRequest,
    DevPacketOut,
    DevPacketRequest,
    FeedbackIn,
    PromptInfo,
    ProviderInfo,
)
from careeros.modules.ai.service import AIService

router = APIRouter(prefix="/ai", tags=["ai"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _service(request: Request, user: CurrentUser, session: AsyncSession) -> AIService:
    return build_ai_service(request.app.state.settings, session=session, user_id=user.id)


@router.get("/providers", response_model=list[ProviderInfo])
async def providers(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> list[ProviderInfo]:
    return _service(request, user, session).provider_infos()


@router.get("/prompts", response_model=list[PromptInfo])
async def prompts(request: Request, user: CurrentUserDep, session: SessionDep) -> list[PromptInfo]:
    return _service(request, user, session).prompt_infos()


@router.get("/runs", response_model=list[AIRunOut])
async def runs(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    entity_type: str | None = None,
    entity_id: str | None = None,
    limit: int = 50,
) -> list[AIRunOut]:
    return await _service(request, user, session).list_runs(
        entity_type=entity_type, entity_id=entity_id, limit=limit
    )


@router.get("/runs/{run_id}", response_model=AIRunOut)
async def run(
    request: Request, run_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> AIRunOut:
    out = await _service(request, user, session).get_run(run_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return out


@router.post("/runs/{run_id}/feedback", response_model=AIRunOut)
async def run_feedback(
    request: Request, run_id: uuid.UUID, fb: FeedbackIn, user: CurrentUserDep, session: SessionDep
) -> AIRunOut:
    out = await _service(request, user, session).feedback(run_id, fb)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return out


@router.post("/bundles", response_model=BundleOut)
async def bundle(
    req: BundleRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> BundleOut:
    try:
        return await _service(request, user, session).bundle(req)
    except PromptNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown prompt {exc}") from exc
    except PromptRenderError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/dev-packets", response_model=DevPacketOut)
async def dev_packet(
    req: DevPacketRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> DevPacketOut:
    try:
        return await _service(request, user, session).dev_packet(req)
    except PromptRenderError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
