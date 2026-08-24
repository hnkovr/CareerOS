# getmatch (getmatch.ru) — paste-only connector

getmatch has no public API and no data export, and its pages are **never fetched** by CareerOS
([ADR-005](../adr/005-no-autonomous-platform-scraping.md)). Every capability works from text you
copy out of your own browser session and paste in. Digest e-mails will be picked up by the
Inbox (P1) — `email_fallback` is declared so the capabilities matrix stays honest.

| Capability | Method | What you paste |
|---|---|---|
| Own profile → profile snapshot | paste | «Мой профиль» page |
| Jobs → opportunities (dedup + scoring) | paste | «Вакансии» list (the cards you see) |
| Application statuses → observations | paste | «Отклики» page |
| Auth | none | — |
| `official_api` / `apply` / `write_profile` | `false` / `none` / `none` | — |

Russian UI is the primary target; the English UI variant («Jobs», «Responses», «Status: Sent»)
is parsed too.

## How to copy a page

1. Open the page in your browser while logged in (getmatch.ru → «Мой профиль», «Вакансии» or
   «Отклики»). For the vacancies list, scroll until every card you want is on screen.
2. Select all (`Cmd/Ctrl+A`) and copy (`Cmd/Ctrl+C`). Plain text is what the parser expects —
   the visible card text, one card per paragraph.
3. Either save it as a `.txt` file (`profile.txt`, `vacancies.txt`, `responses.txt`) or pipe it
   from the clipboard with `--text-file -` (stdin), e.g. `pbpaste | … --text-file -` on macOS.

Nothing is sent anywhere: the connector only reads the text you give it.

## Commands

Always start with `--dry-run` — it parses and prints, and persists nothing.

```bash
# «Мой профиль» → profile snapshot (then `careeros profiles audit …` can score it)
careeros platform profile getmatch --text-file profile.txt --dry-run
careeros platform profile getmatch --text-file profile.txt

# «Вакансии» cards → opportunities (dedup + deterministic scoring happen in the opportunities module)
careeros platform jobs getmatch --text-file vacancies.txt --dry-run
careeros platform jobs getmatch --text-file vacancies.txt

# «Отклики» → application observations (status history is kept per row)
careeros platform applications getmatch --text-file responses.txt --dry-run
careeros platform applications getmatch --text-file responses.txt

# clipboard → stdin
pbpaste | careeros platform jobs getmatch --text-file - --dry-run

# Justfile equivalents (dry/apply pairs)
just platform-jobs-dry getmatch --text-file vacancies.txt
just platform-jobs getmatch --text-file vacancies.txt
just platform-profile-dry getmatch --text-file profile.txt
just platform-applications-dry getmatch --text-file responses.txt
```

`--json` prints the parsed DTOs; the API equivalent is
`POST /api/platform/getmatch/parse/{profile|jobs|applications}` (dry parse) and
`POST /api/platform/getmatch/sync/{kind}` with `{"text": "…", "dry_run": true}`.

## What the parser understands

The parser never invents values: anything it cannot read stays `null`, and the pasted text is
kept verbatim (`raw_text` on profile and jobs, `raw_payload.lines` on responses). A paste in a
layout it does not recognise falls back to the shared generic parsers (title/company per
paragraph, first recognisable status and date).

### «Мой профиль»

| Page element | Result |
|---|---|
| Name line (two–four capitalised words) | `raw_payload.name` |
| Position line right after the name (e.g. `Senior Data Engineer`), or `Позиция: …` | `headline` |
| `Тбилиси, Грузия` / `Remote` / `Локация: …` | `preferences.location` |
| `Открыта к предложениям`, `Ищу работу`, `Open to offers` … | `availability` |
| «О себе» block | `about` |
| «Стек» / «Навыки» / «Технологии» / `Skills:` (comma, `·` or line separated) | `skills` |
| «Опыт работы» entries: `Позиция — Компания`, `2023 — настоящее время`, description lines | `experience[]` (`title`, `company`, `period`, `description`) |
| `Ожидания по зарплате: от 400 000 ₽` / `$6 000` | `rates = {"salary_min": 400000, "currency": "RUB", "period": "month", "raw": "…"}` (`до …` gives `salary_max`) |
| `Английский: B2` / `English — C1` | `preferences.english` |
| `Удалённо: да/нет`, `Формат работы: …`, `Готов(а) к переезду`, `Не готова к переезду`, `Relocation: yes` | `preferences.remote`, `preferences.relocation` (`true` / `false` / `null` when not stated) |

Section headers end the previous section; «Образование», «Языки», «Проекты» etc. are skipped
(their text stays in `raw_text`). Navigation chrome («Вакансии», «Мой профиль»,
«Редактировать») is ignored.

### «Вакансии» cards

One card = one paragraph block. A block counts as a card only when it carries at least one
card marker (a salary, «Откликнуться»/«Apply», «Опубликовано …»/«Posted …», a «Стек:» row,
or a format tag together with a level tag) — so filters, counters and navigation are skipped.

| Card element | Result |
|---|---|
| Company and title lines (company first on the site; the line with a role word — engineer, разработчик, analyst … — is the title) | `title`, `company`; also `Компания: …` / `Позиция: …` labels |
| `от 300 000 ₽`, `250 000 – 350 000 ₽`, `$4 000 – $6 000`, `до 5 000 €`, `$4k–$6k`, `300 тыс. руб.` | `extraction.compensation` — `min`/`max` (`от` → min only, `до`/`up to` → max only, bare amount → min = max), `currency` from the symbol (₽ RUB, $ USD, € EUR), `period` month unless the line says «в год»/«per year»/«в час», `type = salary`, `raw` = the line. Thin / no-break spaces and commas are thousand separators. |
| `Удалённо` / `Remote`, `Гибрид · Москва` / `Hybrid · Tbilisi`, `Офис · Санкт-Петербург` / `Office · Warsaw` | `extraction.remote_policy` (`remote_global` / `hybrid` / `onsite`), the city → `location` (a remote-only card has `location = null`) |
| `Senior`, `Middle`, `Junior`, `Lead`, `Senior/Lead`, `Middle+` (own line or tag row); otherwise the level word in the title | `extraction.seniority` (`Middle` → `mid`) |
| `Стек: Python · dbt · ClickHouse` or an unlabelled tag row of known technologies | `extraction.technologies` |
| `Полная занятость` / `Full-time`, `Частичная занятость`, `Проект` | `extraction.employment_type` |
| `Опубликовано 2 дня назад`, `Опубликовано 12 августа 2026`, `12 августа` (current year), `20.08.2026`, `Posted Aug 20, 2026`, `вчера` | `posted_at` (UTC) |
| `https://getmatch.ru/vacancies/<id>` when present | `url`, `external_id` |

### «Отклики»

One row = one paragraph block (`Позиция`, `Компания`, `Статус: …`, date); a copied HTML table
(tab-separated cells) is accepted too.

| Row element | Result |
|---|---|
| Title and company lines (title first on the site) or `Позиция · Компания` | `job_title`, `company` |
| `Статус: Отправлен` / `Просмотрен` / `Приглашение` / `Интервью` / `Отказ` / `Оффер` / `Отозван` (EN: `Sent`, `Viewed`, `Invited`, `Interview`, `Rejected`, `Offer`, `Withdrawn`) | `status` (applied / viewed / invited / interview / rejected / offer / withdrawn), `status_raw` = the line |
| `12 августа 2026`, `2 дня назад`, `Отклик отправлен 5 августа 2026` | `applied_at` |
| `Обновлено 22 августа 2026` | `updated_at_platform` |

Rows without a recognisable status *and* without a date are ignored (tabs like «Все · Активные ·
Архив» never become observations).

## Limits

* No API, no export, no automation: nothing is fetched, no browser is driven, no credentials
  are stored ([ADR-004](../adr/004-platform-adapter-model.md), ADR-005). Re-paste whenever you
  want fresher data.
* The parser is heuristic. A company whose name contains a role word (e.g. «… Engineering Lead»)
  may be swapped with the title; a line of the form `Something at Something` inside an
  experience description may start a new experience entry. Check `--dry-run` output before
  applying; the raw text is always kept so nothing is lost.
* `posted_at` for relative phrases («2 дня назад») is computed from the moment of parsing.
* Digest e-mails («новые вакансии по вашему профилю») are not parsed yet — they will arrive
  through the Inbox module (P1).
