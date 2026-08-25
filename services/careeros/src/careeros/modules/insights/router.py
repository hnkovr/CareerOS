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


from fastapi import HTTPException, status  # noqa: E402

from careeros.modules.insights.funnel import FunnelOut, funnel_for  # noqa: E402
from careeros.modules.insights.market import MarketOut, market_for  # noqa: E402
from careeros.modules.insights.skills_gap import SkillsGapOut, skills_gap_for  # noqa: E402
from careeros.modules.vault.deps import get_vault  # noqa: E402
from careeros.modules.vault.service import VaultInvalid  # noqa: E402


def _data(request: Request):  # type: ignore[no-untyped-def]
    try:
        return get_vault(request.app.state.settings).require()
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@brief_router.get("/market", response_model=MarketOut)
async def market(
    request: Request, user: CurrentUserDep, session: SessionDep, window_days: int = 90
) -> MarketOut:
    _ = user
    return await market_for(session, _data(request), window_days=window_days)


@brief_router.get("/skills-gap", response_model=SkillsGapOut)
async def skills_gap(request: Request, user: CurrentUserDep, session: SessionDep) -> SkillsGapOut:
    _ = user
    return await skills_gap_for(session, _data(request))


@brief_router.get("/funnel", response_model=FunnelOut)
async def funnel(request: Request, user: CurrentUserDep, session: SessionDep) -> FunnelOut:
    _ = request, user
    return await funnel_for(session)
