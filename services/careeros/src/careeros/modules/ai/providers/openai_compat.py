"""OpenAI-compatible adapter: OpenAI, xAI/Grok, Gemini (OpenAI endpoint), OpenRouter, Ollama,
LM Studio.

Structured output tries the native ``json_schema`` response format first and falls back to
plain-JSON prompting for servers that reject it (common on local endpoints).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel

from careeros.modules.ai.provider import AIUnavailable, extract_json_object, schema_instructions
from careeros.modules.ai.schemas import (
    ChatMessage,
    EmbeddingsResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderInfo,
    StreamChunk,
    ToolCall,
    ToolChatRequest,
    ToolSpec,
    ToolTurn,
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

    # ------------------------------------------------------------------ tool use (ADR-014)
    @staticmethod
    def tool_messages(system: str | None, messages: list[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "user":
                out.append({"role": "user", "content": m.content or ""})
            elif m.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ]
                out.append(entry)
            else:
                out.append(
                    {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""}
                )
        return out

    @staticmethod
    def tool_specs(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    @staticmethod
    def parse_tool_turn(
        completion: Any, *, provider: str, model: str, latency_ms: int, usage: Usage
    ) -> ToolTurn:
        choice = completion.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        for tc in getattr(message, "tool_calls", None) or []:
            fn = tc.function
            try:
                args = json.loads(fn.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.arguments}
            if not isinstance(args, dict):
                args = {"_raw": args}
            calls.append(ToolCall(id=tc.id, name=fn.name, arguments=args))
        text = message.content or ""
        return ToolTurn(
            text=text,
            tool_calls=calls,
            response=GenerateResponse(
                text=text,
                provider=provider,
                model=completion.model or model,
                usage=usage,
                latency_ms=latency_ms,
                stop_reason=choice.finish_reason,
            ),
        )

    async def chat_with_tools(self, req: ToolChatRequest) -> ToolTurn:
        client = self._get_client()
        model = req.model or self.default_model
        started = time.perf_counter()
        completion = await client.chat.completions.create(
            model=model,
            messages=self.tool_messages(req.system, req.messages),
            tools=self.tool_specs(req.tools),
            tool_choice="auto",
            temperature=req.temperature,
            max_completion_tokens=req.max_tokens,
        )
        return self.parse_tool_turn(
            completion,
            provider=self.name,
            model=model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage=self._usage(completion),
        )
