from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Thread(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "thread"

    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    external_id: Mapped[str | None] = mapped_column(String(200), index=True)
    subject_norm: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    counterpart_email: Mapped[str | None] = mapped_column(String(320), index=True)
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", order_by="Message.received_at"
    )


class Message(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "message"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("thread.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    provider_message_id: Mapped[str | None] = mapped_column(String(300), index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="inbound")
    from_email: Mapped[str | None] = mapped_column(String(320), index=True)
    from_name: Mapped[str | None] = mapped_column(String(200))
    to: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500))
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    classification: Mapped[str] = mapped_column(
        String(30), nullable=False, default="other", index=True
    )
    urgency: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")
    classified_by: Mapped[str] = mapped_column(String(20), nullable=False, default="rules")
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    classification_signals: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    deadline_hint: Mapped[str | None] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("opportunity.id", ondelete="SET NULL"), index=True
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("company.id", ondelete="SET NULL")
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contact.id", ondelete="SET NULL")
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("application.id", ondelete="SET NULL"), index=True
    )
    extracted_opportunity: Mapped[bool] = mapped_column(default=False, nullable=False)

    thread: Mapped[Thread] = relationship(back_populates="messages")
