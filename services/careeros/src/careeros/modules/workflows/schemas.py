"""Workflow API contract (ADR-017)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from careeros.modules.workflows.enums import RunState, StepStatus, WorkflowKind


class StartRequest(BaseModel):
    kind: WorkflowKind
    target_id: uuid.UUID = Field(description="opportunity id (apply) or application id (follow_up)")
    options: dict[str, Any] = Field(
        default_factory=dict, description="e.g. {'use_ai': false, 'formats': ['md', 'json']}"
    )


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


class StepInfo(BaseModel):
    name: str
    kind: Literal["auto", "approval"]
    description: str


class WorkflowDefinitionOut(BaseModel):
    kind: WorkflowKind
    title: str
    description: str
    target_type: str
    steps: list[StepInfo]


class StepOut(BaseModel):
    name: str
    kind: Literal["auto", "approval"]
    description: str
    status: StepStatus
    summary: str | None = None
    output: dict[str, Any] | None = None
    suggestion_id: uuid.UUID | None = None
    decided: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowRunOut(BaseModel):
    id: uuid.UUID
    kind: WorkflowKind
    title: str
    target_type: str
    target_ref: str
    state: RunState
    current_step: int
    steps: list[StepOut]
    context: dict[str, Any]
    suggestion_id: uuid.UUID | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class FollowUpDraft(BaseModel):
    """AI output for the follow-up step — numbers are checked against the inputs it saw."""

    subject: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
