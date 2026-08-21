# Developer guide

## Setup

```bash
make env && docker compose up -d postgres redis && uv sync --all-groups && just migrate && just seed
uv run pre-commit install
```

Run: `make dev` (API with reload on :8000 + worker). Tests: `make test`. Quality gate: `make lint`.

Tests that need Postgres use `CAREEROS_TEST_DATABASE_URL` (defaults to the compose instance's
`careeros_test` database) and are marked `@pytest.mark.db`; they are skipped when the DB is
unreachable. Tests that call a real AI provider are marked `@pytest.mark.ai` and run only with
`CAREEROS_AI_LIVE=1`.

## Adding a module slice

1. `services/careeros/src/careeros/modules/<name>/` with `enums.py`, `schemas.py`, `models.py`
   (if it owns tables), `service.py`, `router.py`, optional `cli.py`, `tasks.py`.
2. Register the router in `careeros/api/routers.py`, the CLI in `careeros/cli.py`, models in
   `migrations/env.py`, tasks in `worker/main.py`.
3. `just migration "<message>"` → review the generated file → `just migrate`.
4. Tests next to the module in `services/careeros/tests/<name>/`.
5. Import rule: other modules may import only `service`, `schemas`, `enums` of your module
   (import-linter contracts in the root `pyproject.toml`).
6. Docs: update `docs/architecture/02-domain-model.md` if tables/enums changed; ADR for any
   boundary decision.

## Configuration

All settings: `services/careeros/src/careeros/core/config.py` (`CAREEROS_*`). Templates in
`config/`; `scripts/env-render.py` renders `.env` and `config/.env.docker`. Never put literal
secrets in templates (`make lint` checks).

## Conventions

Commits: conventional commits (`feat(vault): …`, `fix(api): …`, `docs: …`, `career(experience): …`
is reserved for vault data commits made by the app). Python: ruff (100 cols), pyright, pytest-asyncio
auto mode. TS: eslint, tsc strict, vitest.
