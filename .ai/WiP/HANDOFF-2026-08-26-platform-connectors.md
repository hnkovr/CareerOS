# HANDOFF — platform connectors lane (careeros-d2) — 2026-08-26

Keep-list snapshot for `/compact-safely`. State is on disk; the summary only needs to point here.

## Resume
```
cd ~/gi/@hnkovr/CareerOS && claude --resume d65a7aca-097b-4eea-ba49-6975c0845dc5
```
Lane: platform connectors (own profile · job search · application statuses). Test DB for this lane: `CAREEROS_TEST_DATABASE_URL=postgresql+asyncpg://careeros:careeros@localhost:5432/careeros_test_d2`.

## Open tasks (keys + status)
| Key | Status | What |
|---|---|---|
| GH [#10](https://github.com/hnkovr/CareerOS/issues/10) / Linear [MY-26](https://linear.app/my-1st/issue/MY-26) | open (epic) | umbrella; close when #20/#21 land or are re-scoped |
| GH [#20](https://github.com/hnkovr/CareerOS/issues/20) / MY-36 | backlog | web Platforms page (capabilities, connections/connect, paste box, sync runs, application statuses) |
| GH [#21](https://github.com/hnkovr/CareerOS/issues/21) / MY-37 | backlog | follow-ups: Fly volume + public redirect base if sync moves to Fly; single job-URL capture; email statuses via P1 inbox; observations ↔ pipeline Application; live token tests; Upwork `VERIFY LIVE` fields; shared-parser suggestions (relative dates need `ago`, compact units, `now` propagation, schema fields `location/name/education`, `CompensationPeriod.week`, `duration`) |
| owner | blocked on owner | register OAuth apps (hh.ru `dev.hh.ru`; Upwork `upwork.com/developer/keys`) → `.env.secrets` → `just platform-connect hh|upwork`; download the LinkedIn archive |
| owner | decision | `git push` of `main` (last ~12 commits unpushed); Todoist overdue apply: `just -f ~/.ai/skills/_scripts/tasks/todoist/Justfile postpone-overdue` (42 tasks previewed, not applied) |
| Linear MY-27…MY-35 | statuses stale | wrapper is create-only; GH #11–#19 are closed, Linear still shows In Progress/Done as created |

## Done (commit SHAs, all on `main`)
- `155c30c` enums (Platform/Source += hh, indeed, getmatch), demo channels, schemas regenerated, CORS template quoting fix
- `7d5052d` contract/registry/matrix/parsers/stubs · `394936e` tokens/OAuth/HTTP/env templates · `d3f8548` models+migration+PlatformService · `11eac38` sync/API/CLI/just · `54eaa05` `jobs --extra` · `db14cce` public search w/o token
- connectors: hh `f76824c` · upwork `d754447` · linkedin `68795b4` · wellfound `c1490fd` · indeed `04da32b` · getmatch `5038989` · toptal `1bcec8d`
- docs/tooling: `25c0035` ADR + matrix + guide + agents · `adc0cd1` ADR→013 + `config/platform.yml` (also swept the bot lane's staged files via the shared index) · `9b3f800` status/TODO · `3f7b36c` guide row · `db7964e` migration format
- `d9a1629` review fixes: auto token refresh (expired/401), env-pinned tokens `pinned` (never refreshed/deleted), paste↔API observation merge (hash excludes external_id), DB-backed OAuth state + callback auth by state, `?status` alias, warnings→partial, exception-safe runs, ingest `external_id/raw_payload` passthrough, user-scoped URL lookup, hh resume PII stripped
- skill `/careeros-platform-sync` (catalog `~/.ai/skills/_catalog/projects/careeros/careeros-platform-sync`, Claude hardlink, hub symlink, INDEX rows — ~/.ai changes are uncommitted in that repo); agents `.claude/agents/careeros-platform-{ops,connector-dev}.md`
- GH issues #10–#21 created (#11–#19 closed with commit refs); Linear project CareerOS MY-26…MY-37 created without Todoist reminders
- memory: `~/.claude/projects/-Users-nk-myg-gi--hnkovr-CareerOS/memory/` (parallel sessions, ADR numbering, platform connectors, linear bulk)

## Not done / not captured — and why
- Web Platforms page (#20) — not requested in the original scope; API/CLI only.
- Platform sync on Fly — deliberately local-only; bot lane excluded `CAREEROS_HH_*/UPWORK_*/PLATFORM_*` from the env push (pinned by `tests/deploy/test_deploy_config.py`).
- Live API verification (hh `/resumes/mine`, `similar_vacancies`; Upwork GraphQL fields) — no credentials exist yet; marked `VERIFY LIVE`, checked by `careeros platform doctor <p>`.
- Shared-parser improvements suggested by builders — left as #21 to avoid destabilising seven green connectors at the end of the run.
- Scratchpad artifacts (`gh-issues.txt`, `linear-issues.txt`, `linear-settings-bulk.yml`, `hh-openapi.yaml`, `write_fixtures.py`) — disposable; the reusable facts (bulk Linear settings trick) are in memory.
- Uncommitted files in the tree at snapshot time belong to the P1 lane (insights/funnel/market/skills_gap, pipeline/opportunities service edits, profiles drift test) and to `Makefile`/`Justfile`/`.claude/CLAUDE.md` edits by another session — not this lane's.

## Decisions accepted (do not re-litigate)
- ADR numbering frozen with the peer sessions: **012 = Telegram bot surface, 013 = platform connectors**; 011 is a permanent gap.
- OAuth tokens are allowed (ADR-013 §4): 0600 token file `generated/platform/tokens.json`, env override marked `pinned`; passwords/cookies/HTML fetching remain forbidden (ADR-005).
- No HTML is fetched from any platform, not even single job pages (possible follow-up per ADR-004's ATS precedent).
- `application_observation` lives in the platform module until Pipeline consumes it; `PlatformSyncService` is the only platform→domain caller (import-linter enforces connector purity).
- Shared-tree etiquette: stay on `main`, explicit paths only, ping peers "committing now" while they are active, per-session test DBs (`_d1` P1 lane, `_d2` this lane).
- Todoist overdue plan is applied only after the owner reviews it.

## Gates at snapshot
`uv run pytest` (all lanes) green · `just lint` clean (ruff, format, pyright, import-linter, env templates, web lint) · `tests/deploy` 49 green · web `typecheck`/`lint` clean.
