# Integration Capabilities Matrix

Status: accepted for P0 (2026-08-20); **implemented 2026-08-25** as `careeros.modules.platform` ([ADR-011](../adr/011-platform-connectors.md)) — the live matrix is served by `GET /api/platform/capabilities` / `careeros platform capabilities`, per-platform guides live in [docs/platform](../platform/README.md). **Verify each "official API" cell at integration time** —
platform programs change; this table records the working assumption used for planning, not a
guarantee. Rule of precedence ([ADR-004](../adr/004-platform-adapter-model.md), [ADR-005](../adr/005-no-autonomous-platform-scraping.md)):

```
official API > official export/import > email ingestion > user-initiated capture > manual paste
```

Legend: ✅ planned/available · ⚠️ restricted/conditional · ❌ not available or not allowed · ⏳ later phase · 👤 user-initiated only

| Platform | Read profile | Write profile | Read opportunities | Read messages | Apply | Export | Official API | Email fallback | Manual capture | Phase |
|---|---|---|---|---|---|---|---|---|---|---|
| **LinkedIn** | ✅ export importer (`Profile/Positions/Skills…csv`) · 👤 paste | ❌ (no API; user applies suggested text) | ✅ `Saved Jobs.csv` import · 👤 paste of job list | ❌ API; ✅ notification emails | ❌ automation; 👤 manual with generated material | ✅ "Download your data" archive → `Job Applications.csv` = application statuses | ⚠️ Sign-In only for normal apps (unused) | ✅ job alerts, InMail notifications | ✅ paste, share sheet, extension (P2) | **done: export + paste** · P1 email |
| **hh.ru** | ✅ API `GET /resumes/mine` · 👤 paste | ❌ | ✅ API `GET /vacancies` (+ similar to resume) · 👤 paste | ⚠️ via negotiations messages (later) | ❌ automation | n/a | ✅ OAuth2 (`dev.hh.ru` app) | — | ✅ | **done: api + paste** |
| **Wellfound** | 👤 paste (profile page) | ❌ | 👤 paste of job list; ✅ email digests (P1) | ✅ email notifications | ❌ automation | ⚠️ minimal | ❌ | ✅ | ✅ | **done: paste** · P1 email |
| **Upwork** | ⚠️ GraphQL API (approved API key + OAuth2) · 👤 paste | ⚠️ limited (if approved) | ⚠️ `marketplaceJobPostingsSearch` if approved · 👤 paste | ⚠️ rooms API if approved; ✅ email | ❌ automated proposals (ToS) | ⚠️ | ⚠️ conditional — `doctor` checks the live schema | ✅ job alert emails | ✅ | **done: api (conditional) + paste** |
| **Indeed** | 👤 paste (Indeed profile/resume) | ❌ | 👤 paste of search results; ✅ job-alert emails (P1) | ✅ email | ❌ automation | ❌ | ❌ (Publisher API discontinued) | ✅ | ✅ | **done: paste** · P1 email |
| **getmatch** | 👤 paste («Мой профиль») | ❌ | 👤 paste of vacancies; ✅ digest emails (P1) | ✅ email | ❌ automation | ❌ | ❌ | ✅ | ✅ | **done: paste** · P1 email |
| **Toptal** | 👤 paste (talent profile) | ❌ | 👤 paste from the talent portal; ✅ email | ✅ email | ❌ | ❌ | ❌ | ✅ | ✅ | **done: paste** · P1 email |
| **Generic ATS** (Greenhouse/Lever/Ashby job pages) | n/a | n/a | ✅ public job JSON (Greenhouse/Lever/Ashby boards expose JSON) — fetch on user-provided URL only | n/a | ❌ automation | n/a | ✅ public board endpoints | n/a | ✅ URL paste | P0 URL fetch (single page, user-initiated) |
| **Gmail** | n/a | n/a | via email parsing | ✅ Gmail API (OAuth, `gmail.readonly` P1; `gmail.compose` for drafts P1; send only on explicit action) | n/a | ✅ takeout | ✅ | — | ✅ forward to app | P1 |
| **Generic IMAP** | n/a | n/a | via email parsing | ✅ | n/a | n/a | ✅ IMAP | — | — | P2 |
| **JSON Resume / RenderCV / LinkedIn export / PDF-DOCX** | ✅ file import → suggestions → human confirms facts | — | — | — | — | ✅ | file formats | — | ✅ | P0: RenderCV/JSON Resume/YAML · P1: LinkedIn export, PDF/DOCX (AI-assisted) |
| **Browser extension / iOS Share** | 👤 capture current page/profile/message | ❌ | 👤 | 👤 | ❌ | — | — | — | ✅ | P2 |

**Application statuses** (read-only) are a fourth column in practice: hh.ru `negotiations` (API), Upwork proposals (API, conditional), LinkedIn `Job Applications.csv` (export), and the "Applied" pages of Wellfound / Indeed / getmatch / Toptal (paste). They land in `application_observation` rows with a normalized status and history ([ADR-011](../adr/011-platform-connectors.md)).

## Adapter contract

Every connector implements `BaseConnector` (`modules/platform/base.py`) and declares a static `Capabilities`. As implemented, the declaration lists the *methods* per capability and the ADR-004 levels are derived from them (so the matrix cannot drift from the code):

```python
class Capabilities(BaseModel):
    platform: Platform
    profile: list[SyncMethod]        # api | export | paste — best first
    jobs: list[SyncMethod]
    applications: list[SyncMethod]
    write_profile: CapabilityLevel = none
    read_messages: CapabilityLevel = none
    apply: ApplyLevel = none          # never above manual_assist
    official_api: bool; email_fallback: bool; auth: AuthKind; notes: str
    # derived: read_profile, read_opportunities, read_applications, export_import, manual_capture
```

The original ADR-004 sketch, kept for reference:

```python
class Capabilities(BaseModel):
    platform: Platform
    read_profile: CapabilityLevel  # none | manual | export | api
    write_profile: CapabilityLevel
    read_opportunities: CapabilityLevel
    read_messages: CapabilityLevel
    apply: CapabilityLevel  # always "none" or "manual_assist" — never automated
    export_import: CapabilityLevel
    official_api: bool
    email_fallback: bool
    manual_capture: bool
    notes: str
```

P0 ships: `ManualCaptureAdapter` (all platforms; paste/URL/structured), `AtsBoardAdapter`
(Greenhouse/Lever/Ashby public JSON on a user-supplied URL), `FileImportAdapter` (RenderCV, JSON
Resume, YAML). The UI renders this matrix from `GET /platform/capabilities` so the user always sees
what each channel can and cannot do.

## Explicitly out of scope (all phases)

Credentialed scraping, headless-browser login, CAPTCHA bypass, bulk profile crawling, auto-apply,
auto-reply. See [ADR-005](../adr/005-no-autonomous-platform-scraping.md).
