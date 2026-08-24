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

## Next (P1 — Inbox & Pipeline)
Gmail OAuth + incremental sync, classification, email→opportunity extraction, application Kanban,
contacts, follow-ups, notifications, pgvector search. See `docs/architecture/04-roadmap.md`.

## Known quirks
- Demo vault lives inside the monorepo, so its `vault status` reports the monorepo git state
  (`is_repo: true, dirty` reflects the outer repo). A real private vault at `CAREEROS_VAULT_PATH`
  behaves as its own repo.
- Web `next build` prerenders pages that call the API at request time only (client components) —
  no API needed at build time.

## How to run
`make env && docker compose up -d postgres redis && uv sync --all-groups && just migrate && just seed`
API: `uv run careeros-api` · web: `npm install && npm run dev` · all-in-docker: `make up`
Gates: `uv run pytest` · `just lint` · `npm run -w apps/web test` · smoke: `scripts/e2e-smoke.sh`
