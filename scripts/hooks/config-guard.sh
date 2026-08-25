#!/usr/bin/env bash
# config-guard.sh — SessionStart guard: does the rendered .env still build Settings?
#
# The failure this catches is disproportionate to how quiet it is. Every optional value in
# config/.env.*.template renders blank until the owner fills it in, so a new setting whose
# type cannot parse "" (an `int | None`, say) makes Settings() raise — and then EVERYTHING
# that imports it dies: the API, the CLI, the worker, `make all`. The traceback names
# pydantic, not the template, so the cost lands on whoever runs the pipeline next.
#
# A guard must never break or slow a session: every failure path prints a diagnostic and
# exits 0, and the answer is cached because importing the app costs ~1s.
set -uo pipefail

CACHE_TTL=900
emit() { printf '⚙ [careeros-config] %s\n' "$1"; }

# Only speak inside the CareerOS repo.
ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[[ -f "$ROOT/services/careeros/src/careeros/core/config.py" ]] || exit 0
cd "$ROOT" || exit 0

CACHE="${TMPDIR:-/tmp}/careeros-config-guard.$(id -u)"
# .env is the input; re-check whenever it (or the settings module) is newer than the answer
if [[ -f "$CACHE" ]] \
  && [[ -z $(find .env services/careeros/src/careeros/core/config.py -newer "$CACHE" 2>/dev/null) ]] \
  && (( $(date +%s) - $(stat -f %m "$CACHE" 2>/dev/null || echo 0) < CACHE_TTL )); then
  [[ -s "$CACHE" ]] && emit "$(cat "$CACHE")"
  exit 0
fi

command -v uv >/dev/null 2>&1 || exit 0
[[ -f .env ]] || { emit "no .env yet — run: make env"; exit 0; }

# Settings reads the process environment (env_file=None); `just` feeds it .env via
# dotenv-load, so the guard has to do the same or it would test an empty environment.
if err=$(
  set -a; . ./.env; set +a
  uv run --quiet python -c 'from careeros.core.config import get_settings; get_settings()' 2>&1
); then
  : > "$CACHE"
else
  # Surface the field pydantic named, not the whole traceback.
  field=$(printf '%s\n' "$err" | grep -A1 "validation error" | tail -1 | tr -d ' ')
  msg="Settings() does not build from .env${field:+ — field: $field}; run: make env, then check config/.env.config.template"
  printf '%s' "$msg" > "$CACHE"
  emit "$msg"
fi
exit 0
