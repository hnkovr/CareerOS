from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from careeros.modules.profiles.enums import (
    AuditCategory,
    CaptureMethod,
    FindingResolution,
    Severity,
)
from careeros.modules.vault.enums import Platform


class SnapshotExperienceItem(BaseModel):
    company: str
    title: str | None = None
    period: str | None = None
    description: str | None = None


class SnapshotIn(BaseModel):
    platform: Platform
    capture_method: CaptureMethod = CaptureMethod.paste
    captured_at: datetime | None = None
    headline: str | None = None
    about: str | None = None
    experience: list[SnapshotExperienceItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    portfolio: list[dict[str, Any]] = Field(default_factory=list)
    rates: dict[str, Any] | None = None
    availability: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    raw_text: str | None = Field(default=None, description="unstructured paste; kept verbatim")
    raw_payload: dict[str, Any] | None = None

    @field_validator("skills", mode="before")
    @classmethod
    def _none_to_list(cls, v: object) -> object:
        return [] if v is None else v


class SnapshotOut(SnapshotIn):
    id: uuid.UUID
    captured_at: datetime  # pyright: ignore[reportGeneralTypeIssues, reportIncompatibleVariableOverride] — required in output
    content_hash: str
    created_at: datetime
    latest_audit_id: uuid.UUID | None = None
    latest_health_score: int | None = None


class FindingOut(BaseModel):
    id: uuid.UUID | None = None
    category: AuditCategory
    severity: Severity
    problem: str
    why_it_matters: str
    suggested_change: str | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, default=1.0)
    origin: str = Field(default="deterministic", description="deterministic | ai")
    resolution: FindingResolution = FindingResolution.open


class AIFinding(BaseModel):
    category: AuditCategory
    severity: Severity
    problem: str
    why_it_matters: str
    suggested_change: str | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1, default=0.7)


class ProfileAuditOutput(BaseModel):
    """AI audit output schema (prompt: profile_audit)."""

    findings: list[AIFinding] = Field(default_factory=list)
    headline_suggestion: str | None = None
    about_suggestion: str | None = None


class AuditOut(BaseModel):
    id: uuid.UUID
    snapshot_id: uuid.UUID
    platform: Platform
    vault_sha: str | None
    engine_version: str
    health_score: int
    category_scores: dict[str, int]
    findings: list[FindingOut]
    headline_suggestion: str | None = None
    about_suggestion: str | None = None
    ai_used: bool
    ai_run_id: uuid.UUID | None = None
    created_at: datetime


class AuditRequest(BaseModel):
    use_ai: bool = False
    provider: str | None = None


class PlatformHealth(BaseModel):
    platform: Platform
    snapshot_id: uuid.UUID | None
    captured_at: datetime | None
    health_score: int | None
    open_findings: int
    top_severity: Severity | None
    audited_at: datetime | None


class FindingResolutionUpdate(BaseModel):
    resolution: FindingResolution
