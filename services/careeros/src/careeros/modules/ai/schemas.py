"""Provider-neutral request/response types."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

AIMode = Literal["builtin", "external_bundle", "dev_packet"]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class GenerateRequest(BaseModel):
    system: str | None = None
    prompt: str
    model: str | None = Field(default=None, description="override the provider default")
    temperature: float = 0.2
    max_tokens: int = 4096
    json_mode: bool = Field(default=False, description="ask the provider for JSON text")
    metadata: dict[str, str] = Field(default_factory=dict)


class GenerateResponse(BaseModel):
    text: str
    provider: str
    model: str
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    stop_reason: str | None = None


class StreamChunk(BaseModel):
    text: str
    done: bool = False


class EmbeddingsResponse(BaseModel):
    vectors: list[list[float]]
    provider: str
    model: str
    usage: Usage = Field(default_factory=Usage)


class ProviderInfo(BaseModel):
    name: str
    kind: Literal["anthropic", "openai_compatible", "fake"]
    default_model: str
    configured: bool
    supports_embeddings: bool
    base_url: str | None = None


class AIRunOut(BaseModel):
    id: uuid.UUID
    prompt_id: str
    prompt_version: int
    provider: str
    model: str
    mode: AIMode
    status: str
    valid: bool
    retries: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    latency_ms: int
    entity_type: str | None
    entity_id: str | None
    feedback: str | None
    created_at: datetime
    output: dict[str, Any] | None = None
    error: str | None = None


class FeedbackIn(BaseModel):
    feedback: Literal["up", "down"] | None
    note: str | None = None


class PromptInfo(BaseModel):
    id: str
    version: int
    area: str
    purpose: str
    inputs: list[str]
    output_schema: str | None
    provider_preferences: list[str]
    source: Literal["library", "vault"]


class BundleRequest(BaseModel):
    prompt_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    target: Literal["chatgpt", "claude", "gemini", "grok", "perplexity", "generic"] = "generic"
    entity_type: str | None = None
    entity_id: str | None = None


class BundleOut(BaseModel):
    target: str
    title: str
    text: str
    deep_link: str | None = Field(
        default=None, description="best-effort prefill URL; copy/paste always works"
    )
    prompt_id: str
    prompt_version: int
    run_id: uuid.UUID | None = None


class DevPacketRequest(BaseModel):
    agent: Literal[
        "claude-code", "codex", "gemini-cli", "cursor", "windsurf", "antigravity", "generic"
    ] = "claude-code"
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    context: str
    goal: str
    relevant_files: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    suggested_commands: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    entity_type: str | None = None
    entity_id: str | None = None


class DevPacketOut(BaseModel):
    agent: str
    path: str
    markdown: str
    run_id: uuid.UUID | None = None
