# RocketHunt (rockethunt.ai)

Aggregator of tech vacancies (mostly re-posted from Telegram channels) with RU/EN pages, filters and a
paid "Show contacts" gate. CareerOS supports it as a **read-one provider**: paste a vacancy URL (or
forward it to the bot) and CareerOS reads that one public page — nothing else (ADR-015).

| Capability | Method | Notes |
|---|---|---|
| Own profile | — | not applicable (no candidate profile API/export) |
| Job search | paste · `search_url` deep link | listings are never fetched |
| Read one vacancy | `public_html` → `jina` → `wayback` | JSON-LD `JobPosting` + embedded state |
| Application statuses | — | none (applications happen on the original source) |

## How to use

```bash
careeros platform read 'https://rockethunt.ai/en/vacancies/<uuid>' --dry-run --show-attempts
careeros platform read 'https://rockethunt.ai/en/vacancies/<uuid>'          # ingest + score
careeros platform doctor rockethunt
```

Or forward the link to the Telegram bot, or `POST /api/platform/read {"url": "..."}`.

## URL shapes

Only `https://rockethunt.ai/{en,ru}/vacancies/<uuid-v4>` is a vacancy (detection confidence
`0.95`). Query and fragment are dropped, `www.` and uuid casing are normalised, and the canonical
form is always the **`en`** page of the same uuid — the ru page is the same vacancy, so the locale
the user gave travels on `CanonicalSource.locale` (and becomes the read's `Accept-Language`)
instead of forking the identity. `external_id` is the uuid. Every other RocketHunt URL (home,
`/en/faq`, `/en/profiles/…`, a non-v4 id) is **not** claimed — the generic connector keeps it at
its usual `0.1`.

## What is extracted

Two deterministic passes, no LLM: the JSON-LD `JobPosting` block, then the vacancy record inside
the React-Server-Components payload (`self.__next_f.push([1, "…"])`). The record is located **by
its uuid** — the page also ships its i18n dictionary, which repeats the same key names with
translated values (`"grade": "Grade"`), and a `similar_vacancies` list; neither may be mistaken
for the vacancy.

| Page key | → `JobPosting` / `OpportunityExtraction` | Notes |
|---|---|---|
| `title` | `title`, `extraction.title` | |
| `hiringOrganization.name` | `company` | |
| `jobLocation.address` | `location` | `"Lisbon, Portugal"` |
| `description` (markdown) | `raw_text`; `extraction.summary` · `requirements` · `responsibilities` | intro paragraph → summary; bullets under a *Requirements* / *Key tasks* heading → the matching list |
| `employmentType` | `extraction.employment_type` / `contract_type` | |
| `baseSalary` | `extraction.compensation` (min/max/currency/period) | provenance below |
| `datePosted` | `published_at`, `posted_at` | |
| `validThrough` | `expires_at`, `extraction.deadline` | |
| `skills[]` | `extraction.technologies` | extended by `key_skills_*` |
| `qualifications` (`"Head, English: B2"`) | `extraction.seniority` + `requirements[0]` | the grade word is consumed, not left as a requirement |
| `identifier.value` | `external_id` | the uuid |
| `grade` | `extraction.seniority` | `intern/junior → junior`, `middle → mid`, `senior`, `lead`/`head → lead`, `director`/`c-level → principal` |
| `english_level` (`englishLevel`) | `requirements` → `"English: B2"` | |
| `work_formats[].kind` (`workFormat`, `is_remote`) | `extraction.remote_policy` | `remote → remote_global`, `hybrid`, `office`/`onsite → onsite` |
| `relocations` (`relocation`) | `requirements` → `"Relocation: Portugal, Spain"` | |
| `experience_min_years` / `experience_max_years` | `requirements` → `"Experience: 5–8 years"` | `"5+ years"` / `"up to 8 years"` when one side is null |
| `key_skills_en` / `key_skills_ru` | `extraction.technologies` | the page locale picks the list; merged case-insensitively after the JSON-LD skills |
| `industry`, `specialization`, `company_type`, `company_website` | `field_evidence` (`source="embedded"`) + `raw_payload.embedded` | no canonical field for them yet |
| `published_at` / `updated_at` | `published_at` / evidence `updated_at` | |
| `archived` / `expired` / `status` | `raw_payload.closed = true` + evidence `status` | |
| `source`, `source_name`, `source_type` (top level) | `raw_payload.source_hint` | read from the record's top level only — `salary_analytics.source_type` names the salary basis, not the vacancy's origin |
| `original` / `original_url` / `source_url` / `apply_url` | `original_url` + `relation = aggregates` | only when the value **is** a public non-RocketHunt http(s) URL; the rendered original-language block never counts |
| `contact*`, `recruiter*` | — | never read (see below) |

Everything the record adds also lands in `raw_payload = {"jsonld": …, "embedded": {…extracted keys
only…}}` — never the whole RSC payload.

**Salary is an aggregator estimate.** RocketHunt shows an "average by country" figure computed from
its own data (`salary_estimated` / `salary_analytics` / `avgSalary`). When the page states that and
the figures do **not** appear verbatim in the vacancy text (any digit grouping — `4 200`, `4,200`),
the compensation is kept but carries `FieldEvidence(field="compensation",
source="aggregator_estimate", confidence=0.4)`, `Compensation.raw` is prefixed
`RocketHunt estimate (source_type=…, confidence=…, salary_count=…)`, and
`raw_payload.salary_is_estimate = true`. When the text does state the figures itself, the evidence
is `source="board_page", confidence=0.9` instead. Both keep the JSON-LD evidence beneath them, so
the authority order of ADR-016 §3 decides — never last-write-wins.

**Contacts are never fetched.** The "Show contacts" gate (a paid feature) is left untouched: no
`contact*` / `recruiter*` key is ever read into a field or into `raw_payload`, the gate's button and
hint text ("Show contacts", "Показать контакты", "Reach out directly about this role", …) are
stripped from `raw_text` on **every** extraction path (JSON-LD, readable text, Jina markdown,
archive), `extraction.recruiter` is forced to `None`, and `/api/` endpoints are never called. A
handle or address written openly inside the vacancy body stays in `raw_text` and is re-parsed at
ingest like any other pasted text — the connector neither recovers nor invents contacts.

**Original source.** When the page publicly names the original posting, CareerOS sets
`original_url`, marks the posting `relation = aggregates` (stored as
`rockethunt → aggregates → original` in `opportunity_source`) and keeps the RocketHunt link as
provenance; reading the original is a separate, explicit user action — this connector never
follows it.

## `careeros platform doctor rockethunt`

```text
[OK ] capabilities: profile=none jobs=manual applications=none
[OK ] detection: /{en,ru}/vacancies/<uuid> on rockethunt.ai
[OK ] robots: rockethunt.ai: allow for /en/vacancies/
[OK ] public_html: GET https://rockethunt.ai/en/vacancies/00000000-0000-4000-8000-000000000000 → 200
[OK ] structured_data: jsonld: JobPosting present
[OK ] original_source: absent (aggregated post only)
[OK ] contacts: gated (never fetched): contact keys and 'Show contacts' are ignored
```

The probe URL is a **shape-only** uuid that belongs to no vacancy, so the check pins nobody's live
posting into the repo. Against the real site it normally answers `404`, and the doctor says so
without crying wolf:

```text
[OK ] public_html: GET https://rockethunt.ai/en/vacancies/00000000-… → 404 (the sample uuid belongs to no vacancy — the host answered)
```

— `structured_data` / `original_source` are then simply not reported (nothing was read). If
`robots.txt` forbids the path, the page is not requested at all and only `robots` goes red.

## Tests

`services/careeros/tests/platform/test_rockethunt.py` (24 test functions → 38 cases, offline) over the sanitised
fixtures in `tests/platform/fixtures/rockethunt/` — `vacancy_en.html`, `vacancy_ru.html`,
`vacancy_with_original.html`, `not_found.html`, `robots.txt`. The fixtures reproduce the real
page's *shape* (JSON-LD `@graph` + `JobPosting` + `BreadcrumbList`, the i18n label chunk, the
vacancy record chunk, the contact-gate card) with invented companies, people and skills.

```bash
uv run pytest tests/platform/test_rockethunt.py -q      # from services/careeros
```

Covered: URL detection and refusal (locale, query, fragment, trailing slash, uuid casing, non-v4
id, `/profiles/`, `/api/`, another host), canonicalisation to the `en` page and private
`SourceRef`s, the full JSON-LD mapping, the embedded keys, the i18n dictionary **not** being
mistaken for data, `similar_vacancies` not leaking, the aggregator-estimate vs. `board_page` salary
evidence, `original_url` + `aggregates`, the contact gate on both the JSON-LD and the text path,
the read chain (`robots.txt` + the canonical page and *nothing else* — the mock transport fails the
test on any other path, `/api/` first of all), a 404 read raising `JobReadError` with its attempts,
`robots: Disallow` stopping the read, `search_url`, the paste path and the doctor output.

## Verification classification (master prompt §73)

**Verified from fixture (2026-08-26)** — detection, canonicalisation, extraction, provenance, the
contact gate and the read chain all run offline against the sanitised fixtures.
**Live: not tested** — no automated test touches rockethunt.ai. The only live call made while
building this connector was one manual `curl` of `https://rockethunt.ai/en` to confirm the
`potentialAction.urlTemplate` used by `search_url`
(`https://rockethunt.ai/en?text={search_term_string}`, confirmed 2026-08-26). The fixtures were
sanitised from one page read during the 2026-08-26 research pass.

## Research record (ADR-015 §66–67)

| Item | Value | Verified |
|---|---|---|
| Hosts | `rockethunt.ai` | 2026-08-26 |
| URL forms | `/{en,ru}/vacancies/<uuid>`; canonical = `en` locale, same uuid for both locales | 2026-08-26 |
| Rendering | Next.js App Router, fully server-rendered (~300 KB), RSC payload in `self.__next_f`, no `__NEXT_DATA__` | 2026-08-26 |
| Structured data | JSON-LD `@graph` (Organization, WebSite, WebPage, SiteNavigationElement) + separate `JobPosting` block + BreadcrumbList | 2026-08-26 |
| Public API | none documented; `/api/` disallowed by robots — **not used** | 2026-08-26 |
| robots.txt | `Allow: /`; `Disallow: /api/`, `/ru|en/payment/`, `/ru|en/profiles/` (same for GPTBot, ClaudeBot, PerplexityBot, Google-Extended); sitemap index with 14 parts (~650 vacancies × 2 locales each) — **not crawled** | 2026-08-26 |
| Terms | Terms of service effective 26.01.2026, Russian law: mass collection (scraping/parsing) and automated access prohibited; copying vacancies without a source link prohibited; building competing job databases prohibited | 2026-08-26 |
| Listing page | `/en/vacancies` → 404; search is client-side — use `search_url` deep link only | 2026-08-26 |
| Search deep link | JSON-LD `potentialAction.urlTemplate` = `https://rockethunt.ai/en?text={search_term_string}` (one manual `curl` of `/en`) | 2026-08-26 |
| RSC record keys | `id, source, salary_from/to, currency, experience_min_years, experience_max_years, employment_types[], work_formats[].kind, published_at, updated_at, archived, employer_name, city, country, relocations, salary_period, salary_estimated, salary_analytics{source_type, confidence, salary_count}, specialization{name_en,name_ru}, industry{…}, company_type, company_website, grade, english_level, language, is_remote, key_skills_en/ru, similar_vacancies[]` | 2026-08-26 |
| Label collision | the i18n dictionary ships the same key names with translated values (`"grade":"Grade"`, `"englishLevel":"English level"`) **before** the record ⇒ the record is located by uuid, never by key name | 2026-08-26 |
| Auth | none for public pages; contacts and profiles behind login/payment — never touched | 2026-08-26 |
| Preferred strategy | `public_html` (1 GET, identifying User-Agent, robots honoured) | — |
| Fallbacks | `jina` (public URL only), `wayback` (marked historical) | — |
| Rate limits | unknown; CareerOS sends ≤ 1 request per URL per cache TTL | — |
| Fixture | `tests/platform/fixtures/rockethunt/` (sanitised) | — |
| Limitations | no search, no statuses, salary is an estimate, original source not always public | — |
