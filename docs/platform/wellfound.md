# Wellfound connector (paste-only)

Wellfound has no public API and its site sits behind JavaScript + Cloudflare, so CareerOS
**never fetches it** ([ADR-005](../adr/005-no-autonomous-platform-scraping.md),
[ADR-013](../adr/013-platform-connectors.md)). Everything below works on text **you** copy from
the page in your own browser. Nothing is written back to Wellfound.

| Capability | Method | Input | Lands in |
|---|---|---|---|
| Own profile | `paste` | profile page text | `profiles` snapshot (`capture_method=paste`) → audits |
| Jobs | `paste` | jobs list cards **or** one job page | `opportunities` ingest (dedup + deterministic scoring) |
| Application statuses | `paste` | "Applied" tab rows | `application_observation` rows (P1 pipeline) |
| Apply / write profile / messages | none | — | — |

`official_api=false`, `auth=none`, `email_fallback=true` — job-alert e-mails will be picked up by
the inbox module (P1); until then the paste path is the only one.

## 1. Capture the text

Same recipe for every page: open it logged in, **select all** (`⌘A` / `Ctrl+A`), **copy**, then
either save the clipboard as a `.txt` file or pipe it straight in with `--text-file -`.

| What | Where on Wellfound | Notes |
|---|---|---|
| Profile | `wellfound.com/u/<handle>` (your public profile) or *Edit profile → Overview* | Both layouts parse; the edit page adds the preference lines (remote, role types, desired salary, locations). |
| Jobs | `wellfound.com/jobs` with your filters applied | Copy the whole results page; cards may hold several roles per company. A single job page (`wellfound.com/jobs/<id>-…`) also parses. |
| Applications | *Jobs → Applied* (your applications list) | Copy the list; the parser keys on the `Applied <date>` line of every row. |

```bash
# macOS: clipboard → dry-run preview, nothing persisted
pbpaste | careeros platform jobs wellfound --text-file - --dry-run

# or save the paste first
pbpaste > ~/Downloads/wellfound-jobs.txt
```

Site chrome that comes along with a select-all copy (`Wellfound`, `Jobs`, `Messages`, `Apply`,
`Save`, `Edit profile` …) is ignored.

## 2. Commands

```bash
careeros platform profile wellfound --text-file profile.txt            # → profile snapshot
careeros platform jobs wellfound --text-file jobs.txt --dry-run         # preview postings
careeros platform jobs wellfound --text-file jobs.txt                   # → opportunities
careeros platform applications wellfound --text-file applied.txt       # → observations

just platform-profile-dry wellfound --text-file profile.txt
just platform-jobs-dry wellfound --text-file jobs.txt
just platform-applications-dry wellfound --text-file applied.txt
```

Add `--json` for machine-readable output. `--dry-run` parses and prints without touching the
database — use it first and check that titles/companies/salaries look right before persisting.
`careeros platform sync wellfound` reports the platform as `skipped: needs paste`, by design.

## 3. What the parsers read

### Profile page

| Field | Source line(s) |
|---|---|
| name → `raw_payload.name` | first line, when it looks like a person name (2–4 capitalised words, no role words) |
| `headline` | next line, e.g. `Senior Data Engineer · Tbilisi, Georgia` (kept verbatim) |
| `preferences.location` | `City, Country` tail of the headline, a `City, Country` header line, or `Location: …` |
| `availability` | `Ready to interview` / `Open to offers` / `Closed to offers` / `Actively looking` … |
| `about` | lines under **About** (also *About me*, *Bio*, *Summary*) |
| `skills` | lines under **Skills** / **Expertise** (one chip per line or `Python · SQL · dbt`), or `Skills: …` |
| `experience[]` | under **Work experience** / **Experience**: `Title at Company` + `Jan 2023 – Present`, description lines until the next entry; the chip layout `Company / Title / period` also works |
| `raw_payload.education` | lines under **Education**, verbatim |
| `preferences.remote` | `Open to remote` / `Remote: yes` → `true`; otherwise `null` (never assumed) |
| `preferences.roles` | `Looking for: Full-time, Contract` |
| `preferences.desired_salary` | `Desired salary: $120k+` (string, verbatim) |
| `preferences.locations` | `Preferred locations: Remote, Tbilisi` |
| `profile_url` / `external_id` | a `https://wellfound.com/u/<handle>` URL anywhere in the paste |

Sections may appear in any order. Unknown sections (*Achievements*, *Culture*, *Links* …) are kept
only in `raw_text`. Rates, projects and portfolio are never inferred.

### Jobs list

One **card** per company, separated by a blank line, possibly with several roles:

```
Northwind Commerce · 51-200 employees        ← company (size may also be its own line)
Composable commerce platform …               ← tagline / badges — kept in raw_text only
Actively Hiring
Senior Data Engineer                         ← title
Remote • US                                  ← location line
$140k – $170k • 0.05% – 0.1%                 ← compensation (equity kept in raw only)
Posted 3 days ago                            ← posted_at (relative to the time of parsing)
Apply
Save
```

| Field | Rule |
|---|---|
| `title`, `company` | title = the plain line right before a location / salary / posted / Apply line; company = card header |
| `location` | verbatim line |
| `extraction.remote_policy` | `Remote` → `remote_global`; `Remote • US`, `Remote • Europe` → `remote_region` with `remote_regions=["US"]` / `["EU"]` (the card names a region, so worldwide eligibility is **not** assumed); `Hybrid` → `hybrid`; `In office` / `Onsite` → `onsite`; a bare city → `unknown` |
| `extraction.compensation` | `$120k`, `$120K`, `$120,000`, `€80k`, `£70k+`, `Up to $150k`, `C$120k`, `120k – 150k USD`; currency from the symbol/code, `period=year` unless `/ hr`, `/ month`, `/ day`; `raw` = the whole line; equity percentages are ignored (also stored in `raw_payload.equity`) |
| `posted_at` | `Posted 3 days ago`, `Reposted today`, `Posted yesterday`, `2w ago`, `Posted Aug 12, 2026` |
| `raw_text` | company header + the role's own lines (one posting per role) |
| `raw_payload` | `company_size`, `equity`, `posted` when present |

`extraction.summary` stays `null`; the opportunities module's own parser/AI fills the rest from
`raw_text` (`--use-ai`). A single **job page** paste (`Job Location`, `Remote Work Policy`,
`Hires remotely in`, `Skills`, `About the job` …) yields one posting with `technologies` from the
Skills section and regions from *Hires remotely in*.

### Applications ("Applied" tab)

```
Northwind Commerce                           ← company
Senior Data Engineer                         ← title
Remote • US                                  ← optional extras (ignored)
Applied Aug 12, 2026                         ← applied_at ("Applied 3 days ago" also works)
Application sent                             ← status line
```

| Status line | `status` |
|---|---|
| `Application sent` / `Applied` / `Submitted` | `applied` |
| `Viewed` / `Viewed by company` | `viewed` |
| `Interview requested` | `invited` |
| `Interviewing` / `Interview scheduled` | `interview` |
| `Not moving forward` / `Rejected` / `Declined` / `Closed` | `rejected` |
| `Hired` / `Offer` | `offer` |
| `Withdrawn` | `withdrawn` |
| anything else (`In review`, `Archived` …) | `unknown` — the wording is kept in `status_raw` |

Rows are delimited by the `Applied …` / status lines, so blank lines between rows are optional.
`raw_payload.lines` keeps each row verbatim.

## 4. Limits and fallbacks

* **No API, no automation.** Nothing is fetched, no browser is driven, no credentials are stored,
  nothing is submitted (ADR-005). Job-alert e-mails: P1 inbox.
* **Unknown layouts fall back to the generic paste parsers** (`Title at Company` blocks): you still
  get postings/observations, but without salary/remote extraction. If `--dry-run` shows
  `extraction: null` or odd titles, the layout was not recognised — keep one blank line between
  cards (needed when cards carry no `N employees` line) and re-run.
* Titles are told apart from company taglines by role words (*engineer, manager, lead, …*); a
  role whose title has none and whose location is a bare city may be read the other way round —
  check the dry-run preview.
* Relative dates (`3 days ago`) are resolved against the clock at parse time; paste soon after
  copying. Dates without a year are not guessed (`applied_at`/`posted_at` stay `null`).
* `preferences.remote` is `true` or `null`, never `false`; salaries without a currency symbol or
  `k` suffix are not parsed; single figures (`$120k`) become `min = max`.
