# CareerOS — agent guide

Personal **Career Operating System**: canonical career data in a private Git vault (YAML) →
positioning → channel projections (CV/LinkedIn/Wellfound/Upwork/Toptal) → opportunities → AI
suggestions with provenance → human-approved actions. Not a resume builder, not a job tracker.

Read first: `docs/architecture/01-architecture-proposal.md`, `docs/adr/README.md`,
`docs/product/00-product-brief.md` (requirements SoT).

## Invariants (do not violate)
1. Canonical facts live in the vault (Git). Postgres never becomes the source of truth for a fact.
2. AI may select/summarize/rephrase/combine/project facts — never invent. Every generated bullet
   carries `derived_from[]`; the provenance guard rejects unknown IDs or new numbers/companies.
3. Vault writes go only through `Vault.apply_change()` → validate → diff → commit.
4. Scoring is deterministic over `scoring/model.yaml`; AI only interprets the breakdown.
5. No scraping, no platform passwords/cookies, no auto-apply/auto-send. External writes need an approved `Action`. OAuth tokens (user-granted, revocable) are allowed and live only in the 0600 token file / env (ADR-011).
6. No provider-specific AI code outside `modules/ai/providers/`.
7. Cross-module calls go through `service.py`, never another module's ORM models.
8. AI output is never valid until Pydantic-validated.

## Layout
`services/careeros` — Python modular monolith (uv): `core/`, `modules/{vault,cv,opportunities,profiles,ai,platform,pipeline,inbox,insights}`, `api/`, `worker/`, `cli.py`.
`modules/platform/connectors/<p>` — one connector per platform (hh, upwork, linkedin, wellfound, indeed, getmatch, toptal): own profile · job search · application statuses via api > export > paste (ADR-011); connectors are pure (import-linter) and `PlatformRegistry.verify()` enforces declared ⇒ implemented.
`apps/web` — Next.js. `packages/{schemas,api-client,ui}` — generated/shared TS.
`career/` — schemas (generated), demo vault (`examples/demo`), prompt library, scaffold; `career/private/` is git-ignored.
`config/` — env templates; `deploy/docker/`; `docs/`.

## Commands
`make dev` · `make test` · `make lint` · `make validate-career` · `make generate-cv` · `make seed` — see `Justfile` for granular recipes.

## Conventions
Commits: conventional (`feat(vault): …`, `career(experience): …` for vault data). Python: ruff + pyright strict-ish, pytest, loguru/structlog. TS: eslint, tsc, vitest. Each slice = code + tests + docs + commit.
Session files: `.claude/TODO.md` (next work), `.claude/CLAUDE-curr-status.md` (status), `.claude/PROMPTS-LOG.md`.
