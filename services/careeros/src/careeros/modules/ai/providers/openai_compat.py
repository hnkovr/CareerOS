"""OpenAI-compatible adapter: OpenAI, xAI/Grok, Gemini (OpenAI endpoint), OpenRouter, Ollama,
LM Studio.

Structured output tries the native ``json_schema`` response format first and falls back to
plain-JSON prompting for servers that reject it (common on local endpoints).
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from careeros.modules.ai.provider import AIUnavailable, extract_json_object, schema_instructions
from careeros.modules.ai.schemas import (
    EmbeddingsResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderInfo,
    StreamChunk,
    Usage,
)


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        api_key: str | None,
        base_url: str,
        default_model: str,
        *,
        embeddings_model: str = "text-embedding-3-small",
        native_json_schema: bool = True,
        requires_key: bool = True,
    ) -> None:
        self.name = name
        self._api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        self.embeddings_model = embeddings_model
        self.native_json_schema = native_json_schema
        self.requires_key = requires_key
        self._client: Any = None

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name=self.name,
            kind="openai_compatible",
            default_model=self.default_model,
            configured=bool(self._api_key) or not self.requires_key,
            supports_embeddings=True,
            base_url=self.base_url,
        )

    def _get_client(self) -> Any:
        if self.requires_key and not self._api_key:
            raise AIUnavailable(f"{self.name}: API key is not set")
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._api_key or "not-needed", base_url=self.base_url
            )
        return self._client

    @staticmethod
    def _messages(req: GenerateRequest, suffix: str = "") -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = []
        if req.system:
            msgs.append({"role": "system", "content": req.system})
        msgs.append({"role": "user", "content": req.prompt + suffix})
        return msgs

    @staticmethod
    def _usage(completion: Any) -> Usage:
        usage = getattr(completion, "usage", None)
        if usage is None:
            return Usage()
        return Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    async def generate(self, req: GenerateRequest) -> GenerateResponse:
        client = self._get_client()
        model = req.model or self.default_model
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": self._messages(req),
            "temperature": req.temperature,
            "max_completion_tokens": req.max_tokens,
        }
        if req.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        completion = await client.chat.completions.create(**kwargs)
        choice = completion.choices[0]
        return GenerateResponse(
            text=choice.message.content or "",
            provider=self.name,
            model=completion.model or model,
            usage=self._usage(completion),
            latency_ms=int((time.perf_counter() - started) * 1000),
            stop_reason=choice.finish_reason,
        )

    async def stream(self, req: GenerateRequest) -> AsyncIterator[StreamChunk]:
        client = self._get_client()
        model = req.model or self.default_model
        stream = await client.chat.completions.create(
            model=model,
            messages=self._messages(req),
            temperature=req.temperature,
            max_completion_tokens=req.max_tokens,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamChunk(text=chunk.choices[0].delta.content)
        yield StreamChunk(text="", done=True)

    async def structured(
        self, req: GenerateRequest, schema: type[BaseModel]
    ) -> tuple[dict[str, Any], GenerateResponse]:
        client = self._get_client()
        model = req.model or self.default_model
        started = time.perf_counter()
        if self.native_json_schema:
            try:
                completion = await client.chat.completions.parse(
                    model=model,
                    messages=self._messages(req),
                    temperature=req.temperature,
                    max_completion_tokens=req.max_tokens,
                    response_format=schema,
                )
                choice = completion.choices[0]
                parsed = choice.message.parsed
                text = choice.message.content or ""
                obj = (
                    parsed.model_dump(mode="json")
                    if isinstance(parsed, BaseModel)
                    else extract_json_object(text)
                )
                return obj, GenerateResponse(
                    text=text,
                    provider=self.name,
                    model=completion.model or model,
                    usage=self._usage(completion),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    stop_reason=choice.finish_reason,
                )
            except Exception as exc:
                from openai import APIStatusError

                if not isinstance(exc, APIStatusError):
                    raise
        fallback = req.model_copy(
            update={"prompt": req.prompt + schema_instructions(schema), "json_mode": True}
        )
        resp = await self.generate(fallback)
        return extract_json_object(resp.text), resp

    async def embeddings(self, texts: list[str], model: str | None = None) -> EmbeddingsResponse:
        client = self._get_client()
        model = model or self.embeddings_model
        result = await client.embeddings.create(model=model, input=texts)
        return EmbeddingsResponse(
            vectors=[d.embedding for d in result.data],
            provider=self.name,
            model=result.model or model,
            usage=Usage(input_tokens=getattr(result.usage, "prompt_tokens", 0) or 0),
        )
