"""Anthropic adapter (official SDK). Structured output via native JSON-schema ``output_format``."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from careeros.modules.ai.provider import AIUnavailable, extract_json_object
from careeros.modules.ai.schemas import (
    EmbeddingsResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderInfo,
    StreamChunk,
    Usage,
)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, default_model: str) -> None:
        self._api_key = api_key
        self.default_model = default_model
        self._client: Any = None

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name,
            kind="anthropic",
            default_model=self.default_model,
            configured=bool(self._api_key),
            supports_embeddings=False,
        )

    def _get_client(self) -> Any:
        if not self._api_key:
            raise AIUnavailable("anthropic: CAREEROS_ANTHROPIC_API_KEY is not set")
        if self._client is None:
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _kwargs(req: GenerateRequest, model: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if req.system:
            kwargs["system"] = req.system
        return kwargs

    async def generate(self, req: GenerateRequest) -> GenerateResponse:
        client = self._get_client()
        model = req.model or self.default_model
        started = time.perf_counter()
        msg = await client.messages.create(**self._kwargs(req, model))
        text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
        return GenerateResponse(
            text=text,
            provider=self.name,
            model=msg.model,
            usage=Usage(input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            stop_reason=msg.stop_reason,
        )

    async def stream(self, req: GenerateRequest) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        model = req.model or self.default_model
        async with client.messages.stream(**self._kwargs(req, model)) as stream:
            async for text in stream.text_stream:
                yield StreamChunk(text=text)
        yield StreamChunk(text="", done=True)

    async def structured(
        self, req: GenerateRequest, schema: type[BaseModel]
    ) -> tuple[dict[str, Any], GenerateResponse]:
        client = self._get_client()
        model = req.model or self.default_model
        started = time.perf_counter()
        msg = await client.messages.parse(**self._kwargs(req, model), output_format=schema)
        text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
        parsed = msg.parsed_output
        obj = (
            parsed.model_dump(mode="json")
            if isinstance(parsed, BaseModel)
            else extract_json_object(text)
        )
        resp = GenerateResponse(
            text=text,
            provider=self.name,
            model=msg.model,
            usage=Usage(input_tokens=msg.usage.input_tokens, output_tokens=msg.usage.output_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            stop_reason=msg.stop_reason,
        )
        return obj, resp

    async def embeddings(self, texts: list[str], model: str | None = None) -> EmbeddingsResponse:
        raise AIUnavailable(
            "anthropic: embeddings are not offered; configure an OpenAI-compatible provider"
        )
