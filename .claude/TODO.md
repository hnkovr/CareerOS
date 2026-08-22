# TODO

Roadmap detail: `docs/architecture/04-roadmap.md`.

## P0 — Career Core
- [x] P0.0 Architecture proposal, domain model, capabilities matrix, roadmap, risks, ADR 001–010
- [x] P0.1 Foundation: monorepo, uv/pnpm workspaces, FastAPI skeleton, compose (postgres+pgvector, redis), Alembic, config templates, Makefile/Justfile, pre-commit, CI
- [x] P0.2 Vault: Pydantic schemas → JSON Schema; demo vault; reader/validator; git diff/commit; API + CLI
- [x] P0.3 AI gateway core (provider port, Anthropic + OpenAI-compatible, prompt registry, structured output, runs ledger, bundles, dev packets)
- [x] P0.4 CV engine: fact selection, AI rewriting + provenance guard, RenderCV adapter, variants, comparison
- [ ] P0.5 Opportunities: ingest, parser, dedup, deterministic scoring, recommendation
- [ ] P0.5b AI analysis of opportunities (uses gateway)
- [ ] P0.6 Profiles: snapshots, audit engine, health score
- [ ] P0.7 Web: dashboard, vault editor w/ diff, CV generate/compare, opportunities, prompts, snapshots/audit, ⌘K
- [ ] P0.8 Hardening: compose smoke e2e, contract tests, README walkthrough, seed

## Parked
- `packages/ui` extraction (when Tauri lands)
- Embedding model choice for P1 semantic search
- Gmail app verification path for SaaS
