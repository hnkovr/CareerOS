# Technical Risks

Status: living document (2026-08-20).

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Platform APIs are closed or conditional** (LinkedIn partner-only, Upwork approval, Wellfound/Toptal none). Integration depth may stay at email + manual capture. | High | Medium | Design around it: email and manual capture are first-class; capabilities matrix is visible in UI; no roadmap item depends on a non-public API. |
| R2 | **AI output unreliability** (invented facts, schema drift, provider outages). | High | High | Structured output + Pydantic validation + bounded retry; provenance guard rejects bullets citing unknown fact IDs or introducing numbers/companies absent from sources; all outputs are `Suggestion`s; provider fallback chain. |
| R3 | **RenderCV schema churn** between versions breaks rendering. | Medium | Medium | Pin major version; isolate mapping in `RenderCVAdapter`; golden-file tests for each variant; `careeros render --no-ai` smoke test in CI. |
| R4 | **Git-in-container ergonomics** (identity, SSH keys, concurrent edits, merge conflicts in YAML). | Medium | Medium | Commit as configured identity; mount `~/.ssh` read-only; single-writer lock per vault in the API; `ruamel` round-trip writes to minimize diffs; never auto-push without opt-in. |
| R5 | **Scoring calibration** — weights produce plausible but wrong rankings; user distrusts the tool. | Medium | High | Transparent per-dimension breakdown with signals; weights in a versioned vault file; feedback loop (`ai_run.feedback`, score overrides) feeds later calibration; AI interprets but never overrides the deterministic score silently. |
| R6 | **Scope creep / over-engineering** (72-section brief). | High | High | Phase gates; modular monolith; every slice ships with tests + docs; P0 DoD is the only target until it's green. |
| R7 | **Privacy leaks** (career data, emails, tokens in logs/fixtures/artifacts). | Medium | High | Private vault outside repo; `generated/` and `career/private/` git-ignored; log redaction; encrypted tokens; demo data is synthetic; retention settings. |
| R8 | **Two-language duplication** (business logic drifting between Python and TS). | Medium | Medium | Logic server-side only; TS types/client generated from OpenAPI in CI; contract tests. |
| R9 | **Multiplatform maintenance cost** (Expo + Tauri). | Medium | Medium | Deferred to P2; shared `packages/ui` + generated client; mobile scoped to triage/inbox only. |
| R10 | **Gmail OAuth verification** for restricted scopes when moving to SaaS. | Low (P0) / High (SaaS) | Medium | Single-user "testing" app in P1; revisit before any multi-user release. |
| R11 | **Opportunity dedup false positives/negatives** (same role reposted, agencies). | Medium | Low | Dedup key = normalized URL or (company, title, location) fuzzy; never auto-merge — flag `possible_duplicate_of`. |
| R12 | **Vendor lock-in of AI providers** (structured-output features differ). | Low | Low | Lowest-common-denominator JSON mode + local validation; provider-specific features opt-in inside adapters only. |
