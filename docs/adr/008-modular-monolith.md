# 008 — Modular monolith in one Python project; task-runner port (ARQ now, Temporal-ready)

* Status: accepted
* Date: 2026-08-20

## Context

The brief asks for `services/api` and `services/worker`, low operational complexity, no
microservices for their own sake, ARQ-style workers, and a path to Temporal. Two separate Python
projects sharing a domain would duplicate code or require fragile cross-project imports.

## Decision

* One Python project, `services/careeros` (uv), one package `careeros`, organized as **bounded-context
  modules** under `careeros.modules.*`, each with `models / schemas / service / router / tests`.
  Cross-module access goes through `service.py` interfaces only (enforced by an import-linter contract).
* Two process entrypoints from the same image: `careeros-api` (FastAPI/uvicorn) and
  `careeros-worker` (ARQ). Compose shows them as `api` and `worker` services.
* Background work goes through a `TaskRunner` port (`enqueue(name, payload, *, delay=None) → TaskId`;
  `InlineTaskRunner` for tests/CLI, `ArqTaskRunner` on Redis). Task handlers are plain async
  functions registered by name; no ARQ types leak outside `core/tasks`. Temporal can replace the
  runner implementation without changing call sites; a `task` ledger table already records state.
* Celery is not used.

## Alternatives considered

* **Separate `api/` and `worker/` projects** — duplicated domain, double dependency management.
* **Microservices per context** — premature; personal tool with one operator.
* **Celery** — heavier, sync-first; ARQ fits the async stack.
* **Temporal from day one** — extra server + SDK learning curve before there is a workflow that needs durability.

## Consequences

* + Single deploy unit, shared types, simple local dev, trivial refactors across contexts.
* − Module boundaries must be policed by tooling (import-linter) rather than by network boundaries.
