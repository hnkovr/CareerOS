"""The ``AIProvider`` port. Adapters live in ``providers/``; nothing else imports SDKs."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from careeros.modules.ai.schemas import (
    EmbeddingsResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderInfo,
    StreamChunk,
)


class AIError(Exception):
    """Base class for gateway errors."""


class AIUnavailable(AIError):
    """Provider not configured / unreachable / feature unsupported."""


class AIOutputInvalid(AIError):
    """Model output failed schema validation after all retries."""

    def __init__(
        self, message: str, *, last_text: str = "", errors: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.last_text = last_text
        self.errors = errors or []


@runtime_checkable
class AIProvider(Protocol):
    name: str

    def info(self) -> ProviderInfo: ...

    async def generate(self, req: GenerateRequest) -> GenerateResponse: ...

    def stream(self, req: GenerateRequest) -> AsyncIterator[StreamChunk]: ...

    async def structured(
        self, req: GenerateRequest, schema: type[BaseModel]
    ) -> tuple[dict[str, Any], GenerateResponse]:
        """Return the raw JSON object the model produced plus the response envelope.

        Validation against ``schema`` happens in the gateway (``structured.py``), never here —
        adapters may use provider-native JSON modes but must not be trusted.
        """
        ...

    async def embeddings(
        self, texts: list[str], model: str | None = None
    ) -> EmbeddingsResponse: ...


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Pull a JSON object out of model text (tolerates code fences and leading prose)."""
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text)] + [text]
    for cand in candidates:
        cand = cand.strip()
        start = cand.find("{")
        end = cand.rfind("}")
        if start == -1 or end == -1 or end <= start:
            continue
        try:
            obj = json.loads(cand[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise ValueError("no JSON object found in model output")


def schema_instructions(schema: type[BaseModel]) -> str:
    """Prompt suffix used when a provider has no native JSON-schema mode."""
    return (
        "\n\nRespond with a single JSON object only — no prose, no code fences — that validates "
        "against this JSON Schema:\n" + json.dumps(schema.model_json_schema(), indent=None)
    )
