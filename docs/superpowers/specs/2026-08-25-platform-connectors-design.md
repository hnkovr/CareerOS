# Platform connectors — design (2026-08-25)

Status: draft written autonomously; **review requested** (see "Decisions taken without the owner").
Scope: `careeros.modules.platform` — the integration layer named in ADR-004 — with one connector
submodule per platform: **hh.ru, Upwork, LinkedIn, Wellfound, Indeed, getmatch, Toptal**.
Each connector exposes three read-only capabilities: **read own profile**, **search jobs**,
**check application statuses**.

Related: [ADR-004](../../adr/004-platform-adapter-model.md) (adapter model),
[ADR-005](../../adr/005-no-autonomous-platform-scraping.md) (no scraping / no credential storage),
[capabilities matrix](../../architecture/03-integration-capabilities-matrix.md),
[roadmap P2](../../architecture/04-roadmap.md). Decisions specific to this slice are recorded in
[ADR-011](../../adr/011-platform-connectors.md).

## 1. Goals / non-goals

Goals
* One `PlatformConnector` contract; a `PlatformRegistry` that exposes the capabilities matrix at
  `GET /api/platform/capabilities` (ADR-004 promise, not yet implemented before this slice).
* Per platform, the *highest legitimately available* method for each capability
  (official API > official export > email > user-initiated capture > manual paste), and a
  first-class **paste** path for every platform so the product works on day one without any API.
* Results land in the existing domain contexts: profile → `profiles` snapshot (then auditable),
  jobs → `opportunities` ingest (dedup + deterministic scoring), application statuses → new
  `application_observation` rows (consumed by the P1 Pipeline/Kanban later).
* CLI + API + Justfile recipes; a project sub-agent that operates the connectors; docs.

Non-goals (unchanged by this slice)
* No scraping, headless browsers, cookie/password storage, CAPTCHA bypass, auto-apply — ADR-005.
* No profile *writes* to any platform. `write_profile` stays `none`/`manual_assist`.
* No email ingestion yet (P1 Inbox) — connectors declare `email_fallback` so the UI is honest.
* No web UI page in this slice (only the two hard-coded platform lists in the web app are
  extended so existing pages stay consistent with the new enums).

## 2. Capability matrix (working assumptions — verify each API cell at connect time)

| Platform | read_profile | search_jobs | application_statuses | Auth | Notes |
|---|---|---|---|---|---|
| **hh.ru** | **api** `GET /resumes/mine`, `GET /resumes/{id}` | **api** `GET /vacancies?text=…&area=…` (public, `HH-User-Agent` required) + `GET /resumes/{id}/similar_vacancies` | **api** `GET /negotiations` (states: `response`, `invitation`, `discard`) | OAuth2 code flow (`hh.ru/oauth/authorize`, `api.hh.ru/token`; access 14 d, refresh single-use) | Full official API. Search works unauthenticated; profile/negotiations need the user token. |
| **Upwork** | **api** GraphQL `https://api.upwork.com/graphql` (own user + freelancer profile) — *conditional on API-key approval*; paste fallback | **api** `marketplaceJobPostingsSearch` — conditional; paste fallback | **api** proposals/offers queries — conditional; paste fallback | OAuth2 code flow (`upwork.com/ab/account-security/oauth2/authorize`, `upwork.com/api/v3/oauth2/token`) | GraphQL field names must be verified against the live schema (`careeros platform doctor upwork` runs a minimal introspection). |
| **LinkedIn** | **export** "Download your data" archive: `Profile.csv`, `Positions.csv`, `Skills.csv`, `Education.csv`, `Certifications.csv`, `Projects.csv`, `Languages.csv`; paste fallback | **manual** paste of job list/page; `Saved Jobs.csv` from the archive imported as `watching` | **export** `Job Applications.csv`; paste of "My jobs → Applied" | none (archive is user-downloaded; `Sign In with LinkedIn` gives only name/email — not used) | No job/search API for normal apps. |
| **Wellfound** | manual paste | manual paste (job list / job page) · email P1 | manual paste ("Applications") · email P1 | none | No public API; site is JS+Cloudflare — never fetched. |
| **Indeed** | manual paste (Indeed profile/resume text) | manual paste · email (job alerts) P1 | manual paste ("My jobs → Applied": *Applied · Viewed by employer · Interviewing · Not selected*) · email P1 | none | Publisher API discontinued. |
| **getmatch** | manual paste | manual paste · email digests P1 | manual paste · email P1 | none | No public API. |
| **Toptal** | manual paste (public/talent profile text) | manual paste (talent portal jobs) | manual paste · email P1 | none | No API/export. |

`apply` = `none` everywhere. `write_profile` = `none` (the profiles audit produces the text; the
user pastes it). `official_api` true only for hh.ru and Upwork. `email_fallback` true for every
platform except hh.ru (API covers it). `manual_capture` true for all.

## 3. Module layout

```
services/careeros/src/careeros/modules/platform/
  __init__.py
  enums.py        CapabilityLevel, AuthKind, SyncKind, SyncMethod, SyncStatus,
                  ConnectionStatus, ApplicationStatus (normalized), PROFILE_PLATFORMS
  schemas.py      Capabilities, JobQuery, JobPosting, ProfileRead, ApplicationObservationIn,
                  ConnectionOut, SyncRequest, SyncResult, ParseRequest/ParseResult, OAuthStart …
  base.py         BaseConnector (ABC with NotSupported defaults), ConnectorContext, errors
  registry.py     PlatformRegistry: discovers connectors/<platform>/connector.py, capabilities()
  tokens.py       OAuthTokens, TokenStore (file, 0600, git-ignored) + env overlay, resolve()
  oauth.py        generic OAuth2 code-flow helper (authorize URL, exchange, refresh; PKCE-less)
  http.py         shared httpx.AsyncClient factory (timeouts, UA, retries on 429/5xx, no redirects
                  to non-https), transport injection for tests
  models.py       PlatformConnection, PlatformSyncRun, ApplicationObservation (ORM)
  service.py      PlatformService: capabilities, connections (no secrets), sync runs, observations
  sync.py         PlatformSyncService: orchestrates connector → ProfileService / OpportunityService
                  / observations; picks method by precedence; dry-run support
  router.py       /api/platform/*
  cli.py          careeros platform …
  parsers.py      shared paste heuristics (dates, "Applied on", currency, company/title splitting)
  connectors/
    __init__.py   CONNECTOR_MODULES tuple (one per Platform handled here)
    hh/           connector.py, client.py (REST), mapping.py
    upwork/       connector.py, client.py (GraphQL), queries.py, mapping.py
    linkedin/     connector.py, export.py (archive CSV/ZIP), parsers.py
    wellfound/    connector.py, parsers.py
    indeed/       connector.py, parsers.py
    getmatch/     connector.py, parsers.py
    toptal/       connector.py, parsers.py
tests/platform/   test_core.py (registry, tokens, oauth, sync w/ fake connector, API),
                  test_<platform>.py + fixtures/<platform>/ (recorded/synthetic payloads, pastes)
docs/platform/    README.md (how to connect / sync / paste) + <platform>.md per connector
```

Import rule (new import-linter contract): `careeros.modules.platform.connectors` may import only
`careeros.modules.platform.{base,enums,schemas,parsers,http,tokens}`, `careeros.core.config`,
`careeros.core.logging`, `careeros.modules.vault.enums`, `careeros.modules.opportunities.{enums,schemas}`,
`careeros.modules.profiles.{enums,schemas}` — never `sqlalchemy`, `careeros.core.db`, any
`service.py` or `models.py`. Connectors are pure I/O + mapping (ADR-004).

## 4. Contract

```python
class BaseConnector(ABC):
    platform: Platform
    capabilities: Capabilities                # static, declared per connector

    # ---- profile
    async def read_profile(self, ctx: ConnectorContext) -> ProfileRead            # api tier
    def import_profile_export(self, path: Path) -> ProfileRead                    # export tier
    def parse_profile_text(self, text: str) -> ProfileRead                        # manual tier
    # ---- jobs
    async def search_jobs(self, ctx, query: JobQuery) -> list[JobPosting]         # api tier
    def import_jobs_export(self, path: Path) -> list[JobPosting]                  # export tier
    def parse_jobs_text(self, text: str) -> list[JobPosting]                      # manual tier
    # ---- applications
    async def application_statuses(self, ctx) -> list[ApplicationObservationIn]  # api tier
    def import_applications_export(self, path) -> list[ApplicationObservationIn] # export tier
    def parse_applications_text(self, text) -> list[ApplicationObservationIn]    # manual tier
    # ---- auth / health
    def oauth_config(self, settings) -> OAuthConfig | None                        # api platforms
    async def whoami(self, ctx) -> AccountInfo                                    # cheap probe
    async def doctor(self, ctx) -> list[DoctorCheck]                              # config+token+API
```

Defaults raise `CapabilityUnavailable(platform, capability, method)`; the registry cross-checks
that every declared level ≥ `manual` has the matching method overridden (test in `test_core.py`).

DTOs (all Pydantic; nulls = "not stated"; every DTO keeps `raw_payload`/`raw_text` verbatim):
* `ProfileRead` — mirrors `profiles.schemas.SnapshotIn` fields (headline, about, experience[],
  skills[], projects[], portfolio[], rates, availability, preferences) + `capture_method`,
  `external_id`, `profile_url`. Mapped 1:1 to `SnapshotIn` by the sync service.
* `JobPosting` — `external_id`, `url`, `title`, `company`, `location`, `posted_at`, `raw_text`,
  `extraction: OpportunityExtraction | None` (structured fields when the API provides them),
  `source: opportunities.enums.Source`. Mapped to `IngestRequest(source, url, text, structured,
  received_at)` — dedup and scoring are the opportunities module's job, untouched.
* `ApplicationObservationIn` — `external_id`, `job_title`, `company`, `job_url`, `status_raw`,
  `status` (normalized `ApplicationStatus`: applied · viewed · invited · interview · offer ·
  rejected · withdrawn · unknown), `applied_at`, `updated_at_platform`, `raw_payload`.
* `JobQuery` — `text`, `location`, `remote: bool|None`, `salary_min`, `currency`, `posted_since`,
  `limit` (≤100), `extra: dict` (platform-specific: hh `area`, `schedule=remote`; upwork
  `category`, `sort`).

`ConnectorContext` — `settings`, `http: httpx.AsyncClient`, `tokens: OAuthTokens | None`,
`now: datetime`. Built by `PlatformSyncService`; tests build it with `httpx.MockTransport`.

## 5. Auth & tokens

* OAuth2 access/refresh tokens are **permitted** (user-granted, scoped, revocable — not passwords
  or session cookies; ADR-011 records this reading of ADR-005). Stored in
  `settings.platform_token_file` (default `generated/platform/tokens.json`, git-ignored, chmod 600),
  overridable per platform by env (`CAREEROS_HH_ACCESS_TOKEN`, `CAREEROS_UPWORK_ACCESS_TOKEN`, …)
  for container deployments. Never logged (structlog redaction already masks `*_token`).
* Client credentials (`CAREEROS_HH_CLIENT_ID/SECRET`, `CAREEROS_UPWORK_CLIENT_ID/SECRET`) live in
  `.env.secrets` via `config/.env.secrets.demo.template` placeholders (blank / `${VAR:-}` only).
* Flow: `careeros platform connect hh` → prints authorize URL → user logs in in *their* browser →
  pastes the `code` (or the redirect hits `GET /api/platform/oauth/{platform}/callback`) →
  exchange → tokens saved → `platform_connection` upserted with `whoami()` info (no secrets).
  `careeros platform refresh <p>` / automatic refresh on 401 when a refresh token exists.
* `platform_connection` keeps only non-secret state: status, account id/label, scopes,
  `token_expires_at`, `last_sync_at`, `last_error`.

## 6. Persistence (new tables, one Alembic migration)

| Table | Key fields |
|---|---|
| `platform_connection` | `user_id`, `platform` (unique per user), `status` (disconnected\|connected\|needs_reauth\|error), `auth_kind`, `account_id?`, `account_label?`, `scopes jsonb`, `token_expires_at?`, `last_sync_at?`, `last_error?`, `meta jsonb` |
| `platform_sync_run` | `user_id`, `platform`, `kind` (profile\|jobs\|applications), `method` (api\|export\|paste), `status` (ok\|partial\|failed), `started_at`, `finished_at`, `items_seen`, `items_created`, `items_updated`, `items_skipped`, `error?`, `details jsonb` (ids created, duplicates) |
| `application_observation` | `user_id`, `platform`, `external_id?`, `job_title`, `company?`, `job_url?`, `status_raw`, `status`, `applied_at?`, `updated_at_platform?`, `observed_at`, `opportunity_id?` (matched by url/dedup, no FK), `sync_run_id FK?`, `content_hash`, `raw_payload jsonb` |

Upsert key for observations: `(user_id, platform, external_id)` when `external_id` is present,
else `(user_id, platform, content_hash)`; a changed `status` updates the row and records the
previous status in `raw_payload.history[]`.

## 7. Enum changes (additive)

* `vault.enums.Platform` += `hh`, `indeed`, `getmatch` (Visibility keeps defaulting to `true`;
  `Offer.platforms`/`ChannelRules.platform` accept them). Demo vault gains `channels/hh.yaml`,
  `channels/indeed.yaml`, `channels/getmatch.yaml`. `just export-schemas` regenerates
  `career/schemas/*.json` + `packages/schemas` (CI checks freshness).
* `opportunities.enums.Source` += `hh`, `indeed`, `getmatch`.
* `profiles.enums.PROFILE_PLATFORMS` (new) drives `ProfileService.platform_health()` and the web
  `PLATFORMS` list instead of the hard-coded four.

## 8. API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/platform/capabilities` | matrix (ADR-004) |
| GET | `/api/platform/connections` | per-platform connection state (no secrets) |
| POST | `/api/platform/{platform}/connect` | → `{authorize_url, state}` |
| GET | `/api/platform/oauth/{platform}/callback?code&state` | exchange + save + upsert connection |
| DELETE | `/api/platform/{platform}/connection` | delete tokens + mark disconnected |
| GET | `/api/platform/{platform}/doctor` | config/token/API checks |
| POST | `/api/platform/{platform}/parse/{kind}` | dry parse of pasted text / export → DTOs, nothing persisted |
| POST | `/api/platform/{platform}/sync/{kind}` | `SyncRequest{method?, text?, file_path?, query?, use_ai?, dry_run?}` → `SyncResult` |
| GET | `/api/platform/sync-runs?platform&kind&limit` | history (dashboard "Sync Status") |
| GET | `/api/platform/applications?platform&status` | observations |

Errors: `CapabilityUnavailable` → 409 with the capability level and the suggested method;
`NotConnected` → 401-ish 409 `{"detail": "connect first: careeros platform connect hh"}`;
upstream HTTP errors → 502 with platform + status; parse failures → 422.

## 9. CLI (`careeros platform …`) and Justfile

`capabilities` · `connections` · `connect <p>` · `refresh <p>` · `disconnect <p>` · `doctor <p>` ·
`profile <p> [--api|--export PATH|--text-file F] [--use-ai] [--dry-run] [--json]` ·
`jobs <p> [--query "…"] [--limit N] [--export PATH|--text-file F] [--dry-run] [--json]` ·
`applications <p> [--api|--export PATH|--text-file F] [--dry-run] [--json]` ·
`sync <p|all> [--dry-run]` (best available method per capability; paste-only platforms are
reported as `skipped: needs paste`). `--dry-run` never touches the DB (like `profiles audit-file`).

Justfile (dry/apply pairs share one `_platform` helper): `platform-capabilities`,
`platform-connect P`, `platform-doctor P`, `platform-sync P="all"` / `platform-sync-dry`,
`platform-profile P *ARGS` / `-dry`, `platform-jobs P *ARGS` / `-dry`,
`platform-applications P *ARGS` / `-dry`, `test-platform`. Makefile: `make platform-sync`.

## 10. Agents & skill (deliverables, not just build tooling)

* `.claude/agents/careeros-platform-ops.md` — operator: connect/doctor/sync, paste-capture
  coaching, status reporting; refuses scraping/credential requests and routes to the paste path.
* `.claude/agents/careeros-platform-connector-dev.md` — builder used for this slice's parallel
  fan-out; reusable for future connectors (ATS boards, IMAP). Encodes the contract, the
  import rule, the fixture policy and the gates.
* Skill `careeros-platform-sync` created through `/create-skill` (policy) wrapping the Justfile
  recipes; if the canonical-catalog flow cannot complete unattended, a project candidate is
  recorded instead and reported.

## 11. Testing

* Connector unit tests never hit the network: `httpx.MockTransport` with fixture JSON under
  `tests/platform/fixtures/<platform>/`; paste parsers tested on realistic copied text
  (synthetic persona — no real people/companies); LinkedIn export tested on a synthetic ZIP.
* `test_core.py`: registry completeness (declared level ⇒ method overridden), token store
  round-trip + permissions + env overlay, OAuth exchange/refresh with mock transport, sync
  orchestration with a `FakeConnector` (profile → snapshot, jobs → opportunities with dedup,
  applications → observations upsert + status history), API smoke (`@pytest.mark.db`).
* Live checks are opt-in only: `@pytest.mark.live` skipped unless `CAREEROS_PLATFORM_LIVE=1`
  and tokens exist (not run in CI).
* Gates unchanged: `uv run pytest`, `just lint` (ruff, pyright, import-linter incl. the new
  contract, env-template check), demo-vault validation, schema freshness.

## 12. Decisions taken without the owner (please confirm or override)

1. **OAuth tokens are stored** (file 0600 / env) — read as compatible with ADR-005's ban on
   passwords/cookies. Alternative: env-only tokens (no refresh persistence).
2. **No fetching of platform HTML at all** (not even single job pages) — only official JSON APIs
   and user-supplied exports/pastes. Alternative: single-page user-initiated fetch (ADR-004's
   ATS precedent) — deferred; can be added as a `PublicPageCapture` helper later.
3. `application_observation` lives in the platform module until P1 Pipeline exists.
4. `PlatformSyncService` (platform → profiles/opportunities *services*) is allowed; connectors
   remain pure and that is enforced by import-linter. Context map updated accordingly.
5. New enum values (`hh`, `indeed`, `getmatch`) rather than mapping them onto `other`.
