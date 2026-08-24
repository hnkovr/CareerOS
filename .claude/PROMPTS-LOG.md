# Prompts log

| Date | Summary | Outcome |
|---|---|---|
| 2026-08-20 | Founder brief: build CV-as-Code / Career Data Platform (CareerOS). Full text preserved in `docs/product/00-product-brief.md`. Asked for: architecture analysis → bounded contexts → canonical vs operational → domain model → monorepo → capabilities matrix → P0–P3 → risks → ADRs → P0 implementation. | Architecture docs + ADR 001–010 committed; P0 slices started. |
| 2026-08-25 | Platform connectors: create submodules to read own profile, search jobs and check application statuses for Upwork, Wellfound, LinkedIn, Indeed, getmatch, Toptal, hh.ru (scripts/skills/sub-agents, parallel agents); mid-turn: upsert related tests (& debug), docs, GHIs, Linear project. | `modules/platform` core (contract, registry, tokens/OAuth, sync, API, CLI, just) + 7 connectors built by parallel sub-agents; ADR-011; docs/platform; agents `careeros-platform-{ops,connector-dev}`; GitHub #10–#21; Linear MY-26…MY-37 (project CareerOS). |
