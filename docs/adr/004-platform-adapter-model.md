# 004 — Platform adapters declare capabilities; precedence of integration methods

* Status: accepted
* Date: 2026-08-20

## Context

LinkedIn, Wellfound, Upwork, Toptal, ATS boards and email offer radically different and changing
levels of official access. The UI and workflows must not assume symmetric capabilities, and the
product must never degrade into scraping (see ADR-005).

## Decision

* `careeros.modules.platform` defines `PlatformAdapter` with a static `Capabilities` declaration
  (`read_profile`, `write_profile`, `read_opportunities`, `read_messages`, `apply`, `export_import`,
  `official_api`, `email_fallback`, `manual_capture`, each a `CapabilityLevel ∈ {none, manual, export, api}`; `apply` is never above `manual_assist`).
* A `PlatformRegistry` exposes the matrix at `GET /platform/capabilities`; the web UI renders it so
  the user always sees what each channel supports.
* Integration precedence is fixed: **official API > official export/import > email ingestion >
  user-initiated capture > manual paste.** An adapter may implement several; the highest available is used.
* P0 adapters: `ManualCaptureAdapter` (all platforms), `AtsBoardAdapter` (public Greenhouse/Lever/Ashby
  JSON for a user-supplied URL, single fetch), `FileImportAdapter` (RenderCV/JSON Resume/YAML).
  P1: `GmailAdapter`. P2: LinkedIn export importer, Upwork API spike, browser/share capture.
* Adapters are pure I/O + mapping to domain DTOs; they never call domain services or write to the DB
  directly. Domain services call adapters.

## Alternatives considered

* **One generic "scraper" adapter with per-site selectors** — violates ToS, brittle, and hides the capability asymmetry from the user.
* **Per-platform bespoke modules inside each domain context** — duplicates capture logic across opportunities/profiles/inbox.

## Consequences

* + Honest UX; new platforms are additive; workflows can branch on capabilities.
* − Most platforms will stay at `manual`/`email` for a long time; the product must be excellent at paste/share/email capture.
