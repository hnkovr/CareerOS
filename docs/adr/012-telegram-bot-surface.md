# 012 — Telegram bot as the P0 mobile surface: webhook, owner-gated, read-only vault

* Status: accepted
* Date: 2026-08-25

## Context

The brief scopes a mobile client for triage, inbox, approve/reject, AI summaries, notifications and
share-to-app (§ mobile), but a native Expo app is P2 work. Meanwhile the highest-value moments in
this product are mobile ones: a job description arrives in a chat, and the useful response is to
capture, score and triage it in seconds — not to open a laptop.

Telegram already carries those messages. A bot gives us capture, triage and push notifications on a
phone without building or shipping an app, and without the App Store in the loop.

Two properties of the Bot API shape everything below. A **webhook is exclusive**: registering one
takes updates away from whoever held it. And Telegram **retries any update not acknowledged within
~60s**, so a slow handler produces duplicate deliveries rather than a delay.

## Decision

1. **Telegram is a projection, not a source of truth.** `modules/bot` renders what `opportunities`,
   `cv`, `vault` and `profiles` already compute; it adds no domain logic and owns no facts. It calls
   other modules only through their `service.py`.
2. **Webhook, not long polling.** The bot is deployed on Fly, where machines stop when idle. A
   webhook delivery wakes a stopped machine in ~9s, so nothing must stay awake; a poller would have
   to. Consequence: `--ha=false` and a single machine, because two machines are two claimants.
3. **Three gates on every update, in order**: the shared secret in
   `X-Telegram-Bot-Api-Secret-Token` (mismatch → `403`, body never parsed); the owner chat id
   (anything else → `200` with no side effect, never `403`, so a stranger cannot distinguish a live
   bot from a dead one); and an `update_id` idempotency check.
4. **Acknowledge first, then work.** The handler returns `200` immediately and continues in the
   background, because AI parsing can exceed Telegram's retry window. The `update_id` guard is what
   makes the resulting retries harmless. This also forces `auto_stop_machines = "off"` on Fly — an
   auto-stop would kill a handler after it had already acknowledged the update.
5. **A cold start claims the webhook only when it is unset or already its own.** Never from a
   different live URL. Without this, restarting a demoted host silently undoes a failover.
   `CAREEROS_TG_PUBLIC_URL` is the eligibility key: unset means the process never contacts Telegram,
   so a local dev run cannot steal production's updates. Deliberate takeover is explicit (`--force`).
6. **One bot per deployment target.** Separate tokens cannot contend for a single webhook URL, so a
   future staging deploy can never take production's updates.
7. **The vault is read-only in P0.** The bot may search and render facts; it may not write them.
   Writing a fact from chat would need `Vault.apply_change()` plus an approval gate (ADR 010) and is
   deliberately deferred.
8. **No external writes.** The bot performs no platform action; buttons mutate internal state only
   (ADR 005). Notifications are outbound-to-owner and deduped per opportunity.

## Alternatives considered

* **Expo/React Native app now** — the right long-term surface and already planned for P2, but weeks
  of work plus store review for a single user, when the phone already has a client we can target.
* **Long polling instead of webhook** — simpler locally and needs no public URL, but it is a
  `getUpdates` singleton that must stay awake, which defeats scale-to-zero and costs money to keep
  honest. It also fails outright on Fly's trial, where machines stop 5 minutes after each wake.
* **A standalone bot service calling the API over HTTP** — cleaner isolation, but it doubles the
  deployment, needs its own auth against our own API, and buys nothing while there is one user and
  one machine. Revisit if the bot ever needs to scale separately.
* **Vault writes from chat in P0** — attractive ("I shipped X today" → new achievement), but a
  write path without a review UI is exactly where invented facts would enter the canonical store.

## Consequences

* A phone-first capture and triage loop exists without a mobile app, and notifications get a real
  delivery channel for the first time.
* The single-machine constraint is now load-bearing: scaling this app out requires either moving the
  bot to its own service or electing a webhook owner. `--ha=false` and
  `auto_stop_machines = "off"` are tested invariants (`tests/deploy/`), not conventions.
* Telegram becomes a trust boundary. The secret token, the owner gate and the idempotency guard are
  the only things between the public internet and the ingest path; all three are unit-tested.
* The webhook is global per bot token, so local development against the real bot requires releasing
  the webhook first (`just bot-webhook-delete`) or using a second bot.
* Operational state ("who owns the webhook") lives at Telegram, not in our database. Diagnosis must
  ask Telegram (`just bot-webhook-info`); the app's own `/status` knows only its startup claim.
