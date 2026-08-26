# Platform connectors — user guide

CareerOS reads three things from each job platform, always **read-only** and always through the
best *legitimate* method the platform offers ([ADR-013](../adr/013-platform-connectors.md)):

| Capability | What lands in CareerOS |
|---|---|
| **Own profile** | a `profiles` snapshot → `careeros profiles` audit, health score, drift vs the vault |
| **Job search** | `opportunities` (parsed, deduplicated, deterministically scored, AI-analysable) |
| **Application statuses** | `application_observation` rows with a normalized status + history (`careeros platform status`) |

Precedence per capability: **official API > official export > paste** — the CLI picks the best one
that is available (`--api`, `--export PATH`, `--text-file FILE` force a method). Every command has
`--dry-run` (parse/fetch, print, persist nothing) and `--json`.

| Platform | Own profile | Job search | Read one job by URL (ADR-015) | Application statuses | Auth | Guide |
|---|---|---|---|---|---|---|
| hh.ru | API · paste | API (search needs no token) · paste | `api` (vacancy id) → `jina` → `wayback` | API · paste | OAuth2 app at dev.hh.ru | [hh.md](hh.md) |
| Upwork | API* · paste | API* · paste | — | API* · paste | OAuth2, *approved API key required | [upwork.md](upwork.md) |
| LinkedIn | export · paste | export (`Saved Jobs.csv`) · paste | — | export (`Job Applications.csv`) · paste | none — "Download your data" archive | [linkedin.md](linkedin.md) |
| Wellfound | paste | paste | — | paste | none | [wellfound.md](wellfound.md) |
| Indeed | paste | paste | — | paste | none | [indeed.md](indeed.md) |
| getmatch | paste | paste | — | paste | none | [getmatch.md](getmatch.md) |
| Toptal | paste | paste | — | paste | none | [toptal.md](toptal.md) |
| RocketHunt | — | paste · `search_url` deep link | `public_html` (JSON-LD + embedded state) → `jina` → `wayback`; contacts gate never touched | — | none (public pages) | [rockethunt.md](rockethunt.md) |
| JustJoin.it | — | paste · `search_url` deep link | `api` (offer detail by slug) → `public_html` → `wayback` | — | none (public) | [justjoin.md](justjoin.md) |
| Generic (`website`) | — | paste | `public_html` (JSON-LD / Open Graph / text) → `jina` → `wayback` | — | none | [generic.md](generic.md) |

Read-one (ADR-015): one user-supplied URL is fetched the way a browser would — robots.txt honoured, identifying User-Agent, ≤ 1 request per URL per cache TTL, no cookies/login, no CAPTCHA/WAF bypass, no listings or `/api/` bulk access; Jina/Wayback fallbacks apply to public URLs only and archive results are marked historical. `careeros platform read <url> --dry-run --show-attempts` explains every attempt.

Live matrix: `careeros platform capabilities` · `GET /api/platform/capabilities`.

## Commands

```bash
just platform-capabilities                 # what each platform supports, through which method
just platform-connections                  # connection state per platform (no secrets shown)
just platform-connect hh                   # OAuth: prints the authorize URL, asks for the code
just platform-doctor hh                    # config / token / live reachability checks

just platform-profile hh --api             # own profile → snapshot (then: careeros profiles …)
just platform-profile linkedin --export ~/Downloads/Basic_LinkedInDataExport.zip
just platform-profile toptal --text-file profile.txt

just platform-jobs hh -q "data engineer" --remote --limit 50
just platform-jobs-dry wellfound --text-file jobs.txt      # preview only
just platform-applications hh --api
just platform-applications indeed --text-file applied.txt
just platform-sync                         # every API-backed capability of connected platforms
just platform-status                       # observed application statuses, newest first

just platform-detect https://rockethunt.com/jobs/123   # which provider owns that URL
just platform-read   https://rockethunt.com/jobs/123 --show-attempts
just platform-read-dry https://boards.greenhouse.io/acme/jobs/1   # parse it, persist nothing
just platform-refresh 01a03aef-b36f-792d-b2d6-adf41a6963b6        # re-read a stored job
```

**Reading one job by URL** (ADR-015). `platform-read` fetches exactly the page behind the link
you give — `robots.txt` honoured, no cookies, no login, no listings — and files it as an
opportunity with its provenance. A URL you already have is not captured twice: the read attaches
a *snapshot* to the job you know, and only when the posting actually changed
(`GET /api/opportunities/{id}/diff` shows what). A read that fails says which strategy hit what
(`--show-attempts` prints them all); the paste path stays available for pages that cannot be read.
`platform-refresh` takes an opportunity id — a posting that came back 404/gone is recorded as
closed rather than reported as an error.

Paste inputs: open the page on the platform, select all, copy, save as a `.txt` file (or pipe via
stdin with `--text-file -`). Each guide says exactly which page to copy. Parsers never invent
values — anything they cannot read stays empty, and the raw text is kept on the record.

**Sweep semantics.** `platform sync all` walks every capability of every platform and ends with a
tally. A capability that needs something from you — connect first, paste a page, pass a query —
is reported `SKIPPED` with the exact next step. `FAILED` means the platform was actually reached
and something went wrong. Only `FAILED` makes the command exit non-zero, so a sweep on a fresh
install (nothing connected yet) is clean and safe to put in a pipeline such as `make all`.

## Deployment notes (read before shipping the platform layer to Fly/containers)

* **Tokens are a file.** `CAREEROS_PLATFORM_TOKEN_FILE` defaults to `generated/platform/tokens.json`
  — on an ephemeral filesystem (Fly without a volume) it is lost on every redeploy and hh.ru/Upwork
  must be re-authorised. Options: mount a volume and point the variable at it (e.g.
  `/data/platform/tokens.json`), pin tokens via `CAREEROS_<PLATFORM>_ACCESS_TOKEN` /
  `_REFRESH_TOKEN` secrets (shown as `pinned` by `careeros platform connections`; pinned tokens are
  never refreshed or deleted — `disconnect` tells you to unset the variable), or keep platform sync
  local-only (the default today). Tracked in
  [#21](https://github.com/hnkovr/CareerOS/issues/21).
* **Redirect URI must be public.** `CAREEROS_PLATFORM_OAUTH_REDIRECT_BASE` defaults to
  `http://localhost:8000/api/platform/oauth`; on a deployed instance set it to the public base URL
  (`https://<host>/api/platform/oauth`) *and* register `<base>/<platform>/callback` in the hh.ru /
  Upwork app consoles — otherwise the callback 404s after the user has already consented.
* **Platform credentials are NOT pushed to Fly today.** `config/deploy.yml` excludes
  `CAREEROS_HH_*`, `CAREEROS_UPWORK_*` and `CAREEROS_PLATFORM_*` from the `CAREEROS_*` env push
  (pinned by `tests/deploy/test_deploy_config.py`, which reads the credential field names from
  `core/config.py`), because the deployed app does not sync. Adding a new credential setting means
  extending those excludes; moving sync to Fly means inverting the block together with the volume.

## Links for the user (no fetching)

`GET /api/platform/{platform}/urls?q=…&location=…&remote=true` returns the platform's own job-search
page for a query (`search_url`) and the owner's profile URL (`profile_url`). Connectors implement
`search_url(JobQuery)` / `profile_url(handle)`; `None` means "cannot be expressed" (Toptal has no
public search; Indeed/getmatch profiles have no public URL) — callers should say so rather than omit
the platform. The owner's URL comes from the OAuth identity or the latest snapshot
(`preferences.profile_url` / `external_id`). URL templates are best-effort and may need updating
when a platform changes its routes.

## What CareerOS will never do

Log in for you, store passwords or session cookies, run a headless browser, scrape listings, or
apply on your behalf ([ADR-005](../adr/005-no-autonomous-platform-scraping.md)). OAuth tokens you
grant are kept in a git-ignored file with mode 0600 (`CAREEROS_PLATFORM_TOKEN_FILE`); revoke them
on the platform at any time and run `careeros platform disconnect <platform>`.

## Where things are

* Code: `services/careeros/src/careeros/modules/platform/` (connectors under `connectors/<platform>/`).
* API: `/api/platform/*` (capabilities, connections, OAuth, parse, sync, sync-runs, applications).
* Agents: `.claude/agents/careeros-platform-ops.md` (operate), `careeros-platform-connector-dev.md` (extend).
* Tracking: GitHub [#10](https://github.com/hnkovr/CareerOS/issues/10) (epic) · Linear [MY-26](https://linear.app/my-1st/issue/MY-26).
