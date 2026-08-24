# LinkedIn

Export + paste connector. **No API, no fetching**: LinkedIn offers no job/search/application API
to normal apps, and [ADR-005](../adr/005-no-autonomous-platform-scraping.md) forbids scraping,
browser automation and credential storage. CareerOS reads only two things: the data archive
**you** downloaded from LinkedIn, and page text **you** copied. `Sign In with LinkedIn` is not
used (it only yields name/e-mail).

Connector: `services/careeros/src/careeros/modules/platform/connectors/linkedin/`
(`connector.py` mapping, `export.py` archive reader, `parsers.py` paste heuristics).
Design: [ADR-004](../adr/004-platform-adapter-model.md),
[ADR-013](../adr/013-platform-connectors.md).

## Capabilities

| Capability | Methods (best first) | Source |
|---|---|---|
| `read_profile` | **export**, paste | `Profile.csv` + `Positions.csv`, `Skills.csv`, `Education.csv`, `Certifications.csv`, `Projects.csv`, `Languages.csv` · profile page copy |
| `read_opportunities` | **export**, paste | `Saved Jobs.csv` (your saved jobs, imported as postings) · job search list copy |
| `read_applications` | **export**, paste | `Job Applications.csv` · "My jobs → Applied" copy |
| `write_profile` / `apply` | none | the profiles audit produces text; you paste it yourself |

`auth=none`, `official_api=false`, `email_fallback=true` (LinkedIn e-mails — P1 Inbox).

## 1. Request your archive

1. LinkedIn → **Me → Settings & Privacy → Data privacy → Get a copy of your data**.
2. Either **"Larger data archive"** (everything) or **"Want something in particular?"** and tick
   at least: *Profile, Positions, Skills, Education, Certifications, Projects, Languages,
   Job applications, Saved jobs*.
3. **Request archive**. LinkedIn e-mails a download link — usually within minutes for the
   specific files, up to **~24 h** for the larger archive (which may arrive in two parts; the
   files above are in the first part).
4. Save the ZIP, e.g. `~/Downloads/Basic_LinkedInDataExport_08-25-2026.zip`. No need to unzip —
   a directory with the CSVs works too.

## 2. Import the archive

Always dry-run first (nothing is persisted; the parsed items are printed):

```bash
careeros platform profile linkedin --export ~/Downloads/Basic_LinkedInDataExport_08-25-2026.zip --dry-run
careeros platform profile linkedin --export ~/Downloads/Basic_LinkedInDataExport_08-25-2026.zip
careeros platform applications linkedin --export ~/Downloads/Basic_LinkedInDataExport_08-25-2026.zip
careeros platform jobs linkedin --export ~/Downloads/Basic_LinkedInDataExport_08-25-2026.zip
```

Justfile equivalents: `just platform-profile-dry linkedin --export …`,
`just platform-profile linkedin --export …`, `just platform-applications linkedin --export …`,
`just platform-jobs linkedin --export …`. Add `--json` for machine-readable output.

What lands where:

| Import | Files read | Result |
|---|---|---|
| `profile` | `Profile.csv` (**required**), the rest optional | one profile **snapshot** (`capture_method=export`): headline ← *Headline*, about ← *Summary*, experience ← `Positions.csv` (`period` = "Jan 2023 – Dec 2024", open-ended → "Jan 2023 – now"), skills ← `Skills.csv`, projects ← `Projects.csv`; `preferences` carries name, industry, location (*Geo Location*), websites, languages, certifications, education |
| `applications` | `Job Applications.csv` (**required**) | one **application observation** per row: `status=applied`, `applied_at` ← *Application Date* (`8/12/26, 10:15 AM`, kept to the minute, treated as UTC), `external_id`/`job_url` ← *Job Url* |
| `jobs` | `Saved Jobs.csv` (**required**) | one **posting** per saved job → opportunities ingest (dedup + deterministic scoring there); `received_at` ← *Saved Date* |

Missing optional files just yield empty lists. A missing required file raises
`ParseError("<file> not found in export …")` with the box to tick. CSVs are read UTF-8 with BOM
tolerance, by header name; LinkedIn's occasional `Notes:` preamble before the header row is
skipped.

**Privacy.** Never stored: *Contact Email* and *Contact Phone Number* (applications); *Address*,
*Birth Date*, *Zip Code*, *Instant Messengers* (profile). `Email Addresses.csv`,
`PhoneNumbers.csv`, `Connections.csv`, messages and the activity files are never read.

## 3. Paste alternatives (no archive needed)

Copy the page text (select all on the page, copy), save it to a file, pass `--text-file` (`-`
reads stdin). LinkedIn copies contain every string twice (accessibility twins) — that is expected
and collapsed automatically.

| What | Where to copy | Command |
|---|---|---|
| Profile | `linkedin.com/in/<you>` — the whole page | `careeros platform profile linkedin --text-file profile.txt` |
| Jobs | Jobs → search results / Recommended — the result list | `careeros platform jobs linkedin --text-file jobs.txt` |
| Applications | Jobs → **My jobs → Applied** — the list | `careeros platform applications linkedin --text-file applied.txt` |

Recognised shapes (synthetic examples: `services/careeros/tests/platform/fixtures/linkedin/*.txt`):

* **Profile page** — name / headline / location lines, *About*, *Experience* in both layouts
  (`Title / Company · Full-time / Jan 2023 - Present · 1 yr 8 mos / Location / Description` and the
  grouped `Company / Full-time · 3 yrs / Title / Dates / …`), *Top skills* and *Skills*
  (endorsement noise dropped), *Education*, *Languages*, *Licenses & certifications*. Periods keep
  LinkedIn's wording minus the duration (`Jan 2023 - Present`); locations are not stored on the
  experience item (they stay in `raw_text`). A paste that does not look like a LinkedIn page goes
  through the generic layout parser (first line = headline, `About` / `Skills` / `Experience`
  headers).
* **Job search list** — `Title / Company / Location (Remote|Hybrid|On-site) / meta` where meta is
  `Promoted · 12 applicants`, `Easy Apply`, `2 days ago`, salary… → `title`, `company`,
  `location`, `posted_at` (from the relative age), `raw_payload.{easy_apply,promoted,applicants}`.
  The copy carries no URLs, so dedup falls back on title + company.
* **Applied list** — `Title / Company / Location / Applied 3d ago` plus status chips:

  | Chip | `status` | Notes |
  |---|---|---|
  | *(none)* / `Applied …` | `applied` | `applied_at` from the relative age (`3d`, `2w`, `1mo`, `5h` are expanded) |
  | `Application viewed` | `viewed` | |
  | `Resume downloaded` | `viewed` | the employer opened your CV — same tier as viewed |
  | `No longer accepting applications` | stays `applied` | the **posting** closed; that is not a decision on you. Recorded as `raw_payload.posting_closed=true` + `raw_payload.notes` |
  | anything else (`Interview…`, `Not selected…`, `Offer…`) | shared EN/RU status rules | |

  Blocks without an `Applied` line go through the generic applications parser.

## Limits

* **No live job search.** Only your *saved* jobs come from the archive; fresh listings come from
  paste (or later from e-mail alerts, P1 Inbox).
* **Application statuses beyond "applied" only via paste** (Applied-tab chips) or e-mail; the
  export only records that you applied. Applications made on company websites are usually absent
  from `Job Applications.csv`.
* The archive has no public profile URL / member id: `profile_url` and `external_id` stay empty
  (set the URL in the vault channel file).
* Timestamps in the archive carry no timezone; they are stored as UTC. `Positions.csv` dates are
  month-granular.
* Paste heuristics are best-effort: a description line that contains `Remote` or a comma-separated
  place may be taken for a location and left out of `description` (never lost — it is in
  `raw_text`); a job whose title itself matches a status word is still parsed positionally.

## Troubleshooting

| Message | Fix |
|---|---|
| `Profile.csv not found in export` | re-request the archive with *Profile* ticked (or the larger archive) |
| `Job Applications.csv not found in export` / `Saved Jobs.csv not found in export` | tick *Job applications* / *Saved jobs* in the request |
| `expected the LinkedIn export as a directory or a .zip archive` | pass the ZIP or the unpacked folder, not a single CSV (a lone CSV: put it in a folder) |
| `export path not found` | check the path; `~` is expanded |
| empty experience/skills after a profile paste | you probably copied only the header — copy the whole page; or use the archive |
