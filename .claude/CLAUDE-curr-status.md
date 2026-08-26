# Current status

Updated: 2026-08-26

## P0 "Career Core": complete (pending final hardening commit)

| Slice | Commit | State |
|---|---|---|
| Architecture + ADR 001–010 | `docs(architecture)` | done |
| P0.1 foundation (uv monolith, compose, CI) | `feat(core)` | done |
| P0.2 vault (schema SoT, git-backed changes, demo vault) | `feat(vault)` | done |
| P0.3 AI gateway (provider port, prompts, runs, Mode B/C) | `feat(ai)` | done |
| P0.4 CV engine (selection → guarded rewrite → RenderCV) | `feat(cv)` | done |
| P0.5 opportunities (parse, dedup, scoring, analysis) | `feat(opportunities)` | done |
| P0.6 profiles (snapshots, audit engine, health) | `feat(profiles)` | done |
| P0.7 web (dashboard, editor, triage, provenance viewer) | `feat(web)` | done |
| P0.8 hardening (e2e smoke, docs, docker build) | in progress | — |

Backend: 68 tests, ruff/pyright/import-linter clean. Web: tsc/eslint clean, vitest 5, prod build OK,
e2e smoke (built web + real API + proxy) verified.

## P1 progress
- P1.1 pipeline (Kanban, timeline, interviews, follow-ups, contacts) — `dffd335`
- P1.2 inbox (paste capture, rule+AI classification, linking, extraction, reply suggestions) — `cb5495e`
- P1.5a unified search (FTS + optional pgvector semantic, /search UI) — this commit
- parallel worktree: platform connectors (hh/upwork OAuth, sync runs) + Telegram bot surface (ADR-011)
- ADR numbering (coordination, 2026-08-25): Telegram bot surface = **012**, platform connectors = **013** (`docs/adr/013-platform-connectors.md`); the platform-connectors session yields on collisions — please keep 012 for the bot.
- remaining: P1.3 Gmail adapter (needs user's Google OAuth app; can reuse platform token store),
  P1.4 suggestion approval flow, P1.5b notifications + PWA

## P2 pulled forward — Platform connectors (lane careeros-d2, 2026-08-25)
`modules/platform` ([ADR 013](../docs/adr/013-platform-connectors.md), [spec](../docs/superpowers/specs/2026-08-25-platform-connectors-design.md), [plan](../docs/superpowers/plans/2026-08-25-platform-connectors.md), guide `docs/platform/`):
- core `7d5052d` `394936e` `d3f8548` `11eac38` `db14cce` — contract + capabilities matrix, 0600 token store, OAuth2, HTTP retries, tables `platform_connection`/`platform_sync_run`/`application_observation`, sync (api > export > paste, dry-run), `/api/platform/*`, `careeros platform …`, `just platform-*`
- connectors: hh.ru `f76824c` (API) · Upwork `d754447` (GraphQL, VERIFY LIVE) · LinkedIn `68795b4` (export) · Wellfound `c1490fd` · Indeed `04da32b` · getmatch `5038989` · Toptal `1bcec8d` (paste)
- tests: ~470 platform cases, no network; full suite + `just lint` green; per-session test DB `careeros_test_d2`
- tooling: skill `/careeros-platform-sync`, agents `.claude/agents/careeros-platform-{ops,connector-dev}.md`, `config/platform.yml`
- tracking: GitHub [#10](https://github.com/hnkovr/CareerOS/issues/10) epic (connectors #12–#18 closed; #19 docs, #20 web page, #21 follow-ups open) · Linear MY-26…MY-37
- deploy: local-only for now — platform credentials are excluded from the Fly env push; see `docs/platform/README.md#deployment-notes`
- pushed 2026-08-26: origin/main = `6ed6325`, CI green (ci.yml `hashFiles` fix `f650878`, pgvector extension `f23b677`, SSoT-test skip `6ed6325`)

## Build pipeline — `make all` (2026-08-25)
`make all` is now the whole local pipeline in dependency order and exits 0:
env → infra → openapi → fmt → lint → test → migrate → seed → validate-career → generate-cv →
platform-sync → bot-check. `make check` is the gate alone (lint + test, mutates nothing).
Two blockers found and fixed while wiring it up:
- `platform sync all` counted "not connected" as **failed** (hh's public job search needs query
  text without a token), so a sweep on a fresh install exited 1. `sync_all` now reports
  `NotConnected`/`CapabilityUnavailable` as **skipped** with the next step, and the CLI prints a
  tally; only a real upstream failure exits non-zero.
- The vault default had no fallback: `CAREEROS_VAULT_PATH=career/private` is an empty scaffold on
  a fresh checkout, so `validate-career` and `generate-cv` failed even though README promised the
  demo vault. `get_vault()` now falls back to `career/examples/demo` when the configured path has
  no `vault.yaml`, and opens it **read-only** (`VaultReadOnly` → HTTP 403) so demo facts can never
  be committed as the owner's. `VaultStatus.read_only` exposes it.

### Round 2 — gaps the green pipeline was hiding
- **contract drift**: CI diffed `packages/schemas` in a step that only regenerated `career/schemas`,
  so the generated TS API types could drift from the FastAPI models unnoticed.
  `scripts/contracts-check.sh` (one implementation, used by `make contracts` and the new `contracts`
  CI job) regenerates both and fails on any diff; it requires both toolchains rather than passing on
  the half it can see. Negative-tested: adding a field to a response model makes it exit 1.
- **offline**: `make all` died at `bot-check` with no network. `tg-bot.sh` now exits **4** for
  "cannot reach Telegram" (was conflated with 2 = Telegram rejected the token); `make bot-check`
  tolerates 4 only — a revoked token, wrong bot (2) or foreign webhook owner (3) still fail.
- **silent half-gate**: `just lint/test/fmt/typecheck` skipped the web steps without `node_modules`
  and said nothing; they now print why, so a half-run gate cannot read as a green gate.
- CI additionally builds `deploy/docker/Dockerfile.web` (P0.8 docker item now verified in CI).

## P2/P3 progress (lane careeros-d1, 2026-08-26)
- `0f7383e` P2 drift detection (platform↔platform, platform↔vault; persisted, resolvable) + P3 daily brief (`/api/insights/brief`, dashboard 'Today')
- `58d5370` P3 insights: market intelligence, skills-gap + portfolio planner, funnel analytics (`/insights`)
- `33b8eef` P2 per-platform update checklists (`/api/profiles/checklist/{platform}`, copy-ready headline/about)
- `f23b677` fix handed over by the platform lane: the search_documents migration now creates the pgvector extension (CI's bare Postgres had none) — CI fully green at 6ed6325 (contracts, python, web, docker)
- P3 interview prep + negotiation intelligence + §31 AI-ranked comparison (`modules/opportunities/assistants.py`): deterministic frame → AI → provenance guard; Suggestion rows; opportunity page cards + list compare mode
- invariant-7 tech debt paid for insights: notifications/brief read other modules only via service helpers; import-linter contract 5 enforces it
- ADR 014 assistants via tool-calling: tool-use in the provider port (3 adapters), `AIService.with_tools` loop + ledger trace, `modules/assistant` (6 read-only tools, answer guard), API/CLI/web `/assistant`; ADR numbers: 014 mine, 015/016 claimed by the platform lane
- shared test infra: `tests/conftest.py` now registers `careeros.modules.bot.models` for create_all (another session's uncommitted per-test truncate fixture, GH #24, needs it — otherwise TRUNCATE hits a table that was never created)
- ADR 017 workflows with WAIT_FOR_APPROVAL: `modules/workflows` (engine, runner, `workflow_run` migration c4d8e2f1a9b3 off b1c7d0e9a4f2 — the platform lane chains after it), apply + follow_up workflows, API/CLI/web; the first write actions of the platform, provably gated (tests: reject writes nothing)
- `start_workflow` assistant tool — the ADR-014 loop can now prepare an apply/follow-up run that waits at the ADR-017 gate
- daily follow-up sweep (API/CLI/worker cron) — the first scheduled workflow start; still approval-gated per run
- lane gates: 315 backend tests (excl. platform/deploy lanes); note: the platform lane's uncommitted `OpportunitySource` model broke every db test in the shared tree for a while (relation missing at TRUNCATE) — theirs to fix, reported, pyright 0, ruff clean, 4 import contracts kept; web tsc/eslint/vitest/build green
- still blocked on user: P1.3 Gmail (Google OAuth app credentials); next candidates: §55 tool-calling assistants (ADR + go-ahead), §53 WAIT_FOR_APPROVAL workflows, notifications.py invariant-7 tech debt

### 2026-08-26 — gate hardening (session 3)
`make all` → exit 0, 13/13 steps, 590 python + 5 web tests.
- **[#22](https://github.com/hnkovr/CareerOS/issues/22) blank env var broke everything.** A freshly
  rendered `.env` could not build `Settings()` — `CAREEROS_TG_OWNER_CHAT_ID=` is what the template
  emits, and `int | None` cannot parse `""`. API, worker, CLI and pipeline all died on import, with
  a traceback naming pydantic rather than the template. Same root cause made blank `SecretStr`
  fields into `SecretStr("")`, truthy but useless (`ProviderRegistry` tested it with a bare `if`).
  `Settings._blank_means_unset` maps `""` → `None` for every `| None` field.
- **`config/gate.yml`** — the pipeline as data (step order, `proves`, `fails_when`, bot exit codes,
  guards, contracts). `tests/test_gate_config.py` asserts the order equals the Makefile's `all:`,
  so the description cannot drift from the thing it describes.
- **Guards**: `scripts/hooks/config-guard.sh` (SessionStart, cached, always exit 0) builds
  `Settings()` from the live `.env` and names the offending field — this bug now costs one line
  instead of one pipeline run. Registered next to `bot-guard.sh` in `.claude/settings.json`.
- **Skill + agent**: `/careeros-gate` (catalog `projects/careeros/careeros-gate`, with
  `references/triage.md` = every failure this repo has produced) and `.claude/agents/careeros-gate.md`
  (runs the 4-minute pipeline, returns a verdict not a log).
- Cleared 54 pyright errors in the bot lane's new tests (`Settings(**dict)` unpacks).
- [#23](https://github.com/hnkovr/CareerOS/issues/23) filed: CI still repeats the lint commands
  inline instead of calling the Justfile recipe.

### 2026-08-26 — one gate, isolated tests (session 4)
- **[#24](https://github.com/hnkovr/CareerOS/issues/24) test isolation.** Tables are created once
  per session and nothing cleaned rows, so every test's data stayed visible for the rest of the run
  and assertions about *new* data silently tested earlier tests' leftovers — the opportunities dedup
  assertion was green alone and red in a full run. An autouse fixture now truncates every table
  except the seeded `user` after each `@pytest.mark.db` test. Truncate, not a wrapping transaction:
  API tests drive the app, which opens its own sessions.
- **[#23](https://github.com/hnkovr/CareerOS/issues/23) one gate.** `scripts/gate.sh lint|test` is
  the single definition; `just lint`/`just test` and the CI `python` job both call it.
  `tests/test_gate_config.py` asserts both callers still delegate and that CI has not gone back to
  spelling the checks out itself.
- `just infra-up` now names the fix ("start Docker Desktop") instead of printing a socket path — a
  stopped daemon cost a full run this session.
- Reflowed 9 over-long lines across the bot/assistant lanes' in-flight files (mechanical, no logic).

## Telegram bot lane (2026-08-26)

Chat surface complete except triage callbacks and the first deploy. Latest: `35898ba` —
[#28](https://github.com/hnkovr/CareerOS/issues/28) `/queries`,
[#29](https://github.com/hnkovr/CareerOS/issues/29) `/cv update`,
[#30](https://github.com/hnkovr/CareerOS/issues/30) `/cv improve`.

- **Shipped**: webhook + 3 gates, ownership claim, capture, `bot_preference`, `/services`, `/open`,
  `/profiles`, `/urls` (quoted or by index), `/queries`, `/cv`, `/cv update`, `/cv improve`,
  `/next`, `/top [n]`, `/opp <handle>` and the four inline buttons. 220 bot + 49 deploy tests.
- **Read-only against the vault.** Generation writes only under `generated/` (invariant 3);
  `/queries` projects the vault's positionings rather than storing anything.
- **`/cv improve` regenerates its own baseline** instead of diffing against the last artifact —
  otherwise the comparison answers a different question each run, and an AI-vs-AI diff when the
  previous artifact was itself an AI pass. `CVService.improve` owns the two-pass logic.
- **Bugs fixed alongside**: `/help` was invalid MarkdownV2 (`[services]`, `<query>` unescaped → 400);
  no message was ever split at Telegram's 4096-char limit; the artifact read blocked the event loop.
- **Decided 2026-08-26**: aiogram is not adopted; the thin httpx client stays. `callback_data`
  parsing is ~40 hand-written lines with its own tests, against a dependency whose dispatcher
  assumes it owns the update loop — which here belongs to FastAPI. Recorded in the design spec
  under *Decision reversals*. Note the earlier claim that ADR-012 chose aiogram was wrong: the
  ADR never names a library.
- **Remaining**: [#5](https://github.com/hnkovr/CareerOS/issues/5) `/facts` `/profile` ·
  [#7](https://github.com/hnkovr/CareerOS/issues/7) notifications ·
  [#9](https://github.com/hnkovr/CareerOS/issues/9) first Fly deploy ·
  [#31](https://github.com/hnkovr/CareerOS/issues/31) mini-app.
  Linear: MY-38 … MY-42 (project CareerOS).

## Known quirks
- Concurrent sessions sharing `careeros_test` make db-marked tests flaky; this lane runs its
  gates against `careeros_test_d1` (see developer guide); the platform lane uses `careeros_test_d2`.
- Demo vault lives inside the monorepo, so its `vault status` reports the monorepo git state
  (`is_repo: true, dirty` reflects the outer repo). A real private vault at `CAREEROS_VAULT_PATH`
  behaves as its own repo.
- Web `next build` prerenders pages that call the API at request time only (client components) —
  no API needed at build time.

## How to run
`make env && docker compose up -d postgres redis && uv sync --all-groups && just migrate && just seed`
API: `uv run careeros-api` · web: `npm install && npm run dev` · all-in-docker: `make up`
Gates: `uv run pytest` · `just lint` · `npm run -w apps/web test` · smoke: `scripts/e2e-smoke.sh`
