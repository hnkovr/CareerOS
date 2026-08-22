"""Deterministic provider for tests and offline demos. Never talks to the network."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic import BaseModel

from careeros.modules.ai.schemas import (
    EmbeddingsResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderInfo,
    StreamChunk,
    Usage,
)

Responder = Callable[[GenerateRequest, type[BaseModel] | None], Any]


def _default_responder(req: GenerateRequest, schema: type[BaseModel] | None) -> Any:
    if schema is None:
        return f"[fake] {req.prompt[:80]}"
    # Build a minimal object from the schema's example/defaults — callers in tests override this.
    return {}


class FakeProvider:
    name = "fake"

    def __init__(self, responder: Responder | None = None, default_model: str = "fake-1") -> None:
        self.responder = responder or _default_responder
        self.default_model = default_model
        self.calls: list[tuple[GenerateRequest, type[BaseModel] | None]] = []

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name,
            kind="fake",
            default_model=self.default_model,
            configured=True,
            supports_embeddings=True,
        )

    async def generate(self, req: GenerateRequest) -> GenerateResponse:
        self.calls.append((req, None))
        out = self.responder(req, None)
        text = out if isinstance(out, str) else json.dumps(out)
        return GenerateResponse(
            text=text,
            provider=self.name,
            model=req.model or self.default_model,
            usage=Usage(input_tokens=len(req.prompt) // 4, output_tokens=len(text) // 4),
            latency_ms=1,
            stop_reason="end_turn",
        )

    async def stream(self, req: GenerateRequest) -> AsyncIterator[StreamChunk]:
        resp = await self.generate(req)
        for word in resp.text.split(" "):
            yield StreamChunk(text=word + " ")
        yield StreamChunk(text="", done=True)

    async def structured(
        self, req: GenerateRequest, schema: type[BaseModel]
    ) -> tuple[dict[str, Any], GenerateResponse]:
        self.calls.append((req, schema))
        out = self.responder(req, schema)
        if isinstance(out, BaseModel):
            obj = out.model_dump(mode="json")
        elif isinstance(out, str):
            obj = json.loads(out)
        else:
            obj = dict(out)
        text = json.dumps(obj)
        return obj, GenerateResponse(
            text=text,
            provider=self.name,
            model=req.model or self.default_model,
            usage=Usage(input_tokens=len(req.prompt) // 4, output_tokens=len(text) // 4),
            latency_ms=1,
            stop_reason="end_turn",
        )

    async def embeddings(self, texts: list[str], model: str | None = None) -> EmbeddingsResponse:
        vectors = [[float(len(t) % 7), float(sum(map(ord, t)) % 11), 1.0] for t in texts]
        return EmbeddingsResponse(vectors=vectors, provider=self.name, model=model or "fake-embed")
