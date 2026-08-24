# CareerOS — main commands. Granular recipes live in the Justfile (`just --list`).
#
# Aggregates:
#   make check   quality gate only — lint + tests, mutates nothing (this is what CI runs)
#   make all     the whole local pipeline: contracts → gate → database → artifacts → platforms
#   make run     bring the full stack up in docker and open the web app

.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.NOTPARALLEL:  # every target shells out to just/docker — the order of the steps is the contract

VARIANT  ?= general-core          # make generate-cv VARIANT=remote-us
PLATFORM ?= all                   # make platform-sync PLATFORM=hh
WEB_URL  ?= http://localhost:3000

# banner printed at the top of each step so a long `make all` stays readable
step = @printf '\n\033[1;34m▸ %s\033[0m\n' "$(1)"

.PHONY: help env infra dev up down build test lint fmt typecheck check all run \
        validate-career generate-cv migrate seed openapi platform-sync \
        bot-check bot-webhook deploy deploy-dry clean distclean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- environment

env: ## Render .env files from config/*.template
	$(call step,env — render .env from config/*.template)
	@python3 scripts/env-render.py

infra: ## Start postgres + redis in docker (idempotent)
	$(call step,infra — postgres + redis)
	@just infra-up

dev: env ## Start infra in docker, API + worker locally with reload
	@just dev

up: env ## Full stack in docker compose (api, worker, web, postgres, redis)
	docker compose --profile web up --build -d

down: ## Stop the compose stack
	docker compose --profile web down

build: ## Build the docker images (api + web) without starting them
	$(call step,build — docker images)
	@just build

# ---------------------------------------------------------------- quality gate

test: ## Run all tests (python + web when present)
	$(call step,test — pytest + vitest)
	@just test

lint: ## Lint + format check + typecheck + import contracts
	$(call step,lint — ruff · pyright · import-linter · eslint)
	@just lint

fmt: ## Auto-format python and TS
	$(call step,fmt — ruff format + prettier)
	@just fmt

typecheck: ## pyright + tsc
	@just typecheck

check: lint test ## Quality gate only: lint + tests, mutates nothing

# ---------------------------------------------------------------- career data

validate-career: ## Validate the career vault (CAREEROS_VAULT_PATH, else the bundled demo vault)
	$(call step,validate-career — vault schemas + referential integrity)
	@just validate-career

generate-cv: ## Generate CV variant(s) from the vault: make generate-cv VARIANT=general-core
	$(call step,generate-cv — variant $(VARIANT))
	@just generate-cv $(VARIANT)

# ---------------------------------------------------------------- db + contracts

migrate: ## Apply alembic migrations
	$(call step,migrate — alembic upgrade head)
	@just migrate

seed: ## Seed the database with the single user and demo data
	$(call step,seed — single user + demo data)
	@just seed

openapi: ## Export OpenAPI + regenerate TS client/types
	$(call step,openapi — export schema + regenerate TS types)
	@just openapi

# ---------------------------------------------------------------- platforms + bot

platform-sync: ## Sync profile/jobs/applications from connected platforms: make platform-sync PLATFORM=hh
	$(call step,platform-sync — $(PLATFORM))
	@just platform-sync $(PLATFORM)

bot-check: ## Telegram bot: token valid? who owns the webhook?
	$(call step,bot-check — telegram token + webhook owner)
	@just bot-token-check

bot-webhook: ## Claim the Telegram webhook for this deployment
	@just bot-webhook-set

# ---------------------------------------------------------------- deploy

deploy-dry: ## Print every command the Fly deploy would run, execute none
	@just deploy-dry

deploy: ## Deploy to Fly, then claim the webhook
	@just deploy-fly

# ---------------------------------------------------------------- aggregates

# Ordered on purpose: .env before the gate reads it · fmt before lint checks formatting ·
# openapi before typecheck consumes the generated TS · db before seed · vault before CV.
all: env infra openapi fmt lint test migrate seed validate-career generate-cv platform-sync bot-check ## Everything verifiable locally, in dependency order
	$(call step,all — done)
	@echo "contracts · gate · database · CV artifacts · platform sync · bot: all green"

run: env up migrate seed ## Full stack in docker, then open the web app
	$(call step,run — opening $(WEB_URL))
	@python3 -m webbrowser $(WEB_URL) >/dev/null 2>&1 || echo "open $(WEB_URL)"

# ---------------------------------------------------------------- housekeeping

clean: ## Remove build artifacts and caches (keeps .venv, node_modules and generated/platform)
	rm -rf apps/web/.next .pytest_cache .ruff_cache .import_linter_cache \
	       generated/cv generated/test services/careeros/openapi.json

distclean: clean ## Also drop .venv and node_modules (re-run `uv sync` + `npm install` after)
	rm -rf .venv node_modules
