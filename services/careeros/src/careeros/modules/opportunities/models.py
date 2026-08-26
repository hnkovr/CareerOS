"""Operational tables for the opportunities context."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
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
    # ADR-016: a raw is a *snapshot* of its opportunity. Nullable because the first raw is
    # inserted before the opportunity exists (``Opportunity.raw_id`` keeps its direction), and
    # deliberately **not** a database FK: opportunity.raw_id -> raw -> opportunity would be a
    # cycle, which SQLAlchemy can only create/drop via ALTER — and ``drop_all`` on a test
    # database created before the constraint existed fails outright. Same shape as
    # ``platform.models.ApplicationObservation.opportunity_id``.
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    strategy: Mapped[str | None] = mapped_column(String(40))
    fetched_url: Mapped[str | None] = mapped_column(String(2000))
    resolved_url: Mapped[str | None] = mapped_column(String(2000))
    is_archive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    archive_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality: Mapped[float | None] = mapped_column(Float)
    extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Opportunity(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    __tablename__ = "opportunity"
    __table_args__ = (
        # Layer-1 identity (ADR-016 §4): the provider's own id, scoped per user and platform.
        Index("ix_opportunity_user_id_platform_external_id", "user_id", "platform", "external_id"),
    )

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
    # ADR-016: identity and provenance. ``platform`` is a ``vault.enums.Platform`` value stored
    # as a string like ``source``; ``canonical_url`` is ``dedup.normalize_url(url)``;
    # ``field_evidence`` is ``{field: [{value, source, source_url, observed_at, confidence}]}``
    # — every observed value is kept, conflicts included.
    platform: Mapped[str | None] = mapped_column(String(30))
    external_id: Mapped[str | None] = mapped_column(String(200))
    canonical_url: Mapped[str | None] = mapped_column(String(2000), index=True)
    field_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

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


class OpportunitySource(UUIDPrimaryKeyMixin, OwnedMixin, TimestampMixin, Base):
    """Where a job was seen: one row per (opportunity, platform, id-or-url) — ADR-016 §2.

    ``relation`` says how the source relates to the canonical job (``primary`` for the capture that
    created it, ``aggregates``/``mirror``/``repost_of``… for other listings of the same job);
    ``authority`` ranks the source when field values disagree (see ``FieldSource``).
    """

    __tablename__ = "opportunity_source"
    __table_args__ = (
        Index(
            "ix_opportunity_source_identity",
            "opportunity_id",
            "platform",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("opportunity.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    canonical_url: Mapped[str | None] = mapped_column(String(2000), index=True)
    original_url: Mapped[str | None] = mapped_column(String(2000))
    relation: Mapped[str] = mapped_column(String(30), default="primary", nullable=False)
    authority: Mapped[str] = mapped_column(String(30), nullable=False)
    strategy: Mapped[str | None] = mapped_column(String(40))
    raw_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("opportunity_raw.id", ondelete="SET NULL")
    )
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    is_archive: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(Float)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
