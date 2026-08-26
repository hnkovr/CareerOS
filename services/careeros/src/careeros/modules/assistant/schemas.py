"""Assistant request/response contract (ADR-014)."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from careeros.modules.ai.schemas import ToolStep


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    opportunity_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None
    provider: str | None = None
    max_steps: int = Field(default=8, ge=1, le=12, description="model turns, tool calls included")


class AssistantOutput(BaseModel):
    """What the model must return when it stops calling tools."""

    answer: str = Field(min_length=1)
    derived_from: list[str] = Field(
        default_factory=list, description="vault fact ids / entity ids the answer rests on"
    )
    suggested_next_action: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"


class ToolInfo(BaseModel):
    name: str
    description: str
    read_only: bool = True


class AskResponse(BaseModel):
    answer: str
    derived_from: list[str] = Field(default_factory=list)
    suggested_next_action: str | None = None
    confidence: str
    guarded: bool = Field(description="True when the provenance guard withheld the model's answer")
    provenance_problems: list[str] = Field(default_factory=list)
    tools_used: list[ToolStep] = Field(default_factory=list)
    turns: int
    ai_run_id: uuid.UUID | None = None
    provider: str | None = None
    model: str | None = None
