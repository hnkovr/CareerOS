#!/usr/bin/env bash
# End-to-end smoke test against a running CareerOS stack (docker compose or local dev).
# Usage: scripts/e2e-smoke.sh [API_URL] [WEB_URL]   (defaults: http://localhost:8000, http://localhost:3000)
set -euo pipefail

API="${1:-http://localhost:8000}"
WEB="${2:-}"
PASS=0
FAIL=0

check() { # name, command...
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "ok   $name"; PASS=$((PASS + 1))
  else
    echo "FAIL $name"; FAIL=$((FAIL + 1))
  fi
}

jqget() { curl -sf "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d$2)"; }

echo "== API $API"
check "health" curl -sf "$API/health"
check "vault status valid" test "$(jqget "$API/api/vault/status" "['valid']")" = "True"
check "vault achievements" curl -sf "$API/api/vault/achievements"
check "fact search" curl -sf "$API/api/vault/facts/search?q=clickhouse"
check "cv variants" curl -sf "$API/api/cv/variants"
check "ai providers" curl -sf "$API/api/ai/providers"
check "profiles health" curl -sf "$API/api/profiles/health"

echo "== write path (opportunity ingest → score)"
OPP=$(curl -sf -X POST "$API/api/opportunities/ingest" -H 'content-type: application/json' -d '{
  "source": "manual",
  "text": "Senior Data Engineer (remote worldwide) at SmokeTest Corp. Requirements:\n- dbt, BigQuery, Python\nB2B contract, $120k-140k per year."
}')
OPP_ID=$(echo "$OPP" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
SCORE=$(echo "$OPP" | python3 -c "import json,sys; print(json.load(sys.stdin)['score']['overall'])")
echo "ok   ingested opportunity $OPP_ID (score $SCORE)"; PASS=$((PASS + 1))
check "opportunity detail" curl -sf "$API/api/opportunities/$OPP_ID"
check "external prompt" curl -sf -X POST "$API/api/opportunities/$OPP_ID/external-prompt" -H 'content-type: application/json' -d '{"target": "generic"}'

echo "== cv generation (no AI, json only)"
CV=$(curl -sf -X POST "$API/api/cv/generate" -H 'content-type: application/json' \
  -d '{"variant_id": "general-core", "use_ai": false, "formats": ["json"]}')
CV_ID=$(echo "$CV" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d['status']=='ready', d; print(d['id'])")
echo "ok   generated cv artifact $CV_ID"; PASS=$((PASS + 1))
check "cv artifact json file" curl -sf "$API/api/cv/artifacts/$CV_ID/file/json"

if [ -n "$WEB" ]; then
  echo "== WEB $WEB"
  for path in / /vault /opportunities /cv /profiles; do
    check "page $path" curl -sf "$WEB$path"
  done
  check "web → api proxy" curl -sf "$WEB/api/vault/status"
fi

echo
echo "passed: $PASS, failed: $FAIL"
exit "$([ "$FAIL" -eq 0 ] && echo 0 || echo 1)"
