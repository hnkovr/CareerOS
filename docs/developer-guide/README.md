# Developer guide

## Setup

```bash
make env && docker compose up -d postgres redis && uv sync --all-groups && just migrate && just seed
uv run pre-commit install
```

Run: `make dev` (API with reload on :8000 + worker). Tests: `make test`. Quality gate: `make lint`.
`make check` runs the gate alone (lint + tests, mutates nothing); `make all` runs the whole
local pipeline in dependency order — env → infra → openapi → fmt → lint → test → migrate →
seed → validate-career → generate-cv → platform-sync → bot-check. `make help` lists every target.

**Blank env vars mean *unset*.** Every optional value renders blank out of
`config/.env.*.template` until the owner fills it in (a non-empty literal there would be a leaked
secret), so `Settings` maps `""` → `None` for every field whose annotation accepts `None`. Without
that, an `int | None` setting makes `Settings()` raise and takes down the API, worker, CLI and
pipeline at once, and a `SecretStr | None` becomes `SecretStr("")` — a credential that tests
truthy and authenticates nothing. Adding an optional setting needs nothing extra;
`test_blank_env_var_reads_as_unset` covers it.

**SessionStart guards** (`scripts/hooks/`, wired in `.claude/settings.json`; both always exit 0):
`config-guard.sh` builds `Settings()` from the current `.env` and names the offending field;
`bot-guard.sh` asks Telegram who owns the webhook.

**One gate, two callers.** `scripts/gate.sh lint|test` is the single definition of the quality
gate; `just lint` / `just test` and the CI `python` job both call it, so a check cannot end up
running in one place and not the other ([#23](https://github.com/hnkovr/CareerOS/issues/23)).
`tests/test_gate_config.py` asserts both callers still delegate to it.

**Test isolation.** Tables are created once per session, so an autouse fixture truncates every
table except the seeded `user` after each `@pytest.mark.db` test. Without it, rows accumulate
across a run and assertions about *new* data start testing whatever an earlier test left behind —
which is why the opportunities dedup assertion passed alone and failed in a full run
([#24](https://github.com/hnkovr/CareerOS/issues/24)). Truncate, not a wrapping transaction: API
tests drive the app, which opens its own sessions.

**Generated contracts.** Two generators feed two committed trees: `career/schemas` from
`careeros vault export-schemas` (python) and `packages/schemas` from `openapi-typescript`
(node, off the exported OpenAPI). `scripts/contracts-check.sh` — `make contracts` locally, the
`contracts` CI job — regenerates both and fails if the tree moved. It needs *both* toolchains and
refuses to run with only one, because checking the half that happens to be installed would report
"fresh" while the other half drifts.

**`make all` offline.** `bot-check` talks to Telegram. Exit 4 means unreachable, which says
nothing about the token, so `make all` warns and carries on; a rejected token (2), the wrong bot
(2) or a foreign webhook owner (3) still fail the pipeline.

**Vault default:** the CLI and API read `CAREEROS_VAULT_PATH` when that directory carries a
`vault.yaml`; otherwise they fall back to the bundled demo vault (`career/examples/demo`) and
open it **read-only** — `apply_change()` raises `VaultReadOnly` (HTTP 403) so demo facts can
never be committed as the owner's. `just vault-init <path>` creates a real one.

**Concurrent agent sessions:** point each session's gates at its own database to avoid
cross-run interference — `CAREEROS_TEST_DATABASE_URL=postgresql+asyncpg://careeros:careeros@localhost:5432/careeros_test_<lane> uv run pytest`
(create it once: `docker compose exec postgres psql -U careeros -c "CREATE DATABASE careeros_test_<lane>;"` plus the `vector`/`pg_trgm` extensions).

Note: `pytest` already carries `-q` via `addopts`; passing `-q` again yields `-qq`, which drops the
`N passed` summary line — don't grep for it. Rely on the exit code.

Tests that need Postgres use `CAREEROS_TEST_DATABASE_URL` (defaults to the compose instance's
`careeros_test` database) and are marked `@pytest.mark.db`; they are skipped when the DB is
unreachable. Tests that call a real AI provider are marked `@pytest.mark.ai` and run only with
`CAREEROS_AI_LIVE=1`.

## Adding a module slice

1. `services/careeros/src/careeros/modules/<name>/` with `enums.py`, `schemas.py`, `models.py`
   (if it owns tables), `service.py`, `router.py`, optional `cli.py`, `tasks.py`.
2. Register the router in `careeros/api/routers.py`, the CLI in `careeros/cli.py`, models in
   `migrations/env.py`, tasks in `worker/main.py`.
3. `just migration "<message>"` → review the generated file → `just migrate`.
4. Tests next to the module in `services/careeros/tests/<name>/`.
5. Import rule: other modules may import only `service`, `schemas`, `enums` of your module
   (import-linter contracts in the root `pyproject.toml`; contract 5 enforces it for
   `modules/insights` today). When insights needs another module's data, add a `*_rows` /
   `*_count` helper to that module's `service.py` — never query its ORM models from outside.
6. Docs: update `docs/architecture/02-domain-model.md` if tables/enums changed; ADR for any
   boundary decision.

## Telegram bot

Phone-first capture and triage surface, deployed on Fly. Setup, webhook ownership rules and
diagnosis: [`telegram-bot.md`](telegram-bot.md). Decision: [ADR 012](../adr/012-telegram-bot-surface.md).

## Platform connectors

`careeros.modules.platform` ([ADR-013](../adr/013-platform-connectors.md), user guide: [docs/platform](../platform/README.md)).
To add a connector: create `modules/platform/connectors/<name>/connector.py` with
`class Connector(BaseConnector)` declaring `platform` and `capabilities` (only the methods you
implement: `read_profile` / `import_profile_export` / `parse_profile_text`, `search_jobs` /
`import_jobs_export` / `parse_jobs_text`, `application_statuses` / `import_applications_export` /
`parse_applications_text`, plus `oauth_config`, `whoami`, `doctor` for API platforms, and the URL pair
`search_url(JobQuery)` / `profile_url(handle)` returning `None` when not expressible), add the name to
`connectors/__init__.py:CONNECTOR_MODULES`, the `Platform`/`Source` enum members if new, fixtures +
`tests/platform/test_<name>.py` (no network: `httpx.MockTransport`), and `docs/platform/<name>.md`.
`PlatformRegistry.verify()` (run by `test_core.py`) fails when a declared capability has no
implementation. Connectors may not import the DB or any service module (import-linter contract).
Shared paste heuristics live in `platform/parsers.py`; HTTP goes through `platform/http.py`.
The `careeros-platform-connector-dev` sub-agent (`.claude/agents/`) encodes this checklist.

## AI assistants (P3)

`modules/opportunities/assistants.py` follows the CV engine's shape: a **deterministic frame**
first, AI second, a **guard** last.

- `interview_frame` — story materials (achievements / projects / experience citing the posting's
  technologies, each with its fact id), matched / claimed-only / missing tech, weak score
  dimensions, the questions the posting leaves open. `guard_interview` drops any AI story or answer
  that cites an unknown fact id, states a number absent from its cited facts or names an uncited
  employer.
- `negotiation_frame` — offer normalised to annual/hourly, the owner's floor / target
  (`scoring/model.yaml#compensation`), the observed band (p25 / median / p75 of the *other*
  postings on the same basis and currency), position, anchor = max(target, p75).
  `guard_negotiation` keeps a line only if every number in it is in `frame.allowed_numbers` or in
  a fact it cites by id.
- `ranking_problem` — `POST /opportunities/compare` with `use_ai` asks for a ranking over the
  deterministic rows (§31); anything that is not a permutation of the compared ids is dropped
  (`ranking_note`), never repaired. `ranked` always stays the score order.

Endpoints: `POST /opportunities/{id}/interview-prep`, `POST /opportunities/{id}/negotiation`
(`{"use_ai": false}` returns the frame and needs no provider). AI results are also stored as
`Suggestion` rows (`interview_prep`, `negotiation_plan`) so they enter the approval flow.
Prompts: `career/prompts/{interview,negotiation}/`, `career/prompts/opportunity/opportunity_compare.yaml`.

## Assistant (tool-calling, ADR-014)

`AIService.with_tools(prompt_id, inputs, tools, execute, schema)` runs the loop: the provider's
`chat_with_tools` returns one turn (text and/or tool calls), the gateway executes each call
through `execute` (errors go back to the model as results, never raised), validates the final
JSON against `schema` with the usual retry budget, and records **one** `ai_run` whose
`output.tool_trace` lists every call. Adapters only translate wire formats (`tool_messages` /
`tool_specs` / `parse_tool_turn`); `FakeProvider(tool_responder=…)` scripts turns in tests.

`modules/assistant/tools.py` is the registry: a `Tool` = name + description + Pydantic argument
model (its JSON Schema goes to the provider) + async handler over a module's **service layer**.
Tools are read-only by construction; every result and every surfaced entity id lands in the
`ToolContext`, so `guard_answer` can reject an answer that cites an unknown id or states a number
the model never saw — the answer is withheld, the raw output stays in the ledger. To add a tool:
argument model + handler + an entry in `TOOLS`; never query another module's ORM from a handler,
and never make a tool that writes (proposals go through Suggestions). Surface:
`POST /api/assistant/ask`, `GET /api/assistant/tools`, `careeros assistant ask "…"`, `/assistant`.

## Workflows (WAIT_FOR_APPROVAL, ADR-017)

`modules/workflows/engine.py` declares each workflow as an ordered tuple of `Step(name, kind,
description, run)`; `kind` is `auto` or `approval`. A step receives a `WorkflowCtx` (settings,
vault, AI service, session, the run's accumulated `context`, start `options`) and returns a
`StepResult` — summary, output, context delta, and for approval steps a `Proposal` (title +
payload). `WorkflowService._advance` runs steps in order, commits the `workflow_run` row after
every step, and at an approval step records the proposal as a `Suggestion`
(`target_type="workflow"`) and stops with state `waiting_approval`; `decide(approve|reject)`
moves the Suggestion and resumes or cancels. Raise `StepFailed` for a clean, non-retried failure.
Steps call other modules' `service.py` only; nothing in a workflow may send mail, post to a
platform or edit the vault. Adding a workflow: steps in `engine.py`, a `WorkflowKind`, an entry in
`DEFINITIONS`, a test that proves rejection writes nothing. Surface: `/api/workflows/*`,
`careeros workflows …`, `/workflows`. Gotcha: never mutate a step dict that is already stored in
`run.steps` — `_set_step` deep-copies and calls `flag_modified`, otherwise JSONB changes are lost.

## Web app

`apps/web` (Next.js 15, App Router, Tailwind 4, TanStack Query). All data access goes through the
generated client: `npm run generate` exports OpenAPI from FastAPI and regenerates
`packages/schemas/src/api.d.ts`; `packages/api-client` wraps it with `openapi-fetch`. Never
hand-write response types. Dev: `npm run dev` (rewrites `/api/*` to `API_INTERNAL_URL`, default
`http://localhost:8000`). Gates: `npm run -w apps/web typecheck | lint | test | build`.

## Configuration

All settings: `services/careeros/src/careeros/core/config.py` (`CAREEROS_*`). Templates in
`config/`; `scripts/env-render.py` renders `.env` and `config/.env.docker`. Never put literal
secrets in templates (`make lint` checks).

## Conventions

Commits: conventional commits (`feat(vault): …`, `fix(api): …`, `docs: …`, `career(experience): …`
is reserved for vault data commits made by the app). Python: ruff (100 cols), pyright, pytest-asyncio
auto mode. TS: eslint, tsc strict, vitest.
