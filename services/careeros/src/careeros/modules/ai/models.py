"""AI run ledger and suggestions (approval-gated AI proposals)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class AIRun(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "ai_run"

    prompt_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="builtin")
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    inputs_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_schema: Mapped[str | None] = mapped_column(String(120))
    valid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ok", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(60), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(120), index=True)
    feedback: Mapped[str | None] = mapped_column(String(10))
    feedback_note: Mapped[str | None] = mapped_column(Text)


class Suggestion(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "suggestion"

    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    target_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(20), default="suggested", nullable=False, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
