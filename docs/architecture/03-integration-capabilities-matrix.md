# Integration Capabilities Matrix

Status: accepted for P0 (2026-08-20). **Verify each "official API" cell at integration time** —
platform programs change; this table records the working assumption used for planning, not a
guarantee. Rule of precedence ([ADR-004](../adr/004-platform-adapter-model.md), [ADR-005](../adr/005-no-autonomous-platform-scraping.md)):

```
official API > official export/import > email ingestion > user-initiated capture > manual paste
```

Legend: ✅ planned/available · ⚠️ restricted/conditional · ❌ not available or not allowed · ⏳ later phase · 👤 user-initiated only

| Platform | Read profile | Write profile | Read opportunities | Read messages | Apply | Export | Official API | Email fallback | Manual capture | Phase |
|---|---|---|---|---|---|---|---|---|---|---|
| **LinkedIn** | ⚠️ API only for partner programs; own data via GDPR export | ❌ (no API; user applies suggested text) | ❌ API; 👤 share/paste of job URL/text | ❌ API; ✅ notification emails | ❌ automation; 👤 manual with generated material | ✅ "Download your data" archive (CSV/JSON) | ⚠️ Sign-In only for normal apps | ✅ job alerts, InMail notifications | ✅ paste, share sheet, extension (P2) | P0 manual · P1 email · P2 export importer |
| **Wellfound** | ❌ no public profile API | ❌ | 👤 paste/share; ✅ email digests | ✅ email notifications | ❌ automation | ⚠️ minimal | ❌ | ✅ | ✅ | P0 manual · P1 email |
| **Upwork** | ⚠️ GraphQL API exists but requires app approval & OAuth2 | ⚠️ limited (if approved) | ⚠️ job search via API if approved; 👤 paste/share otherwise | ⚠️ rooms API if approved; ✅ email | ❌ automated proposals (ToS) | ⚠️ | ⚠️ conditional | ✅ job alert emails | ✅ | P0 manual · P1 email · P2 API spike |
| **Toptal** | ❌ | ❌ | 👤 paste from Toptal portal; ✅ email | ✅ email | ❌ | ❌ | ❌ | ✅ | ✅ | P0 manual · P1 email |
| **Generic ATS** (Greenhouse/Lever/Ashby job pages) | n/a | n/a | ✅ public job JSON (Greenhouse/Lever/Ashby boards expose JSON) — fetch on user-provided URL only | n/a | ❌ automation | n/a | ✅ public board endpoints | n/a | ✅ URL paste | P0 URL fetch (single page, user-initiated) |
| **Gmail** | n/a | n/a | via email parsing | ✅ Gmail API (OAuth, `gmail.readonly` P1; `gmail.compose` for drafts P1; send only on explicit action) | n/a | ✅ takeout | ✅ | — | ✅ forward to app | P1 |
| **Generic IMAP** | n/a | n/a | via email parsing | ✅ | n/a | n/a | ✅ IMAP | — | — | P2 |
| **JSON Resume / RenderCV / LinkedIn export / PDF-DOCX** | ✅ file import → suggestions → human confirms facts | — | — | — | — | ✅ | file formats | — | ✅ | P0: RenderCV/JSON Resume/YAML · P1: LinkedIn export, PDF/DOCX (AI-assisted) |
| **Browser extension / iOS Share** | 👤 capture current page/profile/message | ❌ | 👤 | 👤 | ❌ | — | — | — | ✅ | P2 |

## Adapter contract

Every adapter implements `PlatformAdapter` and declares a static `Capabilities`:

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
