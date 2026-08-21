# careeros (service)

Python modular monolith: FastAPI API, ARQ worker and `careeros` CLI in one package.

```
src/careeros/
  core/      config · db · ids · logging · tasks (runner port) · auth · audit
  modules/   vault · cv · opportunities · profiles · ai · platform · (pipeline · inbox · insights: P1+)
  api/       app factory, router mounting, middleware
  worker/    ARQ entrypoint
  cli.py     typer CLI
migrations/  alembic
tests/
```

Module layout rule: `models.py` (ORM) · `schemas.py` (Pydantic) · `enums.py` · `service.py` (use cases) ·
`router.py` (FastAPI). Other modules import only `service`, `schemas`, `enums` — enforced by import-linter.

Run from the repo root: `make dev`, `make test`, `make lint`. See root `README.md`.
