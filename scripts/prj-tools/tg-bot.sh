#!/usr/bin/env bash
# tg-bot.sh — CareerOS Telegram webhook ops: info | set | delete | check.
#
# Why a script and not raw curl: a webhook is EXCLUSIVE. Setting it blindly takes
# updates away from whoever holds it, so `set` refuses a foreign owner unless
# --force is passed. That is the same rule the running app applies at startup.
#
# All scalars come from ~/.ai/skills/_settings/careeros.yml (SSoT). The token is
# resolved through find-secret.sh and is never printed, logged, or passed in argv.
#
# Usage: tg-bot.sh <info|set|delete|check> [--force]
# Exit:  0 ok · 1 usage/config · 2 telegram rejected · 3 refused (foreign owner)
set -euo pipefail

SETTINGS="$HOME/.ai/skills/_settings/careeros.yml"
FIND_SECRET="$HOME/.ai/skills/_scripts/secrets/find-secret.sh"

log()  { printf '[tg-bot] %s\n' "$1" >&2; }
die()  { printf '[tg-bot] ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }

[[ -f "$SETTINGS" ]]    || die "settings file missing: $SETTINGS"
[[ -x "$FIND_SECRET" ]] || die "secret resolver missing: $FIND_SECRET"
command -v yq   >/dev/null || die "yq is required"
command -v curl >/dev/null || die "curl is required"

# Fail-loud settings read: a missing key is a bug, never a silent default.
setting() {
  local path="$1" val
  val=$(yq -r "$path" "$SETTINGS")
  [[ -n "$val" && "$val" != "null" ]] || die "${path#.} not set in $SETTINGS"
  printf '%s' "$val"
}

API_BASE=$(setting '.careeros.api.telegram_bot_api')
TOKEN_VAR=$(setting '.careeros.tg_bot.deploy.token_secret')
SECRET_VAR=$(setting '.careeros.tg_bot.deploy.webhook_secret')
WEBHOOK_PATH=$(setting '.careeros.tg_bot.deploy.webhook_path')
PUBLIC_URL=$(setting '.careeros.tg_bot.deploy.fly.url')
EXPECT_HANDLE=$(setting '.careeros.tg_bot.handle')

TARGET_URL="${PUBLIC_URL}${WEBHOOK_PATH}"

# Resolve a secret without ever echoing it.
resolve() { "$FIND_SECRET" "$1" 2>/dev/null || true; }

TOKEN=$(resolve "$TOKEN_VAR")
[[ -n "$TOKEN" ]] || die "$TOKEN_VAR not found. Mint it: \
$HOME/.ai/skills/_scripts/integrations/telegram/ensure-tg-bot.sh --var $TOKEN_VAR --bot $EXPECT_HANDLE"

# getMe is the only honest test of a token: it proves the token lives AND names its
# owner. A token for the WRONG bot is the expensive failure, so a mismatch is fatal.
api() {
  local method="$1"; shift
  curl -sS -m 20 "${API_BASE}/bot${TOKEN}/${method}" "$@"
}

assert_right_bot() {
  local resp username
  resp=$(api getMe) || die "cannot reach Telegram" 2
  [[ $(jq -r '.ok' <<<"$resp") == "true" ]] || die "Telegram rejected $TOKEN_VAR: $(jq -r '.description' <<<"$resp")" 2
  username="@$(jq -r '.result.username' <<<"$resp")"
  [[ "$username" == "$EXPECT_HANDLE" ]] \
    || die "$TOKEN_VAR belongs to $username, expected $EXPECT_HANDLE — refusing to act" 2
  printf '%s' "$username"
}

current_webhook_url() {
  local resp; resp=$(api getWebhookInfo) || die "cannot reach Telegram" 2
  jq -r '.result.url // ""' <<<"$resp"
}

cmd_info() {
  local who resp; who=$(assert_right_bot)
  resp=$(api getWebhookInfo)
  log "bot:            $who"
  log "webhook url:    $(jq -r '.result.url // "(unset)"'            <<<"$resp")"
  log "pending:        $(jq -r '.result.pending_update_count // 0'   <<<"$resp")"
  log "last error:     $(jq -r '.result.last_error_message // "none"'<<<"$resp")"
  log "custom cert:    $(jq -r '.result.has_custom_certificate'      <<<"$resp")"
  log "expected url:   $TARGET_URL"
}

cmd_set() {
  local force="$1" who current secret
  who=$(assert_right_bot)
  current=$(current_webhook_url)
  # Claim only when unset or already ours. Stealing from a live owner is how a
  # restart of a demoted host silently undoes a failover.
  if [[ -n "$current" && "$current" != "$TARGET_URL" && "$force" != "yes" ]]; then
    die "webhook is owned by $current (we are $TARGET_URL). Re-run with --force to take it." 3
  fi
  secret=$(resolve "$SECRET_VAR")
  [[ -n "$secret" ]] || die "$SECRET_VAR not found — generate one: openssl rand -hex 32"
  local resp
  resp=$(api setWebhook \
      --data-urlencode "url=${TARGET_URL}" \
      --data-urlencode "secret_token=${secret}" \
      --data-urlencode "drop_pending_updates=false")
  [[ $(jq -r '.ok' <<<"$resp") == "true" ]] || die "setWebhook failed: $(jq -r '.description' <<<"$resp")" 2
  log "$who -> $TARGET_URL (secret token set)"
}

cmd_delete() {
  local who resp; who=$(assert_right_bot)
  resp=$(api deleteWebhook --data-urlencode "drop_pending_updates=false")
  [[ $(jq -r '.ok' <<<"$resp") == "true" ]] || die "deleteWebhook failed: $(jq -r '.description' <<<"$resp")" 2
  log "$who webhook removed (pending updates kept)"
}

cmd_check() {
  local who current; who=$(assert_right_bot)
  log "token:   ok ($who)"
  current=$(current_webhook_url)
  if   [[ -z "$current" ]];                then log "webhook: off (unset)";
  elif [[ "$current" == "$TARGET_URL" ]];  then log "webhook: owns ($current)";
  else                                          log "webhook: standby — owned by $current"; fi
  [[ -n "$(resolve "$SECRET_VAR")" ]] && log "secret:  present" || log "secret:  MISSING ($SECRET_VAR)"
}

command -v jq >/dev/null || die "jq is required"

FORCE=no; ACTION="${1-}"; shift || true
for arg in "$@"; do [[ "$arg" == "--force" ]] && FORCE=yes; done

case "$ACTION" in
  info)   cmd_info ;;
  set)    cmd_set "$FORCE" ;;
  delete) cmd_delete ;;
  check)  cmd_check ;;
  *) die "usage: tg-bot.sh <info|set|delete|check> [--force]" ;;
esac
