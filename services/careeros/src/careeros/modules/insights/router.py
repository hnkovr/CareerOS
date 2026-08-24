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
