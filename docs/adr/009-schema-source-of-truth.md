# 009 — Pydantic models are the schema source of truth; JSON Schema and TS are generated

* Status: accepted
* Date: 2026-08-20

## Context

Career YAML needs editor validation (yaml-language-server), CI validation, API serialization and TS
types. Maintaining JSON Schema, Pydantic and TS by hand guarantees drift — the very problem the
product fights in career profiles.

## Decision

* Canonical schemas for vault collections are **Pydantic v2 models** in
  `careeros.modules.vault.schema`. Operational API schemas are Pydantic models in each module's `schemas.py`.
* `careeros export-schemas` writes `career/schemas/*.schema.json` (from the vault models) and the
  OpenAPI document; `pnpm generate` turns OpenAPI into `packages/schemas` TS types and
  `packages/api-client`. CI fails if generated files are stale.
* Vault YAML files carry a `# yaml-language-server: $schema=...` header pointing at the generated schema.
* Enumerations shared across languages are defined once in Python and flow through OpenAPI.

## Alternatives considered

* **JSON Schema first, generate Pydantic (datamodel-codegen)** — generated Pydantic is awkward to extend with validators and cross-references.
* **TypeScript/Zod first** — server is Python; validation must happen where writes happen.

## Consequences

* + One place to change a field; validators (referential integrity) live next to the schema.
* − Generated artifacts are committed and must be refreshed; CI enforces it.
