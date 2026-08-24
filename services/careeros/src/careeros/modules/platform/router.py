"""``/api/platform`` — capabilities matrix, connections/OAuth, parse (dry), sync, observations."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import SINGLE_USER_ID, CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.provider import AIError
from careeros.modules.opportunities.service import OpportunityError
from careeros.modules.platform.base import (
    CapabilityUnavailable,
    NotConnected,
    ParseError,
    PlatformError,
    UpstreamError,
)
from careeros.modules.platform.enums import ApplicationStatus, SyncKind
from careeros.modules.platform.http import build_http
from careeros.modules.platform.registry import UnknownPlatform
from careeros.modules.platform.schemas import (
    ApplicationObservationOut,
    Capabilities,
    ConnectionOut,
    DoctorCheck,
    OAuthStartOut,
    ParseResult,
    SyncRequest,
    SyncResult,
    SyncRunOut,
)
from careeros.modules.platform.sync import PlatformSyncService
from careeros.modules.vault.enums import Platform
from careeros.modules.vault.service import VaultInvalid

router = APIRouter(prefix="/platform", tags=["platform"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> PlatformSyncService:
    return PlatformSyncService(request.app.state.settings, session=session, user_id=user.id)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownPlatform):
        return HTTPException(status.HTTP_404_NOT_FOUND, f"no connector for platform: {exc}")
    if isinstance(exc, CapabilityUnavailable):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            {"error": str(exc), "available": [str(m) for m in exc.available]},
        )
    if isinstance(exc, NotConnected):
        return HTTPException(status.HTTP_409_CONFLICT, {"error": str(exc), "hint": exc.hint})
    if isinstance(exc, UpstreamError):
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    if isinstance(exc, ParseError | OpportunityError | VaultInvalid):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, AIError):
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI failed: {exc}")
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


_ERRORS = (PlatformError, OpportunityError, VaultInvalid, AIError)


@router.get("/capabilities", response_model=list[Capabilities])
async def capabilities(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> list[Capabilities]:
    return _svc(request, user, session).platform.capabilities()


@router.get("/connections", response_model=list[ConnectionOut])
async def connections(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> list[ConnectionOut]:
    return await _svc(request, user, session).platform.list_connections()


@router.post("/{platform}/connect", response_model=OAuthStartOut)
async def connect(
    platform: Platform, request: Request, user: CurrentUserDep, session: SessionDep
) -> OAuthStartOut:
    try:
        return await _svc(request, user, session).platform.oauth_start(platform)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get("/oauth/{platform}/callback", response_model=ConnectionOut)
async def oauth_callback(
    platform: Platform,
    code: str,
    state: str,
    request: Request,
    session: SessionDep,
) -> ConnectionOut:
    """Landing point of the provider redirect — the browser carries no bearer token, so the
    single-use, TTL-bound ``state`` issued by ``/connect`` is the authentication here."""
    settings = request.app.state.settings
    user = CurrentUser(id=SINGLE_USER_ID, email=settings.user_email)
    svc = _svc(request, user, session)
    try:
        async with build_http(svc.settings) as http:
            return await svc.platform.oauth_callback(platform, code, state, http=http)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post("/{platform}/refresh", response_model=ConnectionOut)
async def refresh(
    platform: Platform, request: Request, user: CurrentUserDep, session: SessionDep
) -> ConnectionOut:
    svc = _svc(request, user, session)
    try:
        async with build_http(svc.settings) as http:
            return await svc.platform.refresh(platform, http=http)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.delete("/{platform}/connection", response_model=ConnectionOut)
async def disconnect(
    platform: Platform, request: Request, user: CurrentUserDep, session: SessionDep
) -> ConnectionOut:
    try:
        return await _svc(request, user, session).platform.disconnect(platform)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get("/{platform}/doctor", response_model=list[DoctorCheck])
async def doctor(
    platform: Platform, request: Request, user: CurrentUserDep, session: SessionDep
) -> list[DoctorCheck]:
    svc = _svc(request, user, session)
    try:
        async with build_http(svc.settings) as http:
            return await svc.platform.doctor(platform, http=http)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post("/{platform}/parse/{kind}", response_model=ParseResult)
async def parse(
    platform: Platform,
    kind: SyncKind,
    req: SyncRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> ParseResult:
    """Dry parse of pasted text or an export file — nothing is persisted."""
    try:
        return await _svc(request, user, session).parse(
            platform, kind, text=req.text, file_path=req.file_path
        )
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post("/{platform}/sync/{kind}", response_model=SyncResult)
async def sync(
    platform: Platform,
    kind: SyncKind,
    req: SyncRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> SyncResult:
    try:
        return await _svc(request, user, session).sync(platform, kind, req)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post("/sync-all", response_model=list[SyncResult])
async def sync_all(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    platform: Platform | None = None,
    dry_run: bool = False,
) -> list[SyncResult]:
    try:
        return await _svc(request, user, session).sync_all(platform, dry_run=dry_run)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get("/sync-runs", response_model=list[SyncRunOut])
async def sync_runs(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    platform: Platform | None = None,
    kind: SyncKind | None = None,
    limit: int = 50,
) -> list[SyncRunOut]:
    return await _svc(request, user, session).platform.list_runs(
        platform=platform, kind=kind, limit=limit
    )


@router.get("/applications", response_model=list[ApplicationObservationOut])
async def applications(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    platform: Platform | None = None,
    status_filter: Annotated[ApplicationStatus | None, Query(alias="status")] = None,
    limit: int = 200,
) -> list[ApplicationObservationOut]:
    return await _svc(request, user, session).platform.list_observations(
        platform=platform, status=status_filter, limit=limit
    )
