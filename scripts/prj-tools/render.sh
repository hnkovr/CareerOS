#!/usr/bin/env bash
# render.sh — CareerOS on Render: check | deploy [--dry-run] | logs | status | find-id.
#
# Why a project wrapper and not the generic deploy driver: the shared Render profile
# (~/.ai/templates/profiles/render.yml) assumes `render env set` and an id-less
# `render deploys create`. Render CLI v2 has no `env` verb at all, and deploying needs a
# service id the driver cannot substitute. Here, env vars are declared in render.yaml
# (secrets `sync: false`, entered once at Blueprint launch) and deploys target the id
# recorded in the settings SSoT.
#
# All scalars: ~/.ai/skills/_settings/careeros.yml (careeros.tg_bot.deploy.render.*).
# RENDER_API_KEY is resolved through find-secret.sh and never printed or put in argv.
#
# Usage: render.sh <check|deploy|logs|status|find-id> [--dry-run]
# Exit:  0 ok · 1 usage/config · 2 not launched yet (no service id) · 3 Render rejected/failed
set -euo pipefail

SETTINGS="$HOME/.ai/skills/_settings/careeros.yml"
FIND_SECRET="$HOME/.ai/skills/_scripts/secrets/find-secret.sh"
API="https://api.render.com/v1"

log() { printf '[render] %s\n' "$1" >&2; }
die() { printf '[render] ERROR: %s\n' "$1" >&2; exit "${2:-1}"; }

cmd="${1:-}"; shift || true
DRY=0; [[ "${1:-}" == --dry-run ]] && DRY=1

[[ -f "$SETTINGS" ]] || die "settings file missing: $SETTINGS"
command -v yq >/dev/null || die "yq is required"

# One yq process for every value; a missing key is a bug, never a silent default.
IFS=$'\t' read -r SERVICE SID WORKSPACE CONFIG URL BLUEPRINT_NEW <<<"$(yq -r '[
  .careeros.tg_bot.deploy.render.service,
  .careeros.tg_bot.deploy.render.service_id,
  .careeros.tg_bot.deploy.render.workspace,
  .careeros.tg_bot.deploy.render.config,
  .careeros.tg_bot.deploy.render.url,
  .careeros.tg_bot.deploy.render.blueprint_new] | @tsv' "$SETTINGS")"
for v in SERVICE WORKSPACE CONFIG URL BLUEPRINT_NEW; do
  [[ -n "${!v}" && "${!v}" != null ]] || die "careeros.tg_bot.deploy.render.$(printf %s "$v" | tr '[:upper:]' '[:lower:]') not set in $SETTINGS"
done
[[ "$SID" == null ]] && SID=""

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "run inside the CareerOS repo"

key() {
  [[ -x "$FIND_SECRET" ]] || die "secret resolver missing: $FIND_SECRET"
  local k; k=$("$FIND_SECRET" RENDER_API_KEY 2>/dev/null || true)
  [[ -n "$k" ]] || die "RENDER_API_KEY not found — mint one at https://dashboard.render.com/u/settings?add-api-key"
  printf '%s' "$k"
}
# curl with the key in a header read from a file descriptor — never in argv (visible in ps).
rest() { curl -sS -m 30 -H @<(printf 'Authorization: Bearer %s\nAccept: application/json\n' "$(key)") "$API$1"; }

need_sid() {
  [[ -n "$SID" ]] && return 0
  log "not launched yet: careeros.tg_bot.deploy.render.service_id is null"
  log "1. launch the Blueprint (connects the repo, prompts each sync:false secret): $BLUEPRINT_NEW"
  log "2. record the id:  scripts/prj-tools/render.sh find-id   → put it in $SETTINGS"
  log "3. read back the real URL (Render suffixes a taken subdomain) and update render.yaml + settings"
  exit 2
}

case "$cmd" in
  check)
    command -v render >/dev/null && log "✓ render CLI $(render --version 2>/dev/null | head -1)" || die "render CLI missing: brew install render"
    [[ -f "$ROOT/$CONFIG" ]] && log "✓ $CONFIG present" || die "$CONFIG missing"
    if RENDER_API_KEY="$(key)" render blueprints validate "$ROOT/$CONFIG" --workspace "$WORKSPACE" \
         -o json --confirm 2>/dev/null | yq -e '.valid == true' >/dev/null 2>&1; then
      log "✓ $CONFIG validates against workspace $WORKSPACE"
    else
      die "$CONFIG does not validate — run: render blueprints validate $CONFIG --workspace $WORKSPACE" 3
    fi
    [[ -n "$SID" ]] && log "✓ service id recorded: $SID" || log "· not launched yet (no service id) — deploy will say how"
    ;;
  find-id)
    rest "/services?name=${SERVICE}&limit=20" | yq -p=json -r ".[] | select(.service.name == \"$SERVICE\") | .service.id" \
      | head -1 | { read -r id || true; [[ -n "${id:-}" ]] || die "no service named '$SERVICE' — launch the Blueprint first: $BLUEPRINT_NEW" 2; echo "$id"; }
    ;;
  deploy)
    need_sid
    if (( DRY )); then
      log "[dry] render deploys create $SID --wait --confirm -o text"
      log "[dry] then: just bot-webhook-set   (claims ${URL} unless a foreign owner holds it)"
      exit 0
    fi
    RENDER_API_KEY="$(key)" render deploys create "$SID" --wait --confirm -o text || die "deploy failed — just render-logs" 3
    ;;
  logs)
    need_sid
    RENDER_API_KEY="$(key)" render logs --resources "$SID" --limit 50 -o text --confirm
    ;;
  status)
    need_sid
    rest "/services/$SID" | yq -p=json -r '"service  " + .name + " · suspended=" + .suspended + " · " + (.serviceDetails.url // "")'
    rest "/services/$SID/deploys?limit=1" | yq -p=json -r '.[0].deploy | "deploy   " + .status + " · " + (.commit.id // "")[0:8] + " · " + .finishedAt'
    ;;
  *) die "usage: render.sh <check|deploy|logs|status|find-id> [--dry-run]" ;;
esac
