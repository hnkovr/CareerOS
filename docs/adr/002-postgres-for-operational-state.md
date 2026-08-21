# 002 — PostgreSQL (+pgvector) for all operational state

* Status: accepted
* Date: 2026-08-20

## Context

Opportunities, snapshots, audits, AI runs, applications, messages and scores are high-churn
relational data with search, aggregation and linking needs. We also need semantic search and want
to avoid a second datastore. The app must be self-hostable and portable across VPS/Fly/Railway/Render/Cloud Run.

## Decision

* One PostgreSQL 16 database owns all operational state. Schema managed by Alembic; SQLAlchemy 2 async ORM.
* Full-text search via `tsvector`; semantic search via the `pgvector` extension (P1). No separate vector DB.
* Redis is used only as a task queue/broker and ephemeral cache — never as a system of record.
* `user_id` is present on all operational tables from day one (single seeded user in P0) so a
  multi-tenant SaaS does not require a schema rewrite.
* Raw inputs (`opportunity_raw`, snapshot `raw_payload`) are immutable; normalization writes new rows.

## Alternatives considered

* **SQLite for P0** — simpler locally, but pgvector, concurrent worker writes and the SaaS path
  argue for Postgres now; compose makes Postgres equally easy.
* **Dedicated vector DB (Qdrant/Weaviate)** — operational overhead for a personal tool with tens of thousands of rows at most.
* **Storing operational state in the Git vault** — wrong change cadence; commits per email are absurd.

## Consequences

* + One backup story (`pg_dump`), one query language, portable everywhere Postgres runs.
* − Postgres required even for the CLI-only experience; the `Vault` module therefore must work without a DB (validate/render offline).
* Follow-ups: retention policies for raw payloads and AI inputs; encrypted columns for email bodies/tokens (P1).
