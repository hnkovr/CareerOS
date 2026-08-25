#!/usr/bin/env bash
# Freshness gate for every generated-and-committed contract.
#
# Two generators feed two committed trees:
#   career/schemas      <- careeros vault export-schemas   (python)
#   packages/schemas    <- openapi-typescript              (node, from the exported OpenAPI)
#
# Both toolchains are required: checking only the half that happens to be installed would
# report "fresh" while the other half silently drifts, which is exactly the bug this replaces.
#
# Used by `just contracts-check` and by CI — one implementation, so they cannot diverge.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

command -v uv >/dev/null || { echo "contracts-check needs uv" >&2; exit 1; }
[ -d node_modules ] || { echo "contracts-check needs the web workspace: npm ci" >&2; exit 1; }

uv run careeros vault export-schemas >/dev/null
uv run careeros export-openapi >/dev/null
npm run --silent generate:types >/dev/null

if ! git diff --exit-code --stat -- career/schemas packages/schemas; then
  echo >&2
  echo "generated contracts are stale — run 'just export-schemas && just openapi', then commit" >&2
  exit 1
fi
echo "contracts are in sync with the code"
