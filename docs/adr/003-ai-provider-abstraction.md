# 003 — Single `AIProvider` port; structured output validated locally

* Status: accepted
* Date: 2026-08-20

## Context

The product must work with OpenAI, Anthropic, Gemini, xAI and local OpenAI-compatible endpoints,
support three interaction modes (built-in, external chat handoff, dev-agent packet), keep prompts
version-controlled, and never trust AI output without schema validation. Provider SDKs differ in
structured-output support and failure modes.

## Decision

* One async port in `careeros.modules.ai.provider`:
  ```python
  class AIProvider(Protocol):
      name: str

      async def generate(self, req: GenerateRequest) -> GenerateResponse: ...
      async def stream(self, req: GenerateRequest) -> AsyncIterator[StreamChunk]: ...
      async def structured(
          self, req: GenerateRequest, schema: type[BaseModel]
      ) -> StructuredResponse: ...
      async def embeddings(
          self, texts: list[str], model: str | None = None
      ) -> EmbeddingsResponse: ...
  ```
* Adapters in P0: `AnthropicProvider` (official SDK) and `OpenAICompatibleProvider` (base URL +
  key; covers OpenAI, xAI/Grok, Gemini's OpenAI-compatible endpoint, OpenRouter, Ollama, LM Studio).
  Native Gemini adapter is P1 if the compatible endpoint proves insufficient.
* `structured()` asks for JSON (provider-native JSON/tool mode when available), then **validates
  locally with Pydantic**; on failure it retries up to N times with the validation error appended;
  after N it raises `AIOutputInvalid`. Callers never parse prose.
* Prompts are vault files (`prompts/<area>/<id>.yaml`) with metadata and a Jinja2 template; the
  `PromptRegistry` loads them and stamps `prompt_id@version` into every `AIRun`.
* Every call — including Mode B bundle generation and Mode C packets — writes an `ai_run` row:
  provider, model, prompt version, inputs hash, output, validity, tokens, cost, latency, feedback.
* Provider selection is per call (`provider=`), with a configured default and an ordered fallback list.
* No provider-specific code outside `modules/ai/providers/`.

## Alternatives considered

* **LangChain/LlamaIndex/LiteLLM as the abstraction** — heavy dependencies, their own churn, and we need a thin, typed surface plus our own run ledger. LiteLLM may be used *inside* the OpenAI-compatible adapter later if it pays for itself.
* **Tool-use-only structured output** — not uniformly supported by local endpoints; JSON mode + local validation is the lowest common denominator and is provider-neutral.

## Consequences

* + Swap/mix providers per action; reproducible runs; cost visibility; external-chat mode costs nothing.
* − Local validation + retry adds latency on bad outputs; acceptable for non-interactive background runs.
* Follow-ups: cost tables per model in config; embeddings adapter choice for P1 semantic search.
