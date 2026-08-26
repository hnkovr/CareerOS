# HANDOFF — `make all` / gate lane (2026-08-26)

Lane: build pipeline + gate hardening. Ran alongside the platform, bot and assistant lanes in
the **same working tree** — see “Shared tree” below before reading any failure as yours.

## Resume

```bash
cd ~/gi/@hnkovr/CareerOS && claude --resume 12e5c8cc-6b94-4e7c-8a8b-3938f2396835
```

`~/.ai/skills/_scripts/session/get-resume-cli.sh` returns `claude --resume 4036da63-f3fe-4ce5-b1a9-729abc03280b` — that is the
**first** session of this series (the `make all` refactor), not the latest. Both are valid; the
one above is the current one.

## Done (with commit SHAs)

| Commit | What |
|---|---|
| [`dcfb97e`](https://github.com/hnkovr/CareerOS/commit/dcfb97e) | ordered `make all` (12 steps), `make check`/`infra`/`build`/`distclean`, `.NOTPARALLEL`, `run` opens the web app not Finder, `clean` no longer deletes `generated/platform` (OAuth tokens); vault falls back to the bundled demo vault **read-only** (`VaultReadOnly` → HTTP 403) |
| [`fd0b588`](https://github.com/hnkovr/CareerOS/commit/fd0b588) | `scripts/contracts-check.sh` + `contracts` CI job (the old check diffed `packages/schemas` but only ever regenerated `career/schemas`); `scripts/prj-tools/tg-bot.sh` exit **4** = unreachable, split from 2 = rejected token, so `make all` survives being offline; CI builds `deploy/docker/Dockerfile.web` |
| [`1f4d739`](https://github.com/hnkovr/CareerOS/commit/1f4d739) | `config/gate.yml` (pipeline as data) + `tests/test_gate_config.py`; `scripts/hooks/config-guard.sh`; skill `/careeros-gate` + agent `careeros-gate` |
| [`56a77cb`](https://github.com/hnkovr/CareerOS/commit/56a77cb) | autouse truncate between db tests ([#24](https://github.com/hnkovr/CareerOS/issues/24)); `scripts/gate.sh` as the single gate for local **and** CI ([#23](https://github.com/hnkovr/CareerOS/issues/23)); `just infra-up` names the fix when Docker is down |

Closed: [#22](https://github.com/hnkovr/CareerOS/issues/22) (blank env var → `Settings()` refused
to build), [#23](https://github.com/hnkovr/CareerOS/issues/23), [#24](https://github.com/hnkovr/CareerOS/issues/24).

Last verified: `make all` → **exit 0**, 13/13 steps, 759 python + 5 web tests, contracts 6/6.

## Open

- [ ] [#21](https://github.com/hnkovr/CareerOS/issues/21) platform follow-ups ·
      [#20](https://github.com/hnkovr/CareerOS/issues/20) web Platforms page — not this lane
- [ ] `just build` (docker images) still unverified **locally**; CI builds both images since
      `fd0b588`, so consider dropping the local claim rather than running it on a full disk
- [ ] `services/careeros/tests/assistant/test_assistant.py:81` — one E501 left unreflowed
- [ ] `CareerOS_ClaudeCode_Master_Prompt_Universal_Job_Intelligence.md` is modified and
      uncommitted — **not this lane's**; left for its owner

## Not done, and why

- **CI does not call `just`.** `scripts/gate.sh` was chosen over adding
  `extractions/setup-just` because the action's current major version could not be verified
  offline. If `just` ever lands on the runner, the script stays the right seam anyway.
- **`make all` excludes `run`/`up`/`build`.** Deliberate: they start containers, build images and
  open a browser, so a verification pipeline that included them would never terminate cleanly.
  The user's original draft had `run` in `all`; this was changed on purpose, not overlooked.
- **The vault demo fallback is read-only rather than writable.** A writable fallback would let
  demo facts be committed as the owner's, breaking invariant 1.

## Decisions already agreed (do not re-litigate)

- Blank env var = **unset** for every optional field (`Settings._blank_means_unset`), because the
  templates render every unfilled optional blank and a non-empty literal there would be a leak.
- `bot-check` tolerates **exit 4 only**. Tolerating 2 (rejected token) would make the step decorative.
- Platform sweep: “not connected” / “needs a paste” is **SKIPPED**, never FAILED — a sweep on a
  fresh install must exit 0.
- Test isolation is fixed by **truncating rows**, not by loosening assertions and not by a wrapping
  transaction (API tests open their own sessions through the app).
- `feat/platform-connectors` was fully merged (0 unique commits) and **deleted** local + remote at
  the owner's instruction; recoverable at `cb5495e`. Only `main` remains.

## Shared tree

Other lanes commit into this same checkout and sweep up whatever is present, so several of this
lane's edits landed under **their** commits (`d9a1629`, `f26d4e3`, `0f0d8b8`). Content is intact;
attribution is not. Before reading a red step as yours: `git status --short && git log --oneline -3`.
This lane reflowed 9 over-long lines in the bot/assistant lanes' in-flight files — mechanical only.

Triage detail for every failure this pipeline has produced:
`~/.ai/skills/_catalog/projects/careeros/careeros-gate/references/triage.md` (skill `/careeros-gate`).
