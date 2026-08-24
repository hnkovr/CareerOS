# Toptal connector

**Method:** paste only. Toptal offers talent neither an API nor a data export, and the talent
portal is a JS application behind a login — CareerOS never fetches it
([ADR-005](../adr/005-no-autonomous-platform-scraping.md)). You copy the page text; the
connector parses it. No login, no tokens, no credentials to configure.

| Capability | Method | What you paste |
|---|---|---|
| read profile | `paste` | your public talent profile (`toptal.com/resume/<name>`) or the portal profile editor |
| search jobs | `paste` | the talent portal **Jobs** list |
| application statuses | `paste` (portal e-mails via the inbox, P1) | the talent portal **My Applications** list |
| write profile / apply | none | — |

`careeros platform capabilities` shows the same matrix (`official_api=false`, `auth=none`,
`email_fallback=true`).

## Capturing a page

Same recipe for every page: open it in *your* browser (logged in for the portal pages) →
**select all** (`Cmd/Ctrl+A`) → **copy** → save into a `.txt` file, or pipe the clipboard
straight to stdin with `--text-file -` (`pbpaste | careeros platform jobs toptal --text-file -
--dry-run` on macOS). Keep the text as copied: blank lines between cards/rows help the parser,
but it also copes with a copy that has none (job cards end at `Apply`, application rows at their
`Stage:` / `Applied …` line).

1. **Profile** — your public talent profile `https://www.toptal.com/resume/<your-name>` (the
   page with *Bio · Expertise · Work Experience · Project Highlights · Education*) or the portal
   profile editor. Pasting the profile URL as the first line is fine — it becomes
   `profile_url`. Save as `profile.txt`.
2. **Jobs** — talent portal → **Jobs** (the card list: *Title · Client · Engagement · Remote …
   · Duration · Rate/Budget · Posted · Skills · Apply*). Save as `jobs.txt`; repeat per page if
   the list paginates.
3. **Applications** — talent portal → **My Applications** (*Title · Client · Applied <date> ·
   Stage*). Save as `applied.txt`. The stage (*Applied · Under review · Interviewing · Matched ·
   Declined · Withdrawn · Closed*) is only visible here, so this is the paste that records
   status changes.

## Commands

```bash
# own profile → profile snapshot (then `careeros profiles audit …`)
careeros platform profile toptal --text-file profile.txt --dry-run     # preview, nothing persisted
careeros platform profile toptal --text-file profile.txt
careeros platform profile toptal --text-file - < profile.txt            # from stdin

# portal job list → opportunities (dedup + deterministic scoring happen in the opportunities module)
careeros platform jobs toptal --text-file jobs.txt --dry-run
careeros platform jobs toptal --text-file jobs.txt

# applied jobs → application observations (status history is kept per row)
careeros platform applications toptal --text-file applied.txt --dry-run
careeros platform applications toptal --text-file applied.txt

# Justfile equivalents (dry/apply pairs)
just platform-profile-dry toptal --text-file profile.txt
just platform-jobs-dry toptal --text-file jobs.txt
just platform-applications-dry toptal --text-file applied.txt
just platform-applications toptal --text-file applied.txt
```

`--json` prints the parsed DTOs; `--dry-run` never touches the database. The API equivalent is
`POST /api/platform/toptal/parse/{profile|jobs|applications}` (dry) and
`POST /api/platform/toptal/sync/{kind}` with `{"method": "paste", "text": "…"}`.
`careeros platform doctor toptal` only confirms the declared matrix — there is nothing to
connect.

## What the parsers extract

The parsers never invent values — anything not stated on the page stays `null`; the verbatim
text is kept in `raw_text` (profile, jobs) / `raw_payload.lines` (applications). Layout
assumptions below are what the parser recognises; a paste that matches none of them falls back to
the shared generic parsers (`careeros.modules.platform.parsers`: first line = title/company, first
recognisable status and date).

**Profile** (`ProfileRead`, `capture_method=paste`)

- Header: page chrome (*Toptal · Hire Talent · Apply as a Freelancer · Hire <name> · Share …*) is
  dropped; then **name** (`preferences.name`) → **title** (`headline`) → location, either
  `Based in …` / `Location: …` or a bare `City, Country` line (`preferences.location`) →
  `Member since …` (`preferences.member_since`) → `Availability: Full-time` (`availability`) →
  an hourly rate such as `$90/hr` (`rates = {"hourly": 90, "currency": "USD", "raw": …}`).
  A `toptal.com/…` URL anywhere in the text becomes `profile_url`.
- `Bio` / `About` → `about` (lines joined). The public page shows the bio as an unlabelled
  paragraph under the header — that is picked up too.
- `Expertise` / `Skills` → `skills[]`, split on commas / newlines. Toptal's categorised *Skills*
  section (*Languages · Frameworks · Libraries/APIs · Tools · Paradigms · Platforms · Storage ·
  Other*) is understood: the category labels are skipped, their items kept.
- `Work Experience` entries → `experience[]`: `Title / Company / 2023 - PRESENT` **or**
  `Title / 2023 - PRESENT / Company`, followed by bullet lines and a `Technologies: …` line, which
  become the entry's `description` (one line each, bullet markers stripped). Titles written as
  `Title at Company` are split. An entry whose company cannot be read gets `company=""`.
- `Project Highlights` / `Portfolio` — and Toptal's bare `Experience` section when a separate
  `Work Experience` exists — → `portfolio[]` items `{"name", "description", "url"}` (name = short
  line, description = the prose after it, url = a link line).
- `Education` → `preferences.education[]` (`degree`, `institution`, `period`);
  `Languages` → `preferences.languages[]`; `Certifications` → `preferences.certifications[]`.

**Jobs** (`JobPosting` + `extraction`) — one card per paragraph (or per `Apply`-terminated run
of lines): `Title / Client · Industry / Engagement: … / location line / Duration: … / Rate: … |
Budget: … / Posted … / Skills: … / Apply`

- `Client · Industry` → `company` = client, `raw_payload.industry`; `Client:` / `Industry:`
  key-value lines work too. Every card gets `contract_type=freelance`, `summary=null`.
- `Engagement: Full-time` / `Part-time (20 hrs/week)` / `Hourly` → `employment_type`
  (`full_time` / `part_time`; other wording stays `null`), `raw_payload.engagement`,
  `raw_payload.hours_per_week`.
- Location line: `Remote` → `remote_policy=remote_global`; `Remote — US hours` / `Remote (EU
  time zones)` / `Remote, PST overlap` → `remote_region` with `timezone_range` = the qualifier
  verbatim and `remote_regions` from recognised tokens only (US · CA · UK · EU · LATAM · APAC ·
  AU · IN — anything else leaves the list empty); `On-site, Berlin` → `onsite`, `Hybrid, Warsaw`
  → `hybrid`, `Berlin, Germany (On-site)` likewise; `location` = the city for on-site/hybrid, the
  whole line for remote.
- `Rate: $70 - $90/hr` → `compensation {min 70, max 90, USD, period=hour, type=rate}`;
  `Rate: $85/hr` → `min` only; `Budget: $20,000` / `Fixed price: $15k` → `period=project`;
  `/month`, `/day`, `/year` are recognised; `$ € £ ₽` and `USD EUR GBP RUB` map to ISO codes, no
  currency → `null`. The line is kept in `compensation.raw`.
- `Duration: 6 months` / `Ongoing` → `raw_payload.duration` (there is no duration field on an
  opportunity yet).
- `Posted 2 days ago` / `Posted yesterday` / `Posted Aug 12, 2026` → `posted_at`
  (`raw_payload.posted` keeps the wording; relative dates are anchored to the moment of parsing).
- `Skills: Python, dbt, Snowflake` (inline or on the next line) → `technologies[]`.
- Page chrome (*Jobs · Recommended for you · 3 open jobs · Showing 3 of 3 · Apply · Save*) is
  never a card: a block counts as a card only when it carries at least one portal signal
  (engagement, location line, duration, rate/budget, posted, skills or `Client · Industry`).

**Applications** (`ApplicationObservationIn`) — one row per block, `Title / Client / Applied
Aug 12, 2026 | Applied 3 days ago / [Updated …] / Stage: …`, or single-line rows
`Title · Client · Applied … · Stage: …`

| stage on the page | `status` |
|---|---|
| Applied | `applied` |
| Under review | `viewed` |
| Interviewing | `interview` |
| Matched | `offer` |
| Declined, Closed | `rejected` |
| Withdrawn | `withdrawn` |

`status_raw` is the whole `Stage: …` line. Unlisted wording goes through the shared
`best_status` heuristics (`Status: Interview scheduled` → `interview`); unknown wording →
`unknown`. `Applied …` → `applied_at`, `Updated …` / `Last activity …` → `updated_at_platform`.
Buttons and tabs (*View job · Withdraw application · Active (4) · Archived (1)*) are ignored — a
*Withdraw* button never becomes a `withdrawn` status. A block is a row only when it carries a
`Stage:` or `Applied …` line.

## Limits

- No API and no export: everything is a manual paste; nothing runs on a schedule
  (`careeros platform sync` lists Toptal as `skipped: needs paste`).
- No automation of any kind: no scraping, no browser driving, no stored Toptal credentials, no
  auto-apply ([ADR-005](../adr/005-no-autonomous-platform-scraping.md)).
- Portal e-mails (new matches, stage changes) are not ingested yet — they arrive via the inbox
  module (P1); until then re-paste **My Applications** to record stage changes (the observation
  keeps the previous status in its history).
- Relative dates (*Posted 2 days ago*, *Applied 3 days ago*) are resolved against the paste time;
  absolute dates are exact.
- The layouts above were written against the portal's text rendering without fetching the site;
  if Toptal relabels a section the parser degrades to the generic one — keep the paste and add a
  synthetic fixture to `tests/platform/fixtures/toptal/`.

## Files

- `services/careeros/src/careeros/modules/platform/connectors/toptal/{connector,parsers}.py`
- `services/careeros/tests/platform/test_toptal.py` +
  `fixtures/toptal/paste_{profile,jobs,applications}.txt` (synthetic persona: Dana Kovalenko;
  Northwind Commerce, Lumen Analytics, Orbit Fintech)
- Design: [platform connectors spec](../superpowers/specs/2026-08-25-platform-connectors-design.md),
  [ADR-004](../adr/004-platform-adapter-model.md),
  [ADR-005](../adr/005-no-autonomous-platform-scraping.md),
  [ADR-013](../adr/013-platform-connectors.md)
