"""``/api/assistant`` — ask the tool-using assistant (ADR-014)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.provider import AIError
from careeros.modules.assistant.schemas import AskRequest, AskResponse, ToolInfo
from careeros.modules.assistant.service import AssistantService
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.service import VaultInvalid

router = APIRouter(prefix="/assistant", tags=["assistant"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> AssistantService:
    settings = request.app.state.settings
    return AssistantService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user.id),
        session=session,
        user_id=user.id,
    )


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools(request: Request, user: CurrentUserDep, session: SessionDep) -> list[ToolInfo]:
    """The read-only tools the assistant may call — nothing here writes anywhere."""
    return _svc(request, user, session).tools()


@router.post("/ask", response_model=AskResponse)
async def ask(
    req: AskRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> AskResponse:
    """One question → tool loop → answer with provenance. Withheld when the guard finds an
    uncited id or a number the model never saw."""
    try:
        return await _svc(request, user, session).ask(req)
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except AIError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"assistant failed: {exc}"
        ) from exc
