# Handoff — Universal Job Intelligence lane (careeros-d2), 2026-08-26

Resume: `cd ~/gi/@hnkovr/CareerOS && claude --resume d65a7aca-097b-4eea-ba49-6975c0845dc5`
Plan (approved by owner 2026-08-26): `~/.claude/plans/cozy-percolating-lollipop.md` — slice 1 = phases 0, A, D, E, B, C, G, H.
Spec/RFC: `docs/superpowers/specs/2026-08-26-universal-job-intelligence-design.md` · ADR-015 `docs/adr/015-public-job-url-reads.md` · ADR-016 `docs/adr/016-job-provenance-snapshots.md` (rows added to `docs/adr/README.md`; d1 owns the 017 row in the same file — stage only our lines via a filtered `git apply --cached`).
GitHub: epic #32; children #33 foundation, #34 provenance/snapshots, #35 generic, #36 RocketHunt, #37 hh read-one, #38 JustJoin, #39 discovery, #40 refresh scheduler; backlog #41 web+MCP, #42 ATS, #43 archive.today/search-recovery. Comment posted on #21. Linear: not created yet (use the bulk script with `todoist_reminder: false`).

## Decisions (frozen)
D1 read-one by user URL only (no listings, no `/api/`, no contact gate, no browser automation) · D2 provider = connector, Job = Opportunity, snapshot = OpportunityRaw, provenance = `opportunity_source` · D3 no Playwright · D4 no Redis/S3 · D5 LLM fallback only · D6 ADR 014 = d1 (assistants), 017 = d1 (workflows, landed ff9fd14 with migration c4d8e2f1a9b3); ours 015/016 · D7 Wayback + Jina enabled by default (Jina skipped for private sources) · D8 slice 1 = A–H · D9 MCP backlog · D10 additive edits only in `opportunities/service.py`; never touch `modules/ai`, `api/routers.py`, `cli.py`, `pipeline/service.py`.

## Verified facts (2026-08-26)
RocketHunt: `/{en,ru}/vacancies/<uuid>`, SSR + JSON-LD JobPosting + RSC keys; robots Allow `/`, Disallow `/api/`; ToS forbids bulk/automated access; salary = aggregator estimate; contacts gated. JustJoin: `GET /api/candidate-api/offers[/<slug>]` 200 (cursor pagination), robots Disallow `/api/`, user terms §11.3. HH API anonymous → 403. Jina anonymous 200. Reference payloads saved in the session scratchpad (`rh-vacancy.html`, `jj.json`, `jj-detail.json`, `hh.json`) — sanitise before turning into fixtures.

## Landed (pushed to origin/main)
- `9ea8de2` enums (Platform += rockethunt/justjoin/website, Source += rockethunt/justjoin, SourceRelation/FieldSource) + regenerated `career/schemas` and `packages/schemas` — built in a clean worktree at HEAD so the contracts gate matches committed code.
- `7cbd5c1` ruff-format of the root master prompt (the CI python/lint job checks `.md` python blocks — that was the standing red).
- `c1e3e17` **platform read-one foundation** (#33, #35): `sources.py` (SourceKind/SourceRef/CanonicalSource/DetectionResult/detect), `fetch/` (artifact, quality, budget, RFC-9309 robots, cache, extract/{jsonld,embedded,text}, strategies/{api,public_html,jina,wayback} + `run_chain`), `Capabilities.read_job/access`, `BaseConnector.detect/canonicalize/fetch_job/extract_job/fetch_job_api`, `registry.verify()` rules, `http.request_text`, `connectors/generic` (Platform.website), `CAREEROS_JOB_FETCH_*` settings, import-linter contract extended, tests+fixtures. Bot: `HOME_URLS` += rockethunt/justjoin, `known_platforms()` excludes `website`; `sync_all` no longer iterates `SyncKind.job`.
- `685112b` **provenance/snapshots** (#34): `opportunity_source` table, snapshot columns on `OpportunityRaw`, `Opportunity.platform/external_id/canonical_url/field_evidence`, `fingerprint`/`identity_candidates`, `record_source/record_snapshot/list_*/diff/merge_field_evidence`, `GET /api/opportunities/{id}/{sources,snapshots,diff}`, migration **e887c1a8d8c5** (chained after the workflow lane's c4d8e2f1a9b3, applied to the dev DB, `alembic check` clean).
- `061c01f` docs: ADR-015, ADR-016, README rows, UJI spec/RFC, `docs/platform/{rockethunt,justjoin,generic}.md` research records, both capability matrices.
- `98746a6` this handoff.
Full lane green on `careeros_test_d2` (platform, opportunities, pipeline, search, profiles, workflows, bot) after Docker/Postgres were restarted — the earlier 367 s/OSError run was the dead Docker daemon, not network access.
Trackers: GH epic #32, children #33–#43; Linear MY-43…MY-54 (bulk script + settings copy, no Todoist spam); comment posted on #21.

## In flight (four subagents, resumed on Opus 5 after the Fable 5 limit stopped them)
- **A3 read_job integration** — `platform/sync.py` (`read_job`, `refresh`), `POST /api/platform/read`, `GET /api/platform/detect`, `POST /api/opportunities/{id}/refresh`, CLI `platform read|detect|refresh`, bot `_capture` URL branch, Justfile recipes, `tests/platform/test_read.py`.
- **D RocketHunt** — `connectors/rockethunt/**` + fixtures/tests/docs (JSON-LD primary, RSC embedded best-effort, aggregator-estimate salary evidence, contacts gate untouched, `/api/` never called).
- **B hh.ru read-one** — `connectors/hh/**` (+`hosts.py`), status semantics 200/403/404/429/5xx, jina/wayback fallbacks, §53 canonicalisation tests, live test behind `CAREEROS_LIVE_TESTS=1`.
- **C JustJoin** — `connectors/justjoin/**` (detail-by-slug only; no listing function exists by design), open enums, schema-drift fingerprint, fixtures/tests/docs.
Orchestrator still owes: register the three connectors in `connectors/__init__.py::CONNECTOR_MODULES` and `enums.PLATFORMS`; add `Capabilities.fallback: bool = False` set true only by the generic connector (the web /platforms page files account-less read-one connectors into a "Generic readers" section by a derived predicate that otherwise misfiles rockethunt/justjoin — promised to the web lane); then `make contracts`, run the gate, commit per path, push, and ping careeros-41 that read/detect are live.
Shared-file caution: a third lane (workstation) is editing `Makefile`, `.claude/CLAUDE.md`, `.claude/settings.json`, `config/workstation.yml`, `scripts/workstation-*.sh`, `docs/runbooks/`, `tests/test_workstation_config.py` — stage `Makefile`/`Justfile` hunk-scoped, never whole-file.

## Next after the connectors
Phase G (JobQuery filters + fan-out, #39), phase H (refresh scheduler behind `CAREEROS_JOB_REFRESH_ENABLED`, #40), then `.claude/CLAUDE.md` layout line (connectors 7→10, `fetch/`), `.claude/TODO.md`, `CLAUDE-curr-status.md`, `PROMPTS-LOG.md` (+ru), `make all` via the careeros-gate agent, smart-commit. Backlog: #41 web+MCP, #42 ATS, #43 archive.today/search-recovery.

## Shared-tree etiquette
main only; `git add` by explicit paths only; ping d1 (uds:/tmp/cc-socks/80628.sock, "careeros-41") and the bot lane before staging shared files; test DB `careeros_test_d2`; never `git add -A`, never commit `.claude/settings.local.json` or `.env.secrets*`.
