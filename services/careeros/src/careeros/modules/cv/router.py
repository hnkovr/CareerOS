"""``/api/cv`` — variants, generate, artifacts (+ files), compare."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.core.auth import CurrentUser, CurrentUserDep
from careeros.core.db import get_session
from careeros.modules.cv.deps import build_cv_service
from careeros.modules.cv.schemas import (
    CompareRequest,
    CVArtifactOut,
    CVComparison,
    GenerateCVRequest,
    VariantOut,
)
from careeros.modules.cv.service import CVError, CVService, VariantNotFound
from careeros.modules.vault.service import VaultInvalid

router = APIRouter(prefix="/cv", tags=["cv"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _svc(request: Request, user: CurrentUser, session: AsyncSession) -> CVService:
    return build_cv_service(request.app.state.settings, session=session, user_id=user.id)


@router.get("/variants", response_model=list[VariantOut])
async def variants(request: Request, user: CurrentUserDep, session: SessionDep) -> list[VariantOut]:
    try:
        return _svc(request, user, session).variants()
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.post("/generate", response_model=CVArtifactOut)
async def generate(
    req: GenerateCVRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> CVArtifactOut:
    svc = _svc(request, user, session)
    context_text = None
    if req.opportunity_id is not None:
        from careeros.modules.opportunities.deps import opportunity_context_text

        context_text = await opportunity_context_text(session, req.opportunity_id)
        if context_text is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "opportunity not found")
    try:
        return await svc.generate(req, context_text=context_text)
    except VariantNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except VaultInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.get("/artifacts", response_model=list[CVArtifactOut])
async def artifacts(
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
    variant_id: str | None = None,
    limit: int = 50,
) -> list[CVArtifactOut]:
    return await _svc(request, user, session).list_artifacts(variant_id=variant_id, limit=limit)


@router.get("/artifacts/{artifact_id}", response_model=CVArtifactOut)
async def artifact(
    artifact_id: uuid.UUID, request: Request, user: CurrentUserDep, session: SessionDep
) -> CVArtifactOut:
    out = await _svc(request, user, session).get_artifact(artifact_id)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    return out


@router.get("/artifacts/{artifact_id}/file/{kind}")
async def artifact_file(
    artifact_id: uuid.UUID,
    kind: Literal["pdf", "md", "typst", "json"],
    request: Request,
    user: CurrentUserDep,
    session: SessionDep,
) -> FileResponse:
    out = await _svc(request, user, session).get_artifact(artifact_id, with_document=False)
    if out is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "artifact not found")
    path = getattr(out.files, "json_" if kind == "json" else kind)
    if not path or not await anyio.Path(path).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {kind} file for this artifact")
    media = {
        "pdf": "application/pdf",
        "md": "text/markdown",
        "typst": "text/plain",
        "json": "application/json",
    }[kind]
    return FileResponse(
        path, media_type=media, filename=f"{out.variant_id}.{kind if kind != 'typst' else 'typ'}"
    )


@router.post("/compare", response_model=CVComparison)
async def compare(
    req: CompareRequest, request: Request, user: CurrentUserDep, session: SessionDep
) -> CVComparison:
    try:
        return await _svc(request, user, session).compare(req.a, req.b)
    except CVError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
