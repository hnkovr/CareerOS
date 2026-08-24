"""``/api/search`` — unified search + reindex."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.search.schemas import DocKind, ReindexOut, ReindexRequest, SearchOut
from careeros.modules.search.service import SearchService
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.service import VaultInvalid

router = APIRouter(prefix="/search", tags=["search"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> SearchService:
    settings = request.app.state.settings
    return SearchService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user.id),
        session=session,
        user_id=user.id,
    )


@router.get("", response_model=SearchOut)
async def search(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    q: Annotated[str, Query(min_length=2)],
    kind: Annotated[list[DocKind] | None, Query()] = None,
    limit: int = 20,
) -> SearchOut:
    return await _svc(request, user, session).search(q, kinds=kind, limit=limit)


@router.post("/reindex", response_model=ReindexOut)
async def reindex(
    req: ReindexRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> ReindexOut:
    try:
        return await _svc(request, user, session).reindex(req)
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
