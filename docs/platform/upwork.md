# Upwork

API + paste connector. Upwork has an **official GraphQL API** (`https://api.upwork.com/graphql`,
OAuth2), but every API key is **reviewed by Upwork before it works** — so each capability also has a
paste path that works on day one. Nothing else is touched: no HTML fetching, no browser automation,
no cookies or passwords ([ADR-005](../adr/005-no-autonomous-platform-scraping.md)); the only
credentials CareerOS holds are the OAuth tokens *you* grant (git-ignored file, mode 0600).

Connector: `services/careeros/src/careeros/modules/platform/connectors/upwork/`
(`connector.py` contract, `client.py` GraphQL client, `queries.py` documents, `mapping.py`
payload → DTOs, `parsers.py` paste heuristics). Design: [ADR-004](../adr/004-platform-adapter-model.md),
[ADR-013](../adr/013-platform-connectors.md).

## Capabilities

| Capability | Methods (best first) | Source |
|---|---|---|
| `read_profile` | **api**, paste | `user { freelancerProfile … }` · your public profile page copy |
| `read_opportunities` | **api**, paste | `marketplaceJobPostingsSearch` · "Find Work" / job search results copy |
| `read_applications` | **api**, paste | `vendorProposals` (one query per proposal status) · "My proposals" page copy |
| `write_profile` / `apply` | none | the profiles audit produces text; you paste it yourself |

`auth=oauth2`, `official_api=true`, `email_fallback=true` (Upwork notification e-mails — P1
Inbox). Notes on the live matrix: *GraphQL API needs an approved Upwork API key; paste works
without it.*

## 1. API key (approval needed) — use paste meanwhile

1. Log in to Upwork and open <https://www.upwork.com/developer/keys>.
2. **Request a new key**: project name (e.g. *CareerOS*), a short description of what it does
   (read-only sync of your own profile, job search and proposal statuses into a personal tool),
   type **OAuth 2.0** (authorization-code grant), and the **callback URL**
   `http://localhost:8000/api/platform/oauth/upwork/callback` (that is
   `CAREEROS_PLATFORM_OAUTH_REDIRECT_BASE` + `/upwork/callback`; on a deployed instance register the
   public base instead — see the [README](README.md#deployment-notes-read-before-shipping-the-platform-layer-to-flycontainers)).
   Tick read permissions for the current user, marketplace job postings and proposals.
3. Upwork reviews the request; approval can take days. **Until it is approved, every command below
   still works with `--text-file`** (section 4).
4. Put the key pair in `.env.secrets` (never in a template with a value — templates stay blank):

   ```dotenv
   CAREEROS_UPWORK_CLIENT_ID=
   CAREEROS_UPWORK_CLIENT_SECRET=
   ```

   Containers can pin tokens instead of a token file: `CAREEROS_UPWORK_ACCESS_TOKEN`,
   `CAREEROS_UPWORK_REFRESH_TOKEN`.

## 2. Connect and check

```bash
careeros platform connect upwork     # prints the authorize URL; log in in YOUR browser, paste the code
careeros platform doctor upwork      # config / token / live-schema checks (see below)
careeros platform refresh upwork     # access tokens live ~24 h; refresh is also automatic on 401
careeros platform disconnect upwork  # deletes the tokens; revoke the app on Upwork as well
```

Justfile: `just platform-connect upwork`, `just platform-doctor upwork`.

`doctor` output, line by line:

| Check | Meaning |
|---|---|
| `capabilities` | the declared matrix |
| `client_credentials` | `CAREEROS_UPWORK_CLIENT_ID` / `_SECRET` present |
| `tokens` | tokens present and not expired (`fix` names the command otherwise) |
| `graphql:user`, `graphql:marketplaceJobPostingsSearch`, `graphql:vendorProposals` | whether the **root fields used by `queries.py` exist on the live schema** (a minimal introspection `{ __type(name:"Query") { fields { name } } }`). A missing root means the field was renamed or your key lacks that permission — see *Limits*. |
| `graphql:introspection` | shown instead of the three above when the probe itself fails (introspection disabled, network, 401); never raises |

## 3. Sync through the API

Always dry-run first (fetch + print, persist nothing):

```bash
careeros platform profile upwork --api --dry-run
careeros platform profile upwork --api
careeros platform jobs upwork --api -q "data engineer dbt clickhouse" --limit 50 --dry-run
careeros platform jobs upwork --api -q "data engineer dbt clickhouse" --limit 50
careeros platform applications upwork --api --dry-run
careeros platform applications upwork --api
careeros platform sync upwork                  # all three, best available method
```

Justfile equivalents: `just platform-profile upwork --api`, `just platform-jobs upwork -q "…"`,
`just platform-applications upwork --api`, `-dry` variants, `just platform-sync upwork`.

What lands where:

| Sync | GraphQL | Result |
|---|---|---|
| `profile` | `user { id nid name ciphertext freelancerProfile { personalData skills availability project } }` | one profile **snapshot** (`capture_method=api`): headline ← `personalData.title`, about ← `personalData.description`, skills ← `skills` names, `rates={"hourly", "currency", "raw"}` ← `chargeRate`, availability ← `availability.name` + capacity label (`fullTime` → *More than 30 hrs/week*), portfolio ← `project[]` `{name, url}`, `profile_url` ← `personalData.profileUrl` or `https://www.upwork.com/freelancers/~<ciphertext>`, `external_id` ← user id. A client-only account (no freelancer profile) yields a sparse snapshot, not an error. |
| `jobs` | `marketplaceJobPostingsSearch(searchType: USER_JOBS_SEARCH, sortAttributes: [{field: RECENCY}])` | one **posting** per node → opportunities ingest (dedup + deterministic scoring there): `url=https://www.upwork.com/jobs/~<ciphertext>`, `title`, `location` ← client country, `posted_at` ← `createdDateTime`, `extraction`: `contract_type=freelance`, `remote_policy=remote_global`, hourly range → `Compensation(min, max, USD, period=hour, type=rate)`, fixed budget → `Compensation(amount, currency, period=project)` + `employment_type=project`, `technologies` ← skills, `summary` ← description[:600]. The client is anonymous in search results: `company` stays empty. |
| `applications` | `vendorProposals(filter: {status_eq}, sortAttribute: {CREATEDDATETIME DESC}, pagination)` for each of `Accepted, Activated, Offered, Hired, Declined, Withdrawn, Archived` (≤ 3 pages × 50) | one **application observation** per proposal: `external_id` ← proposal id, `job_title` ← `marketplaceJobPosting.content.title`, `company` ← `clientCompanyPublic.name`, `status_raw` ← Upwork status, `applied_at` / `updated_at_platform` ← `auditDetails`, whole node in `raw_payload` |

Query knobs for `jobs`: `-q/--query` → `searchExpression_eq` (title + description + skills),
`--location` → `locations_any`, `--limit` → `pagination_eq.first`; `--remote` is ignored (every
Upwork job is remote). `posted_since` (API `SyncRequest.query.posted_since`) has no filter
counterpart on Upwork and is applied **client-side** on `createdDateTime`. Through the HTTP API
(`POST /api/platform/upwork/sync/jobs`) `query.extra` also accepts `title`
(`titleExpression_eq`), `skills` (`skillExpression_eq`), `category_ids`, `job_type`
(`hourly` | `fixed`) and `verified_payment_only`.

Proposal status → normalized `status`:

| Upwork status (`status.status`) | `status` | Notes |
|---|---|---|
| `Accepted`, `Pending`, `Active`, `Submitted` | `applied` | `viewedByClient=true` upgrades to `viewed` |
| `Activated` | `interview` | created by accepting a client's invitation — an interview room is open |
| `Offered`, `Hired` | `offer` | |
| `Declined`, `Archived` (job closed without you), `Rejected`, `Closed` | `rejected` | |
| `Withdrawn` | `withdrawn` | |
| anything else | shared EN/RU keyword rules, else `unknown` | the raw value is always kept in `status_raw` |

## 4. Paste alternatives (no API key needed)

Open the page, select all, copy, save to a file, pass `--text-file` (`-` reads stdin). Dry-run
variants: `just platform-profile-dry upwork --text-file …` etc.

| What | Where to copy | Command |
|---|---|---|
| Profile | your public profile `https://www.upwork.com/freelancers/~…` (or *Settings → My profile*) — the whole page | `careeros platform profile upwork --text-file profile.txt` |
| Jobs | *Find Work* feed or job search results (`upwork.com/nx/find-work/`, `upwork.com/nx/search/jobs/`) — select the job cards | `careeros platform jobs upwork --text-file jobs.txt` |
| Proposals | *My proposals* (`upwork.com/nx/proposals/`) — the whole page incl. section titles | `careeros platform applications upwork --text-file proposals.txt` |

Recognised shapes (synthetic examples: `services/careeros/tests/platform/fixtures/upwork/paste_*.txt`):

* **Profile page** — the title is the line right above `$NN.NN/hr` (badges like *Top Rated*,
  *100% Job Success* and the local-time line are skipped); the overview is the text between the
  rate and the first section title; `$NN.NN/hr` → `rates={"hourly": NN, "currency": "USD"}`;
  *Available now* / *More than 30 hrs/week* / *Open to contract to hire* → `availability`;
  *Skills* (one per line or `Skills: a, b`) → `skills`; *Portfolio* item names → `portfolio`;
  *Work history* contract titles (+ period) → `projects`; *Employment history*
  `Title | Company` + period → `experience`. A text with neither a rate nor Upwork section titles
  goes through the generic layout parser.
* **Find Work cards** — `Posted N hours ago` / title / `Hourly: $X.00-$Y.00 - Expert - Est. time: …`
  or `Fixed-price - Intermediate - Est. budget: $N` / description / skill chips / `Payment
  verified` / `$50K+ spent` / country / `Proposals: 5 to 10`. Cards are split on blank lines or on
  each `Posted …` line. Result: `posted_at` (relative age expanded), `extraction.compensation`
  (hourly → `period=hour`, fixed → `period=project`, amounts only when printed), experience level
  → `seniority` (*Expert* → senior, *Intermediate* → mid, *Entry level* → junior), `30+ hrs/week`
  → `full_time`, `Less than 30 hrs/week` → `part_time`, chips → `technologies`, country →
  `location`, *Payment unverified* → `red_flags`; `raw_payload` keeps `proposals`,
  `client_spent`, `payment_verified`. Cards without a `Posted` or budget line fall back to the
  generic list parser.
* **My proposals** — section titles set the default status, row keywords refine it:

  | Section | default `status` | Row keyword overrides |
  |---|---|---|
  | *Offers* | `offer` | |
  | *Invitations to interview* | `invited` | |
  | *Active proposals* (the client has responded — interview room open) | `interview` | |
  | *Submitted proposals* | `applied` | `Viewed by client` → `viewed` |
  | *Referrals* | `unknown` | |
  | *Archived proposals* | `rejected` | `Withdrawn` → `withdrawn`, `Declined …` → `rejected` |

  A row is `Title` + `Client · Initiated Aug 12, 2026 · Viewed by client` (or one line:
  `Title · Initiated …`, `Title / Client / Aug 12, 2026 / Interviewing`). A keyword only wins over
  the section default when it is at least as advanced (`Offer received` under *Offers* keeps
  `offer`; `Viewed by client` under *Active proposals* keeps `interview`); terminal words
  (`Declined`, `Withdrawn`) always win. `applied_at` ← the row's date; `status_raw` ← the winning
  keyword line, or the section title. A paste without section titles goes through the generic
  applications parser.

## Limits

* **API access is conditional** on Upwork approving your key and granting the permissions each
  document needs (current user, marketplace job postings, proposals). A permission gap surfaces as
  `upstream error 200: … oauth2 permissions/scopes …` or a missing root in `doctor`.
* **Schema fields marked `# VERIFY LIVE`** in `queries.py` could not be confirmed offline against
  the public reference: `skills.edges.node.{prettyName,preferredLabel}`, whether `project` is a
  list, `personalData.profileUrl`, `client.location.country` on search nodes, `publishedDateTime`
  / `durationLabel` on search nodes, `clientCompanyPublic.name` on a proposal's job,
  `sortOrder: DESC`, `pageInfo` names, one-status-per-call for `status_eq`, and whether
  introspection is enabled at all. A wrong field fails the *whole* document with a
  `Cannot query field …` error — edit the document, re-run `doctor`.
* Search results **anonymise the client** (`company` empty, only the country); proposals carry
  the client company only when Upwork exposes it publicly. Hourly budgets are assumed **USD**
  (the search node has no currency for them). `salary_min` is not mapped (`IntRange` shape
  unconfirmed); `posted_since` filters client-side, so `--limit` caps what is fetched *before*
  the date filter.
* `applications` issues up to 7 × 3 GraphQL calls. A status whose query is rejected is logged and
  skipped; the sync fails only when every status fails. Pending invitations
  (`vendorInvitations`) are not read yet — accept them on Upwork and they show up as `Activated`.
* Paste heuristics are best-effort: a title that itself starts with a status word or contains a
  year is taken for a detail line; a very short description line may be taken for a skill chip.
  Nothing is lost — the copied text stays in `raw_text` / `raw_payload.lines`.

## Troubleshooting

| Message | Fix |
|---|---|
| `upwork: not connected — set CAREEROS_UPWORK_CLIENT_ID / CAREEROS_UPWORK_CLIENT_SECRET (API key at …)` | request the key (section 1), fill `.env.secrets`, re-run `connect` |
| `upwork: not connected — connect first` | `careeros platform connect upwork` (or use `--text-file`) |
| `token rejected (401) — reconnect or refresh` | `careeros platform refresh upwork`; if that fails, `disconnect` + `connect` |
| `upstream error 200: Cannot query field "…"` | live schema differs from `queries.py` — run `doctor`, fix the field under its `# VERIFY LIVE` note |
| `upstream error 200: … oauth2 permissions/scopes …` | the key lacks that permission — edit the key at upwork.com/developer/keys and re-authorise |
| `graphql:vendorProposals … missing on live schema` | proposals permission not granted, or the root was renamed — check the reference, then `queries.py` |
| `graphql:introspection … failed` | introspection is disabled for your key — probe with `careeros platform profile upwork --api --dry-run` |
| paste yields no jobs / wrong titles | copy the cards including their `Hourly:` / `Fixed-price` line and the `Posted …` line |
| paste yields no proposals | copy the whole *My proposals* page so the section titles (*Submitted proposals* …) are included |
