# Roadmap — P0 → P3

Status: accepted (2026-08-20). Each phase is a set of vertical slices; each slice = code + tests + docs + commit.

## P0 — Career Core (usable alone, no email, no platform APIs)

| Slice | Scope | DoD items (brief §71) |
|---|---|---|
| P0.1 Foundation | monorepo, uv + pnpm workspaces, FastAPI skeleton, Postgres/Redis compose, Alembic, config templates, Makefile/Justfile, pre-commit, CI | 19, 20 |
| P0.2 Vault | Pydantic schemas → JSON Schema export; demo vault; reader/validator (referential integrity); git commit/diff; vault API (`GET /vault/*`, `POST /vault/changes`, `/apply`); CLI `careeros vault validate` | 2, 3, 4, 5 |
| P0.3 CV engine | fact selection by positioning+channel(+JD); AI bullet rewriting with provenance guard; RenderCV adapter; variants `general-core`, `remote-us`, `wellfound`, `poland-eu`, `startup-data-engineer`, `consultant`, `agentic-data-platform`; artifacts + `GeneratedBullet`; comparison endpoint | 6, 7, 8 |
| P0.4 Opportunities | ingest (paste/URL/ATS JSON), raw retained, parser (heuristics + AI structured extraction), dedup, deterministic scoring over `scoring/model.yaml` with per-dimension explanation, recommendation | 9, 10, 11 |
| P0.5 AI gateway | `AIProvider` port; Anthropic + OpenAI-compatible adapters; prompt registry from vault; structured output + validation + retry; `ai_run` ledger; opportunity analysis; external prompt bundles (ChatGPT/Claude/Gemini/Grok/Perplexity); dev-agent packets | 12, 13, 14, 15 |
| P0.6 Profiles | snapshot capture (paste/structured/upload); audit engine (deterministic checks + AI findings) with health score and findings; per-platform audit rules | 16, 17 |
| P0.7 Web | Next.js dashboard: cards (Profile Health, New Opportunities, Top Matches, CV Versions, AI Suggestions, Sync Status); vault browser + fact editor with diff; CV generate/compare; opportunity list+detail+ingest; prompt bundle copy; snapshot + audit views; ⌘K palette (basic) | 1, 18 |
| P0.8 Hardening | e2e compose smoke test, API contract tests, docs, seed command, README walkthrough | 19, 20 |

## P1 — Inbox & Pipeline
Gmail OAuth + incremental sync (history API), classification (deterministic rules + AI), opportunity
extraction from email → P0.4 pipeline, recruiter/contact detection, email↔opportunity↔application
linking, Application Kanban (employment + freelance stages), contacts, follow-ups, reply drafts
(Suggestion → approval → Gmail draft; send only on explicit action), notifications (web push),
pgvector semantic search, PWA service worker.

## P2 — Platform & Multiplatform
Per-platform workflows (LinkedIn/Wellfound/Upwork/Toptal update checklists from audit findings),
LinkedIn export importer, Upwork API spike (if approved), drift detection across snapshots & vs
vault, browser extension + iOS Share Sheet capture (user-initiated), Expo iOS app (triage, inbox,
approve/reject, notifications), Tauri macOS app (clipboard, local vault access, dev-agent launch,
menu bar), generic IMAP.

## P3 — Agentic Career OS
Assistants on every entity via tool-calling over domain services, multi-step workflows with explicit
`WAIT_FOR_APPROVAL` states, market intelligence over observed stream, funnel analytics, skills-gap
engine (known/claimed/evidenced/missing/worth-learning), portfolio planner, negotiation + interview
intelligence, daily career brief, per-workflow approval policies.

## DoD for first usable release (brief §71) → where it lands
1 web app → P0.7 · 2–5 vault view/edit/diff/save → P0.2+P0.7 · 6–8 CV variants → P0.3 ·
9–11 ingest/normalize/score → P0.4 · 12–14 AI analysis/recommendation/best CV → P0.5 ·
15 external prompts → P0.5 · 16–17 snapshots/audit → P0.6 · 18 dashboard → P0.7 ·
19 compose → P0.1 · 20 one-command tests → P0.1/P0.8
