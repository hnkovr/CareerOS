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

## What is extracted

From JSON-LD `JobPosting`: title, company, description (markdown), employment type, location,
salary range (`baseSalary`), posted / valid-through dates, skills, seniority + English level
(`qualifications`), the vacancy uuid. From the embedded page state (best effort): grade, English
level, work format, relocation, experience years, industry, specialization, company type/website,
publication and update times, original-source hints.

**Salary is an aggregator estimate.** RocketHunt shows an "average by country" figure computed from
its own data (`salary_estimated` / `avgSalary`). CareerOS records it as `aggregator_estimate`
evidence and never presents it as an employer-stated salary unless the amount also appears in the
vacancy text.

**Contacts are never fetched.** The "Show contacts" gate (a paid feature) is left untouched; the
button text is never interpreted as contact data; `/api/` endpoints are never called.

**Original source.** When the page publicly names the original posting, CareerOS stores the
relation `rockethunt → aggregates → original` in `opportunity_source` and keeps the RocketHunt link
as provenance; reading the original is a separate, explicit user action.

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
| Auth | none for public pages; contacts and profiles behind login/payment — never touched | 2026-08-26 |
| Preferred strategy | `public_html` (1 GET, identifying User-Agent, robots honoured) | — |
| Fallbacks | `jina` (public URL only), `wayback` (marked historical) | — |
| Rate limits | unknown; CareerOS sends ≤ 1 request per URL per cache TTL | — |
| Fixture | `tests/platform/fixtures/rockethunt/` (sanitised) | — |
| Limitations | no search, no statuses, salary is an estimate, original source not always public | — |
