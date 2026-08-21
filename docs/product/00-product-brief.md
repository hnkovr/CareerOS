# CareerOS — Product Brief (canonical requirements)

Source: founder brief, 2026-08-20 (verbatim, lightly formatted). This is the requirements source of
truth; the [architecture proposal](../architecture/01-architecture-proposal.md) maps it to design.

> **Canonical career data → positioning → channel-specific profiles/CVs → opportunities → communications → applications → AI-assisted decisions/actions**

Система должна быть **single source of truth для карьеры**, а LinkedIn, Wellfound, Upwork, Toptal, email и AI-сервисы должны быть внешними адаптерами / projections. Не делать очередной Resume Builder.

## 1. Product goals
1. Поддержка и обновление General/Core CV as Code.
2. Генерация нескольких CV / positioning variants из одного factual source.
3. Адаптация профилей под LinkedIn, Wellfound, Upwork, Toptal, generic ATS, recruiter outreach, direct B2B outreach.
4. Аудит существующих профилей на этих платформах.
5. Проверка: completeness, consistency, positioning, recruiter keywords, channel fit, outdated information, contradictions между платформами.
6. Ingestion вакансий / проектов / контрактов / inbound leads.
7. Автоматическая оценка релевантности каждой возможности.
8. Генерация: краткого AI-analysis, deep analysis, application strategy, interview preparation, prompts для внешних AI chats.
9. Проверка входящих сообщений (career platforms, recruiter/client emails, application updates, invitations, interview requests).
10. Unified career inbox. 11. Application / lead pipeline. 12. AI assistants на уровне каждой основной сущности.
13. Web + mobile/iOS + macOS UX. 14. Self-hostable / privacy-first. 15. Путь из single-user personal tool в SaaS.

## 2. Non-goals (first versions)
Не клонировать LinkedIn; не строить полноценную CRM; не спамить работодателей; не подавать заявки массово без человека; не обходить CAPTCHA/API limits; не хранить пароли платформ; никакой browser automation, нарушающей ToS; не менять factual career history без подтверждения; Kubernetes не обязателен; никаких microservices ради microservices. **Modular monolith first.**

## 3. Critical architectural principle — two data classes
**3.1 Canonical Career Data** (source of truth, as code in private Git repo, YAML + Markdown, стабильные ID): identity/public profile, positioning, experience, roles, achievements, measurable results, projects, technologies, responsibilities, education, certifications, languages, publications, portfolio, links, testimonials, packaged consulting offers.

```yaml
id: achievement_prodamus_001
company_id: prodamus
type: achievement
facts:
  - designed GitLab CI/CD architecture
  - introduced dbt Slim CI
  - improved deployment reliability
technologies:
  first_priority: [dbt, Dagster, GitLab CI, ClickHouse, Docker, ClaudeCode]
evidence:
  - {type: project, ref: pdp}
visibility: {linkedin: true, wellfound: true, upwork: true, toptal: optional}
status: verified
```
AI не должен молча менять `facts`. AI может: `fact → select → summarize → rephrase → combine → project`, но не `fact → invent`. Любое предложение AI изменить canonical facts: `suggested change → diff → explicit approval`.

**4. Operational Data** в PostgreSQL: opportunities, job posts, freelance projects, inbound messages, contacts, recruiters, companies, applications, interviews, profile snapshots, profile audits, generated CV variants, AI runs, prompt runs, scoring, reminders, source sync state, email threads, notes, tasks. `Git = career source of truth; PostgreSQL = operational career state`. Не смешивать.

## 5. Stack
Backend: Python 3.13+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL, pgvector, Redis, ARQ (не Celery без необходимости; task abstraction с возможностью перехода на Temporal).
Web (P0, PWA): Next.js, React, TypeScript, Tailwind, shadcn/ui, TanStack Query.
Mobile: Expo/React Native/TypeScript — переиспользовать API client, schemas, design tokens, domain types; фокус на triage, inbox, approve/reject, AI summaries, notifications, share-to-app.
macOS: Tauri v2 — clipboard, file access, Git repo access, запуск local AI/dev agents, prompt/task bundles, notifications, menu bar.

## 6. Monorepo
`apps/{web,mobile,desktop}`, `services/{api,worker}`, `packages/{domain,schemas,api-client,ui,ai,prompts,platform-adapters}`, `career/{source,positioning,channels,offers,prompts,schemas,rendercv}`, `generated/{cv,profiles,prompts,applications}`, `docs/{architecture,product,developer-guide,adr}`, `tests/`, `docker-compose.yml`, `README.md`. Структуру можно скорректировать — сначала аргументировать.

## 7. CV-as-Code engine
RenderCV через adapter. Pipeline: Canonical Facts → Positioning Strategy → Channel Rules → Opportunity/JD Context [opt] → Fact Selection → AI Rewriting → Validation → RenderCV → PDF / Markdown / JSON.
Варианты: general-core, senior-data-engineer, analytics-engineer, data-platform-engineer, startup-data-engineer, consultant, agentic-data-platform, poland-eu, remote-us.

## 8. Provenance
Каждая bullet должна отвечать «из каких canonical facts получена»: `derived_from[]`, `generation{provider, model, prompt_version}`, `verified`. UI: Generated bullet → Why is this here? → Source facts.

## 9. Channel adaptation
LinkedIn: recruiter search, broad keyword coverage, market-core, credibility, career graph, Featured/Skills/Projects/Certifications; шире, чем PDF CV.
Wellfound: startups, ownership, product thinking, speed to MVP, GCP, BigQuery, dbt, OSS, Dagster, analytics, AI automation, LLMOps.
Upwork: consultant landing page + proof + packaged offers; client problem, result, delivery speed, confidence, packaged services, proof, portfolio, CTA. Offers: BigQuery Cost Optimization Audit; dbt Analytics Warehouse in 7 Days; GA4/CRM → BigQuery → Dashboard; AI Analyst over Your Warehouse; ClickHouse Realtime Analytics MVP; Snowflake/dbt Cleanup; Dagster Pipeline Observability; SQLMesh/dbt CI Upgrade; LLM/RAG Evaluation Dashboard.
Toptal: seniority, technical ownership, architecture, consulting, client communication, system design, production delivery, troubleshooting, leadership.

## 10. Profile snapshots
`PlatformProfileSnapshot{platform, captured_at, source, headline, about, experience, skills, projects, portfolio, rates, availability, preferences, raw_payload}`. Источники: official API, export, upload, paste, browser share/capture (если разрешено), email-derived metadata. Не строить скрытый crawler.

## 11. Profile Audit Engine
Категории: Completeness, Freshness, Consistency, Keyword coverage, Channel fit, Positioning fit, Proof/metrics, Credibility, Call-to-action, Portfolio coverage, Compensation positioning, Remote eligibility clarity, Location clarity. Сравнение snapshot с canonical data. Вывод: Profile Health Score N/100; Critical/High/Medium/Nice-to-have; для каждого: problem, why it matters, suggested change, source facts, confidence, apply manually / generate replacement.

## 12. Drift detection
Напр.: LinkedIn says 12 years / Wellfound says 10; headline contains Snowflake, positioning removed it; Upwork project outdated; Toptal rate conflicts with target. Dashboard: «Profiles out of sync: 3».

## 13–14. Opportunity ingestion & normalization
Источники: LinkedIn, Wellfound, Upwork, Toptal, Email, Recruiter, Direct outreach, Website, Manual, Clipboard, Share Sheet, URL. Поля: source, url, company, role, contract_type, employment_type, location, remote_policy, timezone, compensation, currency, rate, description, requirements, preferred_skills, recruiter, received_at, deadline, raw_source. Pipeline: raw → parser → normalized → enrichment → AI analysis → scoring. Не уничтожать raw.

## 15. Scoring (transparent, configurable, multi-dimensional)
Оценки: Overall Fit, Remote US Fit, EU Fit, Poland Fallback Fit, Upwork Fit, Startup Fit, Enterprise Fit, Technical Fit, Seniority Fit, Compensation Fit, Learning ROI, Strategic Upside, Application Effort, Risk.
Tech groups — Market Core: Python, SQL, dbt, Snowflake, Databricks, Airflow, AWS, Spark, Kafka; детальнее: first_priority [dbt, Dagster, GitLab CI, ClickHouse, Docker, ClaudeCode], additional [Iceberg, Spark, Kafka/PubSub/RedPanda, Airflow, Trino, Prefect], target [GCP, BQ, Databricks, Snowflake, AWS]. Strategic Core: GCP, BigQuery, dbt, Dagster, Python, SQL, DuckDB, ClickHouse, Semantic BI. OSS Builder: Dagster, SQLMesh, DuckDB, ClickHouse, Redpanda, Evidence, Metabase, OpenLineage, DataHub, Soda, Great Expectations. Agentic Differentiators: LLMOps, AgentOps, RAG evaluation, AI analytics, semantic layers, AI-assisted development, agentic workflows.
Разделять: demand, supply/competition, learning curve, rate ceiling, portfolio demonstrability, future upside.

## 16. User strategy
Positioning: Senior Data Engineer / Analytics Engineer. Headline baseline: «Senior Data Engineer / Analytics Engineer | GCP, BigQuery, Snowflake, dbt, Airflow, Dagster, Python, SQL, Databricks, Kafka, ClickHouse, LLMOps». Strategic: «Agentic Data Platform Engineer / Senior Data Engineer building AI-ready analytics platforms with GCP/BigQuery, dbt, Dagster, ClickHouse, OSS, and enterprise-compatible Snowflake/Databricks/Airflow.» Target: Remote US / EU B2B / freelance / contract. Локация: Batumi, Georgia. Fallback: Poland / EU relocation. Scoring учитывает: remote outside US allowed? contractor allowed? Georgia eligible? EU timezone? Poland-compatible stack?

## 17. Recommended actions
IGNORE, WATCH, APPLY, HIGH PRIORITY, REPLY NOW, ASK QUESTIONS FIRST, NEGOTIATE, PREPARE INTERVIEW — с объяснением (Why / Risks / Suggested positioning / CV / Next action).

## 18–19. AI architecture, three modes
`AIProvider{generate, stream, structured_output, embeddings}`; adapters: OpenAI, Anthropic, Gemini, xAI/Grok, local/OpenAI-compatible. Mode A — built-in; Mode B — external chat handoff (Generate → Copy → Open → Paste; deep link только где официально поддерживается); Mode C — dev-agent task packets (Claude Code, Codex CLI, Gemini CLI, Antigravity, Cursor, Windsurf) в `generated/dev-tasks/` с Context, Goal, Relevant source files, Constraints, Acceptance criteria, Suggested commands, Expected artifacts.

## 20. Context-aware assistants
CV: Improve, Shorten, Add metrics, Check ATS, Tailor to JD, Explain changes, Compare versions, Generate variants. Profile: Audit, Rewrite headline/About, Find missing keywords, Compare with Core CV, Optimize for platform, Update checklist. Opportunity: Summarize, Score, Tradeoffs, Compare, Questions, Response, Select CV, Cover letter, Interview plan. Inbox: Classify, Summarize, Urgency, Link, Suggest reply, Follow-up, Deadline. Application: Status, Next step, Follow-up timing, Interview prep, Negotiation, Risk. Career Strategy: What changed? Skills frequency? Funnel? Where losing? Best platform? Highest-ROI portfolio project?

## 21–22. Prompt system & external prompts
`career/prompts/{opportunity,cv,profile,inbox,interview,negotiation,dev-agent}/` с metadata `id, version, purpose, inputs, output_schema, provider_preferences, updated_at`. AI runs хранят prompt_version, provider, model, timestamp, inputs hash, output, cost, latency, user feedback. Кнопка «Generate AI Analysis Prompt» (positioning, facts, target market, JD, constraints, framework) → 12-пунктный ответ: verdict, fit score, strong matches, missing skills, risks, compensation, competition, channel strategy, best CV, suggested response, interview prep, apply/skip.

## 23–25. Career Inbox
Источники: Gmail (P0 интеграции inbox = Gmail OAuth/API), platform notifications, recruiter/client emails, forwarded. Позже IMAP. Классы: New opportunity, Recruiter outreach, Client lead, Interview, Application update, Rejection, Offer, Platform notification, Follow-up required, Spam/noise. Авто-линк: company, recruiter, opportunity, application, platform. Отправка email только по явному действию; никаких авто-ответов в P0.

## 26–27. Platform integration & browser/share
Capabilities matrix (Read/Write Profile, Read Opportunities/Messages, Apply, Export, Official API, Email fallback, Manual capture). Правило: official API > export/import > email ingestion > user-initiated capture > manual paste. Не обходить security controls. Browser/share extension — user initiated, без массового scraping.

## 28–31. Dashboard & UX
Главная за 10 секунд: что нового / что требует внимания / лучшие возможности / профили в порядке? / кому ответить / что с заявками. Cards: Profile Health, New Opportunities, Top Matches, Career Inbox, Applications, Follow-ups, Interviews, AI Suggestions, Sync Status, CV Versions. Profile dashboard per platform со score и issues. Opportunity triage: list + detail (desktop), swipe/buttons (mobile); actions Skip/Save/Analyze/Apply/Reply/Ask AI/Generate Prompt. Comparison mode 2–5 opportunities по Rate, Remote, Stack fit, Strategic fit, Learning ROI, Competition, Effort, Relocation, Upside с ranked recommendation.

## 32–34. Pipeline, timeline, contacts
Kanban: Inbox, Interested, Preparing, Applied, Recruiter Screen, Technical, Final, Offer, Rejected, Archived. Freelance: Lead, Discovery, Proposal, Negotiation, Active, Won, Lost. Timeline per opportunity. Contacts: name, company, role, email, LinkedIn, relationship, opportunities, messages, notes, last_contact, next_action — без HubSpot.

## 35–39. Search, analytics, market intel, skills gap, portfolio
Unified FTS + semantic (Postgres FTS + pgvector). Analytics: applications/week, replies, response rate, interview/offer conversion, platform performance, role/stack/compensation distribution, rejection reasons, time to reply, top sources. Market intelligence — «Based on your observed opportunity stream». Skills gap: known / claimed / evidenced / missing / worth learning («I know X» vs «I can prove X»). Portfolio planner: gap → suggested proof → ROI.

## 40–47. Offers, notifications, security, deployment, CI/CD, tests, observability, ADR
Offers as code `{id,title,customer_problem,deliverables,timeline,ideal_client,technologies,proof,pricing_strategy,platforms}`. Notifications: high-score opportunity, urgent message, interview tomorrow, follow-up due, drift, offer — без спама. Security: OAuth, no passwords, encrypted tokens, secrets via env/secret manager/keychain, HTTPS, CSRF, secure cookies, least privilege, audit log, no logging of bodies/tokens, retention. `docker compose up`; VPS/Fly/Railway/Render/Cloud Run; portable Postgres. CI: lint, format, typecheck, unit, integration, schema validation, API contract tests, build, security; ruff/mypy-pyright/pytest; eslint/tsc/vitest; pre-commit. Тесты: canonical validation, AI output schema, provenance, scoring, adapter contracts, email parsing, dedup, CV generation, prompt snapshots, authorization. Observability: structured logs, request IDs, AI/task telemetry, sync status, error tracking, OTel-ready. ADR минимум: 001 git SoT, 002 postgres, 003 AI provider, 004 platform adapter, 005 no scraping, 006 rendercv, 007 web-first.

## 48–54. Phases & workflows
P0 Career Core: vault, schemas, fact editor, RenderCV, core CV + variants, web dashboard, manual ingest, JD paste, scoring, AI analysis, external prompts, snapshots, audit, compose, tests. P1 Inbox & Pipeline: Gmail, inbox, classification, extraction, recruiter detection, Kanban, contacts, follow-ups, notifications. P2 Platform & Multiplatform: per-platform workflows, drift, browser/share capture, Expo iOS, Tauri macOS. P3 Agentic: assistants everywhere, multi-agent workflows, market intel, analytics, skills gap, portfolio recs, negotiation, interview intel, daily brief — human approval для внешних write actions. Daily brief. Workflows с `WAIT FOR HUMAN APPROVAL`. Approval states: AI suggested → Reviewed → Approved → Executed → Rejected.

## 55–64. Engineering rules
Tools/functions вместо мега-промпта (`get_career_facts, get_profile_snapshot, get_opportunity, score_opportunity, search_experience, search_achievements, generate_cv_variant, compare_cv_to_jd, generate_external_prompt, create_application, draft_reply`); deterministic code first. Structured output (JSON Schema/Pydantic). Версионировать facts, positioning, templates, prompts, generated CV, snapshots, scoring model, provider/model. Edit workflow: Edit → validate → preview diff → save → Git commit (авто-сообщение `career(experience): …`). CV comparison (added/removed/rewritten/keyword diff/source facts). Кнопки вместо промптов; ⌘K palette; iOS share sheet quick capture; importers (PDF/DOCX, LinkedIn export, JSON Resume, RenderCV, Markdown, YAML) с подтверждением пользователя; экспорт YAML/JSON/Markdown/PDF/JSON Resume/RenderCV/CSV.

## 65–69. Docs, seed, DX, sequence, discipline
README: clone → configure → compose up → open → load example → generate CV → add opportunity → AI analysis. Документировать architecture, domain model, schemas, adapters, AI adapters, security, dev workflow, deployment. Realistic fake demo-user; private data вне public repo (`career/private/` в .gitignore или отдельный private repo — предложить лучший подход). `make dev/test/lint/generate-cv/validate-career/seed` или современный task runner. Последовательность: inspect → architecture proposal → domain boundaries → schemas → ADRs → backend → web → vault reader/validator → CV generation → manual ingest → scoring → first AI provider → AI analysis → external prompts → dashboard → tests → docs → P1. Дисциплина: small vertical slice, tests, docs, commit; перед архитектурным изменением — explain, compare, choose, ADR, implement.

## 70–72. First step, DoD, north star
Первый шаг: анализ → final architecture → bounded contexts → canonical vs operational → domain model → monorepo → capabilities matrix → P0–P3 → риски → 5–10 ADR → P0. DoD первой usable версии: 20 пунктов (open app; view/edit fact; git diff; save; Core CV; Remote US CV; Wellfound variant; paste JD/URL; normalized opportunity; transparent score; AI analysis; apply/skip; best CV variant; prompt for ChatGPT/Claude/Gemini/Grok; snapshots LinkedIn/Wellfound/Upwork/Toptal; profile audit; dashboard; compose; one-command tests).

North star: **Personal Agentic Career Data Platform / Career Operating System** = Career Knowledge Base + CV-as-Code + Profile Management + Opportunity Intelligence + Career Inbox + Application CRM + AI Assistants + Agentic Workflows + Career Analytics.

> **Career facts are canonical, channel profiles are projections, opportunities are events, AI outputs are suggestions with provenance, and external actions remain controllable by the user.**
