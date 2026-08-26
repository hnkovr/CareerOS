#!/usr/bin/env bash
# workstation-preflight.sh — is this machine safe to walk away from?
#
# A migration does not fail on the code; it fails on what only ever existed on the old
# laptop — the branch nobody pushed, the vault that is git-ignored by design, the secret
# file that lives at mode 0600 outside the repo. Each of those is silent until the new
# machine needs it, and by then the old one may be wiped.
#
# So this prints a verdict, not a log: every blocker names the command that clears it.
# Secret VALUES are never read, only presence and mode. Read-only — it changes nothing.
#
# Scalars: config/workstation.yml. Usage: scripts/workstation-preflight.sh [--quiet]
# Exit: 0 clean · 3 blockers found · 2 not runnable (missing tooling)
set -uo pipefail

QUIET=0
[[ ${1:-} == --quiet ]] && QUIET=1

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "not a git repo" >&2; exit 2; }
cd "$ROOT" || exit 2
CFG=config/workstation.yml
[[ -f $CFG ]] || { echo "missing $CFG" >&2; exit 2; }
command -v yq >/dev/null 2>&1 || { echo "yq (mikefarah v4) required: brew install yq" >&2; exit 2; }

blockers=() notes=()
block() { blockers+=("$1"); }
note()  { notes+=("$1"); }
say()   { ((QUIET)) || printf '%s\n' "$1"; }
hdr()   { ((QUIET)) || printf '\n\033[1m%s\033[0m\n' "$1"; }

# ------------------------------------------------------------------ git settle
hdr "git — is everything on origin?"
branch=$(git branch --show-current 2>/dev/null || echo detached)
dirty=$(git status --porcelain | grep -vc '^??' || true)
untracked=$(git status --porcelain | grep -c '^??' || true)
say "  branch=$branch  dirty=$dirty  untracked=$untracked"

# Dirt is reported, never committed here: this tree is shared by parallel sessions and a
# blanket commit would sweep a neighbour's work (see .claude/CLAUDE.md).
if (( dirty > 0 || untracked > 0 )); then
  block "$((dirty + untracked)) uncommitted path(s) — commit path-scoped and tracker-bound: /smart-commit"
fi

git fetch origin --quiet 2>/dev/null || note "origin unreachable — ahead/behind below may be stale"
while read -r br; do
  [[ -n $br ]] || continue
  if git rev-parse -q --verify "refs/remotes/origin/$br" >/dev/null 2>&1; then
    ahead=$(git rev-list --count "origin/$br..$br" 2>/dev/null || echo 0)
    (( ahead > 0 )) && block "branch '$br' has $ahead unpushed commit(s) — git push origin $br"
  else
    # A branch with no upstream is only safe if origin already contains every one of its commits.
    uniq=$(git rev-list --count --not --remotes -- "$br" 2>/dev/null || echo 0)
    if (( uniq > 0 )); then
      block "branch '$br' exists only here ($uniq commit(s), no upstream) — git push -u origin $br"
    else
      note "branch '$br' has no upstream but nothing unique — safe to drop"
    fi
  fi
done < <(git for-each-ref --format='%(refname:short)' refs/heads/)

stashes=$(git stash list | wc -l | tr -d ' ')
(( stashes > 0 )) && block "$stashes stash(es) — stashes are machine-local; turn each into a commit"
worktrees=$(git worktree list | tail -n +2 | wc -l | tr -d ' ')
(( worktrees > 0 )) && note "$worktrees extra worktree(s) — machine-local; recreate from origin on the new host"

# ------------------------------------------------- vault (the actual source of truth)
hdr "vault — canonical facts are git-ignored by design"
vault_path=$(grep -E '^CAREEROS_VAULT_PATH=' .env 2>/dev/null | cut -d= -f2-)
vault_path=${vault_path:-career/private}
vault_url_set=no
grep -qE '^CAREEROS_VAULT_GIT_URL=.+' .env 2>/dev/null && vault_url_set=yes
if [[ -d $vault_path ]] && [[ -n $(find "$vault_path" -name '*.yaml' -o -name '*.yml' 2>/dev/null | head -1) ]]; then
  say "  $vault_path — initialised"
  if git -C "$vault_path" rev-parse --git-dir >/dev/null 2>&1 \
     && [[ $(git -C "$vault_path" rev-parse --show-toplevel) != "$ROOT" ]]; then
    vd=$(git -C "$vault_path" status --porcelain | wc -l | tr -d ' ')
    (( vd > 0 )) && block "vault has $vd uncommitted change(s) — commit inside $vault_path"
    if git -C "$vault_path" remote get-url origin >/dev/null 2>&1; then
      va=$(git -C "$vault_path" rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)
      (( va > 0 )) && block "vault has $va unpushed commit(s) — push it: git -C $vault_path push"
    else
      block "vault is a git repo with NO remote — the new machine cannot fetch it; add a private remote and set CAREEROS_VAULT_GIT_URL"
    fi
  else
    block "vault at $vault_path is not its own git repo — it is git-ignored here, so nothing carries it to the new machine"
  fi
else
  say "  $vault_path — empty scaffold (the bundled demo vault is used, read-only)"
  [[ $vault_url_set == yes ]] \
    && note "CAREEROS_VAULT_GIT_URL is set — the new machine restores the vault from it" \
    || note "no private vault on this machine and no CAREEROS_VAULT_GIT_URL — nothing to migrate"
fi

# ------------------------------------------------------- secrets (presence only)
hdr "secrets — presence and escrow, never values"
have=0 missing=0
while read -r slot; do
  [[ -n $slot ]] || continue
  if grep -qE "^${slot}=.+" .env 2>/dev/null \
     || grep -qE "^(export )?${slot}=.+" "$HOME/.ai/.env.secrets" 2>/dev/null; then
    have=$((have + 1))
  else
    missing=$((missing + 1))
  fi
done < <(yq -r '.workstation.secrets.slots[].name' "$CFG")
say "  $have slot(s) filled · $missing blank (blank is legitimate — optional features stay off)"

push_cmd=$(yq -r '.workstation.secrets.escrow_push' "$CFG")
if [[ -f "$HOME/.ai/.env.secrets" ]]; then
  mode=$(stat -f %Lp "$HOME/.ai/.env.secrets" 2>/dev/null)
  [[ $mode == 600 ]] || block "~/.ai/.env.secrets is mode $mode — chmod 600 it before escrowing"
  if command -v bw >/dev/null 2>&1; then
    st=$(bw status 2>/dev/null | yq -r .status 2>/dev/null || echo unknown)
    [[ $st == unlocked ]] \
      && note "Bitwarden unlocked — escrow now: $push_cmd" \
      || block "Bitwarden is '$st' — the new machine has no way to get the secrets: bw unlock, then: $push_cmd"
  else
    block "bw (Bitwarden CLI) absent — no escrow lane: brew install bitwarden-cli"
  fi
else
  (( have > 0 )) && block "secrets are only in .env, not in ~/.ai/.env.secrets — they will not migrate" \
                 || note "no ~/.ai/.env.secrets on this machine"
fi
[[ -f config/.env.secrets ]] && note "config/.env.secrets exists (git-ignored) — it is rendered from the escrow, not copied"

# ------------------------------------------------------------ handoff artefacts
hdr "handoff — what the other machine will read"
state_dir=$(yq -r '.workstation.state_dir' "$CFG")
host=$(hostname -s)
if [[ -f $state_dir/$host.yml ]]; then
  updated=$(yq -r .updated "$state_dir/$host.yml")
  say "  $state_dir/$host.yml — updated $updated"
  # Stale state is worse than none: the other machine trusts it.
  age_days=$(( ( $(date -u +%s) - $(date -u -j -f %Y-%m-%dT%H:%M:%SZ "$updated" +%s 2>/dev/null || echo 0) ) / 86400 ))
  (( age_days > 1 )) && block "state file is $age_days day(s) old — refresh: just workstation-state"
else
  block "this host is not recorded in $state_dir — just workstation-state"
fi
runbook=$(yq -r '.workstation.runbook' "$CFG")
[[ -f $runbook ]] || block "bootstrap runbook missing: $runbook"
guard=$(yq -r '.workstation.guard' "$CFG")
[[ -x $guard ]] || block "session guard missing or not executable: $guard"

# ------------------------------------------------------------------- verdict
hdr "verdict"
for n in ${notes+"${notes[@]}"}; do say "  · $n"; done
if (( ${#blockers[@]} == 0 )); then
  printf '  \033[1;32mclean\033[0m — a fresh clone on another machine loses nothing\n'
  exit 0
fi
printf '  \033[1;31mblocked\033[0m — %d item(s) would not survive the move:\n' "${#blockers[@]}"
i=0
for b in "${blockers[@]}"; do i=$((i + 1)); printf '   %d. %s\n' "$i" "$b"; done
printf '\n  runbook: %s\n' "$runbook"
exit 3
