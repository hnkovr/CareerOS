"""Operational tables of the platform layer: connections (no secrets), sync runs, observations."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformConnection(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    """Per-platform connection state. Tokens live in the token store, never here."""

    __tablename__ = "platform_connection"
    __table_args__ = (
        UniqueConstraint("user_id", "platform", name="uq_platform_connection_user_platform"),
    )

    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="disconnected")
    auth_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    account_id: Mapped[str | None] = mapped_column(String(200))
    account_label: Mapped[str | None] = mapped_column(String(300))
    scopes: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class PlatformSyncRun(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "platform_sync_run"

    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class ApplicationObservation(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    """An application/response as observed on a platform; P1 Pipeline consumes these."""

    __tablename__ = "application_observation"

    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(200), index=True)
    job_title: Mapped[str] = mapped_column(String(300), nullable=False)
    company: Mapped[str | None] = mapped_column(String(200))
    job_url: Mapped[str | None] = mapped_column(String(2000))
    status_raw: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown", index=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at_platform: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("platform_sync_run.id", ondelete="SET NULL")
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    history: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
