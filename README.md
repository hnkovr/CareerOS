# CareerOS — CV-as-Code / Career Data Platform

A personal **Career Operating System**: your career facts live as YAML in a private Git *vault*;
CVs and platform profiles (LinkedIn, Wellfound, Upwork, Toptal, ATS) are *projections* of those
facts; opportunities flow in, get scored transparently and analysed by AI; every AI output is a
suggestion with provenance that **you** approve.

> Career facts are canonical, channel profiles are projections, opportunities are events, AI
> outputs are suggestions with provenance, and external actions remain controllable by the user.

Not a resume builder. Not a job tracker. See [docs/product/00-product-brief.md](docs/product/00-product-brief.md).

## Status

P0 "Career Core" in progress — see [`.claude/TODO.md`](.claude/TODO.md) and the
[roadmap](docs/architecture/04-roadmap.md).

## Quick start

Prerequisites: Docker, `uv` (Python 3.13), `just`, Node 22 (web UI).

```bash
git clone <this repo> career-os && cd career-os
make env                       # render .env from config/*.template (no secrets needed to start)
docker compose up -d postgres redis
uv sync --all-groups
just migrate                   # alembic upgrade head
just seed                      # single user + demo vault pointer
just validate-career           # validates the demo vault at career/examples/demo
just generate-cv general-core  # RenderCV → generated/cv/...
uv run careeros-api            # http://localhost:8000/docs
npm install && npm run dev     # web UI on http://localhost:3000
```

Smoke-test a running stack end to end: `just e2e` (API) or `just e2e http://localhost:8000 http://localhost:3000`.

Full stack in containers: `make up` (adds the web UI on http://localhost:3000).

Point CareerOS at **your** private vault: set `CAREEROS_VAULT_PATH=/path/to/private-career-repo`
(create one with `just vault-init /path`). `career/private/` is git-ignored and is the default.

AI features need a key in `config/.env.secrets` (`CAREEROS_ANTHROPIC_API_KEY` or
`CAREEROS_OPENAI_API_KEY` + `CAREEROS_OPENAI_BASE_URL` for any OpenAI-compatible endpoint).
Without a key the app still validates, renders CVs (non-AI mode), scores opportunities and
produces prompt bundles for external chats.

## Commands

| `make` | purpose |
|---|---|
| `make dev` | infra in docker, API + worker locally with reload |
| `make test` / `make lint` / `make fmt` | quality gate (pytest · ruff · pyright · import-linter · eslint · tsc) |
| `make validate-career` | validate the vault |
| `make generate-cv VARIANT=remote-us` | generate a CV variant |
| `make seed` · `make migrate` · `make openapi` | db + contracts |

Granular recipes: `just --list`.

## Repository layout

```
services/careeros   Python modular monolith — API, worker, CLI (uv)
apps/web            Next.js web app (P0) · apps/mobile, apps/desktop (P2)
packages/           generated TS schemas + API client, shared UI
career/             vault schemas (generated), demo vault, prompt library, scaffold
config/ deploy/     env templates · Dockerfiles, compose fragments
docs/               architecture · ADRs · product · developer guide
```

## Documentation

* [Architecture proposal](docs/architecture/01-architecture-proposal.md) · [Domain model](docs/architecture/02-domain-model.md)
* [Integration capabilities matrix](docs/architecture/03-integration-capabilities-matrix.md)
* [Roadmap P0–P3](docs/architecture/04-roadmap.md) · [Risks](docs/architecture/05-risks.md)
* [ADRs](docs/adr/README.md)
* [Developer guide](docs/developer-guide/README.md)

## Principles (enforced, not aspirational)

* Vault (Git) is the only source of truth for facts; Postgres holds operational state.
* AI may select/summarize/rephrase/combine/project — never invent. Every generated bullet carries `derived_from[]`.
* Scoring is deterministic and explainable; AI interprets it.
* No scraping, no stored platform passwords, no auto-apply, no auto-send.

License: MIT.
