# Architecture Decision Records

Format: [MADR](https://adr.github.io/madr/)-lite — Context · Decision · Alternatives · Consequences.
New ADRs: copy `000-template.md`, next number, link from here. Superseding an ADR: set its status to
`superseded by NNN`, never delete.

| # | Title | Status |
|---|---|---|
| [001](001-git-as-career-source-of-truth.md) | Git repository as the canonical career source of truth (incl. private-vault placement) | accepted |
| [002](002-postgres-for-operational-state.md) | PostgreSQL (+pgvector) for all operational state | accepted |
| [003](003-ai-provider-abstraction.md) | Single `AIProvider` port; structured output validated locally | accepted |
| [004](004-platform-adapter-model.md) | Platform adapters declare capabilities; precedence of integration methods | accepted |
| [005](005-no-autonomous-platform-scraping.md) | No autonomous scraping, no credential storage, no auto-apply/auto-reply | accepted |
| [006](006-rendercv.md) | RenderCV behind an adapter as the CV rendering engine | accepted |
| [007](007-web-first-multiplatform.md) | Web first; iOS (Expo) and macOS (Tauri) reuse the API contract and shared UI | accepted |
| [008](008-modular-monolith.md) | Modular monolith in one Python project; task-runner port (ARQ now, Temporal-ready) | accepted |
| [009](009-schema-source-of-truth.md) | Pydantic models are the schema source of truth; JSON Schema and TS are generated | accepted |
| [010](010-deterministic-first-ai-as-suggestion.md) | Deterministic code first; AI outputs are suggestions with provenance and approval states | accepted |
| [012](012-telegram-bot-surface.md) | Telegram bot as the P0 mobile surface: webhook, owner-gated, read-only vault | accepted |
| [013](013-platform-connectors.md) | Platform connectors: one submodule per platform, OAuth tokens allowed, paste path everywhere, sync placement | accepted |
