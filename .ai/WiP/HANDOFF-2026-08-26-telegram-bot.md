# HANDOFF — CareerOS Telegram bot + Fly deploy

Date: 2026-08-26 (updated) · Lane: bot core features and deploying to hosting
Keep-list snapshot for `/compact-safely`. Everything below is on disk and committed.

Authoritative reading order:
1. `docs/superpowers/specs/2026-08-25-careeros-telegram-bot-design.md` — full design
2. `docs/adr/012-telegram-bot-surface.md` — the decisions and why
3. `docs/developer-guide/telegram-bot.md` — how to run, deploy and diagnose
4. `~/.ai/skills/_settings/careeros.yml` — every ops scalar (SSoT, not duplicated in scripts)

## Open tasks (keys + status)

GitHub `hnkovr/CareerOS` (private). Bot slice = #1–#9, all OPEN, none started.

| # | Task | Status |
|---|---|---|
| [#1](https://github.com/hnkovr/CareerOS/issues/1) | webhook route + three security gates | open — start here |
| [#2](https://github.com/hnkovr/CareerOS/issues/2) | webhook ownership claim at startup | open |
| [#3](https://github.com/hnkovr/CareerOS/issues/3) | capture: forwarded text/URL → scored opportunity | open |
| [#4](https://github.com/hnkovr/CareerOS/issues/4) | triage commands + inline callbacks | open |
| [#5](https://github.com/hnkovr/CareerOS/issues/5) | read-only career commands (/cv, /facts, /profile) | open |
| [#6](https://github.com/hnkovr/CareerOS/issues/6) | owner ops commands (/status, /whoami, /help) | open |
| [#7](https://github.com/hnkovr/CareerOS/issues/7) | outbound high-score notifications | open |
| [#8](https://github.com/hnkovr/CareerOS/issues/8) | `core/db`: normalize `postgres://` for Fly MPG | open — **blocks first deploy** |
| [#9](https://github.com/hnkovr/CareerOS/issues/9) | provision Fly Postgres, first deploy, claim webhook | open — blocked by #1, #8 |

Linear project: <https://linear.app/my-1st/project/careeros-2039a962e2cf>
Tracker binding recorded in `~/.ai/skills/_settings/tracker_binding.yml#projects.CareerOS`.

## Done (with commit SHAs)

| Artifact | SHA |
|---|---|
| `fly.toml` — single machine, `auto_stop_machines="off"`, release_command migrations | `dffd335` |
| `.claude/settings.json` — SessionStart hook wiring | `dffd335` |
| `docs/adr/012-telegram-bot-surface.md` | `adc0cd1` |
| `docs/developer-guide/telegram-bot.md` | `adc0cd1` |
| `scripts/hooks/bot-guard.sh` — webhook-ownership session guard | `adc0cd1` |
| `scripts/prj-tools/tg-bot.sh` — `info\|set\|delete\|check` | `fd0b588` |
| `tests/deploy/` — 49 tests, all green | `fd0b588` |
| `config/deploy.yml` — env_push allow-list + credential excludes | `d548f4d` |

Outside the repo (not under version control here):
- `~/.ai/skills/_settings/careeros.yml` — ops SSoT (handle, app, region, url, var names, smoke checks)
- `~/.ai/templates/patterns/fly.toml` — shared pattern that did not exist before
- `~/.ai/agents/fly-ops.md` — CareerOS wired in; **no new agent was created**
- `~/.ai/skills/_catalog/projects/careeros/careeros-bot/` — the `/careeros-bot` skill
- `~/.ai/config/locations.yml#projects.careeros`

Bot identity: **@careeros_hnkovr_bot** (`@careeros_bot` was already taken).
`CAREEROS_TG_BOT_TOKEN` and `CAREEROS_TG_WEBHOOK_SECRET` are in `~/.ai/.env.secrets` (0600), token verified via `getMe`.

## Unresolved, and why

1. **`modules/bot` does not exist.** Only its spec does. This is why nothing is deployed:
   claiming the webhook for a URL that 404s every delivery is worse than not deploying.
   Do #1 first, then #8, then #9.
2. **`CAREEROS_TG_OWNER_CHAT_ID` is unset — blocked on the owner.** Send any message to
   @careeros_hnkovr_bot while the webhook is unset, then read `getUpdates` and store it.
   A 10-minute watcher already timed out once waiting for this.
3. **Fly Postgres not provisioned.** `fly mpg create` + `fly mpg attach --variable-name
   CAREEROS_DATABASE_URL`. Needs #8 first or the app cannot open the connection.
4. **Vault persistence on Fly is unsolved beyond P0.** `fly.toml` has no volume on purpose
   (P0 bot is read-only). Anything written to disk there is ephemeral.
5. **Commit-message loss.** Three of this lane's commits were absorbed by the parallel
   session's commits during an index race, so their rationale is not in git history — it
   lives in the ADR, guide and spec instead. Content verified intact; nothing to redo.

## Decisions already accepted — do not re-litigate

- **Scope**: career surface (capture + triage) *plus* owner-gated admin commands.
- **Host**: Fly.io, webhook transport. Not Hetzner/Render/Railway.
- **Library**: aiogram 3 (Pydantic-based, matches the repo and invariant 8).
- **Postgres**: Fly Managed Postgres via `fly mpg attach`. pgvector is unused today, verified.
- **No Redis**: `CAREEROS_TASK_RUNNER=inline`. Revisit at P1 Gmail sync.
- **Single machine, `--ha=false`**: two machines are two webhook claimants.
- **`auto_stop_machines = "off"`**: handlers keep working after the 200; an auto-stop would
  kill one mid-flight. Pinned by `tests/deploy/test_deploy_config.py`.
- **ACK-then-background + `update_id` dedup**: Telegram retries anything unacknowledged
  within ~60s.
- **Webhook claim only when unset or already ours**; never taken from a live owner.
- **Vault read-only in P0.** Writing facts from chat needs `Vault.apply_change()` + an
  approval gate — deliberately deferred.
- **No new agent.** CareerOS is wired into the existing `fly-ops`.
- **ADR numbering**: 012 = telegram bot, 013 = platform connectors, **011 is a permanent
  gap** — agreed with the parallel session after a renumbering race. Do not renumber.
- **Platform credentials are excluded from `env_push`** while platform sync is local-only
  (`CAREEROS_HH_*`, `CAREEROS_UPWORK_*`, `CAREEROS_PLATFORM_*`). Inverting this requires a
  volume + public OAuth redirect base together — see #21.

## Commands

```bash
just bot-token-check      # token valid, right bot, secret present
just bot-webhook-info     # ask Telegram who owns the webhook (the authority)
just bot-webhook-set      # claim; refuses a foreign owner without -- --force
just deploy-dry           # print every deploy command, run none
just deploy-fly           # deploy, then claim the webhook
uv run pytest tests/deploy -q   # 49 tests
```


## UPDATE — bot core landed; command batch designed, not built

**Implemented and pushed** (73 bot tests + 49 deploy tests, ruff/pyright/5 import contracts clean):

| # | What | Commit |
|---|---|---|
| [#8](https://github.com/hnkovr/CareerOS/issues/8) | `normalize_database_url` — `postgres://` → `postgresql+asyncpg://` | `8f0fadc` |
| [#1](https://github.com/hnkovr/CareerOS/issues/1) | webhook route + three gates, ACK-then-background | `f26d4e3` |
| [#2](https://github.com/hnkovr/CareerOS/issues/2) | ownership claim (never takes a live owner's webhook) | `f26d4e3` |
| [#3](https://github.com/hnkovr/CareerOS/issues/3) | capture — forwarded JD → scored triage card | `57abb26` |
| partial [#6](https://github.com/hnkovr/CareerOS/issues/6) | `/status` `/whoami` `/help` | `f26d4e3` |

`careeros bot webhook-info|webhook-set|webhook-delete|check` verified live against
@careeros_hnkovr_bot. A real triage card was delivered to the owner chat end to end.

**`CAREEROS_TG_OWNER_CHAT_ID` is now SET** (40937921, @NikolayKrupiy) — captured from a
pending `getUpdates`. That blocker is closed.

### Command batch — decisions made 2026-08-26, code NOT written

Issues [#25](https://github.com/hnkovr/CareerOS/issues/25)–[#30](https://github.com/hnkovr/CareerOS/issues/30)
carry the full spec each. The four decisions behind them:

1. **"Open" means send a tappable link.** A bot cannot open anything on a device. `/open`
   and `/profiles` reply with URLs; each platform's app handles the deep link.
2. **A saved default platform set** (`/services set hh,upwork,…`), stored in **Postgres,
   not the vault** — a search preference is operational state, not a canonical career fact
   (invariant 1, ADR 002). Chosen over the stateless "all configured, override inline".
3. **"meta" = the core/master CV** that channel variants project from; "in ..." = a specific
   variant. Not metadata fields.
4. **Compact before building** — chosen deliberately; the tree was clean.

Command surface: `/open <service>` · `/profiles [services]` · `/urls "<query>" [services]` ·
`/queries` · `/services [set …]` · `/cv update [in <variant>]` · `/cv improve [in <variant>]`.
The original request ended with "..." — more commands are expected; the list is not closed.

### Project B — web / Telegram mini-app ([#31](https://github.com/hnkovr/CareerOS/issues/31))

Deliberately NOT designed. Architectural, needs its own brainstorm → spec → plan cycle.
Key thing already known: a mini-app authenticates by verifying Telegram's `initData`
signature, which is a **different trust model** from the bot's secret-token + owner-chat
gates — it cannot reuse them. Blocked on #25–#30.

### Open question carried forward

**aiogram.** It was the accepted library (ADR 012), but nothing has needed it — the thin
httpx client covers webhook, gates, claim, capture and inline keyboards. I added the
dependency, found nothing imported it, and removed it rather than ship an unused one.
Keep the thin client, or adopt aiogram for the callback work in #4? Reversing an accepted
decision needs the owner's call.

### Also filed

[#24](https://github.com/hnkovr/CareerOS/issues/24) — opportunities dedup test fails only in
a full run (shared `careeros_test` contamination). Verified NOT caused by this lane: it fails
identically without these changes.


---

## UPDATE 2 — команды #25/#26/#27 сделаны

Гейт зелёный целиком: ruff по всему проекту, pyright 0 errors, 5/5 контрактов
импортов, `alembic check` — no drift, тесты бота/core/deploy проходят.

| # | Что | Коммит |
|---|---|---|
| [#25](https://github.com/hnkovr/CareerOS/issues/25) | `/services` — сохранённый набор площадок (Postgres, UNIQUE на user_id) | `af574c9`, `c173512`, `b2e7a38` |
| [#26](https://github.com/hnkovr/CareerOS/issues/26) | `/open`, `/profiles` — ссылки по площадкам | `864693c` |
| [#27](https://github.com/hnkovr/CareerOS/issues/27) | `/urls "<query>" [services]` | `864693c` |

Миграция `b1c7d0e9a4f2` (bot_preference) применена к реальному Postgres, дрейфа нет.

### Два бага, найденных ПОСЛЕ моего гейта — причина в узости гейта

1. **pyright**: я гонял его по `src/careeros/modules/bot`, а не проектный
   `just typecheck`, и тестовые файлы не попадали в область. Деталь на будущее:
   ошибка вылезла в одном файле только потому, что остальные тесты строят
   `Settings` через dict-splat, который pyright проверить не может — тот же
   неверный тип там есть, но невидим.
2. **alembic**: рукописная миграция создала `ix_bot_preference_user_id` как UNIQUE,
   а модель наследовала `user_id` от `OwnedMixin` с обычным `index=True`. Будущий
   autogenerate снёс бы уникальность — единственное, что не даёт двум гонящимся
   `/services set` оставить две строки. Ловится ровно `alembic check`, которого я
   не мог запустить, пока Docker лежал.

**Вывод для следующих слайсов: гейт = `just lint` + `just typecheck` + `alembic check`
целиком, а не по подкаталогу.**

### Решение, изменённое по ходу

Вывод со ссылками уходит **простым текстом, без `parse_mode`**. MarkdownV2 требует
экранировать `.`, `-`, `_` — то есть всё, из чего состоит URL; рендерится верно, но
сырое сообщение нечитаемо, а один пропущенный символ даёт 400 с байтовым смещением.
Telegram сам линкует голые URL. Три теста упали именно на этом.

`/urls` требует запрос **в кавычках**: в `/urls senior data engineer hh` нет
однозначной границы между запросом и списком площадок.

### Контракт с platform-лейном (получен, стабилен, коммит `ccbe87f`)

- `BaseConnector.search_url(JobQuery) -> str | None`
- `BaseConnector.profile_url(handle) -> str | None`
- `PlatformService.own_profile_url(platform) -> str | None`
- `GET /api/platform/{platform}/urls?q=&location=&remote=`

`None` = «не знаем» → бот обязан сказать об этом строкой, а не пропустить площадку.

### Осталось

- [#28](https://github.com/hnkovr/CareerOS/issues/28) `/queries` — read-only, из vault
- [#29](https://github.com/hnkovr/CareerOS/issues/29) `/cv update` — core CV и варианты
- [#30](https://github.com/hnkovr/CareerOS/issues/30) `/cv improve` — Suggestion, не запись в vault
- [#4](https://github.com/hnkovr/CareerOS/issues/4) callback-кнопки — **упирается в вопрос про aiogram**
- [#9](https://github.com/hnkovr/CareerOS/issues/9) первый деплой на Fly
- [#31](https://github.com/hnkovr/CareerOS/issues/31) mini-app — нужен отдельный спек (подпись `initData`)

### Открытый вопрос — решает владелец

**aiogram**. ADR 012 фиксирует aiogram 3, но он не понадобился ни разу: тонкий
httpx-клиент закрыл вебхук, три гейта, claim, capture, клавиатуры, `/services`,
`/open`, `/profiles`, `/urls`. Моя рекомендация — оставить тонкий клиент и на `#4`
дописать типизированный роутер по `callback_data`; aiogram решает задачу владения
event loop, которой у нас нет. Но это отмена принятого решения, и молча я её не делаю.

---

## UPDATE 3 — бот-лейн закрыт до деплоя (2026-08-26, вечер)

**Resume:** `claude --resume c082649a-9918-4009-a587-2872a54c6c6b`

### Сделано (с SHA)

| Слайс | SHA | Что |
|---|---|---|
| [#28](https://github.com/hnkovr/CareerOS/issues/28) `/queries` · [#29](https://github.com/hnkovr/CareerOS/issues/29) `/cv update` · [#30](https://github.com/hnkovr/CareerOS/issues/30) `/cv improve` | `35898ba` | + `bot/{queries,cv}.py`, `CVService.improve`, `client.send_document`, `chunk_message`, 6-й контракт import-linter |
| статус/логи | `236a86a` | секция бот-лейна в `CLAUDE-curr-status.md`, строки в оба PROMPTS-LOG |
| [#4](https://github.com/hnkovr/CareerOS/issues/4) триаж + callback'и | `1a99cf9` | + `bot/callbacks.py`, `client.answer_callback_query`, `/next` `/top` `/opp`, `formatting.{ranked_list,analysis_card,short_id}` |

Гейт на момент `1a99cf9`: ruff · pyright 0 · **6/6 контрактов** · `alembic check` чисто · весь набор
зелёный против **реального Postgres** — 220 bot + 49 deploy тестов.

GH закрыты: 1, 2, 4, 25, 27, 28, 29, 30. Linear: MY-38 → Done; MY-39…MY-42 заведены на остаток.

### Решения, которые НЕ переспрашивать

1. **aiogram не берём** — остаётся тонкий httpx-клиент. Записано в
   `docs/superpowers/specs/2026-08-25-careeros-telegram-bot-design.md` → *Decision reversals*.
   Пересмотреть только если появится многошаговый диалог (там FSM перестаёт быть церемонией).
2. **ADR 012 библиотеку никогда не называл** — прежние заметки «ADR-012 принял aiogram» были
   ошибкой атрибуции, исправлены. Выбор библиотеки жил в спеке, там и разворот.
3. **`meta` = core-вариант CV** из `meta.default_cv_variant`; синонимы `core`/`master`/`main`/`default`.
4. **`/cv improve` генерирует базовую линию заново**, а не берёт последний артефакт: иначе сравнение
   каждый раз отвечает на другой вопрос, а при AI-артефакте показывает дрейф AI-против-AI.
5. **Ссылки и листинги уходят plain text** без `parse_mode` — экранирование ломает URL и `/opp_<id>`.
6. **Callback отвечается ДО работы**; неизвестный payload отклоняется, а не игнорируется.
7. **Skip → `ignored`, не удаление** — dedup-ключ должен продолжать совпадать.

### Не сделано, и почему

- [#5](https://github.com/hnkovr/CareerOS/issues/5) `/facts` `/profile` — просто не дошли руки;
  `/cv` из этого issue закрыт, в issue висит комментарий об этом.
- [#6](https://github.com/hnkovr/CareerOS/issues/6) ops-команды — частично (`/status` `/whoami` `/help`).
- [#7](https://github.com/hnkovr/CareerOS/issues/7) нотификации — нужен воркер-триггер, не трогал.
- [#9](https://github.com/hnkovr/CareerOS/issues/9) **первый деплой на Fly** — требует `fly mpg create`
  + `fly mpg attach --variable-name CAREEROS_DATABASE_URL`. Это единственное, что отделяет бота от
  живого состояния; всё остальное уже проверено локально.
- [#31](https://github.com/hnkovr/CareerOS/issues/31) mini-app — нужен свой цикл brainstorm→spec→plan:
  аутентификация по подписи `initData` — другая модель доверия, три гейта бота не переиспользуются.

### Уроки этого окна

- **Гейт гонять целиком и проектным ruff.** Системный `ruff` 0.13 не знает `ASYNC240`; проектный
  0.16 (через `uv run`) поймал блокирующее чтение файла в event loop. Правило: `just lint`, не своя команда.
- **Docker в середине прогона упал** — db-тесты тихо ушли в SKIP. Проверять, что Postgres реально жив,
  а не полагаться на «тесты прошли».
- **Дерево общее, лейнов минимум три.** Кроме platform (`careeros-d2`) появился assistants/tool-calling
  (ADR-**014**, `3e5add6`) и gate-лейн (`56a77cb`). Стейджить только свои пути, пинговать перед чужими.

### Состояние на момент снимка

Дерево чистое. Незапушенным висит `f35ad22` — **чужой** коммит (jobs/UJI лейн), не трогал.
