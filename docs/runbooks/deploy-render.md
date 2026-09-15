# Runbook — Deploy CareerOS to Render (default target)

- **Trigger:** shipping the API + `@careeros_hnkovr_bot` to production. Render is the default
  (`careeros.yml#careeros.tg_bot.deploy.default_target`); Fly is standby and has never been
  deployed ([#9](https://github.com/hnkovr/CareerOS/issues/9)).
- **Owner:** repo owner. The first launch needs a human at the Render dashboard.
- **Config:** [`render.yaml`](../../render.yaml) · settings `~/.ai/skills/_settings/careeros.yml`
  (`tg_bot.deploy.render.*`) · wrapper [`scripts/prj-tools/render.sh`](../../scripts/prj-tools/render.sh).
  [`tests/deploy/test_deploy_config.py`](../../tests/deploy/test_deploy_config.py) holds all three in step.

## Decisions baked into `render.yaml` — and why

| Decision | Why |
| --- | --- |
| `autoDeployTrigger: "off"` — deploys only via `just deploy` | `main` is shared by several agent lanes committing half-finished slices, and every deploy can claim the Telegram webhook |
| exactly one instance, no autoscaling | two instances are two webhook claimants, and would race the start-up migrations |
| migrations in `dockerCommand`, not `preDeployCommand` | Render runs pre-deploy commands **only on paid instances** — on `free` it would silently never run |
| `CAREEROS_TG_ENABLED` / `CAREEROS_TG_PUBLIC_URL` set in the file | per-host eligibility; `config/deploy.yml` excludes both from any workstation push — a workstation renders `TG_ENABLED=false` |
| every secret `sync: false` | entered once at Blueprint launch; `render.yaml` is committed, values never are |
| database `ipAllowList: []` | private network only — the web service is the sole client |

## Plan: free vs paid — decide before real data lands

`render.yaml` declares **free** for both resources, which costs nothing and carries two limits:

- the **web service spins down after 15 minutes idle**; the next webhook delivery cold-starts it.
  Telegram retries unacknowledged updates and the app is idempotent on `update_id`, so updates are
  delayed, not lost — but the first reply after a quiet spell is slow.
- the **free Postgres is deleted after 30 days.** The vault holds the facts (invariant 1), but
  pipeline, inbox and application rows exist only in Postgres.

To go paid: set `plan: starter` on the service and a paid plan on the database, move the migration
into `preDeployCommand`, and drop `dockerCommand`. The test
`test_render_migrations_actually_run_on_the_declared_plan` checks whichever branch the file declares.

## First launch

```bash
just deploy-check                 # CLI · render.yaml validates against the workspace · id recorded?
just deploy-dry                   # not launched yet → prints these steps and exits 2
```

1. **Confirm the workspace.** The `RENDER_API_KEY` in the escrow sees exactly one workspace,
   *Ольга Крупий's Workspace* (`tea-cspuco9u0jms7384fff0`), which already runs `wordsman-tg-bot`,
   `stambul-tts` and `speech-synth`. Make sure that is where CareerOS belongs before launching.
2. **Launch the Blueprint** — open `careeros.yml#…render.blueprint_new`. Render connects the GitHub
   repo, creates `careeros` + `careeros-db`, and prompts for each `sync: false` secret. Type the
   values in the dashboard yourself; never paste them into a chat.
3. **Record the service id** — `just render-find-id`, then set `tg_bot.deploy.render.service_id`.
4. **Read back the real URL.** Render suffixes a taken subdomain (`speech-synth` became
   `speech-synth-9nqh.onrender.com`). If `careeros.onrender.com` was taken, update the URL in
   `render.yaml` (`CAREEROS_TG_PUBLIC_URL`), `render.url` **and** `public_url` in the settings — the
   tests fail until all three agree.
5. **Deploy** — `just deploy` (= `just deploy-render`): deploys the recorded id and then claims the
   webhook, refusing a foreign owner.

## Verify

```bash
just render-status                # service not suspended · latest deploy "live"
just bot-webhook-info             # our URL · pending=0 — ask Telegram, not the app
just render-logs                  # alembic upgrade ran, then uvicorn started
```

Then message the bot from the owner account.

## Known gaps

- **The host runs on the demo vault.** `CAREEROS_VAULT_GIT_URL` is declared but read by no code
  yet, so `/cv`, `/facts` and `/profile` answer from demo facts —
  [#50](https://github.com/hnkovr/CareerOS/issues/50).
- **The generic deploy driver cannot deploy to Render.** The shared profile
  `~/.ai/templates/profiles/render.yml` expects `render env set` (Render CLI v2 has no `env` verb)
  and an id-less `render deploys create`. That is why CareerOS goes through `render.sh`.

## Standby: Fly

`just deploy fly` still works through the generic driver ([`fly.toml`](../../fly.toml)). Never run both
as production: the webhook belongs to exactly one host, and `targets.*.role` in the settings allows
one `production` — asserted by `test_exactly_one_production_target_and_it_is_the_default`.
