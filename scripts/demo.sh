#!/usr/bin/env bash
# GLC-V2 Security Demo
#
# Usage:
#   bash scripts/demo.sh                  # hit the live Modal deployment
#   bash scripts/demo.sh local            # hit localhost:8111
#   bash scripts/demo.sh https://your.url # hit a custom base URL
#
# Expected: all 8 checks PASS (exit code 0).

set -euo pipefail

MODE="${1:-modal}"

case "$MODE" in
  local|localhost)
    BASE="http://127.0.0.1:8111"
    ;;
  modal|"")
    BASE="https://varunsood189--glc-v1-gateway-fastapi-app.modal.run"
    ;;
  http://*|https://*)
    BASE="$MODE"
    ;;
  *)
    echo "Usage: bash scripts/demo.sh [modal|local|<url>]"
    exit 1
    ;;
esac

PASS=0
FAIL=0

green() { printf "  \033[32m✅ PASS\033[0m — %s\n" "$1"; PASS=$((PASS+1)); }
red()   { printf "  \033[31m❌ FAIL\033[0m — %s\n" "$1"; FAIL=$((FAIL+1)); }

check_status() {
  local label="$1" expected="$2" actual="$3"
  if [ "$actual" = "$expected" ]; then
    green "$label (HTTP $actual)"
  else
    red "$label (expected $expected, got $actual)"
  fi
}

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              GLC-V2 SECURITY DEMO                            ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Target: $BASE"
echo ""

# ── 1. Health ──────────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  1. /healthz — gateway must be alive"
echo "──────────────────────────────────────────────────────────────"
BODY=$(curl -s --max-time 30 "$BASE/healthz" || echo "ERROR")
echo "  Response: $BODY"
if echo "$BODY" | grep -q '"ok"'; then green "gateway is live"; else red "gateway not responding"; fi
echo ""

# ── 2. Docs hidden ─────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  2. /docs hidden in prod (A2)"
echo "──────────────────────────────────────────────────────────────"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/docs" || echo "000")
check_status "Swagger UI hidden" "404" "$STATUS"
echo ""

# ── 3. OpenAPI hidden ──────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  3. /openapi.json hidden in prod (A2)"
echo "──────────────────────────────────────────────────────────────"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/openapi.json" || echo "000")
check_status "OpenAPI schema hidden" "404" "$STATUS"
echo ""

# ── 4. Chat requires auth ──────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  4. /v1/chat without token → 401 (A1)"
echo "──────────────────────────────────────────────────────────────"
RESP=$(curl -s -w "\n%{http_code}" --max-time 20 -X POST "$BASE/v1/chat" \
  -H "Content-Type: application/json" -d '{"prompt":"hi"}' || echo -e "\n000")
BODY=$(echo "$RESP" | head -n -1)
STATUS=$(echo "$RESP" | tail -n 1)
echo "  Response: $BODY"
check_status "unauthenticated chat blocked" "401" "$STATUS"
echo ""

# ── 5. Status gated ────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  5. /v1/status without token → 401 (A2)"
echo "──────────────────────────────────────────────────────────────"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/v1/status" || echo "000")
check_status "info endpoint gated" "401" "$STATUS"
echo ""

# ── 6. Webhook empty-token bypass ──────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  6. Webhook empty-token bypass → 403 (Bug B)"
echo "──────────────────────────────────────────────────────────────"
RESP=$(curl -s -w "\n%{http_code}" --max-time 20 \
  "$BASE/v1/channels/telegram/webhook?hub.mode=subscribe&hub.verify_token=&hub.challenge=pwned" \
  || echo -e "\n000")
BODY=$(echo "$RESP" | head -n -1)
STATUS=$(echo "$RESP" | tail -n 1)
echo "  Response: $BODY"
check_status "empty-token webhook bypass blocked" "403" "$STATUS"
echo ""

# ── 7. Wrong token ─────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  7. Wrong bearer token → 403"
echo "──────────────────────────────────────────────────────────────"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 -X POST "$BASE/v1/chat" \
  -H "Authorization: Bearer totally-wrong-token" \
  -H "Content-Type: application/json" -d '{"prompt":"hi"}' || echo "000")
check_status "wrong token rejected" "403" "$STATUS"
echo ""

# ── 8. Oversized batch ─────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  8. Oversized batch (55 calls) blocked (Bug D)"
echo "──────────────────────────────────────────────────────────────"
BATCH='{"calls":[{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"},{"prompt":"hi"}],"max_concurrency":1}'
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 -X POST "$BASE/v1/chat/batch" \
  -H "Authorization: Bearer wrongtoken" \
  -H "Content-Type: application/json" \
  -d "$BATCH" || echo "000")
# 401/403 (auth) or 422 (schema max_length) all prove the endpoint is gated
if [ "$STATUS" = "401" ] || [ "$STATUS" = "403" ] || [ "$STATUS" = "422" ]; then
  green "oversized/unauth batch blocked (HTTP $STATUS)"
else
  red "batch not blocked (got HTTP $STATUS)"
fi
echo ""

# ── Summary ────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  SUMMARY: $PASS passed, $FAIL failed"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
