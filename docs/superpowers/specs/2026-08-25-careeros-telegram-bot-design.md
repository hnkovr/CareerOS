# CareerOS Telegram bot — design

Date: 2026-08-25 · Status: approved · Slice: P0.9 (after P0.8 hardening)

## Purpose

Give CareerOS the mobile surface the product brief scopes (`docs/product/00-product-brief.md`:
"фокус на triage, inbox, approve/reject, AI summaries, notifications, share-to-app") without
building an Expo app. A Telegram bot delivers capture, triage and notifications on a phone
today; the native mobile client stays a P2 concern.

The bot is a **projection of existing modules**, not a new source of truth. It adds no domain
logic: it renders what `opportunities`, `cv`, `vault` and `profiles` already compute.

## Invariant compliance

| # | Invariant | How this design satisfies it |
|---|---|---|
| 1 | Canonical facts live in the vault | Bot never writes facts. P0 reads only. |
| 2 | AI may not invent; `derived_from[]` always | Bot renders provenance on every generated bullet; never strips it. |
| 3 | Vault writes only via `apply_change()` | P0 bot has no vault-write path at all. P1 seam documented below. |
| 4 | Scoring deterministic over `scoring/model.yaml` | Bot displays the breakdown returned by the scoring module; never recomputes or re-ranks. |
| 5 | No scraping / no auto-apply / no auto-send | Bot has zero outbound platform writes. Buttons mutate internal state only. |
| 6 | No provider-specific AI outside `modules/ai/providers` | Bot calls `modules/ai` service; contains no provider code. |
| 7 | Cross-module calls via `service.py` | `bot/service.py` is the only cross-module caller; handlers never touch another module's ORM. |
| 8 | AI output invalid until Pydantic-validated | aiogram is Pydantic-based; AI results arrive already validated by `modules/ai`. |

## A. Module layout

New `services/careeros/src/careeros/modules/bot/`:

```
__init__.py
router.py       POST /tg/webhook — the only HTTP surface
service.py      the sole cross-module entry point (invariant 7)
handlers/
  commands.py   /next /top /opp /cv /facts /profile /status /whoami /help
  capture.py    free text + URL -> opportunity ingest
  callbacks.py  inline-button transitions
keyboards.py    inline keyboard builders
formatting.py   MarkdownV2 escaping, score cards, provenance rendering
notify.py       outbound: high-score alerts, called by the worker
webhook.py      startup claim/release, ownership rule
dedup.py        update_id idempotency guard
schemas.py      settings + typed view models
enums.py
cli.py          careeros bot webhook-info|set|delete|run
```

Registered in `api/routers.py::ROUTERS` and `cli.py::_register_subcommands`.

## B. Transport and security

Route: `POST /tg/webhook` (matches the fleet convention already used by wordsman-tg-bot).

Three gates, evaluated in order; all must pass:

1. **Secret token** — `X-Telegram-Bot-Api-Secret-Token` compared to
   `CAREEROS_TG_WEBHOOK_SECRET` with `hmac.compare_digest`. Mismatch or absent → `403`,
   no body parsed.
2. **Owner gate** — the update's effective chat id must equal `CAREEROS_TG_OWNER_CHAT_ID`.
   Anything else → `200` with no side effect (never `403`: a non-owner must not be able to
   distinguish a live bot from a dead one). Logged once per chat id, not per update.
3. **Idempotency** — `update_id` checked against a bounded LRU (in-process) plus a short-TTL
   row. A repeat is acknowledged and dropped.

### Webhook ownership

Cold start claims the webhook **only** when Telegram's current `getWebhookInfo().url` is
empty or already equals our own `CAREEROS_TG_PUBLIC_URL`. It never takes the webhook from a
different live URL — otherwise restarting a demoted host silently undoes a failover. That bug
is known-real in this fleet (wordsman, fixed 2026-08-20). Deliberate takeover:
`CAREEROS_TG_WEBHOOK_FORCE_CLAIM=true` or `careeros bot webhook set --force`.

Eligibility key is `CAREEROS_TG_PUBLIC_URL`: unset → the process never talks to Telegram at
all, so a local dev run can never steal production's updates.

### ACK-then-work

The handler returns `200` immediately and dispatches work through the task runner. Telegram
retries an update that is not acknowledged within ~60s, and on Fly `CAREEROS_TASK_RUNNER=inline`
means an AI parse would run inside the request. Without ACK-then-work, a slow AI call produces
a retry storm and duplicate ingests; gate 3 is the second line of defence.

Long operations post a placeholder message and edit it on completion.

## C. Command surface (P0)

**Capture** — any non-command text over a length threshold, or a URL, is treated as a job
description: parse (heuristic, AI optional) → dedup → deterministic score → reply card with
title, company, total score and the top-3 breakdown contributions, plus a dedup note when it
matched an existing opportunity. Buttons: `Skip` · `Save` · `Analyze` · `Prompt`.

**Triage** — `/next` (next untriaged, one card), `/top [n]` (ranked, default 5), `/opp <id>`.

**Career, read-only** — `/cv [variant]` generates and sends the PDF artifact;
`/facts <query>` searches vault facts; `/profile` returns platform health scores.

**Owner ops** — `/status` (env, git sha, DB reachable, AI provider, webhook state, vault rev),
`/whoami` (chat id + owner match), `/help`.

**Outbound** — `notify.high_score(opportunity)` called by the worker when score exceeds
`CAREEROS_TG_NOTIFY_MIN_SCORE`, deduped per opportunity id so a rescore cannot re-alert.
Honours the brief's "без спама".

### Explicitly not in P0

Vault writes from chat (`/fact add` → `Vault.apply_change()`), application Kanban, inbox
triage, multi-user. The seam: `bot/service.py` already isolates cross-module calls, so a P1
write path is an added service method plus an approval gate, not a refactor.

## D. Fly deployment

App `careeros`, region `ams`, kind `paas`, driven by the existing `deploy-app` driver and
`~/.ai/templates/profiles/fly.yml`.

- **One machine, `--ha=false`.** Two machines are two webhook claimants; the second would
  fight the first. The profile already defaults to this for exactly this reason.
- `auto_start_machines = true`, `min_machines_running = 0` — the webhook POST wakes a stopped
  machine in ~9s, which is why webhook beats long-polling on Fly's trial. But
  **`auto_stop_machines = "off"`**, not `"suspend"`: this handler ACKs first and keeps working,
  and an auto-stop would kill it mid-flight. (Rule carried by the `fly-ops` agent, learned on
  wordsman.)
- `release_command` runs `alembic upgrade head` before the new version takes traffic.
- Image: the existing `deploy/docker/Dockerfile.careeros`.
- **Postgres**: `fly mpg create` then
  `fly mpg attach --variable-name CAREEROS_DATABASE_URL`. pgvector is unused today (verified:
  only a P1 comment in `vault/service.py`), so no extension requirement.
- **Redis: none.** `CAREEROS_TASK_RUNNER=inline` removes the add-on and the worker machine.
  Revisit when P1 adds Gmail sync.
- **Vault**: cloned read-only at boot from `CAREEROS_VAULT_GIT_URL` into the image's
  `career/private`; falls back to the bundled demo vault when unset, so a first deploy is
  green with zero private data. No volume in P0 (nothing is written).

### Required code fix

`core/db.py` passes `settings.database_url` straight to `create_async_engine`. Fly MPG hands
out a `postgres://` URL, which SQLAlchemy's async engine rejects. Add a normalizer mapping
`postgres://` and `postgresql://` → `postgresql+asyncpg://`, applied at engine construction.

## E. Configuration and secrets

New secrets (`config/.env.secrets.demo.template`, blank or `${VAR:-}` forms only — a non-empty
literal there is a leak):

| Var | Purpose |
|---|---|
| `CAREEROS_TG_BOT_TOKEN` | bot token; resolved via `find-secret.sh`, verified by `getMe` |
| `CAREEROS_TG_WEBHOOK_SECRET` | echoed in `X-Telegram-Bot-Api-Secret-Token` |
| `CAREEROS_TG_OWNER_CHAT_ID` | the only chat the bot serves |
| `CAREEROS_VAULT_GIT_URL` | private vault remote (carries a token → secret, not config) |

New config (`config/.env.config.template`): `CAREEROS_TG_PUBLIC_URL`,
`CAREEROS_TG_WEBHOOK_PATH` (default `/tg/webhook`), `CAREEROS_TG_NOTIFY_MIN_SCORE`,
`CAREEROS_TG_ENABLED`.

`config/deploy.yml` overlay uses an **allow-list**, so no unrelated machine secret can ship:

```yaml
name: careeros
region: ams
env_push:
  include: ["CAREEROS_*"]
```

## F. Tooling

- **scripts** — `scripts/prj-tools/tg-bot.sh` (`webhook info|set|delete|check`), Just recipes
  (`bot-run`, `bot-webhook-*`, `bot-token-check`, `deploy-fly`, `fly-logs`, `fly-status`),
  `make deploy`. Plus a shared-infra gap: `~/.ai/templates/patterns/fly.toml` is referenced by
  `fly.yml`'s `config_file.template` but does not exist — creating it unblocks every future
  Fly project, not just this one.
- **hook** — `scripts/hooks/bot-guard.sh`, SessionStart, reporting
  `webhook (owns) | standby | off` plus app status, mirroring the existing session guards.
- **skill** — one `careeros-bot` skill, created through `/create-skill` per the
  skill-creation policy (never hand-written).
- **agents** — no new agent. `fly-ops` already claims "ANY project wired to the deploy-app
  driver"; CareerOS is added to its description and to
  `~/.ai/skills/_settings/careeros.yml#tg_bot.deploy` as the agent-facing SSoT, exactly as
  `wordsman.yml` and `wordcloud.yml` do. A `careeros-fly-ops` agent would duplicate `fly-ops`.

## G. Testing

Unit and contract, no network:

- absent / wrong secret token → `403`, body never parsed
- correct secret + non-owner chat → `200`, zero side effects
- duplicate `update_id` → exactly one ingest
- ownership claim: URL unset → claims; URL ours → claims; URL foreign → refuses; `--force` overrides
- command routing table; capture threshold; URL detection
- MarkdownV2 escaping (the `.`/`-`/`(` class that silently 400s the Bot API)
- score-card rendering pins the deterministic breakdown
- `postgres://` → `postgresql+asyncpg://` normalizer

## H. Documentation

- ADR 012 — Telegram bot as the P0 mobile surface: webhook over polling, owner-gated,
  read-only vault, no auto-send.
- `docs/developer-guide/telegram-bot.md` — run locally, claim/release webhook, deploy runbook.
- `README.md`, `.claude/TODO.md`, `.claude/CLAUDE-curr-status.md` updated.

## Risks

| Risk | Mitigation |
|---|---|
| Two machines claim one webhook | `--ha=false`; ownership rule refuses foreign URLs |
| Telegram retry storm on slow AI | ACK-then-work + `update_id` dedup |
| MPG URL scheme rejected by asyncpg | normalizer in `core/db.py`, unit-tested |
| Token belongs to the wrong bot | every path ends in `getMe`; username mismatch is hard failure |
| Fly trial machine sleeps | webhook delivery wakes it; no poller to keep alive |
| Auto-stop kills a background handler | `auto_stop_machines = "off"`; retries covered by `update_id` dedup |
| Secret sprawl to the platform | `env_push.include: ["CAREEROS_*"]` allow-list |
