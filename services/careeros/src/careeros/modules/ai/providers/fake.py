"""Deterministic provider for tests and offline demos. Never talks to the network."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from pydantic import BaseModel

from careeros.modules.ai.provider import AIUnavailable
from careeros.modules.ai.schemas import (
    EmbeddingsResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderInfo,
    StreamChunk,
    ToolCall,
    ToolChatRequest,
    ToolTurn,
    Usage,
)

Responder = Callable[[GenerateRequest, type[BaseModel] | None], Any]
# Returns a ToolTurn, or a dict {"text": str, "tool_calls": [{"name", "arguments", "id"?}]}.
ToolResponder = Callable[[ToolChatRequest], Any]


def _default_responder(req: GenerateRequest, schema: type[BaseModel] | None) -> Any:
    if schema is None:
        return f"[fake] {req.prompt[:80]}"
    # Build a minimal object from the schema's example/defaults — callers in tests override this.
    return {}


class FakeProvider:
    name = "fake"

    def __init__(
        self,
        responder: Responder | None = None,
        default_model: str = "fake-1",
        *,
        tool_responder: ToolResponder | None = None,
    ) -> None:
        self.responder = responder or _default_responder
        self.default_model = default_model
        self.calls: list[tuple[GenerateRequest, type[BaseModel] | None]] = []
        self.tool_responder = tool_responder
        self.tool_requests: list[ToolChatRequest] = []

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

    async def chat_with_tools(self, req: ToolChatRequest) -> ToolTurn:
        self.tool_requests.append(req)
        if self.tool_responder is None:
            raise AIUnavailable("fake: no tool responder configured")
        out = self.tool_responder(req)
        if isinstance(out, ToolTurn):
            return out
        text = ""
        calls: list[ToolCall] = []
        if isinstance(out, dict):
            text = str(out.get("text", "") or "")
            for i, c in enumerate(out.get("tool_calls", []) or []):
                calls.append(
                    ToolCall(
                        id=str(c.get("id") or f"call_{len(self.tool_requests)}_{i}"),
                        name=c["name"],
                        arguments=dict(c.get("arguments", {}) or {}),
                    )
                )
        else:
            text = str(out)
        prompt_chars = sum(len(m.content or "") for m in req.messages)
        return ToolTurn(
            text=text,
            tool_calls=calls,
            response=GenerateResponse(
                text=text,
                provider=self.name,
                model=req.model or self.default_model,
                usage=Usage(input_tokens=prompt_chars // 4, output_tokens=len(text) // 4),
                latency_ms=1,
                stop_reason="tool_use" if calls else "end_turn",
            ),
        )
