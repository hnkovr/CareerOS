# hh.ru connector

Official JSON API (OAuth2) for all three capabilities, plus paste fallbacks. No scraping, no
passwords, no cookies (ADR-005): the connector only talks to `https://api.hh.ru` with a user-granted
token, or parses text you copied from hh.ru yourself.

| Capability | Methods | Endpoints |
|---|---|---|
| Own profile → profile snapshot | `api`, `paste` | `GET /resumes/mine` → `GET /resumes/{id}` |
| Job search → opportunities | `api`, `paste` | `GET /vacancies` · `GET /resumes/{id}/similar_vacancies` · `GET /vacancies/{id}` |
| Application statuses → observations | `api`, `paste` | `GET /negotiations` |
| **One vacancy behind a URL** (§4) | `api` → `jina` → `wayback` | `GET /vacancies/{id}` (+ `POST /token` for an application token) |
| Account probe / doctor | — | `GET /me`, `GET /vacancies?per_page=1`, `GET /vacancies/{id}` |

`official_api=true`, `auth=oauth2`, `email_fallback=false`, `access=public`. Vacancy search is
public (no token); resumes, similar-vacancy search and negotiations need the user token. Reading
one vacancy by URL needs *a* token in practice — anonymous reads answered `403` on 2026-08-26.

Code: `services/careeros/src/careeros/modules/platform/connectors/hh/` (`client.py` REST client,
`hosts.py` host table, `mapping.py` JSON → DTOs, `parsers.py` paste heuristics, `connector.py`).
Tests: `services/careeros/tests/platform/test_hh.py` (API + paste) and `test_hh_read.py`
(read-one; mock transport, synthetic fixtures, one opt-in `live` test).

## 1. Create the hh.ru application (once)

1. Sign in at <https://dev.hh.ru> → **Мои приложения** → **Создать приложение**.
2. Fill in the name and description, choose the applicant scope (default), and set
   **Redirect URI** to exactly

   ```
   http://localhost:8000/api/platform/oauth/hh/callback
   ```

   (`settings.platform_oauth_redirect_base` + `/hh/callback`; change both together if you run
   the API on another host/port). Approval by hh can take a few days for new apps.
3. Copy **Client ID** and **Client Secret** into the git-ignored `.env.secrets`
   (the templates in `config/` keep these blank):

   ```dotenv
   CAREEROS_HH_CLIENT_ID=
   CAREEROS_HH_CLIENT_SECRET=
   ```

4. Optional but recommended — hh requires an identifying `User-Agent` with a contact e-mail
   (every request sends `HH-User-Agent` and `User-Agent`; without one hh answers
   `400 bad_user_agent`):

   ```dotenv
   CAREEROS_PLATFORM_USER_AGENT="CareerOS/0.1 (you@example.com)"
   ```

## 2. Connect

```bash
careeros platform connect hh          # or: just platform-connect hh
```

The command prints the authorize URL (`https://hh.ru/oauth/authorize?response_type=code&…`),
you approve in **your** browser, then paste the `code` (or the whole redirect URL) back. Tokens are
exchanged at `https://api.hh.ru/token` (form body) and stored in `generated/platform/tokens.json`
(mode 0600, git-ignored). For containers you can pin them by env instead:
`CAREEROS_HH_ACCESS_TOKEN` / `CAREEROS_HH_REFRESH_TOKEN`.

```bash
careeros platform doctor hh           # capabilities, client credentials, tokens, API reachability, GET /me, read-one probes
careeros platform connections         # account label = "First Last" from GET /me
```

`doctor` never raises for a failed probe: each check is `ok=false` with a `fix` hint
(e.g. `careeros platform refresh hh (or connect again)`).

## 3. Sync commands

| What | Command | Notes |
|---|---|---|
| Profile | `careeros platform profile hh --api` | newest resume (by `updated_at`) of the account → `SnapshotIn(capture_method=api)` |
| Jobs | `careeros platform jobs hh -q "data engineer" --remote --limit 50` | public search, `order_by=publication_time`, one page (`limit ≤ 100`) |
| Applications | `careeros platform applications hh --api` | responses / invitations / rejections → `application_observation` rows |
| Everything | `careeros platform sync hh` | every API-backed capability; `--dry-run` never touches the DB |

Every command accepts `--dry-run` (parse/fetch only, print a preview) and `--json`.
Justfile pairs: `just platform-profile hh --api`, `just platform-jobs-dry hh -q "dbt" --remote`,
`just platform-applications hh --api`, `just platform-sync-dry hh`.

### Job search knobs

| `JobQuery` field | hh parameter |
|---|---|
| `text` (`-q`) | `text` |
| `remote` (`--remote`) | `schedule=remote` **and** `work_format=REMOTE` (hh deprecated `schedule`; both are sent, results can only narrow) |
| `location` (`--location`) | resolved to an area id through `GET /suggests/areas?text=…` (first hit); a warning is logged when nothing matches |
| `salary_min` + `currency` | `salary`, `currency` (`RUB` is sent as hh's `RUR`; default `RUR`) |
| `posted_since` | `date_from=YYYY-MM-DD` |
| `limit` | `per_page=min(limit, 100)`, `page=0` |
| `extra.area` | `area` (dictionary id, e.g. `1` = Москва, `2` = Санкт-Петербург); wins over `location` |
| `extra.similar_to_resume` | `true` → `GET /resumes/{newest}/similar_vacancies`; a resume id string → that resume |
| `extra.full` | `true` → `GET /vacancies/{id}` for the first 20 results (description, `key_skills` → technologies); a failed detail is recorded in `raw_payload.detail_error`, never fatal |
| `extra.order_by`, `search_field`, `experience`, `professional_role`, `employer_id`, `label`, `period`, `work_format`, `employment_form` | forwarded verbatim |

An **empty query with a token** runs the similar-to-resume search; an empty query without a token
raises `NotConnected` with a hint (a search without text would just return random vacancies).
`extra` is available through the API (`POST /api/platform/hh/sync/jobs` with `query.extra`), the
CLI exposes `-q/--location/--remote/--limit`.

Note: the sync layer (`PlatformSyncService.run_api`) currently requires stored tokens for any API
method, so `careeros platform jobs hh -q …` needs `connect` first even though the endpoint itself
is public.

### What the mapping does

* Resume → profile: `title` → headline, `skills` (free text) → about, `skill_set[]` → skills,
  `experience[]` → `company / position / "start – end|now" / description`, `salary` → rates
  (`RUR` normalised to `RUB`), `schedules[].id`, `employments[].id`, `work_format[].id`, `area.name`
  → preferences; the full JSON is kept in `raw_payload`.
* Vacancy → opportunity: `name @ employer`, area, salary line and the `snippet`
  (`<highlighttext>` stripped) form `raw_text`; `extraction` carries title, company, location,
  compensation (`from/to`, currency, period `month`), employment type (`full` → full_time,
  `part` → part_time, `project` → project), remote policy (`schedule.id=remote` or
  `work_format` `REMOTE` → `remote_global`, `HYBRID` → `hybrid`, `ON_SITE` → `onsite`), experience
  requirement, key skills (with `extra.full`). Dedup and scoring stay in the opportunities module.
* Negotiation → observation (`status_raw` = `state.name`):

  | `state.id` | normalized status |
  |---|---|
  | `response` | `applied`; `viewed` when `viewed_by_opponent=true` |
  | `invitation` | `invited` |
  | `discard` | `rejected` |
  | anything else (`hidden`, call states) | `normalize_status(state.name)` → usually `unknown` |

  `applied_at` = `created_at`, `updated_at_platform` = `updated_at`, `external_id` = negotiation id.
  Pagination: `order_by=updated_at&order=desc&per_page=50`, up to 5 pages (250 newest
  negotiations); the loop stops at the `pages` value hh reports.

## 4. Read one vacancy by URL (ADR-015)

Paste a link someone sent you and CareerOS reads *that one vacancy* — through the official API,
never by scraping the page:

```bash
careeros platform read 'https://hh.ru/vacancy/136537758?from=share_ios' --show-attempts
careeros platform read 'https://hh.ru/vacancy/136537758' --dry-run --json
careeros platform detect 'https://spb.hh.ru/vacancy/136537758/'      # which provider owns a URL
```

`read_job = [api, jina, wayback]`, `access = public`. There is **no `public_html` strategy**: hh
answers non-browser clients with a WAF/captcha challenge and working around that is forbidden
(ADR-005), so when the API says no the chain falls back to Jina Reader (a transformed copy) and
then the Wayback Machine (a historical copy), and reports what each attempt did.

### Which URLs are recognised

`https://<hh site>/vacancy/<digits>` — with or without a trailing slash, with any query string
(`?from=share_ios`, `?hhtmFrom=…`). The canonical form drops the query and the subdomain:

| You give | Canonical | `external_id` |
|---|---|---|
| `https://hh.ru/vacancy/136537758?from=share_ios` | `https://hh.ru/vacancy/136537758` | `136537758` |
| `https://spb.hh.ru/vacancy/136537758/` (any city subdomain, `m.`, `www.`) | `https://hh.ru/vacancy/136537758` | `136537758` |
| `https://hh.kz/vacancy/98765` (a regional site) | `https://hh.kz/vacancy/98765` | `98765` |

Anything else on hh — search pages, employer pages, resumes, `/vacancy/<slug>` — is **not** a job
read: `detect()` returns nothing rather than guessing, and the generic connector does not take
over (it never sees a URL a specific connector claimed the host of).

### Hosts

One API (`https://api.hh.ru`) serves every front-end; the site is passed as `host=<site>` so
areas, currencies and wording come back in that site's locale (`hosts.api_params_for`).

| Host | Country | API base | `host=` sent | Verified |
|---|---|---|---|---|
| `hh.ru` (canonical; `www.`, `m.`, city subdomains) | Russia | `api.hh.ru` | — | **yes** (2026-08-26) |
| `hh.kz` | Kazakhstan | `api.hh.ru` | `host=hh.kz` | no — to verify |
| `headhunter.ge` | Georgia | `api.hh.ru` | `host=headhunter.ge` | no — to verify |
| `headhunter.kg` | Kyrgyzstan | `api.hh.ru` | `host=headhunter.kg` | no — to verify |
| `hh.uz` | Uzbekistan | `api.hh.ru` | `host=hh.uz` | no — to verify |
| `rabota.by` | Belarus | `api.hh.ru` | `host=rabota.by` | no — to verify |
| `hh1.az` | Azerbaijan | `api.hh.ru` | `host=hh1.az` | no — to verify |

Source of truth: `connectors/hh/hosts.py` (each row carries its own `verified` flag). No regional
API base exists — an unverified row still reads through `api.hh.ru`; what is unverified is the
`host=` parameter, not the endpoint.

### Authentication of a read

1. **Your OAuth token** when hh is connected (`careeros platform connect hh`).
2. Otherwise an **application token** — `POST https://api.hh.ru/token` with
   `grant_type=client_credentials`, using `CAREEROS_HH_CLIENT_ID` / `CAREEROS_HH_CLIENT_SECRET`.
   It is fetched once per process, kept in memory only, and never written to
   `generated/platform/tokens.json` (that file holds user grants). A failed token request is a
   warning, not an error: the read still goes out anonymously.
3. Otherwise **anonymous** — which hh currently refuses (see the research record).

### Status semantics

No status collapses into "failed to fetch": every attempt is recorded with an `error_type` you
can act on (`--show-attempts`, or `attempts[]` in the JSON).

| hh answers | `error_type` | Flags | What it means / what to do |
|---|---|---|---|
| `200` + vacancy JSON | — | `job_closed` when `archived: true` | the read succeeded; `archived` marks a closed vacancy |
| `200` + non-JSON / not an object | `malformed` | — | hh answered something that is not a vacancy record |
| `403 {"type":"forbidden"}` | `forbidden` | — | connect with `careeros platform connect hh` or set `CAREEROS_HH_CLIENT_ID`/`SECRET` |
| `403 {"type":"oauth"}` | `forbidden` | — | the token is expired/revoked → `careeros platform refresh hh` |
| `401` | `forbidden` | — | as above |
| `404` / `410` | `not_found` | `job_closed` | closed, archived or never public → the chain tries Wayback |
| `429` (after the retries) | `rate_limited` | — | `Retry-After` is honoured twice before giving up; try later |
| `5xx` | `upstream` | — | hh is degraded; retry later |
| any other 4xx | `http_error` | — | the body's first 120 characters are kept in the message |
| connect/read timeout | `timeout` | — | network or `CAREEROS_PLATFORM_HTTP_TIMEOUT_S` |
| other transport failure | `network` | — | DNS/TLS/connection |
| the URL is not a vacancy | `not_a_vacancy` | — | no request is made at all |

### What a read produces

* **API artifact** → the same mapping as a search hit plus the detail record's `description` and
  `key_skills`, then provenance: `canonical_url`, `external_id`, `published_at` (from
  `published_at`, else `created_at`), `expires_at` when the payload states one, `is_archive=false`,
  `strategy=api`, `content_hash`, `fingerprint`, and per-field evidence with source `board_api`
  (confidence 0.95) for title, company, location, compensation, employment type, remote policy,
  experience and key skills. The verbatim payload stays in `raw_payload`.
* **Jina artifact** → the shared markdown extractor, evidence source `aggregator` (a transformed
  copy, never authoritative over the board's own fields).
* **Wayback artifact** → the shared HTML/JSON-LD extractor, evidence source `archive`,
  `is_archive=true`, `archive_ts` = the capture time (never a publication date) and
  `relation=historical_version_of`.

### Doctor

`careeros platform doctor hh` adds three read checks to the API ones:

| Check | What it proves |
|---|---|
| `read_detect` | a shared vacancy link canonicalises, with the host table's size |
| `read_api` | one real `GET /vacancies/{id}` (the id the search probe returned), classified by the table above — `403` reports the fix, `404` still counts as "the read path answered" |
| `direct_html` | states that hh's HTML is deliberately not fetched (WAF/captcha, ADR-005) |

## 5. Paste alternatives (no API needed)

Copy the page text in your browser (Ctrl/Cmd-A, Ctrl/Cmd-C) into a file and pass `--text-file`
(`-` reads stdin). Unknown shapes fall back to the shared generic parsers.

| Page | Command | What is recognised |
|---|---|---|
| Your resume page, or **Мои резюме** | `careeros platform profile hh --text-file resume.txt` | headline = desired position (line before «Специализации:» / after «Мои резюме»), «Ключевые навыки», «Обо мне», «Опыт работы» blocks (period → company → position → description; the «Москва, site.example» line is skipped), «Занятость:», «График работы:», «Проживает:», «450 000 ₽ на руки» → rates |
| Vacancy search results | `careeros platform jobs hh --text-file vacancies.txt` | one card per vacancy: title, salary («от 300 000 ₽ за месяц, на руки», «250 000 – 350 000 ₽»), company, city, «Опыт 3–6 лет», «Удалённо / Можно удалённо / Гибрид», «Полная занятость», card date («20 августа», «Вчера»); cards split on blank lines, on the date footer, and after «Откликнуться» |
| **Отклики и приглашения** | `careeros platform applications hh --text-file negotiations.txt` | title, company, city, state («Отклик», «Отклик · Просмотрен», «Отклик · Не просмотрен» = applied, «Приглашение», «Отказ»), date → `updated_at_platform` (and `applied_at` for plain responses) |

Dates without a year («20 августа») are assumed to be in the current year, rolling back one year
when that would be in the future. Everything is heuristic: raw text is always kept
(`raw_text` / `raw_payload.lines`), nothing is invented — unknown fields stay `null`.

## 6. Tokens

* Access token lifetime: **14 days** (`expires_in=1209600`). Refresh tokens are **single-use and
  accepted only after the access token has expired** — refreshing a live token fails with
  `token not expired`. So run `careeros platform refresh hh` when `doctor` reports the token as
  expired (or when a sync fails with `NotConnected`); refreshing early is refused by hh, not by
  CareerOS.
* hh signals token problems as `403 {"errors":[{"type":"oauth","value":"token_expired" | "token_revoked" | "bad_authorization"}]}`;
  the client maps these (and plain 401) to `NotConnected` so the sync layer can refresh or ask
  you to reconnect. Revoked tokens need `careeros platform connect hh` again.
* Tokens never reach logs (structlog masks `*_token`); `careeros platform disconnect hh` deletes
  them.

## 7. Limits and behaviour to know

* `per_page ≤ 100`, search depth ≤ 2000 items; the connector fetches a single page per query.
* hh publishes no fixed rate limit; 429/5xx answers are retried twice with backoff
  (`platform.http.request_json`). Unauthenticated similar-vacancy requests may be answered with a
  captcha challenge — use a token.
* `GET /resumes/mine` order is not stable: the connector always picks the resume with the newest
  `updated_at`. Accounts without a resume get a clear error instead of an empty snapshot.
* Marked `# VERIFY LIVE` in the code (not in the public OpenAPI spec, taken from the legacy docs):
  `GET /resumes/mine`, `GET /resumes/{id}/similar_vacancies`, `GET /suggests/areas`, the deprecated
  `schedule=remote` filter, and the default `status` filter of `GET /negotiations`
  (no `status` is sent; if live results look incomplete try `status=all`).

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `400 bad_user_agent` | set `CAREEROS_PLATFORM_USER_AGENT="App/1.0 (mail@example.com)"` |
| `NotConnected: token rejected (403 oauth/token_expired)` | `careeros platform refresh hh` |
| `NotConnected: token rejected (403 oauth/token_revoked)` | `careeros platform connect hh` |
| `oauth_config: set CAREEROS_HH_CLIENT_ID / …` | fill `.env.secrets` (section 1) |
| `hh: the account has no resumes` | create/publish a resume on hh.ru, then `profile hh --api` |
| `platform.hh.area_not_found` warning | pass the area id via `extra.area` or refine `--location` |
| `read` fails with `forbidden` on every attempt | anonymous hh reads are refused (2026-08-26): `careeros platform connect hh`, or set `CAREEROS_HH_CLIENT_ID`/`SECRET` for an application token |
| `read` fails with `not_found` and Wayback has nothing | the vacancy is closed and was never archived — paste the text you still have (`careeros platform jobs hh --text-file …`) |
| `read` says `not_a_vacancy` | the URL is a search/employer/resume page; only `/vacancy/<digits>` is one job |
| a regional link (`hh.kz`, `rabota.by` …) reads oddly | the `host=` parameter is declared from the docs, not verified — report what you saw |
| paste gives odd companies/positions | hh renders industry lines between company and position on some resumes; edit the pasted text or use `--api` |

## 9. Research record (ADR-015 §66–67)

| Item | Value | Verified |
|---|---|---|
| Hosts | `hh.ru` (canonical) + `hh.kz`, `headhunter.ge`, `headhunter.kg`, `hh.uz`, `rabota.by`, `hh1.az`; `www.`/`m.`/city subdomains (`spb.hh.ru`) are the same site | `hh.ru` 2026-08-26; the rest **from HH documentation, not verified live** |
| URL forms | `/vacancy/<digits>` with any query (`?from=share_ios`, `?hhtmFrom=…`) and an optional trailing slash; canonical = `https://<site>/vacancy/<id>` | 2026-08-26 |
| Public API | `https://api.hh.ru` (OpenAPI: <https://api.hh.ru/openapi/redoc>); one API for every front-end, the site is selected with `host=<site>` | endpoint 2026-08-26; `host=` **to verify** |
| Anonymous read | `GET https://api.hh.ru/vacancies/136537758` without a token → **`403 {"errors":[{"type":"forbidden"}]}`** | 2026-08-26 (this workstation) |
| Authenticated read | user OAuth token, or an application token (`POST /token`, `grant_type=client_credentials`) | **not verified live** — no hh application credentials exist on this workstation (2026-08-26) |
| Direct HTML | `https://hh.ru/vacancy/<id>` is served behind a WAF/captcha challenge to non-browser clients — **not fetched**, not worked around (ADR-005) | design decision, 2026-08-26 |
| Auth | OAuth2; access token 14 days, refresh single-use and only after expiry (§6) | 2026-08-25 |
| Preferred strategy | `api` (`GET /vacancies/{id}`, identifying `HH-User-Agent`, bounded retries) | — |
| Fallbacks | `jina` (transformed copy, public URLs only), `wayback` (historical copy, `archive_ts`) | — |
| Rate limits | hh publishes none; `429`/`5xx` are retried twice with `Retry-After` honoured, ≤ 1 request per URL per cache TTL | — |
| Terms | official API under hh's developer terms (<https://dev.hh.ru>); reads are user-initiated, one URL at a time — no listings, no crawling | — |
| Fixtures | `tests/platform/fixtures/hh/vacancy_136537758.json` (API 200), `wayback_136537758.html` (archived snapshot) | — |
| Classification | **verified from fixture; live: not tested** — the 403 above is the only live observation; no successful live read has been made from this repo | 2026-08-26 |
