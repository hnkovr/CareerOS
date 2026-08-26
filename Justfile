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
    #!/usr/bin/env bash
    set -euo pipefail
    # A stopped daemon otherwise fails with a socket path, which names the symptom, not the fix.
    docker info >/dev/null 2>&1 || {
      echo "docker daemon is not running — start Docker Desktop (open -a Docker), then re-run" >&2
      exit 1
    }
    docker compose up -d postgres redis

infra-down:
    docker compose down

# ---------- quality ----------

# true when the web workspace is installed; every web step says so when it is not,
# so a skipped half of the gate can never read as a green gate
_web_ready := "[ -f apps/web/package.json ] && [ -d node_modules ]"
_no_web := "echo '  · web steps skipped — run `npm install` to include them' >&2"

# the gate lives in scripts/gate.sh so CI runs the SAME checks (GH #23)
test *ARGS:
    scripts/gate.sh test {{ARGS}}

test-py *ARGS:
    uv run pytest {{ARGS}}

lint:
    scripts/gate.sh lint

fmt:
    uv run ruff check --fix .
    uv run ruff format .
    if {{_web_ready}}; then npm run -w apps/web format --if-present; else {{_no_web}}; fi

typecheck:
    uv run pyright
    if {{_web_ready}}; then npm run -w apps/web typecheck --if-present; else {{_no_web}}; fi

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

# ---------- platform connectors (ADR-013) ----------

# shared helper: every platform recipe goes through the CLI
_platform *ARGS:
    uv run careeros platform {{ARGS}}

platform-capabilities:
    @just _platform capabilities

platform-connections:
    @just _platform connections

# OAuth connect (hh, upwork): prints the authorize URL, then asks for the code
platform-connect PLATFORM:
    @just _platform connect {{PLATFORM}}

platform-doctor PLATFORM:
    @just _platform doctor {{PLATFORM}}

# sync every API-backed capability of the connected platforms (paste-only ones are listed as skipped)
platform-sync PLATFORM="all" *ARGS:
    @just _platform sync {{PLATFORM}} {{ARGS}}

platform-sync-dry PLATFORM="all" *ARGS:
    @just _platform sync {{PLATFORM}} --dry-run {{ARGS}}

# own profile → snapshot: just platform-profile hh --api | linkedin --export ~/Downloads/Basic_LinkedInDataExport.zip | toptal --text-file paste.txt
platform-profile PLATFORM *ARGS:
    @just _platform profile {{PLATFORM}} {{ARGS}}

platform-profile-dry PLATFORM *ARGS:
    @just _platform profile {{PLATFORM}} --dry-run {{ARGS}}

# job search → opportunities: just platform-jobs hh -q "data engineer" --remote | wellfound --text-file jobs.txt
platform-jobs PLATFORM *ARGS:
    @just _platform jobs {{PLATFORM}} {{ARGS}}

platform-jobs-dry PLATFORM *ARGS:
    @just _platform jobs {{PLATFORM}} --dry-run {{ARGS}}

# application statuses → observations: just platform-applications hh --api | indeed --text-file applied.txt
platform-applications PLATFORM *ARGS:
    @just _platform applications {{PLATFORM}} {{ARGS}}

platform-applications-dry PLATFORM *ARGS:
    @just _platform applications {{PLATFORM}} --dry-run {{ARGS}}

platform-status *ARGS:
    @just _platform status {{ARGS}}

test-platform *ARGS:
    uv run pytest services/careeros/tests/platform {{ARGS}}

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

# export the OpenAPI schema once, then regenerate the TS types from it
openapi:
    uv run careeros export-openapi
    if [ -d node_modules ]; then npm run generate:types; else {{_no_web}}; fi

# CI freshness gate: regenerate every committed contract, fail if the tree moved
contracts-check:
    scripts/contracts-check.sh

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

# ---------- workstation handoff ----------

workstation_jf := "$HOME/.ai/skills/_scripts/session/workstation/Justfile"

# is this machine safe to walk away from? read-only verdict (0 clean, 3 blocked)
preflight *ARGS:
    @bash scripts/workstation-preflight.sh {{ARGS}}

# record THIS host into .ai/workstations/ so the other machine can see where it stopped
workstation-state *ARGS:
    just -f {{workstation_jf}} state --repo "{{justfile_directory()}}" --apply {{ARGS}}

# full handoff: sessions -> clones -> shared repos -> secrets -> state -> verify. DRY-RUN; --apply
workstation-gateway *ARGS:
    just -f {{workstation_jf}} gateway --repo "{{justfile_directory()}}" {{ARGS}}

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
