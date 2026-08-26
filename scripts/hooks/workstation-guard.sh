#!/usr/bin/env bash
# workstation-guard.sh — SessionStart guard: what did the OTHER machines leave open here?
#
# Two laptops working one repo fail in a specific way: each starts from its own tree and
# neither can see that the other is three commits ahead, mid-migration, or holding a dirty
# vault. `git status` cannot show it — the fact lives on the other machine. So each machine
# records itself into .ai/workstations/<host>.yml, and this prints everyone else's line at
# session start, plus a nudge when this host's own record has gone stale.
#
# A guard must never break or slow a session: every failure path exits 0, quietly.
# Data: .ai/workstations/*.yml (written by `just workstation-state`). Scalars: config/workstation.yml.
set -uo pipefail

emit() { printf '⚙ [careeros-workstation] %s\n' "$1"; }

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[[ -f "$ROOT/config/workstation.yml" ]] || exit 0
cd "$ROOT" || exit 0
command -v yq >/dev/null 2>&1 || exit 0

STATE_DIR=$(yq -r '.workstation.state_dir' config/workstation.yml 2>/dev/null) || exit 0
[[ -n $STATE_DIR && $STATE_DIR != null ]] || exit 0
HOST=$(hostname -s)

others=0
for f in "$STATE_DIR"/*.yml; do
  [[ -f $f ]] || continue
  who=$(yq -r .workstation "$f" 2>/dev/null) || continue
  [[ $who == "$HOST" ]] && continue
  others=$((others + 1))
  emit "$(printf '%s · %s · %s +%s/-%s · dirty=%s · open sessions=%s' \
    "$who" "$(yq -r .updated "$f")" "$(yq -r .repo.branch "$f")" \
    "$(yq -r .repo.ahead "$f")" "$(yq -r .repo.behind "$f")" \
    "$(yq -r .repo.dirty "$f")" "$(yq -r '[.sessions[]? | select(.clean_end==false)] | length' "$f")")"
  # An unpushed branch on the other machine is the one thing this machine cannot recover.
  unpushed=$(yq -r '[.repo.unpushed_branches[]?] | length' "$f" 2>/dev/null || echo 0)
  (( unpushed > 0 )) && emit "  ↑ $who holds $unpushed unpushed branch(es) — do not duplicate that work"
done

# Silence is ambiguous — say which of the two reasons it is.
mine="$STATE_DIR/$HOST.yml"
if [[ ! -f $mine ]]; then
  emit "this host ($HOST) is not recorded yet → just workstation-state"
elif [[ -n $(find "$mine" -mtime +1 2>/dev/null) ]]; then
  emit "$HOST's record is over a day old → just workstation-state"
elif (( others == 0 )); then
  emit "$HOST only — no other workstation has recorded state yet"
fi
exit 0
