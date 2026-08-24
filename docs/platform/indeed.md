# Indeed connector

**Method:** paste only. Indeed's Publisher API was discontinued and the site is JS-rendered behind
bot protection, so CareerOS never fetches it
([ADR-005](../adr/005-no-autonomous-platform-scraping.md)). You copy the page text; the connector
parses it. No login, no tokens, no credentials to configure.

| Capability | Method | What you paste |
|---|---|---|
| read profile | `paste` | your Indeed Profile / Resume page |
| search jobs | `paste` (email job alerts via the inbox, P1) | a search-results page |
| application statuses | `paste` (email via the inbox, P1) | **My jobs → Applied** |
| write profile / apply | none | — |

`careeros platform capabilities` shows the same matrix (`official_api=false`, `auth=none`,
`email_fallback=true`).

## Capturing a page

Same recipe for every page: open it in *your* browser → **select all** (`Cmd/Ctrl+A`) →
**copy** → save into a `.txt` file (or pipe it to stdin with `--text-file -`). Blank lines between
cards/rows are what the parser uses to split items — the copied text keeps them, so do not reflow
it.

1. **Profile** — [indeed.com/profile](https://profile.indeed.com/) → open your resume view
   (the page with *Summary · Work experience · Education · Skills …*). Save as `profile.txt`.
2. **Jobs** — run a search on Indeed (e.g. *data engineer* in *Remote*), optionally set filters,
   then copy the results page. Save as `jobs.txt`. Repeat per results page if you want more than
   one page.
3. **Applications** — the **My jobs → Applied** tab
   ([indeed.com/myjobs](https://myjobs.indeed.com/)). Save as `applied.txt`. The status chips
   (*Viewed by employer*, *Interviewing*, *Not selected by employer* …) only appear on this page,
   so this is the paste that carries status changes.

## Commands

```bash
# own profile → profile snapshot (then `careeros profiles audit …`)
careeros platform profile indeed --text-file profile.txt
careeros platform profile indeed --text-file - < profile.txt          # from stdin

# job list → opportunities (dedup + deterministic scoring happen in the opportunities module)
careeros platform jobs indeed --text-file jobs.txt --dry-run          # preview, nothing persisted
careeros platform jobs indeed --text-file jobs.txt

# application statuses → application observations (status history is kept per row)
careeros platform applications indeed --text-file applied.txt --dry-run
careeros platform applications indeed --text-file applied.txt

# Justfile equivalents (dry/apply pairs)
just platform-profile-dry indeed --text-file profile.txt
just platform-jobs-dry indeed --text-file jobs.txt
just platform-applications-dry indeed --text-file applied.txt
just platform-applications indeed --text-file applied.txt
```

`--json` prints the parsed DTOs; `--dry-run` never touches the database. The API equivalent is
`POST /api/platform/indeed/parse/{profile|jobs|applications}` (dry) and
`POST /api/platform/indeed/sync/{kind}` with `{"method": "paste", "text": "…"}`.

## What the parsers extract

The parsers never invent values — anything not stated on the page stays `null`; the verbatim block
is kept in `raw_text` (jobs, profile) / `raw_payload.lines` (applications).

**Profile** (`ProfileRead`, `capture_method=paste`)

- Preamble: line 1 = name (`raw_payload.name`), line 2 = headline, next line = location when it
  looks like one (`preferences.location`). E-mail/phone/URL lines go to `raw_payload.contacts`;
  *Willing to relocate to: …* → `preferences.willing_to_relocate`.
- `Summary` → `about` (line breaks kept).
- `Work experience` entries `Title / Company - Location / January 2023 to Present / description…`
  → `experience[]` with `period` normalised to `January 2023 – Present`; the location of each
  entry is kept in `raw_payload.experience[]`.
- `Skills` → `skills[]` with the `(N years)` / `(10+ years)` / `(Less than 1 year)` suffix
  stripped; comma-separated lines are split.
- `Education`, `Certifications and licenses`, `Assessments`, `Links`, `Languages`, `Awards` … →
  `raw_payload.<section>` as verbatim lines (they are not projected into snapshot fields).

**Jobs** (`JobPosting` + `extraction`) — one card per block: `Title / Company / [rating] /
Location / [pay] / [job type] / [badges] / snippet / Posted …`

- pay lines → `compensation`: `$120,000 - $150,000 a year`, `$60 - $80 an hour`,
  `From $90,000 a year` (min only), `Up to €95,000 a year` (max only), `£450 a day`,
  `Estimated $110K – $140K a year`; `$ € £` → `USD EUR GBP`; `a year|month|day|an hour` →
  `period`; hourly/daily pay is `type=rate`, otherwise `salary`; `a week` keeps the raw line with
  `period=null`.
- `Full-time` / `Part-time` → `employment_type`; other job types (*Contract*, *Temporary* …) are
  recognised but left unmapped.
- `Remote` / `Remote in …` → `remote_policy=remote_global`; `Hybrid work in …` → `hybrid`;
  a plain city stays `unknown`.
- `Posted 3 days ago`, `Just posted`, `Active 2 days ago` → `posted_at` (relative to now);
  `Posted 30+ days ago` is a floor, so `posted_at` stays `null`.
- Badges (*Easily apply*, *Hiring multiple candidates*, *Urgently hiring*, *Responsive employer*,
  *Typically responds within …*) → `raw_payload.badges`; the snippet → `extraction.summary`.
- Header ("… jobs in Remote / Sort by / 1,284 jobs"), job-alert and pagination blocks are skipped:
  a block is a card only when it carries at least one Indeed signal (pay, job type, posted line or
  badge).

**Applications** (`ApplicationObservationIn`) — one row per block: `Title / Company / Location /
Applied on Aug 12, 2026 | Applied 3 days ago / status chip / notes`

| chip on the page | `status` |
|---|---|
| Applied, Application submitted | `applied` |
| Viewed by employer | `viewed` |
| Interviewing | `interview` |
| Not selected by employer | `rejected` |
| Hired | `offer` |
| Application withdrawn | `withdrawn` |

Unlisted wording goes through the shared `best_status` heuristics. `Job expired`, `Applied on
Indeed`, `Applied on company site` are notes (`raw_payload.notes`) and never change the status;
button labels (*Update status*, *Withdraw application* …) are ignored. The tab bar (*My jobs ·
Saved · Applied · Interviews · Archived*) is not a row.

Pastes that match none of these shapes fall back to the shared generic parsers
(`careeros.modules.platform.parsers`) — first line = title/company, first recognisable status and
date.

## Limits

- No API and no export: everything is a manual paste; nothing runs on a schedule
  (`careeros platform sync` lists Indeed as `skipped: needs paste`).
- Job alerts by e-mail are not ingested yet — they arrive via the inbox module (P1).
- Statuses beyond *Applied* are visible only on **My jobs → Applied**; the search-results page
  carries no application state. Re-paste that page to record status changes (the observation keeps
  the previous status in its history).
- Indeed shows at most one results page per paste; company ratings, "new" badges and sponsored
  markers are kept in `raw_text` but not interpreted.
- Only the first `Company - Location` split is used for experience entries; a company name that
  itself contains ` - ` will be truncated (the full line remains in `raw_text`).

## Files

- `services/careeros/src/careeros/modules/platform/connectors/indeed/{connector,parsers}.py`
- `services/careeros/tests/platform/test_indeed.py` +
  `fixtures/indeed/paste_{profile,jobs,applied}.txt` (synthetic persona: Dana Kovalenko;
  Northwind Commerce, Lumen Analytics, Orbit Fintech)
- Design: [platform connectors spec](../superpowers/specs/2026-08-25-platform-connectors-design.md),
  [ADR-004](../adr/004-platform-adapter-model.md),
  [ADR-005](../adr/005-no-autonomous-platform-scraping.md)
