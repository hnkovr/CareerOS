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
- [ ] P1.2 Inbox core: message/thread models, MailProvider port + manual/paste adapter, classification (rules + AI), email→opportunity extraction & linking
- [ ] P1.3 Gmail adapter: OAuth flow, incremental history sync (needs user's Google Cloud app credentials)
- [ ] P1.4 Reply drafts: Suggestion → approval → Gmail draft (send only on explicit action)
- [ ] P1.5 pgvector semantic search; notifications; PWA service worker

## Parked
- `packages/ui` extraction (when Tauri lands)
- Embedding model choice for P1 semantic search
- Gmail app verification path for SaaS
