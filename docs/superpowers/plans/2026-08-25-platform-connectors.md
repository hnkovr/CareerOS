# Platform Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `careeros.modules.platform` — registry + capabilities matrix, OAuth/token plumbing, sync orchestration, API/CLI/Justfile — and seven connector submodules (hh.ru, Upwork, LinkedIn, Wellfound, Indeed, getmatch, Toptal) that read the user's own profile, search jobs and check application statuses through the highest legitimate method each platform offers.

**Architecture:** Connectors are pure I/O + mapping classes (`BaseConnector`) discovered by `PlatformRegistry`; `PlatformSyncService` picks the best method (api > export > paste), runs the connector and hands results to the existing `ProfileService` / `OpportunityService` or to the new `application_observation` table. Tokens live in a git-ignored 0600 file (env overrides); no scraping, no passwords, no writes to platforms (ADR-005).

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLAlchemy async + Alembic, httpx (`MockTransport` in tests), typer, pytest-asyncio, ruff/pyright/import-linter.

**Spec:** `docs/superpowers/specs/2026-08-25-platform-connectors-design.md`

## Global Constraints

- ADR-005: no scraping, no headless browsers, no cookie/password storage, no CAPTCHA bypass, no auto-apply, no writes to platforms. Connectors call only documented JSON/GraphQL APIs, read user-downloaded exports, or parse pasted text.
- Import rule: `careeros.modules.platform.connectors.*` never imports `sqlalchemy`, `careeros.core.db`, any `service.py`/`models.py` (import-linter contract added in Task 1).
- Every DTO keeps the raw source (`raw_text` / `raw_payload`) verbatim; parsers never invent values — unknown = `None`.
- Tests never touch the network; live tests are `@pytest.mark.live` and skipped unless `CAREEROS_PLATFORM_LIVE=1`.
- Fixtures use a synthetic persona (no real people, companies, tokens). Secrets in env templates are blank or `${VAR:-}` only.
- Gates before every commit: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports && uv run pytest` (Postgres from `docker compose` is up locally; DB tests are `@pytest.mark.db`).
- Conventional commits: `feat(platform): …`; connector commits `feat(platform/hh): …`.
- Python style of the repo: `from __future__ import annotations`, structlog `get_logger(__name__)`, 100-col lines, StrEnum, Pydantic models with `Field(default_factory=list)` and `_none_to_list` validators for lists.

---

### Task 0: Enum extensions + generated schemas + web lists

**Files:**
- Modify: `services/careeros/src/careeros/modules/vault/enums.py` (`Platform` += `hh`, `indeed`, `getmatch`)
- Modify: `services/careeros/src/careeros/modules/opportunities/enums.py` (`Source` += `hh`, `indeed`, `getmatch`)
- Modify: `services/careeros/src/careeros/modules/profiles/enums.py` (add `PROFILE_PLATFORMS`)
- Modify: `services/careeros/src/careeros/modules/profiles/service.py:237` (iterate `PROFILE_PLATFORMS`)
- Create: `career/examples/demo/channels/{hh,indeed,getmatch}.yaml`
- Modify: `apps/web/src/app/profiles/page.tsx:10`, `apps/web/src/app/opportunities/page.tsx:11`
- Regenerate: `career/schemas/*.json`, `packages/schemas/src/api.d.ts` (`just export-schemas`, `just openapi`)
- Modify: `docs/architecture/02-domain-model.md` §3 enum lines

**Interfaces:**
- Produces: `Platform.hh / Platform.indeed / Platform.getmatch`, `Source.hh / Source.indeed / Source.getmatch`, `profiles.enums.PROFILE_PLATFORMS: tuple[Platform, ...] = (linkedin, wellfound, upwork, toptal, hh, indeed, getmatch)`.

- [ ] Step 1: Add the enum members; add `PROFILE_PLATFORMS` to `profiles/enums.py`; replace the tuple in `ProfileService.platform_health` with `PROFILE_PLATFORMS`.
- [ ] Step 2: Add three demo channel files modelled on `career/examples/demo/channels/upwork.yaml` (hh: headline 100 chars, priorities `[title_keywords, salary_expectation, skills, experience_relevance, about]`, cta none; indeed: `[title_keywords, location, skills, summary]`; getmatch: `[stack, salary, remote, seniority, english_level]`).
- [ ] Step 3: `uv run careeros vault validate --path career/examples/demo` → OK; `just export-schemas`; `uv run careeros export-openapi && npm run generate` (regenerates TS types).
- [ ] Step 4: Extend the two web `const` lists with `"hh", "indeed", "getmatch"`; `npm run -w apps/web typecheck && npm run -w apps/web lint`.
- [ ] Step 5: Existing tests still green: `uv run pytest services/careeros/tests/profiles services/careeros/tests/vault -q`.
- [ ] Step 6: Commit `feat(platform): extend Platform/Source enums with hh, indeed, getmatch; demo channels; regenerated schemas`.

### Task 1: Platform core contract — enums, schemas, base, parsers, registry, stub connectors

**Files:**
- Create: `services/careeros/src/careeros/modules/platform/{__init__,enums,schemas,base,parsers,registry}.py`
- Create: `services/careeros/src/careeros/modules/platform/connectors/__init__.py` + `connectors/<p>/{__init__,connector}.py` for p in hh, upwork, linkedin, wellfound, indeed, getmatch, toptal (paste-only stubs using `parsers.generic_*`)
- Modify: root `pyproject.toml` — import-linter contract "platform connectors are pure"
- Test: `services/careeros/tests/platform/{__init__,test_core}.py`

**Interfaces (exact — connectors code against these):**

```python
# enums.py
class CapabilityLevel(StrEnum): none="none"; manual="manual"; export="export"; api="api"
class ApplyLevel(StrEnum): none="none"; manual_assist="manual_assist"
class AuthKind(StrEnum): none="none"; oauth2="oauth2"; api_key="api_key"
class SyncKind(StrEnum): profile="profile"; jobs="jobs"; applications="applications"
class SyncMethod(StrEnum): api="api"; export="export"; paste="paste"
class SyncStatus(StrEnum): ok="ok"; partial="partial"; failed="failed"; skipped="skipped"
class ConnectionStatus(StrEnum): disconnected="disconnected"; connected="connected"; needs_reauth="needs_reauth"; error="error"
class ApplicationStatus(StrEnum): applied="applied"; viewed="viewed"; invited="invited"; interview="interview"; offer="offer"; rejected="rejected"; withdrawn="withdrawn"; unknown="unknown"
METHOD_ORDER: tuple[SyncMethod, ...] = (SyncMethod.api, SyncMethod.export, SyncMethod.paste)
PLATFORMS: tuple[Platform, ...] = (hh, upwork, linkedin, wellfound, indeed, getmatch, toptal)
SOURCE_BY_PLATFORM: dict[Platform, Source]   # hh→Source.hh, upwork→Source.upwork, … linkedin→Source.linkedin

# schemas.py
class Capabilities(BaseModel):
    platform: Platform
    profile: list[SyncMethod]; jobs: list[SyncMethod]; applications: list[SyncMethod]
    write_profile: CapabilityLevel = none; read_messages: CapabilityLevel = none
    apply: ApplyLevel = none; official_api: bool = False; email_fallback: bool = False
    auth: AuthKind = none; notes: str = ""
    # computed (ADR-004 names): read_profile, read_opportunities, read_applications, export_import, manual_capture
    def methods(self, kind: SyncKind) -> list[SyncMethod]
    def level(self, kind: SyncKind) -> CapabilityLevel
class ProfileRead(BaseModel):   # → profiles.schemas.SnapshotIn via .to_snapshot()
    platform: Platform; capture_method: CaptureMethod = paste; external_id: str|None = None
    profile_url: str|None = None; captured_at: datetime|None = None
    headline: str|None; about: str|None; experience: list[SnapshotExperienceItem]; skills: list[str]
    projects: list[dict]; portfolio: list[dict]; rates: dict|None; availability: str|None
    preferences: dict; raw_text: str|None; raw_payload: dict|None
class JobPosting(BaseModel):    # → opportunities.schemas.IngestRequest via .to_ingest(use_ai=False, provider=None)
    platform: Platform; external_id: str|None; url: str|None; title: str; company: str|None
    location: str|None; posted_at: datetime|None; raw_text: str
    extraction: OpportunityExtraction|None; raw_payload: dict|None
class ApplicationObservationIn(BaseModel):
    platform: Platform; external_id: str|None; job_title: str; company: str|None; job_url: str|None
    status_raw: str; status: ApplicationStatus = unknown; applied_at: datetime|None
    updated_at_platform: datetime|None; raw_payload: dict|None
    def content_hash(self) -> str   # sha256 of platform|external_id|job_title|company|job_url
class JobQuery(BaseModel):
    text: str|None; location: str|None; remote: bool|None; salary_min: float|None; currency: str|None
    posted_since: date|None; limit: int = Field(30, ge=1, le=100); extra: dict[str, Any]
class AccountInfo(BaseModel): account_id: str|None; label: str|None; profile_url: str|None; raw: dict
class DoctorCheck(BaseModel): name: str; ok: bool; detail: str = ""; fix: str|None = None
class OAuthConfig(BaseModel):
    authorize_url: str; token_url: str; client_id: str; client_secret: SecretStr
    scopes: list[str] = []; redirect_uri: str; extra_authorize_params: dict[str,str] = {}
    token_auth: Literal["body","basic"] = "body"

# base.py
class PlatformError(Exception)
class CapabilityUnavailable(PlatformError): (platform, kind, method, available: list[SyncMethod])
class NotConnected(PlatformError): (platform, hint)
class UpstreamError(PlatformError): (platform, status_code: int|None, detail: str)
class ParseError(PlatformError)
@dataclass(slots=True) class ConnectorContext: settings: Settings; http: httpx.AsyncClient; tokens: OAuthTokens|None; now: datetime
class BaseConnector(ABC):
    platform: ClassVar[Platform]; capabilities: ClassVar[Capabilities]
    async def read_profile(self, ctx) -> ProfileRead                      # default: raise CapabilityUnavailable(kind=profile, method=api)
    def import_profile_export(self, path: Path) -> ProfileRead            # default: raise (profile, export)
    def parse_profile_text(self, text: str) -> ProfileRead                # default: raise (profile, paste)
    async def search_jobs(self, ctx, query: JobQuery) -> list[JobPosting]
    def import_jobs_export(self, path: Path) -> list[JobPosting]
    def parse_jobs_text(self, text: str) -> list[JobPosting]
    async def application_statuses(self, ctx) -> list[ApplicationObservationIn]
    def import_applications_export(self, path: Path) -> list[ApplicationObservationIn]
    def parse_applications_text(self, text: str) -> list[ApplicationObservationIn]
    def oauth_config(self, settings) -> OAuthConfig|None                  # default None
    async def whoami(self, ctx) -> AccountInfo                            # default raise CapabilityUnavailable
    async def doctor(self, ctx) -> list[DoctorCheck]                      # default: generic checks (oauth config present? tokens present/expired?)
    def auth_headers(self, ctx) -> dict[str, str]                         # {"Authorization": "Bearer …"} when tokens
METHOD_IMPL: dict[tuple[SyncKind, SyncMethod], str]  # (profile, api)→"read_profile", (profile, export)→"import_profile_export", … used by registry.verify()

# parsers.py (shared heuristics, all pure)
def split_lines(text: str) -> list[str]                    # strip, drop blanks, collapse spaces
def parse_date(s: str, *, now: datetime|None=None) -> datetime|None   # ISO, "Aug 12, 2026", "12 августа 2026", "3 days ago", "2 дня назад", "Applied on …"
def normalize_status(raw: str) -> ApplicationStatus         # keyword table EN+RU (viewed/просмотрен, invited/приглашение, interview/собеседование, rejected/отказ/not selected, offer/оффер, withdrawn/отозван, applied/отклик отправлен/submitted)
def guess_title_company(line: str) -> tuple[str|None, str|None]   # "Title at Company", "Title — Company", "Title · Company", "Company · Title"
def generic_profile(text: str, platform: Platform) -> ProfileRead   # first non-empty line → headline; "About/О себе" block → about; "Skills/Навыки" line → skills; raw_text kept
def generic_jobs(text: str, platform: Platform) -> list[JobPosting] # blocks separated by blank lines; first line title/company; url lines captured
def generic_applications(text: str, platform: Platform) -> list[ApplicationObservationIn]
def find_urls(text: str) -> list[str]

# registry.py
class UnknownPlatform(PlatformError)
class PlatformRegistry:
    def __init__(self, connectors: Iterable[BaseConnector]) -> None
    @classmethod def default(cls) -> PlatformRegistry        # imports careeros.modules.platform.connectors.<name>.connector:Connector for name in CONNECTOR_MODULES
    def get(self, platform: Platform|str) -> BaseConnector
    def all(self) -> list[BaseConnector]
    def capabilities(self) -> list[Capabilities]
    def verify(self) -> list[str]                            # problems: declared method without override; platform mismatch; duplicate
def get_registry() -> PlatformRegistry   # process cache; reset_registry()
# connectors/__init__.py
CONNECTOR_MODULES = ("hh", "upwork", "linkedin", "wellfound", "indeed", "getmatch", "toptal")
```

- [ ] Step 1: Write `tests/platform/test_core.py` first: `test_registry_default_has_all_platforms`, `test_registry_verify_is_clean`, `test_capabilities_levels_derived` (api+paste → `read_profile == api`, `manual_capture is True`), `test_generic_parsers_profile_jobs_applications` (synthetic paste), `test_normalize_status_en_ru`, `test_parse_date_relative`.
- [ ] Step 2: Run → fails (module missing).
- [ ] Step 3: Implement files above; stub connectors declare `profile=[paste], jobs=[paste], applications=[paste]` and delegate to `generic_*`.
- [ ] Step 4: Add import-linter contract to `pyproject.toml`:
  ```toml
  [[tool.importlinter.contracts]]
  name = "platform connectors are pure I/O + mapping (ADR-004)"
  type = "forbidden"
  source_modules = ["careeros.modules.platform.connectors"]
  forbidden_modules = ["sqlalchemy", "careeros.core.db", "careeros.core.models", "careeros.modules.platform.service", "careeros.modules.platform.sync", "careeros.modules.platform.models", "careeros.modules.profiles.service", "careeros.modules.profiles.models", "careeros.modules.opportunities.service", "careeros.modules.opportunities.models", "careeros.modules.ai", "careeros.modules.cv"]
  ```
- [ ] Step 5: `uv run pytest services/careeros/tests/platform -q && uv run lint-imports && uv run pyright && uv run ruff check .` → green.
- [ ] Step 6: Commit `feat(platform): connector contract, registry, shared paste parsers, stub connectors`.

### Task 2: Tokens, OAuth, HTTP helpers, settings

**Files:**
- Create: `modules/platform/{tokens,oauth,http}.py`
- Modify: `core/config.py` (settings below), `config/.env.config.template`, `config/.env.secrets.demo.template`
- Test: `tests/platform/test_core.py` (add token/oauth/http tests)

**Interfaces:**
```python
# core/config.py additions
platform_token_file: Path = Path("generated/platform/tokens.json")
platform_oauth_redirect_base: str = "http://localhost:8000/api/platform/oauth"   # + /{platform}/callback
platform_http_timeout_s: float = 20.0
platform_user_agent: str = "CareerOS/0.1 (careeros@localhost)"
hh_client_id: str | None = None; hh_client_secret: SecretStr | None = None
hh_access_token: SecretStr | None = None; hh_refresh_token: SecretStr | None = None
upwork_client_id: str | None = None; upwork_client_secret: SecretStr | None = None
upwork_access_token: SecretStr | None = None; upwork_refresh_token: SecretStr | None = None
# tokens.py
class OAuthTokens(BaseModel): access_token: SecretStr; refresh_token: SecretStr|None=None; token_type: str="bearer"; expires_at: datetime|None=None; scope: str|None=None; obtained_at: datetime
    def is_expired(self, now: datetime|None=None, skew_s: int=60) -> bool
    def redacted(self) -> dict
class TokenStore(Protocol): load(platform)->OAuthTokens|None; save(platform, tokens)->None; delete(platform)->None; platforms()->list[Platform]
class FileTokenStore(TokenStore): __init__(path: Path); JSON map; parent mkdir; chmod 0o600 after write
def env_tokens(settings, platform) -> OAuthTokens|None       # getattr(settings, f"{platform}_access_token")
def client_credentials(settings, platform) -> tuple[str, SecretStr] | None
def resolve_tokens(settings, store, platform) -> OAuthTokens|None   # env wins over store
def get_token_store(settings) -> FileTokenStore
# oauth.py
def new_state() -> str
def authorize_url(cfg: OAuthConfig, state: str) -> str
async def exchange_code(http, cfg, code: str) -> OAuthTokens
async def refresh_tokens(http, cfg, tokens: OAuthTokens) -> OAuthTokens
# http.py
def build_http(settings, *, transport: httpx.BaseTransport|None=None, headers: dict|None=None) -> httpx.AsyncClient
async def request_json(client, method, url, *, platform: Platform, ok: tuple[int,...]=(200,), retries: int=2, **kw) -> Any   # 429/503 retry with backoff ≤5s; other non-ok → UpstreamError(status, body[:300]); 401 → NotConnected
```
- [ ] Step 1: Tests: file store round-trip (+ mode 0o600), env overlay wins, `is_expired`, `authorize_url` contains client_id/state/redirect, `exchange_code` posts form and parses `{access_token, refresh_token, expires_in}` via `MockTransport`, `request_json` retries 429 then succeeds, 401 → `NotConnected`.
- [ ] Step 2: Implement; add env template lines (`CAREEROS_HH_CLIENT_ID=${CAREEROS_HH_CLIENT_ID:-}` etc. in secrets template; token file/redirect/timeout/UA in config template); `python3 scripts/env-render.py --check`.
- [ ] Step 3: Gates green; commit `feat(platform): token store, OAuth2 helper, HTTP client with retries`.

### Task 3: Persistence + PlatformService

**Files:**
- Create: `modules/platform/{models,service}.py`; migration via `just migration "platform connections, sync runs, application observations"`
- Modify: `migrations/env.py`, `tests/conftest.py` (register `careeros.modules.platform.models`), `worker/main.py` (task module list gets `careeros.modules.platform.tasks` — module may not exist; loop already tolerates that)
- Test: `tests/platform/test_service.py` (`@pytest.mark.db`)

**Interfaces:**
```python
class PlatformService:
    def __init__(self, settings, registry: PlatformRegistry, store: TokenStore, *, session: AsyncSession, user_id: uuid.UUID)
    def capabilities(self) -> list[Capabilities]
    async def list_connections(self) -> list[ConnectionOut]          # one per PLATFORMS; has_tokens from resolve_tokens; token_expires_at
    async def get_connection(self, platform) -> ConnectionOut
    async def oauth_start(self, platform) -> OAuthStartOut           # {authorize_url, state}; raises PlatformError if no oauth_config/client creds
    async def oauth_callback(self, platform, code, state, *, http) -> ConnectionOut   # verify state (in-memory dict with 10-min TTL), exchange, store.save, whoami → upsert connected
    async def refresh(self, platform, *, http) -> ConnectionOut
    async def disconnect(self, platform) -> ConnectionOut
    async def doctor(self, platform, *, http) -> list[DoctorCheck]
    async def start_run(self, platform, kind, method) -> PlatformSyncRun
    async def finish_run(self, run, *, status, seen, created, updated, skipped, error=None, details=None) -> SyncRunOut
    async def list_runs(self, *, platform=None, kind=None, limit=50) -> list[SyncRunOut]
    async def upsert_observations(self, platform, items: list[ApplicationObservationIn], *, run_id) -> tuple[int,int,int]  # created, updated, skipped(unchanged)
    async def list_observations(self, *, platform=None, status=None, limit=200) -> list[ApplicationObservationOut]
    async def touch_connection(self, platform, *, last_sync_at, error=None)
```
Schemas: `ConnectionOut{platform,status,auth,has_tokens,account_id,account_label,scopes,token_expires_at,last_sync_at,last_error,capabilities: Capabilities}`, `OAuthStartOut{authorize_url,state}`, `SyncRunOut{id,platform,kind,method,status,started_at,finished_at,items_seen,items_created,items_updated,items_skipped,error,details}`, `ApplicationObservationOut{id,…all In fields…,observed_at,opportunity_id,history: list[dict]}`.
- [ ] Step 1: Tests: connections default disconnected for all 7; upsert observations twice (second call with changed status → updated=1 and history has previous status; unchanged → skipped); runs listed newest first.
- [ ] Step 2: Implement models + service; generate migration, review it (indexes on user_id/platform/status/external_id; unique `(user_id, platform)` on connection); `just migrate`.
- [ ] Step 3: Gates green; commit `feat(platform): connections, sync runs, application observations`.

### Task 4: Sync orchestration, API router, CLI, Justfile

**Files:**
- Create: `modules/platform/{sync,router,cli}.py`
- Modify: `api/routers.py`, `cli.py` (`("careeros.modules.platform.cli", "platform")`), `Justfile`, `Makefile`
- Test: `tests/platform/test_sync.py` (FakeConnector; API `@pytest.mark.db`), `tests/platform/test_cli.py` (typer `CliRunner`, `--dry-run` paths)

**Interfaces:**
```python
class SyncRequest(BaseModel): method: SyncMethod|None=None; text: str|None=None; file_path: str|None=None; query: JobQuery|None=None; use_ai: bool=False; provider: str|None=None; dry_run: bool=False
class SyncResult(BaseModel): platform; kind; method: SyncMethod|None; status: SyncStatus; run: SyncRunOut|None; items_seen: int; items_created: int; items_updated: int; items_skipped: int; created_ids: list[uuid.UUID]; duplicates: list[uuid.UUID]; preview: list[dict]  (dry-run DTO dump); message: str|None
class ParseResult(BaseModel): platform; kind; method; items: list[dict]; count: int
class PlatformSyncService:
    def __init__(self, settings, *, session, user_id, registry=None, store=None, http_transport=None)
    def choose_method(self, platform, kind, req: SyncRequest) -> SyncMethod   # explicit req.method (validate available) else: text→paste, file_path→export, tokens→api, else raise CapabilityUnavailable listing available
    async def parse(self, platform, kind, *, text=None, file_path=None) -> ParseResult   # no DB
    async def sync(self, platform, kind, req: SyncRequest) -> SyncResult
    async def sync_all(self, platform: Platform|None, *, dry_run: bool) -> list[SyncResult]  # kinds in order; paste-only kinds → status skipped with message "needs paste"
```
Router paths exactly as spec §8 (prefix `/platform`); `_svc()` builds `PlatformSyncService` from `request.app.state.settings`, session, user. CLI commands per spec §9; DB-backed commands open a session with `get_sessionmaker(get_settings())` inside `asyncio.run`. Justfile: `_platform *ARGS: uv run careeros platform {{ARGS}}` helper, then `platform-capabilities`, `platform-connections`, `platform-connect P`, `platform-doctor P`, `platform-sync P="all"` / `platform-sync-dry P="all"`, `platform-profile P *ARGS` / `platform-profile-dry`, `platform-jobs P *ARGS` / `platform-jobs-dry`, `platform-applications P *ARGS` / `platform-applications-dry`, `test-platform`. Makefile: `platform-sync: ## Sync profiles/jobs/applications from connected platforms (PLATFORM=all)`.
- [ ] Step 1: Tests with a `FakeConnector` registered in a custom registry: profile paste → snapshot row exists; jobs api (fake) → two opportunities, second call → duplicates; applications → observations; `dry_run` → no rows, preview populated; `choose_method` precedence; API smoke: capabilities 200 with 7 rows, `POST /api/platform/hh/parse/applications` with pasted text (stub → generic parser) 200.
- [ ] Step 2: Implement; register router + CLI; Justfile/Makefile recipes; `just --list` shows them.
- [ ] Step 3: Gates green; commit `feat(platform): sync orchestration, /api/platform, careeros platform CLI, just recipes`.

### Tasks 5–11: Connectors (one task per platform; run in parallel by separate agents)

Common rules for every connector task:
- Files: `modules/platform/connectors/<p>/connector.py` (+ `client.py`/`queries.py`/`export.py`/`parsers.py` as listed), `tests/platform/test_<p>.py`, `tests/platform/fixtures/<p>/*`, `docs/platform/<p>.md`. Touch nothing else (no shared files) — ask the integrator for shared changes.
- `class Connector(BaseConnector)` with `platform` and `capabilities` exactly as the spec's matrix (§2); every declared method implemented; undeclared methods left to base defaults.
- TDD: fixture → failing test → implementation. Paste fixtures are realistic copies of what the platform's page renders as text (synthetic persona "Dana Kovalenko", companies "Northwind Commerce", "Lumen Analytics"); API fixtures are JSON documents shaped like the documented API responses.
- Doc page: what is supported, how to obtain each input (API app registration steps + scopes, where to download the export, which page to copy for paste), known limits, and the exact `careeros platform …` / `just …` commands.
- Gates on your files: `uv run ruff check <files> && uv run ruff format <files> && uv run pyright <files> && uv run pytest services/careeros/tests/platform/test_<p>.py -q && uv run lint-imports`.

#### Task 5: hh.ru (`connectors/hh/{connector,client,mapping}.py`)
- Capabilities: profile `[api, paste]`, jobs `[api, paste]`, applications `[api, paste]`, `official_api=True`, `auth=oauth2`, `email_fallback=False`.
- `oauth_config`: authorize `https://hh.ru/oauth/authorize`, token `https://api.hh.ru/token`, scopes `[]`, `token_auth="body"`, redirect `settings.platform_oauth_redirect_base + "/hh/callback"`; client creds from `client_credentials(settings, Platform.hh)`.
- Client (`client.py`): base `https://api.hh.ru`; every request sends `HH-User-Agent: <settings.platform_user_agent>` and `User-Agent`; `me()` → `GET /me`; `resumes_mine()` → `GET /resumes/mine` (`items[]`); `resume(id)` → `GET /resumes/{id}`; `vacancies(params)` → `GET /vacancies` (params: `text`, `area`, `schedule=remote` when `query.remote`, `salary`, `currency`, `date_from`, `per_page=min(limit,100)`, `order_by=publication_time`); `similar_vacancies(resume_id)` → `GET /resumes/{id}/similar_vacancies`; `negotiations(page)` → `GET /negotiations?order_by=updated_at&order=desc&per_page=50` (iterate pages ≤ 5); `vacancy(id)` → `GET /vacancies/{id}` (only for `search_jobs` when `query.extra.get("full") is True`, cap 20).
- Mapping: resume → `ProfileRead` (title→headline, skills (`skill_set`), `skills` free text→about, `experience[]` (company, position, start–end, description), `salary`→rates, `schedules`/`employments`→preferences, `alternate_url`→profile_url, id→external_id); vacancy item → `JobPosting` with `extraction=OpportunityExtraction(title, company=employer.name, location=area.name, remote_policy=remote_global if schedule.id=="remote" else unknown, compensation from `salary{from,to,currency}` period month, technologies=[] , summary=snippet.requirement/responsibility with `<highlighttext>` tags stripped)`; negotiation → `ApplicationObservationIn(external_id=negotiation.id, job_title=vacancy.name, company=vacancy.employer.name, job_url=vacancy.alternate_url, status_raw=state.name, status=response→applied, invitation→invited, discard→rejected, applied_at=created_at, updated_at_platform=updated_at)`.
- Paste parsers: hh "Мои резюме" page text → profile (headline first line; «Навыки»/«Ключевые навыки» line → skills; «Опыт работы» blocks); vacancies list paste (title, company, salary «от 300 000 ₽», city, «Откликнуться») → jobs; «Отклики и приглашения» paste (title · company · state «Отклик», «Приглашение», «Отказ», date) → observations.
- `whoami`: `GET /me` → `AccountInfo(account_id=id, label=f"{first_name} {last_name}", raw)`. `doctor`: client creds present, tokens present/not expired, `GET /me` 200 (with http), `GET /vacancies?per_page=1` 200 (no auth).
- Tests: fixtures `me.json`, `resumes_mine.json`, `resume.json`, `vacancies.json`, `negotiations_p1.json`, `negotiations_p2.json`, `paste_resume.txt`, `paste_vacancies.txt`, `paste_negotiations.txt`; MockTransport routes by path; assert mapping fields, pagination stops at `pages`, User-Agent headers sent, 401 → `NotConnected`.

#### Task 6: Upwork (`connectors/upwork/{connector,client,queries,mapping}.py`)
- Capabilities: profile `[api, paste]`, jobs `[api, paste]`, applications `[api, paste]`, `official_api=True`, `auth=oauth2`, `email_fallback=True`, notes "API access requires an approved Upwork API key; paste works without it".
- `oauth_config`: authorize `https://www.upwork.com/ab/account-security/oauth2/authorize`, token `https://www.upwork.com/api/v3/oauth2/token`, `token_auth="body"`, redirect `…/upwork/callback`.
- GraphQL client: `POST https://api.upwork.com/graphql` JSON `{query, variables}`; errors array → `UpstreamError`; `queries.py` holds the documents: `USER_INFO` (`query { user { id nid rid name email } }`), `FREELANCER_PROFILE` (own profile: title, description/overview, skills, hourly rate, availability, portfolio; verify field names live), `JOB_SEARCH` (`marketplaceJobPostingsSearch(marketPlaceJobFilter:{titleExpression_eq:$q, …}, searchType: USER_JOBS_SEARCH, sortAttributes:[{field:"RECENCY"}]) { totalCount edges { node { id title description ciphertext createdDateTime skills { name } hourlyBudgetMin hourlyBudgetMax amount { rawValue currency } client { location { country } } } } }`), `PROPOSALS` (freelancer's proposals/offers with status). Because the schema can't be verified offline, every query document has a `# VERIFY LIVE` header comment and `doctor` runs a minimal introspection `{ __type(name:"Query"){ fields { name } } }` and reports which of the used root fields exist.
- Mapping: job node → `JobPosting(url=f"https://www.upwork.com/jobs/{ciphertext}", extraction(title, contract_type=freelance, compensation hourly range or fixed amount, technologies=skills, summary=description[:600], remote_policy=remote_global))`; proposal → observation (status_raw=proposal status; ACTIVE→applied, VIEWED→viewed, INTERVIEW/SHORTLISTED→interview, HIRED/OFFER→offer, DECLINED/REJECTED/ARCHIVED→rejected, WITHDRAWN→withdrawn).
- Paste parsers: Upwork profile page text (title line, "Overview"/about, "Skills", "$NN.NN/hr"), "Find Work" job list paste (title, "Hourly: $X-$Y" / "Fixed-price - Est. budget: $N", "Posted N hours ago", skills row), "My Proposals" paste (sections "Active proposals", "Submitted proposals", "Offers", rows "Title · Company · Initiated <date>").
- Tests with fixtures `user.json`, `profile.json`, `jobs.json`, `proposals.json`, `introspection.json`, three paste txt files; GraphQL error payload → `UpstreamError`.

#### Task 7: LinkedIn (`connectors/linkedin/{connector,export,parsers}.py`)
- Capabilities: profile `[export, paste]`, jobs `[export, paste]` (export = `Saved Jobs.csv` → postings), applications `[export, paste]`, `official_api=False`, `auth=none`, `email_fallback=True`.
- `export.py`: accept a directory or the `.zip` archive from "Download your data"; read with `csv` + `zipfile` (UTF-8 BOM-safe); files: `Profile.csv` (First Name, Last Name, Headline, Summary, Industry, Geo Location), `Positions.csv` (Company Name, Title, Description, Location, Started On, Finished On), `Skills.csv` (Name), `Education.csv`, `Certifications.csv`, `Projects.csv`, `Languages.csv`, `Job Applications.csv` (Application Date, Contact Email, Contact Phone Number, Company Name, Job Title, Job Url, Resume Name, Question And Answers), `Saved Jobs.csv` (Saved Date, Job Url, Job Title, Company Name). Missing files → fields empty, never an error; missing `Profile.csv` → `ParseError`.
- Mapping: Positions → experience (period "Mon YYYY – Mon YYYY|now"); Job Applications → observations (`external_id=job_url`, `status_raw="applied"`, `status=applied`, `applied_at=Application Date` (`M/D/YY, H:MM AM` format)); Saved Jobs → `JobPosting(raw_text=f"{title} at {company}\n{url}")`.
- Paste parsers: profile page text (name line, headline line, "About", "Experience" blocks "Title / Company · Full-time / Dates", "Skills"); job search list paste ("Title / Company / Location / Promoted · N applicants"); "My Jobs → Applied" paste ("Title / Company / Location / Applied N days ago", statuses "Application viewed", "Resume downloaded").
- Tests: build a synthetic archive in the test via `zipfile` from fixture CSVs; assert mapping; dates parsed; paste fixtures.

#### Task 8: Wellfound (`connectors/wellfound/{connector,parsers}.py`)
- Capabilities: profile `[paste]`, jobs `[paste]`, applications `[paste]`, `email_fallback=True`, notes "no public API; site is not fetched".
- Paste parsers: profile ("Name / Headline / Location / About / Skills / Work experience blocks: Title at Company, dates"), job list (cards: "Company · size · Title · Remote · $Xk – $Yk · Posted N days ago · Apply"), applications ("Applied" tab rows: "Company · Title · Applied <date> · Status: Application sent / Viewed / Interviewing / Not moving forward").
- Status mapping via `normalize_status` plus Wellfound-specific phrases ("Not moving forward"→rejected, "Application sent"→applied).
- Tests: three fixtures; mixed-order sections; salary parsing into `extraction.compensation` (year, USD).

#### Task 9: Indeed (`connectors/indeed/{connector,parsers}.py`)
- Capabilities: profile `[paste]`, jobs `[paste]`, applications `[paste]`, `email_fallback=True`, notes "Publisher API discontinued; job alerts via email (P1)".
- Paste parsers: Indeed profile/resume ("Name / Headline / Location / Summary / Work experience: Title – Company – Location – Dates / Skills"), job list ("Title / Company / Location / $X - $Y a year / Remote / Posted N days ago / Easily apply"), "My jobs → Applied" ("Title / Company / Location / Applied on <date> / Status: Applied | Viewed by employer | Interviewing | Not selected by employer").
- Tests: three fixtures; "Not selected by employer" → rejected; "Viewed by employer" → viewed; pay parsing (year/hour).

#### Task 10: getmatch (`connectors/getmatch/{connector,parsers}.py`)
- Capabilities: profile `[paste]`, jobs `[paste]`, applications `[paste]`, `email_fallback=True`, notes "no public API; RU/EN pastes".
- Paste parsers (RU + EN): profile («Имя / Позиция / Стек / Опыт / Ожидания по зарплате / Английский»), vacancies list («Компания · Позиция · от 300 000 ₽ · Удалённо · Стек: …»), responses («Отклики»: «Позиция · Компания · Статус: Отправлен / Просмотрен / Приглашение / Отказ · дата»).
- Tests: three RU fixtures + one EN; ruble salaries → `Compensation(currency="RUB", period=month)`.

#### Task 11: Toptal (`connectors/toptal/{connector,parsers}.py`)
- Capabilities: profile `[paste]`, jobs `[paste]`, applications `[paste]`, `email_fallback=True`, notes "talent portal only; no API/export".
- Paste parsers: public talent profile ("Name / Title / Location / Bio / Expertise / Work Experience: Title, Company, dates / Portfolio"), portal job list ("Title / Client · Industry / Engagement: Full-time · Remote · Duration / Rate / Posted"), applications ("Applied jobs: Title · Client · Applied <date> · Stage: Applied / Interviewing / Matched / Declined").
- Tests: three fixtures; "Matched"→offer, "Declined"→rejected.

### Task 12: Docs, ADR-013, capabilities matrix, agents, skill, session files

**Files:**
- Create: `docs/adr/013-platform-connectors.md`; `docs/platform/README.md`; `.claude/agents/careeros-platform-ops.md`; `.claude/agents/careeros-platform-connector-dev.md`
- Modify: `docs/adr/README.md` (row 011), `docs/architecture/03-integration-capabilities-matrix.md` (rows hh/indeed/getmatch + updated cells for existing rows + note that the matrix is now served from `GET /api/platform/capabilities`), `docs/architecture/01-architecture-proposal.md` (Platform row + context map note), `docs/architecture/04-roadmap.md` (P2 item done early), `docs/developer-guide/README.md` (connector how-to), `README.md` (command row), `.claude/CLAUDE.md` (layout line: platform module exists), `.claude/TODO.md`, `.claude/CLAUDE-curr-status.md`, `.claude/PROMPTS-LOG.md` (+ `-ru.md`)
- Skill: run `/create-skill careeros-platform-sync` (policy); fallback `/create-skill-candidate-for-curr-prj`.
- [ ] Steps: write docs; verify every relative link resolves (`grep -o '](\.\./[^)]*)'` and test `-f`); commit `docs(platform): ADR-013, capabilities matrix, connector guide, agents`.

### Task 13: Integration gates, review, final commit

- [ ] `just lint` (ruff, format, pyright, lint-imports, env-render check, web lint) → green.
- [ ] `uv run pytest` (with Postgres up) → green; `npm run -w apps/web typecheck && npm run -w apps/web test`.
- [ ] `uv run careeros vault validate --path career/examples/demo`; `just export-schemas` + `git diff --exit-code -- career/schemas packages/schemas`.
- [ ] Manual smoke: `uv run careeros platform capabilities`; `uv run careeros platform applications hh --text-file services/careeros/tests/platform/fixtures/hh/paste_negotiations.txt --dry-run --json`.
- [ ] Review pass (code-reviewer agent on the diff); fix findings.
- [ ] `/smart-commit` for anything uncommitted; update status/TODO.
