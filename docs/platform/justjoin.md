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
| URL forms | `/job-offer/<slug>` — **verified live** (one GET, 200); legacy `/offers/<slug>` is accepted on input and canonicalised to `/job-offer/<slug>`; slug = `<company>-<title>-<city>-<category>-<hash>` | 2026-08-26 |
| Offer page JSON-LD | **verified live**: exactly one `application/ld+json` block, `@type: JobPosting` — `title`, `description`, `datePosted`, `validThrough`, `employmentType`, `hiringOrganization`, `jobLocation`, `jobLocationType`, `applicantLocationRequirements`, `baseSalary{currency, value{unitText,minValue,maxValue}}` (no `identifier`, no `skills`) | 2026-08-26 |
| Listing deep link | `/job-offers/all-locations` (+ `/job-offers/all-locations/<category>`, `/job-offers/<city>`, `?companies=…`) — base path **verified** from the offer page's own links; the free-text parameter name is **not** verified, `search_url` uses `?keyword=` — to verify | 2026-08-26 |
| Public API | `GET /api/candidate-api/offers` — list, `{data[], meta{from,totalItems,prev,next{cursor,itemsCount}}}`, cursor pagination (`per_page` ignored); `GET /api/candidate-api/offers/<slug>` — detail (`body`, `requiredSkills`, `niceToHaveSkills`, `employmentTypes[]` with salary, `applyUrl`, `url`, `publishedAt`, `expiredAt`, `isActive`, `workplaceType`, `workingTime`, `experienceLevel`, `category{key,parentKey}`, `locations[]`, `languages[]`, `companySize`, `companyUrl`, `companyProfileSlug`); legacy `/api/offers` → 404 | 2026-08-26 |
| robots.txt | `Disallow: /api/` (and `/oferty-pracy/*,*`, `/terms-and-privacy-policies`, …) | 2026-08-26 |
| Terms | User terms v. 14.07.2026: §1.2 the service content is a protected database; §11.3 automated download of service data without written consent is prohibited | 2026-08-26 |
| Access mode | public read-one of a single offer detail by user-supplied URL; no listing access | — |
| Preferred strategy | `api` detail by slug (JSON, stable field names, undocumented) | — |
| Fallbacks | `public_html` (offer page JSON-LD), `wayback` | — |
| Schema notes | additive fields tolerated; key-set fingerprint logged on change | — |
| Fixture | `tests/platform/fixtures/justjoin/` — `offer_detail.json` (sanitised: invented company, title, skills and salaries; same key set as the real payload), `offer_detail_drift.json`, `offer_404.json`, `offer_page.html`, `robots.txt` | 2026-08-26 |
| Schema baseline | 40 top-level keys, fingerprint reported by `careeros platform doctor justjoin` | 2026-08-26 |
| Verification | mapping + read chain: **from fixtures** (2026-08-26, no network in tests); public page form and its JSON-LD: one manual GET (2026-08-26); the candidate API: **live, not tested** — recorded from the 2026-08-26 research capture only | 2026-08-26 |
| Limitations | no search, no statuses, endpoint is not a contract | — |

## Read-one, operationally

"Read-one" is not a setting — it is the shape of the code (ADR-015, plan D1):

* `connectors/justjoin/client.py` contains **one** endpoint function, `offer_detail(http, slug)`.
  There is no listing / search / pagination function to call, and a test asserts the module
  defines nothing else. The policy is enforced by absence.
* The slug is only ever taken from a URL the user handed CareerOS (`careeros platform read <url>`,
  the bot, the inbox). Nothing enumerates slugs, walks a sitemap or follows "similar offers".
* One read = one request. The `api` strategy stops the chain, so a successful read touches
  `/api/candidate-api/offers/<slug>` exactly once (asserted in the tests); a repeat within the
  fetch-cache TTL touches it zero times.
* Job *discovery* on JustJoin happens in the user's own browser via `search_url`
  (`https://justjoin.it/job-offers/all-locations?keyword=…`), or by pasting a list of offers
  (`parse_jobs_text`) — never by CareerOS reading the listing endpoint.
* `robots.txt` disallows `/api/` for crawlers; `CAREEROS_JUSTJOIN_ENABLE_PUBLIC_API=false` turns
  the endpoint off entirely, and the read then falls back to the offer page's JSON-LD
  (`public_html`, which *is* robots-checked) and then to a Wayback capture.

## Mapping (candidate API detail → CareerOS)

`Capabilities`: `jobs=[paste]`, `read_job=[api, public_html, wayback]`, `access=public`,
`auth=none`, `official_api=false`.

| API key | CareerOS field | Notes |
|---|---|---|
| `title` | `JobPosting.title`, `extraction.title` | the only fatal absence — no title, no posting |
| `companyName` | `company` | |
| `companyProfileSlug`, `companySize`, `companyUrl` | `raw_payload["company"]` | |
| `locations[].city` (else `city`) + `countryCode` | `location` | comma-joined, e.g. `Warszawa, Kraków, PL` |
| `workplaceType` | `extraction.remote_policy` | `remote` → `remote_region` with `remote_regions=[countryCode]` (`remote_global` when no country is stated); `hybrid` / `partly_remote` → `hybrid`; `office` → `onsite`; anything else → `unknown` + a note |
| `hybridWorkSchedule` | `extraction.requirements[]` (`Hybrid work schedule: …`) + `raw_payload` | |
| `employmentTypes[].type` | `extraction.contract_type` | `b2b`→`b2b`; `permanent`, `contract_of_employment`→`employment`; `mandate_contract`, `contract`, `freelance`→`freelance`; unknown → `None` + a note, raw value kept |
| `workingTime` | `extraction.employment_type` | `full_time` / `part_time` only; anything else (`b2b_contract`, …) → `None` + a note |
| `employmentTypes[]` | `extraction.compensation` | the row with `currencySource="original"` (the employer's own figures) wins, else the first priced row; `unit=Hour` ⇒ `fromPerUnit`/`toPerUnit` with `period=hour`, `type=rate` (this is what the site's own JSON-LD publishes; `from`/`to` are JustJoin's monthly equivalent), otherwise `from`/`to` with the period from `unit` and `type=salary` |
| `employmentTypes[]` (all rows) | `raw_payload["salaries"]`, `raw_payload["salary_gross"]` | every currency is kept — the non-`original` rows are JustJoin's own conversions, i.e. aggregator estimates (ADR-016 §3), never the stated salary |
| `experienceLevel` | `extraction.seniority` | `junior`/`mid`/`senior` map directly, `c_level` → `principal`; unknown → `None`, string kept in `raw_payload["experience_level"]` + a note |
| `requiredSkills[].name` | `extraction.technologies` | the `level` numbers stay in `raw_payload["required_skills"]` |
| `niceToHaveSkills[].name` | `extraction.preferred` | |
| `languages[]` | `extraction.requirements[]` | one line, `Languages: EN B2, PL C1` |
| `body` (HTML) | `raw_text` (through `fetch.extract.text.html_to_text`) and `extraction.summary` | summary = first paragraph ≥ 40 chars, ≤ 400 chars; requirement/responsibility/salary heuristics run once, downstream at ingest |
| `publishedAt`, `expiredAt` | `published_at` / `posted_at`, `expires_at`, `extraction.deadline` | tolerant ISO-8601 (trailing `Z`, up to seven fractional digits) |
| `slug` | `external_id`, canonical URL `https://justjoin.it/job-offer/<slug>` | identity stays URL-stable |
| `id` | `raw_payload["guid"]` | the board's own uuid, kept but not used as identity |
| `url` | `resolved_url`, `canonical_url` | |
| `applyUrl` | `raw_payload["apply_url"]` | **not** `original_url`: JustJoin is the board, the apply link is only its handoff to the employer's ATS |
| `category` | `raw_payload["category"]` | open string, never validated |
| `isActive` | `raw_payload["is_active"]` | `false` → a note plus the `job_closed` quality flag |
| everything | `raw_payload["api"]` | the verbatim payload travels with the posting |

Every mapped field carries `FieldEvidence(source="board_api", confidence=0.9)`. A `public_html`
read (offer-page JSON-LD) is restamped `board_page`, a Wayback capture `archive`, and the
extractor that produced it stays visible in `raw_payload["extractor"]`.

## Schema fingerprint and drift

The candidate API is undocumented and is not a contract, so every read records what shape it
saw:

* `raw_payload["schema_fingerprint"]` — a short hash of the payload's **top-level key set**
  (order-independent). `mapping.BASELINE_FINGERPRINT` is the same hash over the 40 keys of the
  reference payload; `careeros platform doctor justjoin` prints it.
* Additive change (a new key) ⇒ a different fingerprint, nothing else — reads keep working.
* One of `title`, `companyName`, `body`, `employmentTypes`, `publishedAt`, `slug` missing ⇒ a
  `platform.schema_drift` structlog warning (with the missing keys and both fingerprints), a
  line in `ConnectorContext.warnings` (so the sync run reports `partial`), and a note in
  `raw_payload["notes"]`. The read still returns whatever the payload does contain — only a
  missing `title` fails it.
* Unknown enum-ish values (`category.key`, `currency`, `employmentTypes[].type`,
  `experienceLevel`, `workplaceType`) are never rejected: the raw string is kept and noted.

## Doctor

`careeros platform doctor justjoin` reports: `capabilities`, `detection` (a sample offer URL →
canonical form), `api_reachable` (one request for a slug that cannot exist — a 404 proves the
endpoint answers without reading anybody's offer; `disabled by settings` when the kill switch is
off), `listing_search` (`not implemented by policy`) and `schema_fingerprint` (the baseline).
