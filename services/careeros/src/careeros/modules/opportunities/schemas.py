from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    OpportunityStatus,
    Recommendation,
    RemotePolicy,
    Seniority,
    Source,
)
from careeros.modules.vault.enums import ScoreDimension

# ------------------------------------------------------------------------- extraction (parser + AI)


class Compensation(BaseModel):
    min: float | None = None
    max: float | None = None
    currency: str | None = Field(default=None, description="ISO code, e.g. USD")
    period: CompensationPeriod | None = None
    type: Literal["salary", "rate"] | None = None
    raw: str | None = None

    def is_empty(self) -> bool:
        return self.min is None and self.max is None


class Recruiter(BaseModel):
    name: str | None = None
    email: str | None = None


class OpportunityExtraction(BaseModel):
    """Normalized fields. Produced by heuristics and/or AI; nulls mean 'not stated'."""

    title: str | None = None
    company: str | None = None
    contract_type: ContractType | None = None
    employment_type: EmploymentType | None = None
    location: str | None = None
    remote_policy: RemotePolicy = RemotePolicy.unknown
    remote_regions: list[str] = Field(default_factory=list)
    timezone_range: str | None = None
    compensation: Compensation | None = None
    seniority: Seniority | None = None
    requirements: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    recruiter: Recruiter | None = None
    deadline: date | None = None
    summary: str | None = None
    red_flags: list[str] = Field(default_factory=list)

    @field_validator(
        "technologies",
        "requirements",
        "preferred",
        "responsibilities",
        "remote_regions",
        "red_flags",
        mode="before",
    )
    @classmethod
    def _none_to_list(cls, v: object) -> object:
        return [] if v is None else v


# ----------------------------------------------------------------------------- ingest / API


class IngestRequest(BaseModel):
    source: Source = Source.manual
    url: str | None = None
    text: str | None = Field(default=None, description="pasted job description / message")
    structured: OpportunityExtraction | None = Field(
        default=None, description="pre-parsed fields (share sheet, ATS JSON)"
    )
    use_ai: bool = Field(default=False, description="also run AI extraction to fill gaps")
    provider: str | None = None
    received_at: datetime | None = None
    notes: str | None = None


class DimensionScore(BaseModel):
    name: ScoreDimension
    score: int = Field(ge=0, le=100)
    weight: float
    explanation: str
    signals: list[str] = Field(default_factory=list)


class ScoreOut(BaseModel):
    overall: int
    recommendation: Recommendation
    dimensions: list[DimensionScore]
    scoring_version: int
    vault_sha: str | None
    computed_at: datetime | None = None
    reasons: list[str] = Field(default_factory=list, description="why this recommendation")


class OpportunityAnalysisOutput(BaseModel):
    """AI interpretation of the deterministic score (never recomputes it)."""

    verdict: Literal["apply", "skip", "watch", "ask_first", "negotiate"]
    executive_summary: str
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    compensation_assessment: str | None = None
    competition_assessment: str | None = None
    channel_strategy: str | None = None
    recommended_cv_variant: str | None = None
    recommended_positioning: str | None = None
    suggested_response: str | None = None
    interview_prep: list[str] = Field(default_factory=list)
    questions_to_ask: list[str] = Field(default_factory=list)
    next_action: str


class AnalysisOut(OpportunityAnalysisOutput):
    ai_run_id: uuid.UUID | None = None
    provider: str | None = None
    model: str | None = None
    created_at: datetime | None = None


class OpportunityOut(BaseModel):
    id: uuid.UUID
    source: Source
    url: str | None
    title: str
    company_name: str | None
    contract_type: ContractType | None
    employment_type: EmploymentType | None
    location: str | None
    remote_policy: RemotePolicy
    remote_regions: list[str]
    timezone_range: str | None
    compensation: Compensation | None
    seniority: Seniority | None
    requirements: list[str]
    preferred: list[str]
    technologies: list[str]
    responsibilities: list[str]
    summary: str | None
    red_flags: list[str]
    recruiter: Recruiter | None
    received_at: datetime
    deadline: date | None
    status: OpportunityStatus
    dedup_key: str
    possible_duplicate_of: uuid.UUID | None
    parse_confidence: float
    parser: str
    notes: str | None
    created_at: datetime
    score: ScoreOut | None = None
    analysis: AnalysisOut | None = None


class OpportunityDetail(OpportunityOut):
    description_md: str | None = None
    raw_text: str | None = None


class StatusUpdate(BaseModel):
    status: OpportunityStatus


class AnalyzeRequest(BaseModel):
    provider: str | None = None


class CompareRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=2, max_length=5)


class CompareRow(BaseModel):
    id: uuid.UUID
    title: str
    company_name: str | None
    overall: int
    recommendation: Recommendation
    dimensions: dict[str, int]
    compensation: str | None
    remote_policy: RemotePolicy


class CompareOut(BaseModel):
    rows: list[CompareRow]
    ranked: list[uuid.UUID]
    dimension_names: list[str]


class ExternalPromptRequest(BaseModel):
    target: Literal["chatgpt", "claude", "gemini", "grok", "perplexity", "generic"] = "generic"


def extraction_to_dict(ex: OpportunityExtraction) -> dict[str, Any]:
    return ex.model_dump(mode="json")
