# Generic career pages (`website`)

The fallback provider for any public job URL that no dedicated connector claims: employer career
pages, ATS-hosted postings and boards without a connector. It reads **one** page the user supplied
(ADR-015) and extracts structured data deterministically:

1. JSON-LD `JobPosting` (schema.org) — title, hiring organization, description, location,
   salary, employment type, dates, skills, identifier, apply URL;
2. Open Graph / `<title>` fallbacks;
3. readable page text → the same text parser the paste path uses.

Strategies: `public_html` → `jina` → `wayback`. Robots.txt is honoured; captcha / login / empty
JS shells are rejected with diagnostics (`careeros platform read <url> --show-attempts`).

Dedicated connectors (hh, rockethunt, justjoin, …) claim their hosts first; `website` answers with
low confidence for everything else, so adding a provider never requires touching a central
hostname switch.

## Research record

| Item | Value |
|---|---|
| Hosts | any |
| Structured data | JSON-LD JobPosting, microdata (planned), `__NEXT_DATA__` / RSC / `__NUXT__` embedded state (best effort) |
| Auth | none |
| Limitations | no search; sites that require JavaScript without SSR or structured data are reported as "empty shell" — use paste |
