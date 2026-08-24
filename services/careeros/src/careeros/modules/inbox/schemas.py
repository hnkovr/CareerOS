from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from careeros.modules.inbox.enums import Direction, MailboxProvider, MessageClass, Urgency


class EmailIn(BaseModel):
    """A manually captured email (paste / forward). Gmail sync produces the same shape (P1.3)."""

    from_email: str | None = None
    from_name: str | None = None
    to: list[str] = Field(default_factory=list)
    subject: str | None = None
    body_text: str = Field(min_length=1)
    received_at: datetime | None = None
    direction: Direction = Direction.inbound
    provider: MailboxProvider = MailboxProvider.manual
    provider_message_id: str | None = None
    headers: dict[str, Any] = Field(default_factory=dict)
    raw: str | None = Field(
        default=None, description="full raw paste incl. headers; parsed when fields are empty"
    )

    @field_validator("to", mode="before")
    @classmethod
    def _none_to_list(cls, v: object) -> object:
        return [] if v is None else v


class ClassificationOut(BaseModel):
    classification: MessageClass
    urgency: Urgency
    confidence: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)
    deadline_hint: str | None = None


class InboxAIClassification(BaseModel):
    """AI output schema for the inbox_classify prompt."""

    classification: MessageClass
    urgency: Urgency
    reasoning: str | None = None
    deadline_hint: str | None = None


class ReplyDraftOutput(BaseModel):
    """AI output schema for the inbox_reply prompt."""

    subject: str | None = None
    body: str = Field(min_length=20)
    tone: str | None = None
    notes: str | None = None


class MessageLinks(BaseModel):
    opportunity_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    provider: MailboxProvider
    direction: Direction
    from_email: str | None
    from_name: str | None
    to: list[str]
    subject: str | None
    body_text: str
    received_at: datetime
    classification: MessageClass
    urgency: Urgency
    classified_by: str
    classification_confidence: float
    classification_signals: list[str]
    deadline_hint: str | None
    read_at: datetime | None
    links: MessageLinks
    extracted_opportunity: bool
    created_at: datetime


class ThreadOut(BaseModel):
    id: uuid.UUID
    subject_norm: str
    counterpart_email: str | None
    last_message_at: datetime
    message_count: int
    messages: list[MessageOut] = Field(default_factory=list)


class MessageUpdate(BaseModel):
    classification: MessageClass | None = None
    urgency: Urgency | None = None
    links: MessageLinks | None = None
    mark_read: bool = False


class SuggestReplyRequest(BaseModel):
    provider: str | None = None
    intent: Literal["accept", "decline", "ask_questions", "negotiate", "follow_up", "custom"] = (
        "follow_up"
    )
    instructions: str | None = None


class ReplySuggestionOut(BaseModel):
    suggestion_id: uuid.UUID | None
    subject: str | None
    body: str
    notes: str | None
    ai_run_id: uuid.UUID | None


class InboxStats(BaseModel):
    total: int
    unread: int
    needs_attention: int
    by_class: dict[str, int]
