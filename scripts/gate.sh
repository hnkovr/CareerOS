#!/usr/bin/env bash
# gate.sh — the quality gate, in one place.
#
# Local (`just lint` → `make lint` → `make check` → `make all`) and CI must run the SAME set of
# checks. They used to be written out twice — once in the Justfile, once inline in
# .github/workflows/ci.yml — and kept in step by hand, which is exactly how a check ends up
# running in one place and not the other (see GH #23, and the contract gate before it).
#
# Usage: scripts/gate.sh [lint|test]      (default: lint)
#
# The web half runs only when the workspace is installed, and says so when it is not — a gate
# that skips half of itself in silence reads as a green gate.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

web_ready() { [ -f apps/web/package.json ] && [ -d node_modules ]; }
no_web() { echo "  · web steps skipped — run \`npm install\` to include them" >&2; }

lint() {
  uv run ruff check .
  uv run ruff format --check .
  uv run pyright
  uv run lint-imports
  python3 scripts/env-render.py --check
  if web_ready; then npm run -w apps/web typecheck --if-present; npm run -w apps/web lint; else no_web; fi
}

test_all() {
  uv run pytest "$@"
  if web_ready; then npm run -w apps/web test --if-present; else no_web; fi
}

case "${1:-lint}" in
  lint) shift || true; lint ;;
  test) shift || true; test_all "$@" ;;
  *) echo "usage: ${BASH_SOURCE[0]##*/} [lint|test]" >&2; exit 1 ;;
esac
