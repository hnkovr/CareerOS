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
# jqget indexes into the payload; jexpr evaluates an expression over it (``d`` is the payload).
jexpr() { curl -sf "$1" | python3 -c "import json,sys; d=json.load(sys.stdin); print($2)"; }

echo "== API $API"
check "health" curl -sf "$API/health"
check "vault status valid" test "$(jqget "$API/api/vault/status" "['valid']")" = "True"
check "vault achievements" curl -sf "$API/api/vault/achievements"
check "fact search" curl -sf "$API/api/vault/facts/search?q=clickhouse"
check "cv variants" curl -sf "$API/api/cv/variants"
check "ai providers" curl -sf "$API/api/ai/providers"
check "profiles health" curl -sf "$API/api/profiles/health"
check "notifications" curl -sf "$API/api/notifications"
check "assistant tools are read-only but one" test "$(jexpr "$API/api/assistant/tools" "sum(1 for t in d if not t['read_only'])")" = "1"
check "workflow definitions gate their writes" test "$(jexpr "$API/api/workflows/definitions" "sum(1 for w in d for s in w['steps'] if s['kind']=='approval')")" = "2"
check "platform capabilities" test "$(jexpr "$API/api/platform/capabilities" "len(d) > 0")" = "True"

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

echo "== workflow approval gate (nothing is written before you approve)"
apps_for_opp() {
  curl -sf "$API/api/pipeline/board?kind=employment" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(sum(1 for c in d['columns'] for a in c['applications'] if a['opportunity_id'] == sys.argv[1]))" "$OPP_ID"
}
RUN=$(curl -sf -X POST "$API/api/workflows" -H 'content-type: application/json' \
  -d "{\"kind\": \"apply\", \"target_id\": \"$OPP_ID\", \"options\": {\"use_ai\": false, \"formats\": [\"json\"]}}")
RUN_ID=$(echo "$RUN" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
RUN_STATE=$(echo "$RUN" | python3 -c "import json,sys; print(json.load(sys.stdin)['state'])")
GATE=$(echo "$RUN" | python3 -c "import json,sys; print(next(s['status'] for s in json.load(sys.stdin)['steps'] if s['kind']=='approval'))")
echo "ok   started apply workflow $RUN_ID ($RUN_STATE)"; PASS=$((PASS + 1))
check "run waits at its approval gate" test "$RUN_STATE:$GATE" = "waiting_approval:waiting"
check "no application before approval" test "$(apps_for_opp)" = "0"
check "gate is a pending suggestion" test "$(jexpr "$API/api/ai/suggestions?state=suggested" "any(s['target_ref'] == '$RUN_ID' for s in d)")" = "True"
REJECTED=$(curl -sf -X POST "$API/api/workflows/$RUN_ID/decision" -H 'content-type: application/json' \
  -d '{"decision": "reject", "note": "e2e smoke"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['state'])")
check "rejection cancels the run" test "$REJECTED" = "cancelled"
check "still no application after rejection" test "$(apps_for_opp)" = "0"

if [ -n "$WEB" ]; then
  echo "== WEB $WEB"
  for path in / /vault /opportunities /cv /profiles /platforms /assistant /workflows /insights; do
    check "page $path" curl -sf "$WEB$path"
  done
  check "web → api proxy" curl -sf "$WEB/api/vault/status"
fi

echo
echo "passed: $PASS, failed: $FAIL"
exit "$([ "$FAIL" -eq 0 ] && echo 0 || echo 1)"
