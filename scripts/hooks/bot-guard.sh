#!/usr/bin/env bash
# bot-guard.sh — SessionStart guard for the CareerOS Telegram bot.
#
# Answers one question in one line: does the deployed bot actually own its webhook?
# The failure this catches is silent — a bot whose webhook was taken (or never set)
# looks perfectly healthy from the app's own /status, because that only reports the
# claim made at ITS startup. Telegram is the authority, so we ask Telegram.
#
# A guard must never break or slow a session: every failure path prints a
# diagnostic and exits 0, and the Telegram call is cached (TTL from settings).
set -uo pipefail

SETTINGS="$HOME/.ai/skills/_settings/careeros.yml"
FIND_SECRET="$HOME/.ai/skills/_scripts/secrets/find-secret.sh"
CACHE_DIR="${TMPDIR:-/tmp}/careeros-bot-guard"
CACHE_TTL=600            # seconds; a session-start guard must not hammer the Bot API

emit() { printf '🤖 [careeros-bot] %s\n' "$1"; }

# Only speak inside the CareerOS repo.
[[ -f "$SETTINGS" ]] || exit 0
git rev-parse --show-toplevel >/dev/null 2>&1 || exit 0
[[ -f "$(git rev-parse --show-toplevel 2>/dev/null)/fly.toml" ]] || exit 0

command -v yq >/dev/null 2>&1 || { emit "yq missing — cannot read $SETTINGS"; exit 0; }
command -v jq >/dev/null 2>&1 || { emit "jq missing — skipping webhook check"; exit 0; }

# One yq process, not five: this runs on every session start, so interpreter
# startup is the dominant cost.
raw=$(yq -r '[.careeros.api.telegram_bot_api, .careeros.tg_bot.deploy.token_secret,
              .careeros.tg_bot.deploy.fly.url, .careeros.tg_bot.deploy.webhook_path,
              .careeros.tg_bot.handle] | @tsv' "$SETTINGS" 2>/dev/null)
IFS=$'\t' read -r API_BASE TOKEN_VAR PUBLIC_URL WEBHOOK_PATH HANDLE <<<"$raw"

for pair in "API_BASE:$API_BASE" "TOKEN_VAR:$TOKEN_VAR" "PUBLIC_URL:$PUBLIC_URL" "WEBHOOK_PATH:$WEBHOOK_PATH"; do
  v="${pair#*:}"
  [[ -n "$v" && "$v" != "null" ]] || { emit "settings incomplete (${pair%%:*}) in $SETTINGS"; exit 0; }
done
TARGET="${PUBLIC_URL}${WEBHOOK_PATH}"

[[ -x "$FIND_SECRET" ]] || { emit "secret resolver missing — cannot check token"; exit 0; }
TOKEN=$("$FIND_SECRET" "$TOKEN_VAR" 2>/dev/null)
if [[ -z "$TOKEN" ]]; then
  emit "$TOKEN_VAR absent → mint: ensure-tg-bot.sh --var $TOKEN_VAR --bot $HANDLE"
  exit 0
fi

mkdir -p "$CACHE_DIR" 2>/dev/null
CACHE="$CACHE_DIR/webhook.json"
fresh=no
if [[ -f "$CACHE" ]]; then
  now=$(date +%s)
  mtime=$(stat -f %m "$CACHE" 2>/dev/null || stat -c %Y "$CACHE" 2>/dev/null || echo 0)
  (( now - mtime < CACHE_TTL )) && fresh=yes
fi

if [[ "$fresh" == "no" ]]; then
  # Short timeout: a slow network must not delay the session.
  curl -sS -m 6 "${API_BASE}/bot${TOKEN}/getWebhookInfo" -o "$CACHE" 2>/dev/null \
    || { emit "Telegram unreachable — webhook state unknown"; exit 0; }
  chmod 600 "$CACHE" 2>/dev/null
fi

ok=$(jq -r '.ok // false' "$CACHE" 2>/dev/null)
[[ "$ok" == "true" ]] || { emit "$TOKEN_VAR rejected by Telegram — re-mint it"; exit 0; }

url=$(jq -r '.result.url // ""'                  "$CACHE")
pending=$(jq -r '.result.pending_update_count//0' "$CACHE")
err=$(jq -r '.result.last_error_message // ""'    "$CACHE")

if   [[ -z "$url" ]];            then state="off (webhook unset) → just bot-webhook-set"
elif [[ "$url" == "$TARGET" ]];  then state="owns $url"
else                                  state="standby — owned by $url (ours: $TARGET)"; fi

line="$HANDLE · $state · pending=$pending"
[[ -n "$err" ]] && line="$line · last_error: $err"
emit "$line"
exit 0
