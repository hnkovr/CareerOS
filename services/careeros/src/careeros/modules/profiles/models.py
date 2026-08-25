from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ProfileSnapshot(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "profile_snapshot"

    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    capture_method: Mapped[str] = mapped_column(String(20), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    headline: Mapped[str | None] = mapped_column(Text)
    about: Mapped[str | None] = mapped_column(Text)
    experience: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    skills: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    projects: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    portfolio: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    rates: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    availability: Mapped[str | None] = mapped_column(String(300))
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    audits: Mapped[list[ProfileAudit]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="ProfileAudit.created_at.desc()",
    )


class ProfileAudit(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "profile_audit"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("profile_snapshot.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    vault_sha: Mapped[str | None] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(30), nullable=False)
    health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    category_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    headline_suggestion: Mapped[str | None] = mapped_column(Text)
    about_suggestion: Mapped[str | None] = mapped_column(Text)
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    snapshot: Mapped[ProfileSnapshot] = relationship(back_populates="audits")
    findings: Mapped[list[AuditFinding]] = relationship(
        back_populates="audit", cascade="all, delete-orphan", order_by="AuditFinding.order"
    )


class AuditFinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_finding"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("profile_audit.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_matters: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_change: Mapped[str | None] = mapped_column(Text)
    source_fact_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=1.0)
    origin: Mapped[str] = mapped_column(String(15), nullable=False, default="deterministic")
    resolution: Mapped[str] = mapped_column(String(12), nullable=False, default="open")

    audit: Mapped[ProfileAudit] = relationship(back_populates="findings")


class DriftFinding(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    """A fact told differently on two platforms (or vs vault). Recomputed; decisions persist."""

    __tablename__ = "drift_finding"

    key: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    platform_a: Mapped[str] = mapped_column(String(30), nullable=False)
    platform_b: Mapped[str] = mapped_column(String(30), nullable=False)
    value_a: Mapped[str] = mapped_column(String(300), nullable=False)
    value_b: Mapped[str] = mapped_column(String(300), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    resolution: Mapped[str] = mapped_column(String(12), nullable=False, default="open", index=True)
