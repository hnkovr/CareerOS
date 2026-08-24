# CareerOS — main commands. Granular recipes live in the Justfile (`just --list`).
.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help env dev up down test lint fmt typecheck validate-career generate-cv seed migrate openapi platform-sync clean bot-check bot-webhook deploy deploy-dry

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

env: ## Render .env files from config/*.template
	@python3 scripts/env-render.py

dev: env ## Start infra in docker, API + worker locally with reload
	@just dev

up: env ## Full stack in docker compose (api, worker, web, postgres, redis)
	docker compose --profile web up --build -d

down: ## Stop the compose stack
	docker compose --profile web down

test: ## Run all tests (python + web when present)
	@just test

lint: ## Lint + format check + typecheck + import contracts
	@just lint

fmt: ## Auto-format python and TS
	@just fmt

typecheck: ## pyright + tsc
	@just typecheck

validate-career: ## Validate the career vault (CAREEROS_VAULT_PATH or demo)
	@just validate-career

generate-cv: ## Generate CV variant(s) from the vault: make generate-cv VARIANT=general-core
	@just generate-cv $(or $(VARIANT),general-core)

seed: ## Seed the database with the single user and demo data
	@just seed

migrate: ## Apply alembic migrations
	@just migrate

platform-sync: ## Sync profile/jobs/applications from connected platforms: make platform-sync PLATFORM=hh
	@just platform-sync $(or $(PLATFORM),all)

openapi: ## Export OpenAPI + regenerate TS client/types
	@just openapi

bot-check: ## Telegram bot: token valid? who owns the webhook?
	@just bot-token-check

bot-webhook: ## Claim the Telegram webhook for this deployment
	@just bot-webhook-set

deploy-dry: ## Print every command the Fly deploy would run, execute none
	@just deploy-dry

deploy: ## Deploy to Fly, then claim the webhook
	@just deploy-fly

clean: ## Remove build artifacts and caches
	rm -rf .venv node_modules apps/web/.next .pytest_cache .ruff_cache generated/*

run: up validate-career generate-cv migrate openapi
	open .
