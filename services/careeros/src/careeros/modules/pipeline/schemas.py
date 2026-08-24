from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from careeros.modules.pipeline.enums import (
    EventKind,
    InterviewKind,
    InterviewOutcome,
    PipelineKind,
    Stage,
)


class ApplicationCreate(BaseModel):
    opportunity_id: uuid.UUID
    kind: PipelineKind | None = Field(
        default=None, description="inferred from the opportunity when omitted"
    )
    stage: Stage | None = None
    cv_artifact_id: uuid.UUID | None = None
    notes: str | None = None


class ApplicationUpdate(BaseModel):
    stage: Stage | None = None
    cv_artifact_id: uuid.UUID | None = None
    recruiter_contact_id: uuid.UUID | None = None
    next_follow_up_at: datetime | None = None
    clear_follow_up: bool = False
    notes: str | None = None


class EventIn(BaseModel):
    kind: EventKind = EventKind.note
    title: str = Field(min_length=1, max_length=300)
    body: str | None = None
    at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class EventOut(BaseModel):
    id: uuid.UUID
    kind: EventKind
    at: datetime
    title: str
    body: str | None
    meta: dict[str, Any]


class InterviewIn(BaseModel):
    kind: InterviewKind = InterviewKind.other
    scheduled_at: datetime | None = None
    interviewer_contact_id: uuid.UUID | None = None
    notes: str | None = None


class InterviewUpdate(BaseModel):
    scheduled_at: datetime | None = None
    outcome: InterviewOutcome | None = None
    notes: str | None = None


class InterviewOut(BaseModel):
    id: uuid.UUID
    kind: InterviewKind
    scheduled_at: datetime | None
    interviewer_contact_id: uuid.UUID | None
    outcome: InterviewOutcome
    notes: str | None


class ApplicationOut(BaseModel):
    id: uuid.UUID
    opportunity_id: uuid.UUID
    opportunity_title: str
    company_name: str | None
    kind: PipelineKind
    stage: Stage
    cv_artifact_id: uuid.UUID | None
    recruiter_contact_id: uuid.UUID | None
    applied_at: datetime | None
    next_follow_up_at: datetime | None
    closed_at: datetime | None
    notes: str | None
    score_overall: int | None
    created_at: datetime
    updated_at: datetime


class ApplicationDetail(ApplicationOut):
    events: list[EventOut] = Field(default_factory=list)
    interviews: list[InterviewOut] = Field(default_factory=list)


class BoardColumn(BaseModel):
    stage: Stage
    applications: list[ApplicationOut]


class BoardOut(BaseModel):
    kind: PipelineKind
    columns: list[BoardColumn]
    stages: list[Stage]


class FollowUpOut(BaseModel):
    application: ApplicationOut
    due_at: datetime
    overdue: bool
