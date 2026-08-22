# Current status

Updated: 2026-08-22

## Last completed
1. `docs(architecture)` — proposal, domain model, capabilities matrix, roadmap, risks, ADR 001–010
2. `feat(core)` P0.1 — uv monolith, FastAPI factory, settings, async DB, task-runner port, compose, CI, Make/Just
3. `feat(vault)` P0.2 — Pydantic schema SoT → JSON Schema export, loader/validator (referential integrity),
   git-backed preview/apply with conflict check, demo vault (synthetic persona), scaffold template, API + CLI, 24 tests

## In progress
- P0.3 CV engine (fact selection → provenance-guarded rewriting → RenderCV adapter → variants → comparison)

## Next
- P0.4 opportunities (ingest/parse/dedup/deterministic scoring)
- P0.5 AI gateway (provider port, Anthropic + OpenAI-compatible, prompt registry, runs ledger, analysis, external bundles, dev packets)
- P0.6 profiles (snapshots + audit), P0.7 web, P0.8 hardening

## How to run
`docker compose up -d postgres redis && uv sync --all-groups && just migrate && just seed && uv run careeros-api`
Tests: `uv run pytest` · gate: `just lint`
