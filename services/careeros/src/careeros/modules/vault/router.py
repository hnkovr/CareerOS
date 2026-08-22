"""``/api/vault`` — read canonical data, preview and apply changes, history, fact search."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from careeros.core.auth import CurrentUserDep
from careeros.modules.vault.deps import get_vault
from careeros.modules.vault.layout import EDITABLE_COLLECTIONS
from careeros.modules.vault.service import (
    ChangePreview,
    ChangeRequest,
    ChangeResult,
    CommitOut,
    FactHit,
    IssueOut,
    Vault,
    VaultConflict,
    VaultError,
    VaultInvalid,
    VaultStatus,
    search_facts,
)

router = APIRouter(prefix="/vault", tags=["vault"])


def _vault(request: Request) -> Vault:
    return get_vault(request.app.state.settings)


VaultDep = Annotated[Vault, Depends(_vault)]


@router.get("/status", response_model=VaultStatus)
async def vault_status(vault: VaultDep, _: CurrentUserDep) -> VaultStatus:
    return await asyncio.to_thread(vault.status)


@router.get("/issues", response_model=list[IssueOut])
async def vault_issues(vault: VaultDep, _: CurrentUserDep) -> list[IssueOut]:
    result = await asyncio.to_thread(vault.load)
    return [IssueOut.from_issue(i) for i in result.issues]


@router.get("/collections", response_model=list[str])
async def collections(_: CurrentUserDep) -> list[str]:
    return EDITABLE_COLLECTIONS


@router.get("/facts/search", response_model=list[FactHit])
async def facts_search(
    vault: VaultDep, _: CurrentUserDep, q: Annotated[str, Query(min_length=1)], limit: int = 20
) -> list[FactHit]:
    data = await asyncio.to_thread(vault.require)
    return search_facts(data, q, limit)


@router.get("/history", response_model=list[CommitOut])
async def history(
    vault: VaultDep, _: CurrentUserDep, path: str | None = None, n: int = 20
) -> list[CommitOut]:
    commits = await asyncio.to_thread(vault.history, path, n)
    return [CommitOut(sha=c.sha, date=c.date, message=c.message) for c in commits]


@router.post("/changes/preview", response_model=ChangePreview)
async def preview_change(req: ChangeRequest, vault: VaultDep, _: CurrentUserDep) -> ChangePreview:
    try:
        return await asyncio.to_thread(vault.preview_change, req)
    except VaultError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/changes/apply", response_model=ChangeResult)
async def apply_change(req: ChangeRequest, vault: VaultDep, _: CurrentUserDep) -> ChangeResult:
    try:
        return await asyncio.to_thread(vault.apply_change, req)
    except VaultConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except VaultInvalid as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            [IssueOut.from_issue(i).model_dump() for i in exc.issues],
        ) from exc
    except VaultError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{collection}")
async def get_collection(collection: str, vault: VaultDep, _: CurrentUserDep) -> Any:
    if collection not in EDITABLE_COLLECTIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown collection '{collection}'")
    data = await asyncio.to_thread(vault.require)
    value = getattr(data, collection)
    if isinstance(value, list):
        return [v.model_dump(mode="json") for v in value]
    return value.model_dump(mode="json") if value is not None else None


@router.get("/{collection}/{item_id}")
async def get_item(collection: str, item_id: str, vault: VaultDep, _: CurrentUserDep) -> Any:
    if collection not in EDITABLE_COLLECTIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown collection '{collection}'")
    data = await asyncio.to_thread(vault.require)
    value = getattr(data, collection)
    if not isinstance(value, list):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"'{collection}' is a singleton")
    for item in value:
        if item.id == item_id:
            return item.model_dump(mode="json")
    raise HTTPException(status.HTTP_404_NOT_FOUND, f"{collection}/{item_id} not found")
