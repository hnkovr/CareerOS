# HANDOFF — CareerOS P0 complete (compaction snapshot, 2026-08-25)

Session: built CareerOS from empty dir → full P0 "Career Core" per the founder brief
(`docs/product/00-product-brief.md` = requirements SoT, preserved verbatim).

## Done, with commit SHAs

| Commit | Slice |
|---|---|
| `5983bc4` | Architecture proposal, domain model, capabilities matrix, roadmap, risks, ADR 001–010 (`docs/architecture/`, `docs/adr/`) |
| `203cc92` | P0.1 foundation: uv monolith `services/careeros`, FastAPI factory, settings (`CAREEROS_*`), async SQLAlchemy, TaskRunner port (inline/ARQ), compose (pgvector+redis+api+worker+migrate+web), Make/Just, pre-commit, GitHub Actions |
| `d3c4b57` | P0.2 vault: Pydantic schema SoT → `career/schemas/*.json`, loader/validator (referential integrity), `Vault.preview_change/apply_change` (ruamel round-trip, shadow validation, git commit `career(<coll>): …`, base_sha conflict), demo vault (synthetic persona Dana Kovalenko) at `career/examples/demo`, scaffold `career/templates`, `/api/vault`, `careeros vault` CLI |
| `3db1fed` | P0.3 AI gateway: `AIProvider` port; Anthropic (`messages.parse` output_format) + OpenAI-compatible (`chat.completions.parse` + JSON fallback) + FakeProvider; PromptRegistry (`career/prompts` ⊕ `<vault>/prompts`); structured() with Pydantic validation + retry + fallback chain; `ai_run` ledger; Mode B bundles (deep links chatgpt/claude/perplexity); Mode C dev packets |
| `8935d45` | P0.4 CV engine: deterministic selection (positioning/channel/JD keywords), provenance guard (unknown ids, numbers-with-units, foreign companies → drop + verbatim-fact fallback), RenderCV adapter (input.yaml → typst/pdf/md + cv.json), compare by derived_from, `cv_artifact`+`generated_bullet` |
| `2463ff2` | P0.5 opportunities: heuristic parser (+optional AI merge, gap-fill only), dedup (URL/company+title + fuzzy `possible_duplicate_of`), 13-dimension deterministic scoring over `scoring/model.yaml` + recommendation overrides (reply_now/ask_first/negotiate), AI analysis, compare, external 12-point prompt; core/db engine URL-aware fix |
| `b201541` | P0.6 profiles: snapshots (paste-only capture), deterministic audit engine (13 categories) + AI findings (unknown fact refs stripped, confidence capped 0.4), health = 100 − softened severity penalties, platform health endpoint |
| `9495d58` | P0.7 web: Next 15 + React 19 + Tailwind 4 + TanStack Query 5; npm workspaces (`apps/web`, `packages/schemas` generated OpenAPI types, `packages/api-client` openapi-fetch); dashboard; vault JSON editor with preview-diff→apply; opportunities ingest/triage/detail (score bars, analysis, Mode B copy); CV generate/compare + per-bullet provenance viewer linking to vault; profiles audit UI; ⌘K palette; `/api/*` rewrites |
| (this commit) | P0.8: `scripts/e2e-smoke.sh` (12 checks, verified green), `just e2e`, README/dev-guide updates, status files |

Gate at snapshot time: **68 backend pytest + 5 web vitest green; ruff/pyright/eslint/tsc 0 errors;
import-linter 3 contracts kept; demo vault validates 0/0**. E2E verified twice: built `next start`
+ real API (5 pages + proxy 200), and `scripts/e2e-smoke.sh` 12/12 (ingest scored 84 → apply;
CV artifact generated; files served).

## Not done, and why

- **Docker image build not verified locally** — `docker compose --profile web build` background run
  was killed before finishing (compaction requested). CI has a build job for the api image
  (`.github/workflows/ci.yml`); web image build still unverified anywhere. First thing to re-run:
  `just build`.
- **CI never executed** (no remote configured; repo is local-only, branch `main`, no pushes).
- **DoD §71 items all implemented**; unchecked end-to-end only for: PDF download via *web UI*
  click-path (API file endpoint verified), vault edit via *web UI* click-path (API preview/apply
  verified + tested).
- **P1+ not started**: Gmail/inbox, pipeline Kanban, contacts UI, notifications, pgvector search,
  drift detection (P2), assistants/analytics (P3). Roadmap: `docs/architecture/04-roadmap.md`.
- `.claude/PROMPTS-LOG.md` has the single founder-brief entry; no `-ru` variant was requested.

## Decisions accepted (do not re-litigate)

1. Monorepo deviations from brief §6, argued in `docs/architecture/01-architecture-proposal.md` §6:
   one Python project `services/careeros` (not api/+worker/), TS gets *generated* types (no
   hand-written packages/{domain,ai,prompts,platform-adapters}), prompts live in `career/prompts`,
   private vault = separate repo via `CAREEROS_VAULT_PATH` (default git-ignored `career/private/`).
2. ADR 001–010 accepted (`docs/adr/`): git vault SoT; Postgres operational; AIProvider port;
   platform capabilities model; **no scraping/credentials/auto-send**; RenderCV behind adapter;
   web-first; modular monolith + TaskRunner port; Pydantic = schema SoT; deterministic-first with
   AI-as-suggestion + provenance guard.
3. Versions pinned deliberately: Next **15.5.23** (not 16), TS ^5.9 (not 7), tailwind 4, vitest 4;
   anthropic 1.0 / openai 3.3 verified against installed SDKs; rendercv 2.8 + rendercv-fonts + typst.
4. Scoring weights/thresholds live in vault `scoring/model.yaml` (weights sum to 1.0 enforced).
5. Health-score formula: global severity penalties (100 − Σ/2), not category mean — category mean
   diluted real problems (commit `b201541`).
6. Commit convention: conventional commits, `career(<collection>): …` reserved for app-made vault
   data commits; trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
7. Git identity used for commits: `hnkovr <beloz978@gmail.com>` (repo-local flags, not global).

## Reopen-from-disk pointers

- Session/status: `.claude/CLAUDE-curr-status.md`, `.claude/TODO.md`
- Agent guide (invariants!): `.claude/CLAUDE.md`
- Architecture: `docs/architecture/01-architecture-proposal.md` … `05-risks.md`, `docs/adr/README.md`
- Commands: root `Makefile` / `Justfile`; smoke: `scripts/e2e-smoke.sh`
- Infra running locally: docker `careeros-postgres-1`/`careeros-redis-1` (dbs `careeros`, `careeros_test`)
