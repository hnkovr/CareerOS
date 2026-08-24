# CareerOS — granular recipes. `just --list`.
set dotenv-load := true
set shell := ["bash", "-euo", "pipefail", "-c"]

svc := "services/careeros"
alembic := "uv run alembic -c " + svc + "/alembic.ini"

default:
    @just --list

# ---------- dev loop ----------

# start postgres+redis in docker, run API (reload) and worker locally
dev:
    docker compose up -d postgres redis
    just migrate
    (CAREEROS_TASK_RUNNER=arq uv run careeros-worker &) ; CAREEROS_TASK_RUNNER=arq uv run careeros-api

# API only, no redis needed (tasks run inline)
api-inline:
    CAREEROS_TASK_RUNNER=inline uv run careeros-api

infra-up:
    docker compose up -d postgres redis

infra-down:
    docker compose down

# ---------- quality ----------

test *ARGS:
    uv run pytest {{ARGS}}
    if [ -f apps/web/package.json ] && [ -d node_modules ]; then npm run -w apps/web test --if-present; fi

test-py *ARGS:
    uv run pytest {{ARGS}}

lint:
    uv run ruff check .
    uv run ruff format --check .
    just typecheck
    uv run lint-imports
    python3 scripts/env-render.py --check
    if [ -f apps/web/package.json ] && [ -d node_modules ]; then npm run -w apps/web lint; fi

fmt:
    uv run ruff check --fix .
    uv run ruff format .
    if [ -f apps/web/package.json ] && [ -d node_modules ]; then npm run -w apps/web format --if-present; fi

typecheck:
    uv run pyright
    if [ -f apps/web/package.json ] && [ -d node_modules ]; then npm run -w apps/web typecheck --if-present; fi

# ---------- vault / cv ----------

# validate a vault (default: CAREEROS_VAULT_PATH or demo)
validate-career VAULT="":
    uv run careeros vault validate {{ if VAULT != "" { "--path " + VAULT } else { "" } }}

# initialise a private vault from the scaffold at career/private (or PATH)
vault-init PATH="career/private":
    uv run careeros vault init --path {{PATH}}

generate-cv VARIANT="general-core" *ARGS:
    uv run careeros cv generate {{VARIANT}} {{ARGS}}

export-schemas:
    uv run careeros vault export-schemas

# ---------- db ----------

migrate:
    {{alembic}} upgrade head

migration MESSAGE:
    {{alembic}} revision --autogenerate -m "{{MESSAGE}}"

seed:
    uv run careeros seed run

db-reset:
    docker compose down -v postgres
    docker compose up -d postgres
    sleep 3
    just migrate

# ---------- contracts ----------

openapi:
    uv run careeros export-openapi
    if [ -d node_modules ]; then npm run generate; fi

# ---------- e2e ----------

# smoke-test a running stack (default: local API :8000; pass URLs to override)
e2e API="http://localhost:8000" WEB="":
    scripts/e2e-smoke.sh {{API}} {{WEB}}

# ---------- docker ----------

build:
    docker compose --profile web build

logs *ARGS:
    docker compose logs -f {{ARGS}}

# ---------- telegram bot ----------

deploy_jf := "$HOME/.ai/skills/_scripts/deploy/Justfile"

# run API locally with the bot enabled (tasks inline, no redis needed)
bot-run:
    CAREEROS_TASK_RUNNER=inline CAREEROS_TG_ENABLED=true uv run careeros-api

# ask Telegram who owns the webhook (the app's own /status only knows its startup claim)
bot-webhook-info:
    scripts/prj-tools/tg-bot.sh info

# claim the webhook; refuses a foreign owner unless: just bot-webhook-set -- --force
bot-webhook-set *ARGS:
    scripts/prj-tools/tg-bot.sh set {{ARGS}}

bot-webhook-delete:
    scripts/prj-tools/tg-bot.sh delete

# token present + belongs to the right bot + webhook state + secret present
bot-token-check:
    scripts/prj-tools/tg-bot.sh check

# mint or repair the bot token (opens BotFather, verifies with getMe)
bot-token-ensure:
    $HOME/.ai/skills/_scripts/integrations/telegram/ensure-tg-bot.sh \
      --var $(yq -r '.careeros.tg_bot.deploy.token_secret' $HOME/.ai/skills/_settings/careeros.yml) \
      --bot $(yq -r '.careeros.tg_bot.handle' $HOME/.ai/skills/_settings/careeros.yml)

# ---------- deploy: fly ----------

# preflight: CLI installed? authed? config present?
deploy-check:
    just -f {{deploy_jf}} check fly

# print every command the deploy would run, execute none
deploy-dry:
    just -f {{deploy_jf}} deploy-dry fly

# ship it (preflight must pass); then claim the webhook
deploy-fly:
    just -f {{deploy_jf}} deploy fly
    just bot-webhook-set

fly-logs:
    just -f {{deploy_jf}} logs fly

fly-status:
    flyctl status
