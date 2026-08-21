# 001 — Git repository as the canonical career source of truth

* Status: accepted
* Date: 2026-08-20

## Context

Career facts (experience, achievements, projects, skills, positioning, offers, prompts, scoring
weights) change rarely, must be reviewable, diffable, reproducible and portable, and must never be
silently mutated by AI. Operational state (opportunities, messages, runs) changes constantly and
needs queries, joins and indexes. Mixing both in one store blurs who may write what.

The user's real data is private; this repository may become public or multi-tenant.

## Decision

1. Canonical career data lives in a **Git repository of YAML (+Markdown) files** — the *vault* —
   with stable human-readable IDs on every item. The application reads it via the `Vault` module,
   validates it against Pydantic schemas, and writes it only through
   `validate → diff → (approval) → commit`, producing conventional commit messages
   (`career(experience): add PDP GitLab CI achievement`).
2. The vault is a **separate private repository**, located by `CAREEROS_VAULT_PATH`. This monorepo
   ships schemas, a synthetic demo vault (`career/examples/demo/`) and `careeros vault init`
   scaffolding. `career/private/` is git-ignored and is the default path for local single-user use.
3. Every generated artifact records the vault commit SHA it was produced from.
4. A Postgres `vault_index` table may cache vault content for search; it is rebuilt from Git and is
   never written to directly.

## Alternatives considered

* **Everything in Postgres with an audit table** — loses free diff/review/branching, makes the data
  hostage to the app, and makes "AI may not silently change facts" a policy rather than a mechanism.
* **Git submodule for the private vault** — leaks the private remote URL into `.gitmodules` of a
  potentially public repo; submodule workflows are error-prone for humans and agents alike.
* **Markdown-only vault** — not machine-validatable; YAML with JSON Schema gives editor support and CI validation.

## Consequences

* + Reviewable history, reproducible CVs, trivial backup, agent-friendly (coding agents can edit YAML under schema validation).
* + Clear write authority: only `Vault.apply_change` commits; AI proposals are `Suggestion` rows.
* − The API container needs `git` and a writable vault mount; concurrent edits need a lock (single-writer per vault).
* − YAML merge conflicts are possible if the user edits the vault outside the app while the app holds pending changes; the app refuses to apply a change whose base SHA is stale.
* Follow-ups: vault init/scaffold CLI; `vault_index` sync job; documentation for pointing the app at an existing private repo.
