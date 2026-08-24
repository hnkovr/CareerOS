"""``/api/inbox`` — manual email ingestion, message list, threads, reply suggestions, stats."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.provider import AIError
from careeros.modules.inbox.enums import MessageClass
from careeros.modules.inbox.schemas import (
    EmailIn,
    InboxStats,
    MessageOut,
    MessageUpdate,
    ReplySuggestionOut,
    SuggestReplyRequest,
    ThreadOut,
)
from careeros.modules.inbox.service import InboxService, MessageNotFound
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.service import VaultInvalid

router = APIRouter(prefix="/inbox", tags=["inbox"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> InboxService:
    settings = request.app.state.settings
    return InboxService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user.id),
        session=session,
        user_id=user.id,
    )


@router.post("/ingest", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def ingest(
    email: EmailIn,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    use_ai: bool = False,
    provider: str | None = None,
) -> MessageOut:
    return await _svc(request, user, session).ingest(email, use_ai=use_ai, provider=provider)


@router.get("/messages", response_model=list[MessageOut])
async def messages(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    classification: MessageClass | None = None,
    unread_only: bool = False,
    needs_attention: bool = False,
    limit: int = 100,
) -> list[MessageOut]:
    return await _svc(request, user, session).list_messages(
        classification=classification,
        unread_only=unread_only,
        needs_attention=needs_attention,
        limit=limit,
    )


@router.get("/stats", response_model=InboxStats)
async def stats(request: Request, user: CurrentUserDep, session: SessionDep) -> InboxStats:
    return await _svc(request, user, session).stats()


@router.get("/threads/{thread_id}", response_model=ThreadOut)
async def thread(
    thread_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> ThreadOut:
    try:
        return await _svc(request, user, session).get_thread(thread_id)
    except MessageNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "thread not found") from exc


@router.patch("/messages/{message_id}", response_model=MessageOut)
async def update_message(
    message_id: uuid.UUID,
    req: MessageUpdate,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> MessageOut:
    try:
        return await _svc(request, user, session).update_message(message_id, req)
    except MessageNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found") from exc


@router.post("/messages/{message_id}/suggest-reply", response_model=ReplySuggestionOut)
async def suggest_reply(
    message_id: uuid.UUID,
    req: SuggestReplyRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> ReplySuggestionOut:
    try:
        return await _svc(request, user, session).suggest_reply(message_id, req)
    except MessageNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "message not found") from exc
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except AIError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"reply draft failed: {exc}"
        ) from exc
