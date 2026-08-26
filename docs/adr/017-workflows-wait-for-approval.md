# 017 — Workflows with WAIT_FOR_APPROVAL: a step engine over the Suggestion state machine; first write actions only behind approval

* Status: accepted
* Date: 2026-08-26
* Deciders: maintainer

## Context

The brief (§53) asks for *workflows with WAIT FOR HUMAN APPROVAL* and the approval states
`AI suggested → Reviewed → Approved → Executed → Rejected`. The states exist since P1.4
(`ai.suggestions`, [ADR-010](010-deterministic-first-ai-as-suggestion.md) §3) but every
suggestion so far is a single proposal produced by one request. Multi-step chains — *analyse →
pick the CV → generate it → draft the message → (approval) → create the application* — were
still hand-driven click by click. [ADR-014](014-assistants-tool-calling.md) deliberately kept
assistant tools read-only; the first write actions need a home where a human gate is
structural, not a convention.

## Decision

1. **Workflows are code-declared step lists** (`modules/workflows/engine.py`): a
   `WorkflowDefinition` is an ordered tuple of `Step(name, kind, description, run)` where `kind`
   is `auto` or `approval`. Steps call other modules' **service layers** only (invariant 7) and
   return a `StepResult` (summary, output, context delta, optional `Proposal`).
2. **An approval step produces a Suggestion and pauses the run.** The runner
   (`WorkflowService`) stores the proposal via `AIService.record_suggestion(target_type="workflow",
   target_ref=<run id>)`, marks the step `waiting` and the run `waiting_approval`, and returns.
   `decide(run, approve|reject)` moves that Suggestion (`approved` / `rejected`), then either
   resumes the run (and marks the Suggestion `executed` once the run completes) or cancels it.
   A Suggestion the owner already decided on the `/suggestions` page is honoured, not re-moved.
3. **Persistence is one table, `workflow_run`**: kind, target, state
   (`running | waiting_approval | completed | failed | cancelled`), `current_step`, the step
   records and the accumulated context as JSON, the pending `suggestion_id`. The run is committed
   after every step, so a crash mid-chain leaves an honest `failed` record with the step's error;
   nothing is retried silently.
4. **Write actions are reachable only after a gate.** The two first workflows:
   * `apply` (target: opportunity) — analyze · select_cv · generate_cv · **draft_message ⏸** ·
     create_application. After approval the application is created at `preparing` (employment)
     or `proposal` (freelance) with the approved message on its timeline; **sending is the
     owner's act**, and moving to `applied` records it.
   * `follow_up` (target: application) — review · **draft_follow_up ⏸** · record_follow_up. The
     draft is AI when a provider is configured (numbers checked against the inputs it saw, else
     the vault template), the approved follow-up lands on the timeline and the next one is
     scheduled (+5 days).
   Nothing in either workflow sends mail, posts to a platform, or edits the vault
   ([ADR-005](005-no-autonomous-platform-scraping.md)).
5. **Execution is inline** (request-scoped, `TaskRunner` untouched): every step is seconds, and
   an approval gate is the natural async boundary. Long or scheduled steps can move to the ARQ
   runner later without changing the definition contract.
6. **Surface**: `GET /api/workflows/definitions`, `POST /api/workflows` (start → runs to the
   first gate), `GET /api/workflows[/{id}]`, `POST /api/workflows/{id}/decision`,
   `POST /api/workflows/{id}/cancel`; `careeros workflows …`; the `/workflows` web page and
   "Start apply workflow" / "Follow-up workflow" buttons on the opportunity and application pages.

## Alternatives considered

* **Temporal / Prefect / a generic durable-execution engine** — the right tool at SaaS scale;
  premature for a single user with five-step chains, and it would drag orchestration state out of
  the Postgres the rest of the operational model lives in ([ADR-008](008-modular-monolith.md)
  keeps the runner port open for it).
* **Let assistant tools write directly (`create_application`, `send_reply`)** — violates the
  approval invariant; a tool that *proposes* is fine, a tool that *does* is not. Workflows give
  the proposal a place to wait.
* **Model-driven chains (the assistant decides the steps)** — the tool loop of ADR-014 can call
  `start_workflow` later, but the chain itself stays declared in code so the gate cannot be
  reasoned away.
* **A gate per side effect inside each module's service** — scatters approval logic; the
  Suggestion state machine already centralises it.

## Consequences

* Positive: one place to add a chain; the ledger shows every proposal and decision; the first
  write actions of the platform exist and are provably gated (tests assert that rejection creates
  nothing). The follow-up cadence stops depending on the owner remembering.
* Negative: inline execution ties a run to a request (fine now, a limit for long chains); the
  `steps` JSON is opaque to SQL (fine — nothing queries into it); every new workflow needs code,
  not configuration.
* Follow-ups: `start_workflow` as an assistant tool (ADR-014 §follow-ups); ARQ-backed steps and
  scheduled starts (daily follow-up sweep from the brief); a `send` gate once Gmail (P1.3)
  exists — still approval-first; Telegram bot buttons for pending gates.
