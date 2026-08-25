"""``/api/notifications`` — computed notification center."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.insights.notifications import NotificationsOut, compute_notifications

router = APIRouter(prefix="/notifications", tags=["insights"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=NotificationsOut)
async def notifications(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> NotificationsOut:
    _ = request
    return await compute_notifications(session, user.id)


from careeros.modules.ai.deps import build_ai_service  # noqa: E402
from careeros.modules.insights.brief import BriefOut, compute_brief  # noqa: E402

brief_router = APIRouter(prefix="/insights", tags=["insights"])


@brief_router.get("/brief", response_model=BriefOut)
async def daily_brief(
    request: Request, user: CurrentUserDep, session: SessionDep, narrative: bool = False
) -> BriefOut:
    ai = (
        build_ai_service(request.app.state.settings, session=session, user_id=user.id)
        if narrative
        else None
    )
    return await compute_brief(session, user.id, ai=ai, narrative=narrative)
