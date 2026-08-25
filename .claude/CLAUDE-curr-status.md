# Current status

Updated: 2026-08-24

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
