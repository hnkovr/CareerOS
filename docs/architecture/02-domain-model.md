# CareerOS — Domain Model

Status: accepted for P0 (2026-08-20). Attribute lists are the P0 contract; P1+ additions are marked.

Two worlds, one ID discipline: vault items use stable slugs (`achievement_prodamus_001`),
operational rows use UUIDv7. Operational rows that *reference* vault items store the slug **and**
the vault commit SHA at which it was read.

---

## 1. Canonical (vault) — YAML collections

All items share a base:

```yaml
id: <slug>                 # stable, never renamed (retire + new instead)
status: draft|verified|retired
visibility:                # per channel; missing = inherit channel default
  linkedin: true
  wellfound: true
  upwork: true
  toptal: optional
  ats: true
evidence: [{type: project|link|testimonial|certification|file, ref: <id|url>}]
tags: []
updated_at: 2026-08-20
```

| Collection (file) | Item | Key fields |
|---|---|---|
| `profile.yaml` | `Profile` (singleton) | `name`, `headline_baseline`, `location{city,country,timezone}`, `work_modes[]` (remote_us, eu_b2b, freelance, contract, relocation_pl), `eligibility{eu_remote, us_remote_contractor, georgia_based, relocation_targets[]}`, `contacts{email, phone?, website}`, `links[]`, `languages_spoken[]`, `summary_core` |
| `experience.yaml` | `Experience` | `company_id`, `company_name`, `roles[] {title, start, end?, employment_type, location, remote}`, `summary`, `technologies[]`, `responsibilities[]`, `achievement_ids[]`, `project_ids[]` |
| `achievements.yaml` | `Achievement` | `company_id`, `role_ref?`, `type` (achievement\|responsibility\|result), `facts[]` (atomic, verifiable sentences), `metrics[] {name, value, unit, baseline?}`, `technologies{first_priority[], additional[], target[]}`, `keywords[]` |
| `projects.yaml` | `Project` | `name`, `kind` (work\|oss\|portfolio\|consulting), `company_id?`, `summary`, `problem`, `solution`, `outcome`, `technologies[]`, `links[]`, `period`, `public` |
| `skills.yaml` | `Skill` | `name`, `category`, `tier` (first_priority\|additional\|target), `level` (expert\|proficient\|working\|learning), `years`, `last_used`, `evidence[]`, `market_group[]` (market_core\|strategic_core\|oss_builder\|agentic) |
| `education.yaml` / `certifications.yaml` / `languages.yaml` / `publications.yaml` / `testimonials.yaml` / `links.yaml` | conventional | as named |
| `offers.yaml` | `Offer` | `title`, `customer_problem`, `deliverables[]`, `timeline`, `ideal_client`, `technologies[]`, `proof[]` (project/testimonial ids), `pricing_strategy`, `platforms[]` |
| `positioning/<slug>.yaml` | `Positioning` | `headline`, `summary`, `target_markets[]`, `emphasize{skills[], achievements[], projects[]}`, `deemphasize[]`, `keywords_must[]`, `keywords_nice[]`, `tone` |
| `channels/<slug>.yaml` | `ChannelRules` | `platform`, `limits{headline_chars, about_chars, bullets_per_role, …}`, `priorities[]`, `required_sections[]`, `style`, `cta?`, `default_visibility` |
| `cv/variants/<slug>.yaml` | `CVVariant` | `positioning_id`, `channel_id`, `locale`, `length` (one_page\|two_page), `sections[]`, `rendercv_theme`, `include{companies[]?, years_back?}` |
| `scoring/model.yaml` | `ScoringModel` | `version`, `dimensions{<name>: {weight, rules}}`, `tech_groups{market_core[], strategic_core[], oss_builder[], agentic[]}`, `eligibility_rules`, `compensation_targets` |
| `prompts/<area>/<id>.yaml` | `Prompt` | `id`, `version`, `purpose`, `inputs{}`, `output_schema` (ref to Pydantic name), `provider_preferences[]`, `template` (Jinja2), `updated_at` |

Referential integrity (validated by `careeros vault validate`): every `company_id`,
`achievement_ids[]`, `project_ids[]`, `evidence.ref` must resolve; `retired` items may be referenced
only by other retired items; every `Positioning.emphasize.*` ID must exist.

---

## 2. Operational (Postgres) — aggregates

Common columns: `id uuid`, `user_id uuid FK`, `created_at`, `updated_at`; soft-delete via `archived_at` where noted.

### Opportunities context
| Table | Key fields |
|---|---|
| `opportunity_raw` | `source` (enum: linkedin, wellfound, upwork, toptal, email, recruiter, direct, website, manual, clipboard, share, url), `url?`, `raw_text`, `raw_html?`, `raw_payload jsonb`, `content_hash`, `captured_at`, `capture_method` |
| `opportunity` | `raw_id FK`, `company_id FK?`, `title`, `contract_type` (employment\|b2b\|freelance\|contract_to_hire), `employment_type` (full_time\|part_time\|project), `location`, `remote_policy` (remote_global\|remote_region\|hybrid\|onsite\|unknown), `remote_regions[]`, `timezone_range?`, `compensation{min,max,currency,period,type}`, `description_md`, `requirements[]`, `preferred[]`, `technologies[]`, `seniority`, `recruiter_contact_id FK?`, `received_at`, `deadline?`, `status` (new\|watching\|applied\|ignored\|archived), `dedup_key`, `parser_version`, `parse_confidence` |
| `opportunity_score` | `opportunity_id FK`, `scoring_version`, `vault_sha`, `overall`, `dimensions jsonb` (each: score, weight, explanation, signals[]), `recommendation` (enum §17), `computed_at` |
| `opportunity_analysis` | `opportunity_id FK`, `ai_run_id FK`, `verdict`, `score_ai`, `strengths[]`, `gaps[]`, `risks[]`, `compensation_assessment`, `competition_assessment`, `channel_strategy`, `recommended_cv_variant`, `recommended_positioning`, `suggested_response`, `interview_prep[]`, `next_action` |
| `company` | `name`, `domain?`, `size?`, `industry?`, `hq_location?`, `remote_friendly?`, `notes`, `links[]` |
| `contact` | `name`, `company_id FK?`, `role?`, `email?`, `linkedin_url?`, `relationship` (recruiter\|hiring_manager\|client\|peer\|other), `last_contact_at`, `next_action?`, `notes` |

### CV context
| Table | Key fields |
|---|---|
| `cv_artifact` | `variant_id` (slug), `opportunity_id FK?`, `vault_sha`, `positioning_id`, `channel_id`, `prompt_version?`, `provider?`, `model?`, `files jsonb` {pdf, md, json paths}, `status` (rendering\|ready\|failed), `summary_text`, `render_log` |
| `generated_bullet` | `artifact_id FK`, `section`, `order`, `text`, `derived_from[]` (fact IDs), `generation jsonb` {provider, model, prompt_version, ai_run_id}, `verified bool`, `user_edited bool` |
| `cv_comparison` (P0-lite) | computed on the fly; no table |

### Profiles context
| Table | Key fields |
|---|---|
| `profile_snapshot` | `platform`, `captured_at`, `capture_method` (api\|export\|upload\|paste\|share\|email), `headline`, `about`, `experience jsonb`, `skills[]`, `projects jsonb`, `portfolio jsonb`, `rates jsonb`, `availability`, `preferences jsonb`, `raw_payload jsonb`, `content_hash` |
| `profile_audit` | `snapshot_id FK`, `vault_sha`, `ai_run_id FK?`, `health_score`, `category_scores jsonb`, `engine_version` |
| `audit_finding` | `audit_id FK`, `severity` (critical\|high\|medium\|nice), `category` (§11 list), `problem`, `why_it_matters`, `suggested_change`, `source_fact_ids[]`, `confidence`, `resolution` (open\|applied\|dismissed) |
| `drift_finding` (P2) | `platform_a`, `platform_b|vault`, `field`, `value_a`, `value_b`, `severity`, `resolution` |

### AI context
| Table | Key fields |
|---|---|
| `ai_run` | `prompt_id`, `prompt_version`, `provider`, `model`, `mode` (builtin\|external_bundle\|dev_packet), `inputs_hash`, `inputs_payload jsonb?` (retention-governed), `output jsonb`, `output_schema`, `valid bool`, `retries`, `tokens_in/out`, `cost_usd`, `latency_ms`, `status`, `error?`, `entity_type`, `entity_id`, `feedback` (up\|down\|null), `feedback_note` |
| `suggestion` | `ai_run_id FK`, `target_type` (vault_item\|profile\|cv\|reply\|action), `target_ref`, `payload jsonb` (diff or text), `state` (suggested\|reviewed\|approved\|executed\|rejected), `decided_at`, `decided_by` |
| `action` (P1) | approval-gated outbound: `kind` (send_email\|update_profile\|apply), `adapter`, `payload`, `state` as above, `executed_at`, `result` |

### Core
| Table | Key fields |
|---|---|
| `user` | `email`, `display_name`, `settings jsonb`; P0 seeds exactly one |
| `audit_log` | `actor`, `action`, `entity_type`, `entity_id`, `before/after jsonb?`, `request_id`, `at` |
| `task` | task-runner ledger: `name`, `payload`, `state`, `attempts`, `scheduled_at`, `started_at`, `finished_at`, `error` |
| `sync_state` | `adapter`, `cursor`, `last_ok_at`, `last_error` |
| `note` | `entity_type`, `entity_id`, `body_md` |
| `vault_index` | cache: `fact_id`, `collection`, `vault_sha`, `text`, `tsv tsvector`, `embedding vector(…)` (P1) |

### Pipeline context (P1; minimal tables exist in P0 only if referenced)
`application` (`opportunity_id`, `stage` enum for employment and freelance pipelines, `cv_artifact_id`, `applied_at`, `next_follow_up_at`), `application_event` (timeline), `interview`.

### Inbox context (P1)
`thread`, `message` (`provider_message_id`, `from`, `to[]`, `subject`, `body_md` encrypted-at-rest option, `classification`, `urgency`, `links{opportunity_id, company_id, contact_id, application_id}`).

---

## 3. Enumerations that must stay in sync across Python and TS

Defined once in `careeros.modules.*.enums` and exported via OpenAPI; TS imports generated types.

* `Platform`: linkedin, wellfound, upwork, toptal, ats, direct_outreach, email, other
* `Recommendation`: ignore, watch, apply, high_priority, reply_now, ask_questions_first, negotiate, prepare_interview
* `ScoreDimension`: overall_fit, remote_us_fit, eu_fit, poland_fallback_fit, upwork_fit, startup_fit, enterprise_fit, technical_fit, seniority_fit, compensation_fit, learning_roi, strategic_upside, application_effort, risk
* `SuggestionState`: suggested, reviewed, approved, executed, rejected
* `AuditCategory`: completeness, freshness, consistency, keyword_coverage, channel_fit, positioning_fit, proof_metrics, credibility, call_to_action, portfolio_coverage, compensation_positioning, remote_eligibility_clarity, location_clarity
* `SkillTier`: first_priority, additional, target · `MarketGroup`: market_core, strategic_core, oss_builder, agentic
