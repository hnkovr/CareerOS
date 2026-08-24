"""Suggestion lifecycle (ADR-010 §3, brief §54): explicit approval states, legal transitions only.

``executed`` never triggers an external write here — it records that the human performed the
action themselves (or an approved executor did). Side effects on execution belong to the owning
module (e.g. inbox marks a reply as sent and writes the timeline event).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from careeros.modules.ai.models import Suggestion

STATES = ("suggested", "reviewed", "approved", "executed", "rejected")
LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "suggested": {"reviewed", "approved", "rejected"},
    "reviewed": {"approved", "rejected"},
    "approved": {"executed", "rejected"},
    "executed": set(),
    "rejected": set(),
}


class SuggestionError(Exception):
    pass


class SuggestionNotFound(SuggestionError):
    pass


class IllegalTransition(SuggestionError):
    pass


class SuggestionOut(BaseModel):
    id: uuid.UUID
    target_type: str
    target_ref: str
    title: str
    payload: dict[str, Any]
    state: str
    decided_at: datetime | None
    decision_note: str | None
    ai_run_id: uuid.UUID | None
    created_at: datetime


class SuggestionUpdate(BaseModel):
    state: str = Field(pattern="|".join(s for s in STATES if s != "suggested"))
    note: str | None = None


def _out(s: Suggestion) -> SuggestionOut:
    return SuggestionOut(
        id=s.id,
        target_type=s.target_type,
        target_ref=s.target_ref,
        title=s.title,
        payload=dict(s.payload),
        state=s.state,
        decided_at=s.decided_at,
        decision_note=s.decision_note,
        ai_run_id=s.ai_run_id,
        created_at=s.created_at,
    )


async def list_suggestions(
    session: AsyncSession,
    *,
    state: str | None = None,
    target_type: str | None = None,
    limit: int = 100,
) -> list[SuggestionOut]:
    stmt = select(Suggestion).order_by(Suggestion.created_at.desc()).limit(limit)
    if state:
        stmt = stmt.where(Suggestion.state == state)
    if target_type:
        stmt = stmt.where(Suggestion.target_type == target_type)
    rows = (await session.scalars(stmt)).all()
    return [_out(s) for s in rows]


async def get_suggestion(session: AsyncSession, suggestion_id: uuid.UUID) -> SuggestionOut:
    row = await session.get(Suggestion, suggestion_id)
    if row is None:
        raise SuggestionNotFound(str(suggestion_id))
    return _out(row)


async def transition(
    session: AsyncSession, suggestion_id: uuid.UUID, new_state: str, *, note: str | None = None
) -> SuggestionOut:
    row = await session.get(Suggestion, suggestion_id)
    if row is None:
        raise SuggestionNotFound(str(suggestion_id))
    if new_state not in LEGAL_TRANSITIONS.get(row.state, set()):
        allowed = ", ".join(sorted(LEGAL_TRANSITIONS.get(row.state, set()))) or "none — terminal"
        raise IllegalTransition(f"cannot move '{row.state}' → '{new_state}' (allowed: {allowed})")
    row.state = new_state
    row.decided_at = datetime.now(UTC)
    if note is not None:
        row.decision_note = note
    await session.commit()
    return _out(row)
