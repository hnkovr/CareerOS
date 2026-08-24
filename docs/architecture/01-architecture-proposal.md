# CareerOS — Architecture Proposal

Status: **proposed → accepted for P0** (2026-08-20)
Audience: maintainers, coding agents
Related: [domain model](02-domain-model.md) · [capabilities matrix](03-integration-capabilities-matrix.md) · [roadmap](04-roadmap.md) · [risks](05-risks.md) · [ADRs](../adr/README.md) · [product brief](../product/00-product-brief.md)

---

## 1. What we are building

A **personal Career Operating System**: a single source of truth for career data, from which
channel-specific profiles and CVs are *projected*, into which opportunities and messages *flow*,
and on top of which AI produces *suggestions with provenance* that a human approves.

```
Canonical career data ─▶ positioning ─▶ channel projections (CV / LinkedIn / Wellfound / Upwork / Toptal)
        ▲                                              │
        │ approved diffs only                          ▼
   AI suggestions ◀── analysis/scoring ◀── opportunities ◀── ingest (paste / URL / email / share)
        │                                              │
        └──────────▶ applications / inbox / contacts ◀─┘
```

**Architecture invariant** (from the brief, enforced by module boundaries below):

> Career facts are canonical; channel profiles are projections; opportunities are events;
> AI outputs are suggestions with provenance; external actions remain controllable by the user.

Non-goals for P0–P2 are listed in the [product brief §2](../product/00-product-brief.md) and
codified in [ADR-005](../adr/005-no-autonomous-platform-scraping.md).

---

## 2. Requirements analysis — what actually shapes the architecture

Of the 72 sections in the brief, a handful of requirements are *load-bearing*; everything else is a
feature that must fit inside them.

| Load-bearing requirement | Architectural consequence |
|---|---|
| Two data classes: canonical facts (Git) vs operational state (Postgres) (§3–4) | Two storage engines, two write paths, one read model. The **Vault** module owns Git; everything else owns Postgres. No table ever becomes the source of truth for a fact. |
| AI may `select → summarize → rephrase → combine → project` but never `invent`; fact edits are `suggested change → diff → approval` (§3.1) | Every AI output is a `Suggestion` row with provenance; canonical writes go only through `Vault.apply_change(diff)` which commits to Git. |
| Provenance on every generated bullet (§8) | Generation is a pipeline with explicit *fact selection* before *rewriting*; the selected fact IDs are threaded through and stored with the artifact. Rewriting without selected fact IDs is a type error. |
| Transparent, configurable, multi-dimensional scoring (§15–17) | Scoring is deterministic code over a versioned weights file in the vault; AI only *interprets* the breakdown. "Deterministic first, LLM for interpretation" (§55). |
| Provider-agnostic AI with three interaction modes (§18–19) | One `AIProvider` port; prompts are vault files with metadata; Mode B/C are *renderers* of the same prompt registry, not separate subsystems. |
| Platforms differ wildly in what they allow (§26) | Adapters declare a `Capabilities` object; the UI and workflows query it. Email and manual capture are first-class inputs, not fallbacks bolted on later. |
| Web first, then iOS + macOS sharing code (§5, §50) | API-first backend with an OpenAPI contract; TS types and client are generated, never hand-written. Business logic stays server-side so clients are thin. |
| Self-hostable, `docker compose up`, single-user now, SaaS later (§14–15, §43) | Modular monolith, one image, two processes (api + worker). `user_id` on operational tables from day one; auth is a pluggable dependency. |
| Human-in-the-loop for all external writes (§25, §54) | An `ApprovalState` enum on every outbound action; no adapter exposes a "send" without an approved `Action` record. |

---

## 3. Bounded contexts

Eight domain contexts plus one platform layer. Each is a Python package under
`services/careeros/src/careeros/modules/<context>/` with its own `models.py` (ORM), `schemas.py`
(Pydantic), `service.py` (use cases), `router.py` (FastAPI), and tests. Cross-context calls go
through the other context's `service.py` — never through its ORM models.

| # | Context | Owns | Source of truth | P0? |
|---|---|---|---|---|
| 1 | **Vault** (career knowledge base) | Reading, validating, diffing and committing canonical YAML; stable fact IDs; positioning & channel rule files; private-repo location | **Git** | ✅ |
| 2 | **CV** (CV-as-Code) | Fact selection, AI rewriting, validation, RenderCV rendering, variants, provenance, comparison | Postgres (artifacts + provenance); Git (templates, variant definitions) | ✅ |
| 3 | **Opportunities** (opportunity intelligence) | Ingest (paste/URL/share/email), raw→normalized parsing, enrichment, deterministic scoring, AI analysis, recommendation, comparison, dedup | Postgres | ✅ |
| 4 | **Profiles** (channel profiles) | `PlatformProfileSnapshot`, audit engine, drift detection across platforms and vs vault | Postgres | ✅ (snapshots + audit); drift P2 |
| 5 | **AI** (AI gateway) | `AIProvider` port + adapters, prompt registry (from vault), structured output, AI runs ledger, external-chat prompt bundles (Mode B), dev-agent task packets (Mode C), suggestions | Postgres (runs, suggestions); Git (prompts) | ✅ |
| 6 | **Inbox** (career inbox) | Gmail/IMAP ingestion, classification, linking to opportunity/company/contact, reply drafts | Postgres | P1 |
| 7 | **Pipeline** (applications CRM) | Applications Kanban, freelance lead pipeline, interviews, contacts, companies, follow-ups, timeline | Postgres | P1 (Company/Contact entities exist in P0 as minimal tables because Opportunity references them) |
| 8 | **Insights** (analytics & strategy) | Funnel analytics, market intelligence over observed stream, skills gap, portfolio planner, daily brief | Postgres (derived) | P3 |
| — | **Platform** (integration layer) | Connector contract + `Capabilities` matrix, `PlatformRegistry`, OAuth token store, sync orchestration; connectors `hh`, `upwork`, `linkedin`, `wellfound`, `indeed`, `getmatch`, `toptal` (own profile · job search · application statuses; api > export > paste) — [ADR-013](../adr/013-platform-connectors.md) | Postgres (connections without secrets, sync runs, application observations); token file | ✅ (2026-08-25) |
| — | **Core** (cross-cutting) | config, DB session, migrations, auth, audit log, task runner, logging/telemetry, search | — | ✅ |

Context map (arrows = "depends on the service interface of"):

```
 Insights ──▶ Pipeline ──▶ Opportunities ──▶ Vault
    │            │              │    ▲           ▲
    │            ▼              ▼    │           │
    └────────▶ Inbox ──────▶ Platform │        CV ─┘
                                 │    │         │
                                 ▼    │         ▼
                             Profiles ┘ ◀──── AI ◀── (prompts in Vault)
```

Rules of the map:

* **Vault has no inbound dependencies on Postgres contexts.** It can run with no database (CLI: validate, render).
* **AI depends on nothing domain-specific.** Callers pass it facts/prompts; it returns validated structured output + a run record. Domain "assistants" (CV assistant, Opportunity assistant) live in *their* context and compose AI.
* **Platform is a port.** Domain contexts call `PlatformRegistry.get(platform).capabilities` and the adapter's read methods; adapters never call domain services (import-linter enforced). The one sanctioned exception is `platform/sync.py`, which orchestrates *connector → ProfileService / OpportunityService / application observations* ([ADR-013](../adr/013-platform-connectors.md)) — the edge *Platform(sync) → Profiles, Opportunities*.

---

## 4. Canonical vs operational data (the hard line)

| | Canonical (Git vault) | Operational (Postgres) |
|---|---|---|
| Examples | profile, experience, achievements, projects, skills, education, certifications, languages, publications, testimonials, offers, positioning, channel rules, CV variant definitions, scoring weights, prompts, RenderCV themes | opportunities, companies, contacts, applications, interviews, messages, profile snapshots, audits, generated CV artifacts, AI runs, suggestions, scores, tasks, sync state, notes |
| Identity | stable human-readable IDs (`achievement_prodamus_001`) | UUIDv7 |
| Writes | `Vault.apply_change()` → validate → diff → commit (`career(experience): …`) | ordinary transactional writes |
| Versioning | Git history is the version log; artifacts record the vault commit SHA they were generated from | `created_at/updated_at`, plus explicit version columns on scoring/prompt references |
| Can AI write? | Only via `Suggestion` → human approval → commit | Yes, as `Suggestion`/`AIRun` rows; domain rows only through approved actions |
| Backup | `git push` | `pg_dump` |

Derived data that *looks* canonical but is not: the Postgres `vault_index` (full-text/embedding index
of facts) is a **cache** rebuilt from the vault on every sync; it is never the read model for editing.

**Private data strategy** ([ADR-001](../adr/001-git-as-career-source-of-truth.md)): the vault is
a *separate private Git repository* whose path is `CAREEROS_VAULT_PATH`. This monorepo ships
`career/` with schemas, a demo vault (`career/examples/demo/`), and docs. For local convenience
`career/private/` is git-ignored and is the default vault path when the env var is unset. Submodules
were rejected (leak the private remote URL into the public repo, painful agent ergonomics).

---

## 5. Domain model (summary)

Full attribute-level model: [02-domain-model.md](02-domain-model.md).

**Vault aggregates** (YAML, one file per collection): `Profile`, `Experience[]` (with `Role[]`),
`Achievement[]`, `Project[]`, `Skill[]` (with tiers: `first_priority / additional / target`),
`Education[]`, `Certification[]`, `Language[]`, `Publication[]`, `Testimonial[]`, `Link[]`,
`Offer[]`, `Positioning[]`, `ChannelRules[]`, `CVVariant[]`, `ScoringModel`, `Prompt[]`.
Every item: `id`, `status` (`draft|verified|retired`), `visibility` per channel, `evidence[]` refs,
`updated_at`.

**Operational aggregates**: `Opportunity` (+ `OpportunityRaw`, `OpportunityScore`,
`OpportunityAnalysis`), `Company`, `Contact`, `Application` (+ `ApplicationEvent`, `Interview`),
`Message` (+ `Thread`), `ProfileSnapshot` (+ `ProfileAudit`, `AuditFinding`, `DriftFinding`),
`CVArtifact` (+ `GeneratedBullet` with `derived_from[]`), `AIRun`, `Suggestion`, `Action`
(approval-gated outbound), `Task`, `SyncState`, `Note`, `User`.

---

## 6. Monorepo structure — proposed with deviations from the brief

```
career-os/
├── apps/
│   ├── web/                  Next.js (App Router) · P0
│   ├── mobile/               Expo · P2 (placeholder README only in P0)
│   └── desktop/              Tauri v2 · P2 (placeholder README only in P0)
├── services/
│   └── careeros/             ONE Python project (uv) — modular monolith
│       ├── src/careeros/
│       │   ├── core/         config · db · auth · audit · tasks · logging · search
│       │   ├── modules/      vault · cv · opportunities · profiles · ai · platform · pipeline · inbox · insights
│       │   ├── api/          FastAPI app factory, routers mount, OpenAPI export
│       │   ├── worker/       ARQ worker entrypoint (same image, `careeros-worker`)
│       │   └── cli.py        `careeros` CLI: validate-vault, render-cv, export-openapi, seed
│       ├── migrations/       Alembic
│       └── tests/
├── packages/
│   ├── schemas/              GENERATED: JSON Schema (career YAML) + TS types from OpenAPI
│   ├── api-client/           GENERATED TS client (openapi-typescript + thin fetch wrapper)
│   └── ui/                   shared React components + design tokens (web/desktop/mobile-web)
├── career/
│   ├── schemas/              GENERATED JSON Schemas (for yaml-language-server, CI validation)
│   ├── examples/demo/        fake demo vault (public-safe): source/ positioning/ channels/ offers/ prompts/ rendercv/ scoring/
│   ├── prompts/              canonical prompt library (copied into a new vault by `careeros vault init`)
│   ├── templates/            vault scaffold used by `careeros vault init`
│   └── private/              .gitignored — default local vault location
├── generated/                .gitignored build outputs (cv/ profiles/ prompts/ dev-tasks/)
├── config/                   .env templates, compose env, linters (per user conventions)
├── deploy/docker/            Dockerfiles + compose fragments
├── scripts/                  dev helpers (render .env from templates, etc.)
├── docs/{architecture,product,developer-guide,adr}
├── tests/                    cross-cutting e2e (compose smoke, API contract)
├── docker-compose.yml · Makefile · Justfile · README.md · .pre-commit-config.yaml
```

### Deviations from the brief and why

1. **`services/api` + `services/worker` → `services/careeros`** (one Python project, two
   entrypoints). Two directories would either duplicate the domain code or force path hacks between
   sibling projects. Same image, same package, `careeros-api` and `careeros-worker` console scripts;
   compose still shows two services. ([ADR-008](../adr/008-modular-monolith.md))
2. **`packages/{domain,ai,prompts,platform-adapters}` dropped from TS.** AI providers, prompt
   execution and platform adapters need secrets and DB access — they are server-side Python
   modules. Implementing them in TS too would create a second business-logic implementation. TS gets
   *generated* domain types from OpenAPI instead of a hand-maintained `packages/domain`.
   Prompts live in the **vault** (`career/prompts/`), as the brief itself specifies in §21.
3. **Schemas have one source of truth: Pydantic models** in `careeros.modules.vault.schema`,
   exported to `career/schemas/*.schema.json` and `packages/schemas/` by `careeros export-schemas`.
   The brief lists both `career/schemas/` and `packages/schemas/`; they remain, but as generated
   projections. ([ADR-009](../adr/009-schema-source-of-truth.md))
4. **`career/` in the monorepo is scaffold + demo, not the user's vault.** See §4.
5. **Added `config/`, `deploy/`, `scripts/`, `.claude/`** per the repository conventions in the
   user's global instructions.

---

## 7. Key runtime flows

### 7.1 Edit a canonical fact (web)
```
UI edit form ─▶ POST /vault/changes (JSON patch on item) ─▶ Vault.validate(patched_collection)
 ─▶ Vault.diff() ─▶ 200 {diff, warnings} ─▶ user confirms ─▶ POST /vault/changes/{id}/apply
 ─▶ write YAML (ruamel, comment-preserving) ─▶ git commit "career(<collection>): <msg>" ─▶ reindex
```

### 7.2 Generate a CV variant
```
POST /cv/generate {variant, opportunity_id?, provider?}
 ─▶ Vault.load() @ HEAD sha
 ─▶ CV.select_facts(positioning, channel_rules, jd_context)    # deterministic, returns fact IDs
 ─▶ AI.structured(prompt="cv-bullet", input=facts) → bullets[{text, derived_from[]}]  # schema-validated
 ─▶ CV.validate(bullets ⊆ selected facts; no new numbers/companies)                 # provenance guard
 ─▶ RenderCVAdapter.render(cv_model, theme) → pdf, md, json
 ─▶ persist CVArtifact{vault_sha, variant, prompt_version, model, bullets[], files}
```
Background when AI is involved (task runner); synchronous for `--no-ai` rendering.

### 7.3 Ingest and score an opportunity
```
POST /opportunities/ingest {text | url | structured, source}
 ─▶ store OpportunityRaw (immutable)
 ─▶ Parser (deterministic heuristics + AI structured extraction) → Opportunity (normalized)
 ─▶ Dedup (url hash, company+title fuzzy)
 ─▶ Scoring.score(opportunity, vault.skills, scoring_model) → OpportunityScore{dimensions, breakdown, weights_version}
 ─▶ (async) AI.analyze → OpportunityAnalysis{verdict, strengths, gaps, risks, recommended_cv, next_action} + AIRun
 ─▶ Recommendation = f(score, analysis) ∈ {IGNORE, WATCH, APPLY, HIGH_PRIORITY, REPLY_NOW, ASK_QUESTIONS_FIRST, NEGOTIATE, PREPARE_INTERVIEW}
```

### 7.4 External AI handoff (Mode B) and dev-agent packet (Mode C)
Same prompt registry, different renderer: `PromptBundle.render(target="chatgpt"|"claude"|…)` returns
text + optional deep link; `DevTaskPacket.render(agent="claude-code"|"codex"|…)` writes
`generated/dev-tasks/<slug>.md` with Context/Goal/Files/Constraints/Acceptance/Commands/Artifacts.

---

## 8. Technology decisions

| Layer | Choice | Why / notes |
|---|---|---|
| API | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, PostgreSQL 16 + pgvector, Redis | As specified; async throughout; pgvector avoids a second store |
| Tasks | `TaskRunner` port; `InlineRunner` (tests/CLI), `ArqRunner` (Redis) | Temporal later without touching call sites ([ADR-008](../adr/008-modular-monolith.md)) |
| Vault I/O | `ruamel.yaml` (round-trip, comment-preserving) + `git` CLI via subprocess | Avoid GitPython's libgit quirks; the git binary is in the image |
| CV rendering | RenderCV as a library behind `RenderCVAdapter` | Pinned major version; our CV model → RenderCV YAML mapping isolated ([ADR-006](../adr/006-rendercv.md)) |
| AI | `AIProvider` port; adapters: Anthropic SDK, OpenAI-compatible (covers OpenAI, xAI/Grok, Gemini's OpenAI endpoint, Ollama/LM Studio, OpenRouter), native Gemini later | Structured output = JSON schema + Pydantic validation + bounded retry ([ADR-003](../adr/003-ai-provider-abstraction.md)) |
| Web | Next.js 15 App Router, TypeScript, Tailwind, shadcn/ui, TanStack Query, generated client | PWA manifest in P0, service worker P1 |
| Auth (P0) | single user; bearer token from env or session cookie; `user_id` FK everywhere | Swap to OAuth/OIDC for SaaS without schema changes |
| Search | Postgres `tsvector` (P0) + pgvector embeddings (P1) | |
| Observability | structlog JSON logs, request IDs, AI/task telemetry tables, OpenTelemetry hooks optional | |
| Packaging | uv workspace (Python), pnpm workspace (TS), one Dockerfile per language | |
| Quality | ruff, pyright, pytest; eslint, tsc, vitest; pre-commit; GitHub Actions | |

---

## 9. Security posture (P0)

* No platform passwords, ever. OAuth tokens (Gmail, P1) encrypted at rest with a key from env/keychain.
* Secrets only via env (`config/.env.*` templates, never values in git).
* Vault git remote credentials are the host's (`~/.ssh`, mounted read-only into the container) — the app never stores them.
* Audit log table for every write to vault, every AI run, every approved external action.
* Log redaction: AI inputs are hashed in `AIRun` (`inputs_hash`), raw text stored only in the
  dedicated payload column with configurable retention; email bodies never hit application logs.

---

## 10. What P0 delivers (definition of done mapped to modules)

See [04-roadmap.md](04-roadmap.md) for the full P0–P3 split and the 20-point DoD mapping.

---

## 11. Open questions parked (not blocking P0)

* Whether `packages/ui` should be a real package in P0 or fold into `apps/web` until the desktop app exists — start folded, extract when Tauri lands.
* Embedding model choice for semantic search (P1): provider embeddings vs local (`bge-small` via fastembed). Decide with data.
* Gmail: Google app verification for `gmail.readonly` in a personal project — "testing" mode suffices for single user; SaaS needs verification.
