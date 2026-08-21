# 005 — No autonomous scraping, no credential storage, no auto-apply/auto-reply

* Status: accepted
* Date: 2026-08-20

## Context

Automating LinkedIn/Upwork/Wellfound/Toptal via stored passwords, headless browsers, or CAPTCHA
bypass violates their terms, risks account bans, and makes the product legally and ethically
fragile. The product's value is decision support, not volume.

## Decision

The application will **not**:

* store platform passwords or session cookies;
* run hidden crawlers, headless logins, or CAPTCHA/anti-bot circumvention;
* mass-apply, auto-submit proposals, or auto-send emails/messages.

The application **will**:

* ingest via official APIs, official exports, email, user-initiated capture (share sheet, extension on the current page) and manual paste;
* generate everything the user needs to act (text, checklists, prompts) and leave the external write to the user;
* gate every future outbound action (P1 `Action` table) behind an explicit approval state; any automation policy is opt-in per workflow and never default.

## Alternatives considered

* **"Grey" browser automation with the user's session** — rejected: ToS risk, fragility, and it converts the product into exactly the spam engine it is meant not to be.

## Consequences

* + Safe to self-host and to offer as SaaS; no credential liability.
* − Some workflows require a manual step (copy headline → paste into LinkedIn). The UX must make that step one click + one paste.
