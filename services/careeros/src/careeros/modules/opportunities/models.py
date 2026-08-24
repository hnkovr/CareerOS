"""Operational tables for the opportunities context."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from careeros.core.db import Base, OwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Company(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "company"

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    domain: Mapped[str | None] = mapped_column(String(200))
    size: Mapped[str | None] = mapped_column(String(60))
    industry: Mapped[str | None] = mapped_column(String(120))
    hq_location: Mapped[str | None] = mapped_column(String(200))
    remote_friendly: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    links: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)


class Contact(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "contact"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("company.id", ondelete="SET NULL"), index=True
    )
    role: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    relationship_kind: Mapped[str] = mapped_column(String(40), default="other", nullable=False)
    last_contact_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_action: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class OpportunityRaw(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    """Immutable capture: what came in, as it came in."""

    __tablename__ = "opportunity_raw"

    source: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(String(2000))
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    capture_method: Mapped[str] = mapped_column(String(30), default="paste", nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Opportunity(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "opportunity"

    raw_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("opportunity_raw.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("company.id", ondelete="SET NULL"), index=True
    )
    recruiter_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contact.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200), index=True)
    contract_type: Mapped[str | None] = mapped_column(String(30))
    employment_type: Mapped[str | None] = mapped_column(String(30))
    location: Mapped[str | None] = mapped_column(String(300))
    remote_policy: Mapped[str] = mapped_column(String(30), default="unknown", nullable=False)
    remote_regions: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    timezone_range: Mapped[str | None] = mapped_column(String(120))
    compensation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    seniority: Mapped[str | None] = mapped_column(String(20))
    requirements: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    preferred: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    technologies: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    responsibilities: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    description_md: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    red_flags: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    recruiter: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False, index=True)
    dedup_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    possible_duplicate_of: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    parser: Mapped[str] = mapped_column(String(40), default="heuristic-v1", nullable=False)
    parse_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    raw: Mapped[OpportunityRaw] = relationship()
    scores: Mapped[list[OpportunityScore]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="OpportunityScore.computed_at.desc()",
    )
    analyses: Mapped[list[OpportunityAnalysis]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        order_by="OpportunityAnalysis.created_at.desc()",
    )


class OpportunityScore(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "opportunity_score"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("opportunity.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    scoring_version: Mapped[int] = mapped_column(Integer, nullable=False)
    vault_sha: Mapped[str | None] = mapped_column(String(64))
    overall: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    recommendation: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    dimensions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    reasons: Mapped[list[Any]] = mapped_column(JSONB, default=list, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    opportunity: Mapped[Opportunity] = relationship(back_populates="scores")


class OpportunityAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_analysis"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("opportunity.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    ai_run_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    provider: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    opportunity: Mapped[Opportunity] = relationship(back_populates="analyses")
