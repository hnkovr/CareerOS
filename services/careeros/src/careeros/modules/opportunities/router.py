"""``/api/opportunities``."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.ai.deps import build_ai_service
from careeros.modules.ai.provider import AIError
from careeros.modules.ai.schemas import BundleOut
from careeros.modules.opportunities.enums import OpportunityStatus
from careeros.modules.opportunities.schemas import (
    AnalyzeRequest,
    AssistRequest,
    CompareOut,
    CompareRequest,
    ExternalPromptRequest,
    IngestRequest,
    InterviewPrepOut,
    NegotiationOut,
    OpportunityDetail,
    OpportunityDiffOut,
    OpportunityOut,
    OpportunitySnapshotOut,
    OpportunitySourceOut,
    StatusUpdate,
)
from careeros.modules.opportunities.service import (
    OpportunityError,
    OpportunityNotFound,
    OpportunityService,
)
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.service import VaultInvalid

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> OpportunityService:
    settings = request.app.state.settings
    return OpportunityService(
        settings,
        get_vault(settings),
        build_ai_service(settings, session=session, user_id=user.id),
        session=session,
        user_id=user.id,
    )


@router.post("/ingest", response_model=OpportunityDetail, status_code=status.HTTP_201_CREATED)
async def ingest(
    req: IngestRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> OpportunityDetail:
    try:
        return await _svc(request, user, session).ingest(req)
    except OpportunityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("", response_model=list[OpportunityOut])
async def list_opportunities(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    status_filter: Annotated[OpportunityStatus | None, Query(alias="status")] = None,
    min_score: int | None = None,
    limit: int = 100,
) -> list[OpportunityOut]:
    return await _svc(request, user, session).list(
        status=status_filter, min_score=min_score, limit=limit
    )


@router.get("/{opportunity_id}", response_model=OpportunityDetail)
async def get_opportunity(
    opportunity_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> OpportunityDetail:
    try:
        return await _svc(request, user, session).get(opportunity_id)
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc


@router.post("/{opportunity_id}/rescore", response_model=OpportunityDetail)
async def rescore(
    opportunity_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> OpportunityDetail:
    try:
        return await _svc(request, user, session).rescore(opportunity_id)
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc


@router.post("/{opportunity_id}/analyze", response_model=OpportunityDetail)
async def analyze(
    opportunity_id: uuid.UUID,
    req: AnalyzeRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> OpportunityDetail:
    try:
        return await _svc(request, user, session).analyze(opportunity_id, provider=req.provider)
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc
    except AIError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"AI analysis failed: {exc}"
        ) from exc


@router.post("/{opportunity_id}/external-prompt", response_model=BundleOut)
async def external_prompt(
    opportunity_id: uuid.UUID,
    req: ExternalPromptRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> BundleOut:
    try:
        return await _svc(request, user, session).external_prompt(opportunity_id, req.target)
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc


@router.patch("/{opportunity_id}/status", response_model=OpportunityDetail)
async def set_status(
    opportunity_id: uuid.UUID,
    req: StatusUpdate,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> OpportunityDetail:
    try:
        return await _svc(request, user, session).set_status(opportunity_id, req.status)
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc


@router.post("/compare", response_model=CompareOut)
async def compare(
    req: CompareRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> CompareOut:
    try:
        return await _svc(request, user, session).compare(
            req.ids, use_ai=req.use_ai, provider=req.provider
        )
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"opportunity {exc} not found") from exc
    except OpportunityError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except AIError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"AI ranking failed: {exc}"
        ) from exc


@router.post("/{opportunity_id}/interview-prep", response_model=InterviewPrepOut)
async def interview_prep(
    opportunity_id: uuid.UUID,
    req: AssistRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> InterviewPrepOut:
    """Evidence map (what the vault proves for this posting) + AI prep plan citing fact ids."""
    try:
        return await _svc(request, user, session).interview_prep(
            opportunity_id, use_ai=req.use_ai, provider=req.provider
        )
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc
    except AIError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"AI interview prep failed: {exc}"
        ) from exc


@router.post("/{opportunity_id}/negotiation", response_model=NegotiationOut)
async def negotiation(
    opportunity_id: uuid.UUID,
    req: AssistRequest,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> NegotiationOut:
    """Compensation position vs targets and the observed stream + AI script (frame numbers only)."""
    try:
        return await _svc(request, user, session).negotiation(
            opportunity_id, use_ai=req.use_ai, provider=req.provider
        )
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc
    except AIError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"AI negotiation plan failed: {exc}"
        ) from exc


# ----------------------------------------------------------------------------- provenance (ADR-016)


@router.get("/{opportunity_id}/sources", response_model=list[OpportunitySourceOut])
async def list_sources(
    opportunity_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> list[OpportunitySourceOut]:
    """Every place this job was seen, with relation and authority (oldest first)."""
    try:
        return await _svc(request, user, session).list_sources(opportunity_id)
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc


@router.get("/{opportunity_id}/snapshots", response_model=list[OpportunitySnapshotOut])
async def list_snapshots(
    opportunity_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> list[OpportunitySnapshotOut]:
    """Raw captures of this job in chronological order — one per meaningful content change."""
    try:
        return await _svc(request, user, session).list_snapshots(opportunity_id)
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found") from exc


@router.get("/{opportunity_id}/diff", response_model=OpportunityDiffOut)
async def diff(
    opportunity_id: uuid.UUID,
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    from_raw_id: Annotated[uuid.UUID | None, Query(alias="from")] = None,
    to_raw_id: Annotated[uuid.UUID | None, Query(alias="to")] = None,
) -> OpportunityDiffOut:
    """What changed between two snapshots (default: the latest and the one before it)."""
    try:
        return await _svc(request, user, session).diff(
            opportunity_id, from_raw_id=from_raw_id, to_raw_id=to_raw_id
        )
    except OpportunityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{exc} not found") from exc
