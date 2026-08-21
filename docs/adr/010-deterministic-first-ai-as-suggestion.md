# 010 — Deterministic code first; AI outputs are suggestions with provenance and approval states

* Status: accepted
* Date: 2026-08-20

## Context

The brief's core invariant: facts are canonical, AI may select/summarize/rephrase/combine/project
but never invent, and external actions need human approval. Scoring must be transparent. LLM
orchestration must not absorb logic that deterministic code expresses better.

## Decision

1. **Scoring is deterministic.** `careeros.modules.opportunities.scoring` computes every dimension
   from explicit rules over a versioned `scoring/model.yaml` in the vault and returns a breakdown
   (`score`, `weight`, `signals[]`, `explanation`) per dimension. AI *interprets* the breakdown in
   `OpportunityAnalysis`; it cannot change the number. Overrides by the user are stored separately.
2. **Fact selection precedes rewriting.** CV generation first selects fact IDs deterministically
   (positioning, channel rules, JD keyword overlap), then asks AI to rewrite *only those* facts. The
   provenance guard rejects any bullet whose `derived_from` references unknown IDs or which introduces
   numbers, company names or technologies absent from the source facts.
3. **Every AI output is a `Suggestion`** (or an immutable analysis/audit record) with an
   `AIRun` reference. Suggestions move through `suggested → reviewed → approved → executed | rejected`.
   Vault writes from suggestions happen only via `Vault.apply_change` after `approved`.
4. **Assistants use tools, not mega-prompts.** P3 assistants call domain services
   (`get_career_facts`, `score_opportunity`, `generate_cv_variant`, …) exposed as typed tools;
   the LLM composes, the services compute.
5. **External write actions** (email send, profile update) require an `Action` in `approved` state;
   there is no code path to execute one otherwise. Per-workflow auto-approval policies are an
   explicit, opt-in, later feature.

## Alternatives considered

* **Let the LLM score with a rubric** — opaque, non-reproducible, drifts with model versions.
* **Free-form AI edits to YAML with a diff shown afterwards** — still lets AI originate facts; the guard must be structural, not visual.

## Consequences

* + Reproducibility, explainability, trust; model upgrades don't silently change rankings.
* − More code than "just ask the model"; worth it — this is the product's differentiator.
