# 016 — Job provenance, snapshots and identity relations

* Status: accepted
* Date: 2026-08-26
* Deciders: maintainer
* Tracking: design: [Universal Job Intelligence](../superpowers/specs/2026-08-26-universal-job-intelligence-design.md)

## Context

An `Opportunity` today records `source` and `url`; the provider job id lives inside
`raw_payload`, and re-reading a job either creates a second row or is skipped by an exact URL
match, so nothing answers "where did this come from", "what changed since last time" or "is the
RocketHunt copy the same job as the employer posting". The master prompt requires job-level and
field-level provenance, an authority order, kept conflicts, meaningful-change snapshots and layered
deduplication with explicit relations — without a second Job domain model (§23, §62).

## Decision

1. **`Opportunity` stays the canonical job; `OpportunityRaw` becomes its snapshot row.** New
   nullable columns: `opportunity.platform`, `external_id` (indexed with `user_id`), `canonical_url`
   (indexed), `field_evidence` JSONB; `opportunity_raw.opportunity_id`, `fingerprint`, `strategy`,
   `fetched_url`, `resolved_url`, `is_archive`, `archive_ts`, `quality`, `extracted` JSONB. A new raw
   is written only when the normalised fingerprint changed; `diff` is computed from `extracted`.
2. **`opportunity_source` records provenance per (job, source):** `platform, external_id,
   source_url, canonical_url, original_url, relation, authority, strategy, raw_id, fetched_at,
   published_at, content_hash, is_archive, confidence`. Relations: `primary | aggregates |
   repost_of | same_as | mirror | historical_version_of | possible_duplicate`.
3. **Authority order is a guideline, conflicts are kept.** employer ATS/API > employer page >
   native board structured source > current HTML > aggregator > archive > search result > LLM
   inference. `field_evidence` keeps every observed value with its source and confidence; the
   displayed value is the highest-authority one; aggregator estimates (e.g. RocketHunt salary) are
   labelled `aggregator_estimate` and never shown as employer facts.
4. **Layered identity, never automatic merge of weak matches:** `(platform, external_id)` →
   normalised `canonical_url` → company + title + location → fingerprint → fuzzy
   (`similarity ≥ 92`) → semantic similarity (suggestion only). `find_opportunity_id_by_url`
   normalises URLs like `dedup_key` does.
5. **Backfill without duplicates:** every existing job gets one `primary` source row and its raw
   gets `opportunity_id`; ids, applications, scores, notes and CV links are preserved.

## Alternatives considered

* **A separate `Job`/`Snapshot` model next to `Opportunity`** — two competing domains; rejected.
* **Field-level evidence as a table now** — over-engineering for a single-user store; JSONB first,
  table later if queried.
* **Last-write-wins on refresh** — loses history and hides salary/closure changes; rejected.

## Consequences

* `GET /api/opportunities/{id}/sources|snapshots|diff`, `POST …/refresh`; CLI equivalents.
* A refresh scheduler can exist (ARQ cron behind `CAREEROS_JOB_REFRESH_ENABLED`) with polite
  per-provider intervals.
* Raw retention becomes configurable (`CAREEROS_JOB_FETCH_RAW_RETENTION_DAYS`).
