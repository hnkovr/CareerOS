from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from careeros.modules.opportunities.enums import (
    CompensationPeriod,
    ContractType,
    EmploymentType,
    FieldSource,
    OpportunityStatus,
    Recommendation,
    RemotePolicy,
    Seniority,
    Source,
    SourceRelation,
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
    external_id: str | None = Field(
        default=None, description="the source platform's own id (kept in raw_payload)"
    )
    raw_payload: dict[str, Any] | None = Field(
        default=None, description="verbatim source payload; defaults to the structured fields"
    )
    platform: str | None = Field(
        default=None,
        description="vault Platform value the job was read from (identity with external_id)",
    )
    canonical_url: str | None = Field(
        default=None,
        description="provider-canonical URL when the caller knows it; else normalize_url(url)",
    )


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
    platform: str | None = None
    external_id: str | None = None
    canonical_url: str | None = None


class OpportunityDetail(OpportunityOut):
    description_md: str | None = None
    raw_text: str | None = None


class StatusUpdate(BaseModel):
    status: OpportunityStatus


class AnalyzeRequest(BaseModel):
    provider: str | None = None


class RankedItem(BaseModel):
    opportunity_id: str
    rank: int = Field(ge=1)
    rationale: str


class CompareRankingOutput(BaseModel):
    """AI ranking over the deterministic comparison rows (never recomputes the scores)."""

    ranking: list[RankedItem]
    recommendation: str
    tradeoffs: list[str] = Field(default_factory=list)


class CompareRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=2, max_length=5)
    use_ai: bool = Field(default=False, description="add an AI-ranked recommendation (§31)")
    provider: str | None = None


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
    ranked: list[uuid.UUID] = Field(description="deterministic: by overall score, best first")
    dimension_names: list[str]
    ranking: list[RankedItem] | None = Field(
        default=None, description="AI interpretation; None when not requested or rejected"
    )
    recommendation: str | None = None
    tradeoffs: list[str] = Field(default_factory=list)
    ranking_note: str | None = None
    ai_run_id: uuid.UUID | None = None


class ExternalPromptRequest(BaseModel):
    target: Literal["chatgpt", "claude", "gemini", "grok", "perplexity", "generic"] = "generic"


def extraction_to_dict(ex: OpportunityExtraction) -> dict[str, Any]:
    return ex.model_dump(mode="json")


# ----------------------------------------------------------------------------- provenance (ADR-016)


class SourceIn(BaseModel):
    """One place a job was seen; upserted by (platform, external_id) or (platform, canonical_url).

    ``authority`` ranks it when field values disagree; ``relation`` says how it maps onto the job.
    """

    platform: str = Field(description="vault Platform value, e.g. 'hh', 'rockethunt', 'website'")
    external_id: str | None = None
    source_url: str | None = None
    canonical_url: str | None = Field(
        default=None, description="defaults to normalize_url(source_url)"
    )
    original_url: str | None = Field(
        default=None, description="employer/ATS link an aggregator points to"
    )
    relation: SourceRelation = SourceRelation.primary
    authority: FieldSource
    strategy: str | None = Field(default=None, description="fetch strategy that produced it")
    raw_id: uuid.UUID | None = None
    fetched_at: datetime | None = None
    published_at: datetime | None = None
    content_hash: str | None = None
    is_archive: bool = False
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    meta: dict[str, Any] | None = None


class OpportunitySourceOut(BaseModel):
    id: uuid.UUID
    opportunity_id: uuid.UUID
    platform: str
    external_id: str | None
    source_url: str | None
    canonical_url: str | None
    original_url: str | None
    relation: SourceRelation
    authority: FieldSource
    strategy: str | None
    raw_id: uuid.UUID | None
    fetched_at: datetime | None
    published_at: datetime | None
    content_hash: str | None
    is_archive: bool
    confidence: float | None
    meta: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class SnapshotIn(BaseModel):
    """A re-read of a job. Becomes a new ``OpportunityRaw`` only when the fingerprint changed."""

    raw_text: str
    raw_payload: dict[str, Any] | None = None
    strategy: str | None = None
    fetched_url: str | None = None
    resolved_url: str | None = None
    is_archive: bool = Field(
        default=False, description="archived copy: kept as history, never overwrites the live view"
    )
    archive_ts: datetime | None = None
    quality: float | None = Field(default=None, ge=0.0, le=1.0)
    extracted: dict[str, Any] | None = Field(
        default=None, description="OpportunityExtraction at capture time (validated before use)"
    )
    content_hash: str | None = Field(default=None, description="defaults to sha256(raw_text)")
    captured_at: datetime | None = None
    capture_method: str = "refresh"
    authority: FieldSource | None = Field(
        default=None, description="field-evidence source; defaults to board_page / archive"
    )
    source_url: str | None = Field(
        default=None, description="evidence source_url; defaults to fetched_url"
    )


class OpportunitySnapshotOut(BaseModel):
    id: uuid.UUID = Field(description="the OpportunityRaw id")
    opportunity_id: uuid.UUID | None
    captured_at: datetime
    capture_method: str
    source: str
    url: str | None
    strategy: str | None
    fingerprint: str | None
    content_hash: str
    is_archive: bool
    archive_ts: datetime | None
    quality: float | None
    fetched_url: str | None
    resolved_url: str | None
    extracted: dict[str, Any] | None = None


class FieldChange(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class OpportunityDiffOut(BaseModel):
    from_raw_id: uuid.UUID | None
    to_raw_id: uuid.UUID | None
    from_captured_at: datetime | None = None
    to_captured_at: datetime | None = None
    changes: list[FieldChange] = Field(default_factory=list)


# ----------------------------------------------------------------------------- P3 assistants


class AssistRequest(BaseModel):
    use_ai: bool = True
    provider: str | None = None


class StoryMaterial(BaseModel):
    """A verified vault item the candidate can build an interview story on."""

    fact_id: str
    kind: Literal["achievement", "project", "experience"]
    title: str
    company: str | None = None
    technologies: list[str] = Field(default_factory=list, description="matched opportunity techs")
    facts: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


class InterviewFrame(BaseModel):
    track: Literal["employment", "freelance"]
    stages: list[str]
    matched: list[str] = Field(description="opportunity technologies backed by vault evidence")
    claimed_only: list[str] = Field(description="in skills, but no achievement/project cites them")
    missing: list[str] = Field(description="required by the opportunity, absent from the vault")
    materials: list[StoryMaterial]
    weak_dimensions: list[str] = Field(description="score dimensions below 50 — expect probing")
    questions_to_ask: list[str] = Field(description="deterministic: what the posting leaves open")


class ExpectedQuestion(BaseModel):
    question: str
    why: str
    answer_outline: str
    derived_from: list[str] = Field(default_factory=list)


class Story(BaseModel):
    title: str
    situation: str
    action: str
    result: str
    derived_from: list[str] = Field(min_length=1)


class InterviewPrepOutput(BaseModel):
    focus_areas: list[str] = Field(default_factory=list)
    expected_questions: list[ExpectedQuestion] = Field(default_factory=list)
    stories: list[Story] = Field(default_factory=list)
    gaps_to_prepare: list[str] = Field(default_factory=list)
    questions_to_ask: list[str] = Field(default_factory=list)
    plan: list[str] = Field(default_factory=list, description="ordered preparation steps")


class InterviewPrepOut(BaseModel):
    opportunity_id: uuid.UUID
    frame: InterviewFrame
    plan: InterviewPrepOutput | None = None
    provenance_rejected: list[str] = Field(default_factory=list)
    ai_run_id: uuid.UUID | None = None
    suggestion_id: uuid.UUID | None = None
    provider: str | None = None
    model: str | None = None


class CompBand(BaseModel):
    n: int = 0
    p25: float | None = None
    median: float | None = None
    p75: float | None = None


class LeverageFact(BaseModel):
    fact_id: str
    title: str
    technologies: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)


class NegotiationFrame(BaseModel):
    basis: Literal["annual", "hourly"]
    currency: str
    offered_min: float | None = None
    offered_max: float | None = None
    offered_raw: str | None = None
    offered_currency: str | None = None
    target: float | None = None
    floor: float | None = None
    anchor: float | None = Field(default=None, description="max(target, observed p75), rounded")
    observed: CompBand
    position: Literal["unknown", "below_floor", "below_target", "at_target", "above_target"]
    gap_to_target_pct: float | None = None
    leverage: list[LeverageFact] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    allowed_numbers: list[str] = Field(description="numbers a plan may state without citing facts")


class LeveragePoint(BaseModel):
    point: str
    derived_from: list[str] = Field(default_factory=list)


class NegotiationPlanOutput(BaseModel):
    stance: Literal["accept", "counter", "gather_info", "walk_away"]
    rationale: str
    counter_ask: str | None = None
    leverage: list[LeveragePoint] = Field(default_factory=list)
    concessions: list[str] = Field(default_factory=list)
    script: list[str] = Field(default_factory=list, description="what to say, in order")
    questions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class NegotiationOut(BaseModel):
    opportunity_id: uuid.UUID
    frame: NegotiationFrame
    plan: NegotiationPlanOutput | None = None
    provenance_rejected: list[str] = Field(default_factory=list)
    ai_run_id: uuid.UUID | None = None
    suggestion_id: uuid.UUID | None = None
    provider: str | None = None
    model: str | None = None
