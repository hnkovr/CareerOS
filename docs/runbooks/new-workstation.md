# Runbook — CareerOS on a new workstation

- **Trigger:** moving R&D to another machine (the MacBook M1). The repo must arrive whole —
  code, secrets, and the career vault — with nothing left behind on the old one.
- **Owner:** repo owner. **Severity:** planned migration, not an incident.
- **Scalars:** [`config/workstation.yml`](../../config/workstation.yml) — bootstrap order, secret
  slots, hazards. This page and that file are asserted equal by
  [`tests/test_workstation_config.py`](../../tests/test_workstation_config.py), so neither drifts
  quietly.

CareerOS is unusual in one way that decides this whole runbook: **the source of truth is not in
the database and not in this repo.** Canonical facts live in the private vault
(`career/private`), which is git-ignored on purpose. Postgres is rebuilt from migrations, the
demo vault ships in-tree — but the vault is the one thing a `git clone` cannot bring.

---

## 0. What migrates, what does not

| Arrives with `git clone`                                    | Recreated on the new machine — how                                   |
| ----------------------------------------------------------- | -------------------------------------------------------------------- |
| code, `config/*.template`, docs, tests, Makefile/Justfile     | `.venv`, `node_modules`, caches → `uv sync && npm install`            |
| `career/{schemas,templates,prompts}` + demo vault             | postgres/redis volumes → `make infra migrate seed` (demo rows)        |
| alembic migrations (the DB is rebuilt, never copied)          | `.env`, `config/.env.secrets` → secrets pull, then `make env`         |
| `.claude/{CLAUDE.md,settings.json,agents}` — guide + hooks    | **`career/private`** → clone `CAREEROS_VAULT_GIT_URL` (see §1b)       |
| `.ai/workstations/*.yml` — the other machines' state          | platform OAuth token file (0600) → `just platform-connect <platform>` |
| `.ai/WiP/HANDOFF-*.md` — lane handoffs                        | `.claude/settings.local.json` — per-machine permissions, re-granted   |
|                                                               | Claude/Codex transcripts — `--resume` only works on the owning host   |

---

## 1. On the OLD machine — settle before you leave

```bash
make preflight            # or: just preflight  — read-only verdict, exit 3 = blocked
```

It reports, and refuses to call the machine clean until each is cleared:

1. **uncommitted or unpushed work** — commit path-scoped and tracker-bound (`/smart-commit`).
   This tree is shared by parallel sessions, so never `git add -A`
   ([`.claude/CLAUDE.md`](../../.claude/CLAUDE.md)).
2. **stashes** — machine-local. Anything worth keeping becomes a commit.
3. **the vault** — see §1b.
4. **secrets escrow** — see §1c.
5. **handoff state** — `just workstation-state` records this host into `.ai/workstations/`.

### 1b. The vault is the migration

`career/private` is git-ignored, so nothing carries it automatically and the app **silently
falls back** to the bundled demo vault opened read-only. Before leaving:

```bash
git -C career/private status              # its own repo, with its own private remote
git -C career/private push
```

Record the remote in `CAREEROS_VAULT_GIT_URL` (a secret — it carries a token) so the new
machine can fetch it. If there is no private vault on this machine yet, there is nothing to
migrate and the demo vault is what the new machine will use.

### 1c. Secrets — escrowed, never copied

Values live in `~/.ai/.env.secrets` (mode 0600) and are escrowed to Bitwarden by the shared
`dmp-gateway` toolkit. `config/.env.secrets` in this repo is *rendered* from them, never the
original.

```bash
bw unlock                                                        # human at the keyboard
just -f ~/dmp-gateway/secrets-sync/Justfile push                 # this machine → Bitwarden
```

Every slot is named in [`config/workstation.yml`](../../config/workstation.yml)
(`workstation.secrets.slots`) and in
[`config/.env.secrets.demo.template`](../../config/.env.secrets.demo.template). Blank is a
legitimate state — an unfilled slot just leaves that feature off.

### 1d. Full handoff in one command

```bash
just workstation-gateway            # dry-run: sessions, clones, shared repos, secrets, state
just workstation-gateway --apply    # performs the safe parts; exit 3 lists the human gates
```

---

## 2. On the NEW machine — bootstrap

```bash
# 1 toolchain
brew install git just uv gh yq node docker && brew install --cask docker
gh auth login                       # account hnkovr — the repo is private

# 2 clone
gh repo clone hnkovr/CareerOS ~/gi/@hnkovr/CareerOS && cd ~/gi/@hnkovr/CareerOS

# 3 env + deps
make env
uv sync && npm install

# 4 infra
make infra                          # postgres (pgvector) + redis

# 5 secrets
bw unlock
just -f ~/dmp-gateway/secrets-sync/Justfile pull   # → ~/.ai/.env.secrets (0600)
make env                            # re-render .env now that the values exist

# 6 vault
git clone "$CAREEROS_VAULT_GIT_URL" career/private   # or: uv run careeros vault init

# 7 gate
make check                          # lint + full test suite
```

`uv` provisions Python 3.13 itself; `make env` renders `.env` from the templates, leaving every
unfilled optional **blank** — which CareerOS reads as *unset* on purpose
(`Settings._blank_means_unset`), because an `int | None` cannot parse `""`.

---

## 3. Hazards that only exist because there are now two machines

| Hazard | Why it bites here | Avoid |
| --- | --- | --- |
| **Telegram webhook theft** | A webhook is exclusive — one bot, one host. A second machine starting the bot with `CAREEROS_TG_PUBLIC_URL` set claims `@careeros_hnkovr_bot` away from production and silently swallows the owner's updates. | Leave `CAREEROS_TG_PUBLIC_URL` **unset** on the new machine (health reads `off`). Ask Telegram, not the app: `just bot-webhook-info`. |
| **Shared test database** | `db`-marked tests `drop_all/create_all` their database — two machines on one `careeros_test` truncate each other mid-run. | Give the new machine its own `CAREEROS_TEST_DATABASE_URL=…/careeros_test_m1`. |
| **Vault fallback** | Missing `career/private` does not error; it downgrades to the demo vault, read-only. | `make validate-career` — it must validate *your* vault, not the demo. |
| **Platform tokens** | OAuth tokens sit in a 0600 file outside git by design ([ADR-013](../adr/013-platform-connectors.md)); copying spreads a credential a re-auth mints for free. | `just platform-connect hh` / `upwork` on the new machine. |

---

## 4. Verify

```bash
make check                        # lint + tests green
make validate-career              # the real vault, not the demo
just platform-capabilities        # connectors load; connections are expected to be empty
just bot-webhook-info             # production still owns the webhook
just workstation-state            # record this host — the other machine reads it
make preflight                    # verdict: clean
```

A Claude Code session started here now prints the other machine's line at startup
(`⚙ [careeros-workstation] …`), served by
[`scripts/hooks/workstation-guard.sh`](../../scripts/hooks/workstation-guard.sh).

---

## Related

- [`config/workstation.yml`](../../config/workstation.yml) — the scalars behind this page
- [`docs/developer-guide/README.md`](../developer-guide/README.md) — day-to-day loop
- [`docs/developer-guide/telegram-bot.md`](../developer-guide/telegram-bot.md) — webhook ownership
- [`docs/platform/README.md`](../platform/README.md) — connector tokens
- Skills: `/careeros-workstation`, `/prepare-project-gateway-to-another-pc-workstation`, `/careeros-gate`
