# 015 — User-initiated public job-URL reads: fetch strategies and access policy

* Status: accepted
* Date: 2026-08-26
* Deciders: maintainer
* Supersedes: [ADR-013](013-platform-connectors.md) decision 5 ("no HTML is fetched … in this slice")
* Tracking: GitHub #21 (item 1) · design: [Universal Job Intelligence](../superpowers/specs/2026-08-26-universal-job-intelligence-design.md)

## Context

[ADR-005](005-no-autonomous-platform-scraping.md) forbids autonomous scraping, credential storage and
auto-apply; [ADR-004](004-platform-adapter-model.md) already allowed a single, user-initiated fetch
of a public ATS job JSON (`AtsBoardAdapter`); [ADR-013](013-platform-connectors.md) deliberately
shipped no HTML reads and deferred a single job-URL fetch to #21. The owner now needs to import a
vacancy from a link they found — on hh.ru, JustJoin.it, RocketHunt or an employer page — and
RocketHunt has no API at all. Verified 2026-08-26: RocketHunt and JustJoin both disallow `/api/` for
crawlers and their terms prohibit mass/automated collection; RocketHunt pages are fully
server-rendered with JSON-LD `JobPosting`; JustJoin's candidate API answers per-offer detail; the HH
API refuses anonymous vacancy reads (403) from this workstation.

## Decision

1. **Read-one by user-supplied URL only.** A connector may fetch exactly the resource behind a URL
   the user gave (one page or one API detail record), the way a browser would: identifying
   `User-Agent`, `robots.txt` honoured per host, ≤ 1 request per URL per cache TTL, bounded retries,
   no cookies, no login, no CAPTCHA/WAF circumvention, no contact-gate unlocking. Listings,
   sitemaps, search endpoints and bulk collection stay forbidden; discovery on such sites is a
   `search_url` deep link the user opens themselves. The paste path remains available everywhere.
2. **Strategies are data.** `Capabilities.read_job` lists the ordered `FetchStrategy` chain
   (`api | public_html | jina | wayback`; `archive_today`/`search_recovery` reserved) and
   `Capabilities.access` states the access mode (`public | authenticated_user_api |
   manual_import | unsupported`). The registry verifies declared ⇒ implemented. The chain stops at
   the first artifact of sufficient quality; native API success never waits for archives.
3. **Fallbacks through third parties are allowed for public URLs only.** Jina Reader
   (`r.jina.ai`) and Wayback (CDX) are generic strategies, enabled by default
   (`CAREEROS_JOB_FETCH_ENABLE_JINA`, `…_WAYBACK`), skipped for sources that came from private
   messages (`SourceRef.kind in {email, telegram_message}`). Archive output is marked historical
   and never dated as the publication date; Jina output is a transformed representation, never
   authoritative over native structured fields.
4. **HTTP 200 is not a vacancy.** Every artifact carries quality/completeness scores; captcha,
   login, interstitial, empty-shell, closed and search-result pages are rejected and never cached
   as success. Failures return attempt diagnostics (`JobReadError`), not a bare "failed to fetch".
5. **Per-provider kill switches and budgets** (`CAREEROS_<PROVIDER>_ENABLE_PUBLIC_HTML`,
   `CAREEROS_JOB_FETCH_*`) so the owner can disable any provider or strategy without a deploy.

## Alternatives considered

* **Keep "no HTML ever" (ADR-013 §5)** — leaves RocketHunt and employer pages paste-only; rejected
  by the owner as too little value.
* **Follow the master prompt literally (JustJoin listing search via `/api/candidate-api`, RocketHunt
  SSR reads at scale)** — conflicts with both sites' terms and robots; rejected.
* **Browser rendering (Playwright) as an optional strategy** — product brief forbids browser
  automation; RocketHunt is SSR anyway; rejected.

## Consequences

* ADR-005 stays in force; this ADR narrows what "user-initiated capture" means technically.
* Terms-of-service exposure is documented per provider in `docs/platform/<p>.md` ("research
  record" with verification date); the owner accepts it for personal use.
* Follow-ups: MCP surface (phase I), ATS providers (Greenhouse/Lever/Ashby, ADR-004 precedent),
  inbox links to `opportunity_source`.
