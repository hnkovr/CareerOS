# hh.ru connector

Official JSON API (OAuth2) for all three capabilities, plus paste fallbacks. No scraping, no
passwords, no cookies (ADR-005): the connector only talks to `https://api.hh.ru` with a user-granted
token, or parses text you copied from hh.ru yourself.

| Capability | Methods | Endpoints |
|---|---|---|
| Own profile → profile snapshot | `api`, `paste` | `GET /resumes/mine` → `GET /resumes/{id}` |
| Job search → opportunities | `api`, `paste` | `GET /vacancies` · `GET /resumes/{id}/similar_vacancies` · `GET /vacancies/{id}` |
| Application statuses → observations | `api`, `paste` | `GET /negotiations` |
| Account probe / doctor | — | `GET /me`, `GET /vacancies?per_page=1` |

`official_api=true`, `auth=oauth2`, `email_fallback=false`. Vacancy search is public (no token);
resumes, similar-vacancy search and negotiations need the user token.

Code: `services/careeros/src/careeros/modules/platform/connectors/hh/` (`client.py` REST client,
`mapping.py` JSON → DTOs, `parsers.py` paste heuristics, `connector.py`). Tests:
`services/careeros/tests/platform/test_hh.py` (mock transport, synthetic fixtures).

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
careeros platform doctor hh           # capabilities, client credentials, tokens, API reachability, GET /me
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

## 4. Paste alternatives (no API needed)

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

## 5. Tokens

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

## 6. Limits and behaviour to know

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

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `400 bad_user_agent` | set `CAREEROS_PLATFORM_USER_AGENT="App/1.0 (mail@example.com)"` |
| `NotConnected: token rejected (403 oauth/token_expired)` | `careeros platform refresh hh` |
| `NotConnected: token rejected (403 oauth/token_revoked)` | `careeros platform connect hh` |
| `oauth_config: set CAREEROS_HH_CLIENT_ID / …` | fill `.env.secrets` (section 1) |
| `hh: the account has no resumes` | create/publish a resume on hh.ru, then `profile hh --api` |
| `platform.hh.area_not_found` warning | pass the area id via `extra.area` or refine `--location` |
| paste gives odd companies/positions | hh renders industry lines between company and position on some resumes; edit the pasted text or use `--api` |
