# TODO

Roadmap detail: `docs/architecture/04-roadmap.md`.

## P0 — Career Core
- [x] P0.0 Architecture proposal, domain model, capabilities matrix, roadmap, risks, ADR 001–010
- [x] P0.1 Foundation: monorepo, uv/pnpm workspaces, FastAPI skeleton, compose (postgres+pgvector, redis), Alembic, config templates, Makefile/Justfile, pre-commit, CI
- [x] P0.2 Vault: Pydantic schemas → JSON Schema; demo vault; reader/validator; git diff/commit; API + CLI
- [x] P0.3 AI gateway core (provider port, Anthropic + OpenAI-compatible, prompt registry, structured output, runs ledger, bundles, dev packets)
- [x] P0.4 CV engine: fact selection, AI rewriting + provenance guard, RenderCV adapter, variants, comparison
- [x] P0.5 Opportunities: ingest, parser (heuristic + optional AI), dedup, deterministic scoring, recommendation, AI analysis, compare, external prompt

- [x] P0.6 Profiles: snapshots (paste/structured capture), deterministic audit engine + optional AI findings, health scores, platform health endpoint
- [x] P0.7 Web: dashboard, vault editor w/ diff, opportunities triage + detail, CV generate/compare + provenance viewer, profiles + audits, ⌘K palette
- [x] P0.8 Hardening: e2e smoke script (12 checks green), README walkthrough, docs; ⏳ docker image build re-verify: `just build`

## P1 — Inbox & Pipeline
- [x] P1.1 Pipeline: applications Kanban (employment+freelance stages), timeline events, interviews, follow-ups, contacts/companies CRUD, web UI (board, detail, contacts, dashboard card)
- [x] P1.2 Inbox core: threads/messages, raw-paste parsing, rule classification + optional AI refine, contact/opportunity/application linking, opportunity extraction, reply suggestions (Suggestion rows, never sent), /inbox UI + dashboard card
- [ ] P1.3 Gmail adapter: OAuth flow, incremental history sync (needs user's Google Cloud app credentials)
- [x] P1.4 Suggestion approval flow: suggested→reviewed→approved→executed/rejected with legal transitions; reply-sent closes into the application timeline; /suggestions UI + dashboard card (Gmail draft creation itself lands with P1.3)
- [x] P1.5a unified search: FTS (GIN) + optional pgvector semantic over facts/opportunities/messages/CVs/contacts, /search UI
- [x] P1.5b computed notification center (/api/notifications + header bell) + PWA manifest/icon; web push deferred until HTTPS deploy

- [ ] P0.9 Telegram bot ([GH #1–#9](https://github.com/hnkovr/CareerOS/issues), [Linear: CareerOS](https://linear.app/my-1st/project/careeros-2039a962e2cf))
  - Spec: `docs/superpowers/specs/2026-08-25-careeros-telegram-bot-design.md` · [ADR 012](../docs/adr/012-telegram-bot-surface.md)
  - DONE: deploy path (`fly.toml`, `config/deploy.yml`), ops scripts, SessionStart guard,
    `/careeros-bot` skill, `@careeros_hnkovr_bot` + webhook secret, 47 tests in `tests/deploy/`
  - TODO: [#1](https://github.com/hnkovr/CareerOS/issues/1) webhook + 3 gates ·
    [#2](https://github.com/hnkovr/CareerOS/issues/2) ownership claim ·
    [#3](https://github.com/hnkovr/CareerOS/issues/3) capture ·
    [#4](https://github.com/hnkovr/CareerOS/issues/4) triage ·
    [#5](https://github.com/hnkovr/CareerOS/issues/5) career cmds ·
    [#6](https://github.com/hnkovr/CareerOS/issues/6) ops cmds ·
    [#7](https://github.com/hnkovr/CareerOS/issues/7) notifications ·
    [#8](https://github.com/hnkovr/CareerOS/issues/8) db scheme ·
    [#9](https://github.com/hnkovr/CareerOS/issues/9) first deploy
  - BLOCKED on you: `CAREEROS_TG_OWNER_CHAT_ID` — message @careeros_hnkovr_bot once

## P2 — Platform connectors ([GH #10](https://github.com/hnkovr/CareerOS/issues/10), [Linear MY-26](https://linear.app/my-1st/issue/MY-26), [ADR 013](../docs/adr/013-platform-connectors.md))
- [x] core: contract/registry/matrix, token store + OAuth2, HTTP, tables, sync, API, CLI, just recipes ([#11](https://github.com/hnkovr/CareerOS/issues/11))
- [x] hh.ru [#12](https://github.com/hnkovr/CareerOS/issues/12) · Upwork [#13](https://github.com/hnkovr/CareerOS/issues/13) · LinkedIn [#14](https://github.com/hnkovr/CareerOS/issues/14) · Wellfound [#15](https://github.com/hnkovr/CareerOS/issues/15) · Indeed [#16](https://github.com/hnkovr/CareerOS/issues/16) · getmatch [#17](https://github.com/hnkovr/CareerOS/issues/17) · Toptal [#18](https://github.com/hnkovr/CareerOS/issues/18)
- [x] docs/agents/skill ([#19](https://github.com/hnkovr/CareerOS/issues/19)): ADR-013, matrix, `docs/platform/*`, `/careeros-platform-sync`, `careeros-platform-{ops,connector-dev}` agents
- [ ] YOU: register OAuth apps — hh.ru (dev.hh.ru) and Upwork (API key approval) → `.env.secrets` → `just platform-connect hh|upwork`; download the LinkedIn archive
- [ ] Web Platforms page ([#20](https://github.com/hnkovr/CareerOS/issues/20)): capabilities, connections/connect, paste box, sync runs, application statuses
- [ ] Follow-ups ([#21](https://github.com/hnkovr/CareerOS/issues/21)): Fly volume/redirect base if sync moves to Fly, single job-URL capture, email-based statuses via inbox, observations ↔ pipeline Application, live token tests, VERIFY LIVE Upwork fields, shared-parser improvements (relative dates, `now` propagation, schema fields)

## Build pipeline
- [x] `make all` — ordered 12-step local pipeline (green, exit 0); `make check` = gate only;
  `make run` opens the web app instead of Finder; `clean` no longer deletes `generated/platform`
  (OAuth tokens); `distclean` drops `.venv`/`node_modules`
- [ ] `just build` — docker image build still unverified (carried over from P0.8); `make build` runs it
- [ ] wire `make check` into CI (`.github/workflows`) so the gate is one command in both places

## Parked
- `packages/ui` extraction (when Tauri lands)
- Embedding model choice for P1 semantic search
- Gmail app verification path for SaaS
