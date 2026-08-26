# JustJoin.it

Polish/EU IT job board (part of the Just Join IT ecosystem with rocketjobs.pl). CareerOS supports it
as a **read-one provider**: paste an offer URL and CareerOS reads that one offer (ADR-015). Listing
search through the site's API is deliberately **not** implemented — see the research record.

| Capability | Method | Notes |
|---|---|---|
| Own profile | — | candidate profile is behind login; no export |
| Job search | paste · `search_url` deep link | the candidate API listing is not called |
| Read one offer | `api` (offer detail) → `public_html` (JSON-LD) → `wayback` | |
| Application statuses | — | applications go to the employer's ATS |

## How to use

```bash
careeros platform read 'https://justjoin.it/job-offer/<slug>' --dry-run --show-attempts
careeros platform doctor justjoin
```

## What is extracted

Title, company (+ profile slug, size, url), locations / city / country, workplace type
(remote / hybrid / office), working time, experience level, category, required and nice-to-have
skills, employment types with salary ranges and currency, languages, published / expiry dates,
canonical and apply URLs. Category, currency and contract values are kept as open strings —
unknown values never fail parsing (schema-drift test).

## Research record (ADR-015 §66–67)

| Item | Value | Verified |
|---|---|---|
| Hosts | `justjoin.it` (also `rocketjobs.pl`, same ecosystem — not supported yet) | 2026-08-26 |
| URL forms | `/job-offer/<slug>` (verify on implementation); slug = `<company>-<title>-<city>-<category>-<hash>` | 2026-08-26 |
| Public API | `GET /api/candidate-api/offers` — list, `{data[], meta{from,totalItems,prev,next{cursor,itemsCount}}}`, cursor pagination (`per_page` ignored); `GET /api/candidate-api/offers/<slug>` — detail (`body`, `requiredSkills`, `niceToHaveSkills`, `employmentTypes[]` with salary, `applyUrl`, `url`, `publishedAt`, `expiredAt`, `isActive`, `workplaceType`, `workingTime`, `experienceLevel`, `category{key,parentKey}`, `locations[]`, `languages[]`, `companySize`, `companyUrl`, `companyProfileSlug`); legacy `/api/offers` → 404 | 2026-08-26 |
| robots.txt | `Disallow: /api/` (and `/oferty-pracy/*,*`, `/terms-and-privacy-policies`, …) | 2026-08-26 |
| Terms | User terms v. 14.07.2026: §1.2 the service content is a protected database; §11.3 automated download of service data without written consent is prohibited | 2026-08-26 |
| Access mode | public read-one of a single offer detail by user-supplied URL; no listing access | — |
| Preferred strategy | `api` detail by slug (JSON, stable field names, undocumented) | — |
| Fallbacks | `public_html` (offer page JSON-LD), `wayback` | — |
| Schema notes | additive fields tolerated; key-set fingerprint logged on change | — |
| Fixture | `tests/platform/fixtures/justjoin/` (sanitised detail + drift variant) | — |
| Limitations | no search, no statuses, endpoint is not a contract | — |
