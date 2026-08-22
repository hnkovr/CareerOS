"""CV artifacts and their provenance-carrying bullets."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class CVArtifact(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "cv_artifact"

    variant_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    positioning_id: Mapped[str] = mapped_column(String(80), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(80), nullable=False)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    vault_sha: Mapped[str | None] = mapped_column(String(64))
    ai_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ready", nullable=False)
    files: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary_text: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    render_log: Mapped[str | None] = mapped_column(Text)

    bullets: Mapped[list[GeneratedBullet]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan", order_by="GeneratedBullet.order"
    )


class GeneratedBullet(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "generated_bullet"

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("cv_artifact.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    section: Mapped[str] = mapped_column(String(40), nullable=False)
    group_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    derived_from: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(10), default="fact", nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    artifact: Mapped[CVArtifact] = relationship(back_populates="bullets")
