# Telegram bot

The bot is CareerOS's phone-first surface: forward a job description to it, get it parsed,
deduped and scored, and triage it inline. Owner-gated, single user, no external writes.

Design: [`docs/superpowers/specs/2026-08-25-careeros-telegram-bot-design.md`](../superpowers/specs/2026-08-25-careeros-telegram-bot-design.md) ·
Decision: [ADR 012](../adr/012-telegram-bot-surface.md)

**Status:** the webhook surface, its three gates, the ownership claim and capture are implemented
([#1](https://github.com/hnkovr/CareerOS/issues/1), [#2](https://github.com/hnkovr/CareerOS/issues/2),
[#3](https://github.com/hnkovr/CareerOS/issues/3), [#8](https://github.com/hnkovr/CareerOS/issues/8)).
Still open: triage callbacks ([#4](https://github.com/hnkovr/CareerOS/issues/4)), career commands
([#5](https://github.com/hnkovr/CareerOS/issues/5)), notifications
([#7](https://github.com/hnkovr/CareerOS/issues/7)) and the first deploy
([#9](https://github.com/hnkovr/CareerOS/issues/9)).

## The one rule

A webhook is **exclusive**. Registering one takes updates away from whoever holds it. So:

* `just bot-webhook-set` refuses a foreign owner unless you pass `-- --force`;
* the app claims the webhook at startup only when it is unset or already its own;
* `CAREEROS_TG_PUBLIC_URL` gates eligibility — unset means this process never contacts Telegram,
  so a local run cannot steal the deployed bot's updates.

Telegram, not the app, is the authority on who owns the webhook. `/status` reports only the claim
made at that process's startup; `just bot-webhook-info` asks Telegram.

## Commands

| Intent | Command |
|---|---|
| Is the bot healthy? | `just bot-token-check` |
| Who owns the webhook? | `just bot-webhook-info` |
| Claim it | `just bot-webhook-set` (foreign owner → `just bot-webhook-set -- --force`) |
| Release it | `just bot-webhook-delete` |
| Mint or repair the token | `just bot-token-ensure` |
| Run locally | `just bot-run` |
| Webhook ops from the app | `careeros bot webhook-info \| webhook-set \| webhook-delete \| check` |
| Dry-run the deploy | `just deploy-dry` |
| Deploy | `just deploy-fly` |
| Logs / status | `just fly-logs` · `just fly-status` |

A SessionStart hook (`scripts/hooks/bot-guard.sh`) prints the webhook state at the top of every
agent session, so a bot that quietly lost its webhook is noticed immediately rather than the next
time someone messages it.

## Configuration

Secrets (`config/.env.secrets`, never committed — values resolved via `find-secret.sh`):

| Variable | Purpose |
|---|---|
| `CAREEROS_TG_BOT_TOKEN` | bot token; every code path that uses it ends in `getMe` |
| `CAREEROS_TG_WEBHOOK_SECRET` | echoed by Telegram in `X-Telegram-Bot-Api-Secret-Token` |
| `CAREEROS_TG_OWNER_CHAT_ID` | the only chat served; every other chat gets `200` and no reply |
| `CAREEROS_VAULT_GIT_URL` | private vault remote; unset → the bundled demo vault |

Non-secret settings live in `config/.env.config.template` (`CAREEROS_TG_ENABLED`,
`CAREEROS_TG_WEBHOOK_PATH`, `CAREEROS_TG_PUBLIC_URL`, `CAREEROS_TG_NOTIFY_MIN_SCORE`).

Ops scalars — bot handle, Fly app, region, URL — are **not** duplicated in scripts. They live in
`~/.ai/skills/_settings/careeros.yml`, which the scripts, the `/careeros-bot` skill and the
`fly-ops` agent all read. `tests/deploy/` asserts every key a script reads actually exists there.

## First deploy

1. **Token** — `just bot-token-ensure`. Opens BotFather when the bot does not exist, stores the
   value `0600`, and verifies with `getMe`. A token for the wrong bot is rejected, not stored.
2. **Webhook secret** — `openssl rand -hex 32` → `CAREEROS_TG_WEBHOOK_SECRET`.
3. **Owner chat id** — message the bot once while the webhook is unset, read `getUpdates`, store
   `CAREEROS_TG_OWNER_CHAT_ID`.
4. **Postgres** — `fly mpg create`, then `fly mpg attach --variable-name CAREEROS_DATABASE_URL`.
   Note `core/db.py` normalises the `postgres://` scheme Fly hands out to `postgresql+asyncpg://`.
5. **Preflight** — `just deploy-check`, then `just deploy-dry` to read every command first.
6. **Deploy** — `just deploy-fly`. Migrations run as `release_command`, before traffic shifts; the
   recipe claims the webhook afterwards.
7. **Verify** — `just bot-webhook-info` shows our URL and `pending=0`; message the bot as owner.

Only `CAREEROS_*` variables are pushed to Fly (`config/deploy.yml` `env_push.include` is an
allow-list), and `CAREEROS_DATABASE_URL` is explicitly excluded so a local DSN cannot overwrite the
one the platform attached.

## Why single-machine

`fly.toml` pins `--ha=false`, `min_machines_running = 0` and `auto_stop_machines = "off"`:

* two machines would be two webhook claimants;
* scale-to-zero is fine because a webhook delivery wakes a stopped machine in ~9s;
* but auto-stop must be **off**, because the handler acknowledges Telegram immediately (`200`) and
  finishes the AI work afterwards — an auto-stop would kill it mid-flight.

`tests/deploy/test_deploy_config.py` enforces all three.

## Diagnosing a silent bot

Stop at the first failure.

1. `just bot-token-check` — token valid, right bot, secret present? A token that works but belongs
   to a different bot is the expensive failure mode.
2. `just bot-webhook-info` — `url` empty → nothing is listening. `url` not ours → another
   deployment holds it. `last_error_message` → Telegram is trying and failing.
3. `pending_update_count` climbing → Telegram delivers but the app rejects. Check `just fly-logs`
   for `403` (secret mismatch) or a non-2xx.
4. Messaging from a non-owner account gets `200` and silence, by design.
5. A sleeping machine is not a fault — the next delivery wakes it.

## Local development

The webhook is global per bot token, so a local run cannot share the deployed bot. Either release
the webhook first (`just bot-webhook-delete`, then `just bot-webhook-set` when you redeploy) or use
a second bot with its own token. Leaving `CAREEROS_TG_PUBLIC_URL` unset locally is the safety net:
the process then never contacts Telegram at all.

## Tests

`tests/deploy/` runs the real bash scripts against a faked `$HOME` and a stubbed `curl`, so the
refusal paths are exercised rather than described:

```bash
uv run pytest tests/deploy -q
```

Covered: wrong-bot rejection, unreachable-Telegram vs bad-token distinction, foreign-owner refusal
and `--force` override, secret required before claiming, the token never appearing in output, the
hook never exiting non-zero, response caching, and the fly.toml / deploy.yml invariants above.
