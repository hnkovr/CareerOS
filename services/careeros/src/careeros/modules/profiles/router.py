"""``/api/profiles`` — snapshots, audits, platform health."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.provider import AIError
from careeros.modules.profiles.schemas import (
    AuditOut,
    AuditRequest,
    FindingOut,
    FindingResolutionUpdate,
    PlatformHealth,
    SnapshotIn,
    SnapshotOut,
)
from careeros.modules.profiles.service import ProfileService, SnapshotNotFound
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.enums import Platform
from careeros.modules.vault.service import VaultInvalid

router = APIRouter(prefix="/profiles", tags=["profiles"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> ProfileService:
    settings = request.app.state.settings
    return ProfileService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user.id),
        session=session,
        user_id=user.id,
    )


@router.get("/health", response_model=list[PlatformHealth])
async def platform_health(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> list[PlatformHealth]:
    return await _svc(request, user, session).platform_health()


@router.post("/snapshots", response_model=SnapshotOut, status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    snap: SnapshotIn, request: Request, user: CurrentUserDep, session: SessionDep
) -> SnapshotOut:
    return await _svc(request, user, session).create_snapshot(snap)


@router.get("/snapshots", response_model=list[SnapshotOut])
async def list_snapshots(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    platform: Platform | None = None,
    limit: int = 50,
) -> list[SnapshotOut]:
    return await _svc(request, user, session).list_snapshots(platform=platform, limit=limit)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
async def get_snapshot(
    snapshot_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> SnapshotOut:
    try:
        return await _svc(request, user, session).get_snapshot(snapshot_id)
    except SnapshotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "snapshot not found") from exc


@router.post("/snapshots/{snapshot_id}/audit", response_model=AuditOut)
async def audit(
    snapshot_id: uuid.UUID,
    req: AuditRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> AuditOut:
    try:
        return await _svc(request, user, session).audit(
            snapshot_id, use_ai=req.use_ai, provider=req.provider
        )
    except SnapshotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "snapshot not found") from exc
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except AIError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI audit failed: {exc}") from exc


@router.get("/audits/{audit_id}", response_model=AuditOut)
async def get_audit(
    audit_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> AuditOut:
    try:
        return await _svc(request, user, session).get_audit(audit_id)
    except SnapshotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "audit not found") from exc


@router.patch("/findings/{finding_id}", response_model=FindingOut)
async def set_finding_resolution(
    finding_id: uuid.UUID,
    req: FindingResolutionUpdate,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> FindingOut:
    try:
        return await _svc(request, user, session).set_finding_resolution(finding_id, req.resolution)
    except SnapshotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "finding not found") from exc


# ----------------------------------------------------------------------------- drift (brief §12)
from pydantic import BaseModel as _BaseModel  # noqa: E402
from pydantic import Field as _Field  # noqa: E402

from careeros.modules.profiles.drift import (  # noqa: E402
    DriftOut,
    DriftSummary,
    list_drift,
    recompute_drift,
    set_drift_resolution,
)


class DriftResolutionIn(_BaseModel):
    resolution: str = _Field(pattern="^(open|resolved|dismissed)$")


@router.get("/drift", response_model=DriftSummary)
async def drift(
    request: Request, user: CurrentUserDep, session: SessionDep, open_only: bool = False
) -> DriftSummary:
    _ = request, user
    return await list_drift(session, open_only=open_only)


@router.post("/drift/recompute", response_model=DriftSummary)
async def drift_recompute(
    request: Request, user: CurrentUserDep, session: SessionDep
) -> DriftSummary:
    settings = request.app.state.settings
    try:
        data = get_vault(settings).require()
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return await recompute_drift(session, user.id, data)


@router.patch("/drift/{finding_id}", response_model=DriftOut)
async def drift_resolution(
    finding_id: uuid.UUID,
    req: DriftResolutionIn,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> DriftOut:
    _ = request, user
    out = await set_drift_resolution(session, finding_id, req.resolution)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "drift finding not found")
    return out
