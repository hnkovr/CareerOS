# 014 — Assistants via tool-calling: tool-use in the `AIProvider` port, typed read-only tools, gateway-owned loop

* Status: accepted
* Date: 2026-08-26
* Deciders: maintainer

## Context

The brief (§55, §36) asks for *assistants everywhere* built from **tools/functions instead of a
mega-prompt** — `get_career_facts`, `score_opportunity`, `search_achievements`, `draft_reply`, … —
with deterministic code first and structured output at the end. Until now every AI feature was
one prompt over a deterministic frame ([ADR-010](010-deterministic-first-ai-as-suggestion.md)):
right for CV bullets, opportunity analysis, interview prep and negotiation, but it cannot answer
an open question ("which of my achievements fit this posting and what is missing?") without a
bespoke prompt per question. The invariants still hold: AI may select / summarise / combine /
project facts, never invent them; every output carries provenance; no external write happens
without an approved `Action`; no provider-specific code outside `modules/ai/providers/`
([ADR-003](003-ai-provider-abstraction.md)).

## Decision

1. **Tool-use is part of the `AIProvider` port.** `chat_with_tools(ToolChatRequest) -> ToolTurn`
   takes a provider-neutral conversation (`ChatMessage` with roles user / assistant / tool) and a
   list of `ToolSpec` (name, description, JSON Schema) and returns one model turn: text and/or
   `ToolCall`s plus the usual response envelope. Adapters only translate — Anthropic content
   blocks (`tool_use` / `tool_result`, consecutive results merged into one user turn) and OpenAI
   `tool_calls` / `role: tool` messages. `FakeProvider` takes a scripted `tool_responder`.
2. **The gateway owns the loop.** `AIService.with_tools(prompt_id, inputs, tools, execute,
   schema)` renders the prompt, runs turns until the model stops calling tools, executes each
   call through the supplied `execute` callable (tool errors are returned to the model as results,
   never raised), validates the final JSON answer against the Pydantic `schema` with the same
   retry budget as `structured()`, and writes **one** `ai_run` ledger row whose output carries the
   full `tool_trace` (tool, arguments, ok, preview, latency). `max_steps` bounds the loop; a
   model that will not finish fails with `AIOutputInvalid` and no other provider is tried.
3. **Tools are typed, read-only wrappers over service layers**, registered in
   `modules/assistant/tools.py`: `get_career_facts`, `search_facts`, `get_opportunity`,
   `list_opportunities`, `get_applications`, `get_profile_health`. Each has a Pydantic argument
   model (the JSON Schema comes from it), calls a module's `service.py` — never its ORM
   (invariant 7) — and records its result text and every surfaced entity id in the
   `ToolContext`. **No tool writes anything.** Proposals stay behind Suggestions / Actions and
   human approval ([ADR-010](010-deterministic-first-ai-as-suggestion.md) §3).
4. **The answer is guarded like a CV bullet.** `AssistantOutput{answer, derived_from[],
   suggested_next_action, confidence}`; `guard_answer` rejects an answer that cites an id neither
   in the vault nor surfaced by a tool, or that states a number absent from both the cited facts
   and the tool results the model actually saw. A rejected answer is **withheld** (the response
   says why, the raw answer stays in the ledger), not "repaired".
5. **Surface**: `POST /api/assistant/ask`, `GET /api/assistant/tools`, `careeros assistant ask`,
   the `/assistant` web page (with an opportunity as optional context). The prompt is
   `career/prompts/assistant/assistant_chat.yaml`, overridable from the vault like any other.

## Alternatives considered

* **One mega-prompt with the whole vault inlined** — simplest, but it scales with vault size,
  leaks facts the question does not need, and cannot reach operational state (scores, pipeline,
  profile health) without hand-written context per question.
* **Provider-native agent frameworks / SDK "runners"** — would put orchestration inside the
  adapters, violating ADR-003 (all provider-specific code in `providers/`) and making the ledger,
  retries and the guard provider-dependent.
* **Write-capable tools from day one** (`create_application`, `draft_reply`) — deferred: they
  must land as Suggestion-producing tools so the approval state machine stays the only path to a
  side effect. That is the §53 workflow layer, built on top of this loop.
* **Retrieval-only (FTS/pgvector) instead of tools** — retrieval is one tool among several; it
  does not give the model scores, stages or health, and provenance is weaker.

## Consequences

* Positive: every future "button" (§36) can be a question plus a tool subset; the ledger shows
  exactly what the model looked at; provenance stays enforceable; providers stay swappable.
* Negative: tool loops cost more tokens than one-shot prompts (bounded by `max_steps`); prompts
  must be written for tool use; local OpenAI-compatible servers without tool support fall through
  to `AIUnavailable` — the deterministic frames of ADR-010 remain the offline path.
* Follow-ups: Suggestion-producing write tools + `WAIT_FOR_APPROVAL` workflows (§53); streaming
  turns for the web/bot surfaces; per-tool token budgets in the ledger; a `search_facts` backed by
  the unified search index when the vault outgrows keyword matching.
