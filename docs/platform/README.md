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

| Platform | Own profile | Job search | Application statuses | Auth | Guide |
|---|---|---|---|---|---|
| hh.ru | API · paste | API · paste | API · paste | OAuth2 app at dev.hh.ru | [hh.md](hh.md) |
| Upwork | API* · paste | API* · paste | API* · paste | OAuth2, *approved API key required | [upwork.md](upwork.md) |
| LinkedIn | export · paste | export (`Saved Jobs.csv`) · paste | export (`Job Applications.csv`) · paste | none — "Download your data" archive | [linkedin.md](linkedin.md) |
| Wellfound | paste | paste | paste | none | [wellfound.md](wellfound.md) |
| Indeed | paste | paste | paste | none | [indeed.md](indeed.md) |
| getmatch | paste | paste | paste | none | [getmatch.md](getmatch.md) |
| Toptal | paste | paste | paste | none | [toptal.md](toptal.md) |

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
```

Paste inputs: open the page on the platform, select all, copy, save as a `.txt` file (or pipe via
stdin with `--text-file -`). Each guide says exactly which page to copy. Parsers never invent
values — anything they cannot read stays empty, and the raw text is kept on the record.

## Deployment notes (read before shipping the platform layer to Fly/containers)

* **Tokens are a file.** `CAREEROS_PLATFORM_TOKEN_FILE` defaults to `generated/platform/tokens.json`
  — on an ephemeral filesystem (Fly without a volume) it is lost on every redeploy and hh.ru/Upwork
  must be re-authorised. Options: mount a volume and point the variable at it (e.g.
  `/data/platform/tokens.json`), pin tokens via `CAREEROS_<PLATFORM>_ACCESS_TOKEN` /
  `_REFRESH_TOKEN` secrets, or keep platform sync local-only (the default today). Tracked in
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
