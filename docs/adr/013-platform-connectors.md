# 011 — Platform connectors: one submodule per platform, OAuth tokens allowed, sync placement

* Status: accepted
* Date: 2026-08-25
* Tracking: [#10](https://github.com/hnkovr/CareerOS/issues/10) · [MY-26](https://linear.app/my-1st/issue/MY-26)

## Context

[ADR-004](004-platform-adapter-model.md) promised a `careeros.modules.platform` port with a
capabilities matrix; [ADR-005](005-no-autonomous-platform-scraping.md) forbids scraping, credential
storage and auto-apply. Neither was implemented in P0, while the owner needs three read-only
capabilities on seven platforms — **hh.ru, Upwork, LinkedIn, Wellfound, Indeed, getmatch, Toptal**:
read own profile, search jobs, check application statuses. Only hh.ru offers a full official API;
Upwork's GraphQL API is conditional on an approved API key; LinkedIn offers an official data export;
Wellfound, Indeed, getmatch and Toptal offer neither an API nor an export.

## Decision

1. **One connector submodule per platform** under `modules/platform/connectors/<platform>/`,
   implementing `BaseConnector` (`base.py`). Each declares `Capabilities` as the *list of
   implemented methods* per capability (`profile`, `jobs`, `applications` × `api | export | paste`);
   the ADR-004 level names (`read_profile`, …) are derived from those lists, and
   `PlatformRegistry.verify()` fails the test suite if a declared method is not implemented.
   The matrix is served by `GET /api/platform/capabilities` and `careeros platform capabilities`.
2. **Every platform gets a first-class paste path.** Pasted page text (profile, job list,
   applications list) is parsed by platform-specific heuristics on top of shared EN/RU parsers;
   unknown values stay `None`; raw text is kept verbatim. This is what ADR-005 demands the product
   be excellent at, and it works on day one without any API.
3. **Precedence stays api > export > paste** (`PlatformSyncService.choose_method`); the user may
   force a method. `--dry-run` / `parse` never touch the database.
4. **OAuth2 access/refresh tokens are permitted and stored** — they are user-granted, scoped and
   revocable, unlike the passwords and session cookies ADR-005 bans. They live in a git-ignored
   file created with mode 0600 (`CAREEROS_PLATFORM_TOKEN_FILE`, default
   `generated/platform/tokens.json`), overridable per platform via `CAREEROS_<PLATFORM>_ACCESS_TOKEN`
   for containers. `platform_connection` rows never hold secrets. Client credentials live in
   `.env.secrets`. Logs redact `*_token`.
5. **No HTML is fetched from any platform** — not even single job pages — in this slice. Only
   documented JSON/GraphQL endpoints, user-downloaded exports and pastes. A user-initiated single
   job-URL fetch (the ADR-004 ATS precedent) is a possible follow-up
   ([#21](https://github.com/hnkovr/CareerOS/issues/21)).
6. **Sync orchestration lives in the platform module** (`sync.py`) and is the *only* place that
   calls domain services: profile → `ProfileService.create_snapshot`, jobs →
   `OpportunityService.ingest` (dedup + deterministic scoring unchanged; URLs already ingested are
   skipped), application statuses → `application_observation` rows owned by the platform module
   until the P1 Pipeline consumes them. Connectors remain pure I/O + mapping — enforced by an
   import-linter contract (`connectors` may not import SQLAlchemy, the DB, or any service/model
   module). The context map of the architecture proposal gains the edge *Platform(sync) → Profiles,
   Opportunities*.
7. **Enums are extended, not overloaded:** `Platform` += `hh`, `indeed`, `getmatch`; `Source`
   likewise; `profiles.PROFILE_PLATFORMS` replaces hard-coded platform tuples.

## Alternatives considered

* **Tokens only in environment variables** — no refresh persistence; every 14 days the owner would
  re-run the flow by hand. Rejected for UX; env override kept for deployments.
* **Single-page fetch of public job pages for the API-less platforms** — closest to the ATS
  precedent, but brittle (JS/Cloudflare) and hard to keep inside ADR-005's spirit. Deferred.
* **Application statuses straight into a Pipeline `Application` table** — Pipeline (P1) did not
  exist when this slice was designed; observations are raw platform facts that Pipeline can link
  to later without loss.

## Consequences

* + Honest, testable matrix; seven connectors with identical CLI/API ergonomics; no network in
  tests (fixtures + `httpx.MockTransport`).
* + hh.ru is fully automated once connected; Upwork once the API key is approved (its GraphQL
  field names are marked `VERIFY LIVE` and checked by `careeros platform doctor upwork`).
* − LinkedIn/Wellfound/Indeed/getmatch/Toptal stay manual (export/paste) until email ingestion (P1)
  adds notification-based statuses; paste heuristics need occasional upkeep when pages change.
* − Repeated pastes without URLs can create `possible_duplicate_of` rows (opportunities' existing
  paste semantics); API results with stable URLs are deduplicated by URL.
