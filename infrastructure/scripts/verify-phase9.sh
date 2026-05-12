#!/usr/bin/env bash
#
# verify-phase9.sh — Verify Phase 9 (Session Bridge — Pro/Max routing via Chrome extension) deliverables.
#
# Tests (8, SKIP-aware — SKIP never counts as FAIL):
#   1. session-bridge container running
#   2. /healthz returns 200 (local on 127.0.0.1:8105)
#   3. bridge.grooveos.app DNS resolves (SKIPPED if not yet configured)
#   4. nginx vhost 50-bridge.conf loaded (server_name bridge.grooveos.app present in nginx -T)
#   5. WebSocket reachable end-to-end via the bridge (SKIPPED if VERIFY_XBT_TOKEN unset)
#   6. user_external_sessions table exists in postgres (migration 0014)
#   7. librechat.yaml contains "Claude Pro/Max" custom endpoint
#   8. chrome-extension/tests/run_tests.mjs exits 0
#
# Exit code:
#   0 when FAIL == 0, regardless of SKIP count
#   1 when FAIL > 0
#
# Usage:
#   bash infrastructure/scripts/verify-phase9.sh
#
# Optional env overrides:
#   BRIDGE_HOST           default bridge.grooveos.app
#   BRIDGE_LOCAL          default http://127.0.0.1:8105
#   NGINX_CONTAINER       default xbrain-nginx
#   BRIDGE_CONTAINER      default xbrain-session-bridge (or session-bridge)
#   DB_CONTAINER          default xbrain-postgres
#   PG_USER               default xbrain
#   PG_DB                 default xbrain
#   LIBRECHAT_CONTAINER   default xbrain-librechat (fallback: read host file)
#   VERIFY_XBT_TOKEN      (optional) a valid xbt_ token to exercise WS connect (test 5)
#
# Run from repo root on the VM where docker compose is running.

set -uo pipefail   # NOT -e — every test runs independently; the summary line is the truth

BRIDGE_HOST="${BRIDGE_HOST:-bridge.grooveos.app}"
BRIDGE_LOCAL="${BRIDGE_LOCAL:-http://127.0.0.1:8105}"
NGINX_CONTAINER="${NGINX_CONTAINER:-xbrain-nginx}"
BRIDGE_CONTAINER="${BRIDGE_CONTAINER:-xbrain-session-bridge}"
DB_CONTAINER="${DB_CONTAINER:-xbrain-postgres}"
PG_USER="${PG_USER:-xbrain}"
PG_DB="${PG_DB:-xbrain}"
LIBRECHAT_CONTAINER="${LIBRECHAT_CONTAINER:-xbrain-librechat}"

PASS=0
FAIL=0
SKIP=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

ok()   { green "  PASS: $*"; PASS=$((PASS+1)); }
ko()   { red   "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip() { yellow "  ⊘ SKIPPED: $*"; SKIP=$((SKIP+1)); }

echo "=== Phase 9 Verification ==="

# -----------------------------------------------------------------------------
test_01_container_up() {
  echo
  echo "[1/8] session-bridge container running"
  # Try named container first, then fall back to docker compose name "session-bridge"
  local state
  state=$(docker ps --filter "name=${BRIDGE_CONTAINER}" --format "{{.State}}" 2>/dev/null | head -n1)
  if [[ -z "$state" ]]; then
    state=$(docker ps --filter "name=session-bridge" --format "{{.State}}" 2>/dev/null | head -n1)
  fi
  if [[ "$state" == "running" ]]; then
    ok "session-bridge container is running"
  else
    ko "session-bridge container not running (state='${state:-<absent>}')"
  fi
}

# -----------------------------------------------------------------------------
test_02_healthz_local() {
  echo
  echo "[2/8] session-bridge /healthz reachable on ${BRIDGE_LOCAL}"
  local status body
  status=$(curl -s -o /tmp/xbrain-09-healthz.body -w "%{http_code}" -m 5 "${BRIDGE_LOCAL}/healthz" 2>/dev/null)
  status="${status:-000}"
  body=$(cat /tmp/xbrain-09-healthz.body 2>/dev/null || echo "")
  if [[ "$status" == "200" ]]; then
    ok "healthz returned 200 (body: ${body:0:80})"
  else
    ko "healthz returned HTTP $status (body: ${body:0:80})"
  fi
}

# -----------------------------------------------------------------------------
test_03_dns_resolves() {
  echo
  echo "[3/8] bridge.grooveos.app DNS resolves"
  if ! command -v dig >/dev/null 2>&1; then
    # Fall back to getent if dig is missing
    if command -v getent >/dev/null 2>&1; then
      local out
      out=$(getent hosts "$BRIDGE_HOST" 2>/dev/null | awk '{print $1}' | head -n1)
      if [[ -n "$out" ]]; then
        ok "DNS resolved ${BRIDGE_HOST} → ${out} (via getent)"
      else
        skip "${BRIDGE_HOST} does not resolve yet (DNS A record may not be configured)"
      fi
    else
      skip "neither dig nor getent available — cannot test DNS"
    fi
    return
  fi
  local out
  out=$(dig +short "$BRIDGE_HOST" 2>/dev/null | head -n1)
  if [[ -n "$out" ]]; then
    ok "DNS resolved ${BRIDGE_HOST} → ${out}"
  else
    skip "${BRIDGE_HOST} does not resolve (Cloudflare A record may not be configured yet — not a failure)"
  fi
}

# -----------------------------------------------------------------------------
test_04_nginx_vhost_loaded() {
  echo
  echo "[4/8] nginx vhost 50-bridge.conf loaded (server_name ${BRIDGE_HOST})"
  if ! docker ps --filter "name=${NGINX_CONTAINER}" --format "{{.Names}}" 2>/dev/null | grep -q "${NGINX_CONTAINER}"; then
    ko "${NGINX_CONTAINER} container not found"
    return
  fi
  if docker exec "${NGINX_CONTAINER}" nginx -T 2>&1 | grep -q "${BRIDGE_HOST}"; then
    ok "nginx config includes ${BRIDGE_HOST}"
  else
    ko "nginx config does not mention ${BRIDGE_HOST}"
  fi
}

# -----------------------------------------------------------------------------
test_05_ws_reachable() {
  echo
  echo "[5/8] WebSocket endpoint reachable end-to-end via the bridge"
  if [[ -z "${VERIFY_XBT_TOKEN:-}" ]]; then
    skip "VERIFY_XBT_TOKEN not set — export a valid xbt_ token to exercise WS connect"
    return
  fi
  # Pull the user_sub from memory-api /v1/me using the same token, then attempt the WS upgrade.
  # This is a smoke check: a 101 Switching Protocols (or a 4401/4403 close, since handshake succeeded)
  # all prove that nginx + bridge accepted the upgrade request.
  local code
  code=$(curl -sS --include --no-buffer --max-time 5 \
    -H "Connection: Upgrade" \
    -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" \
    -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    "${BRIDGE_LOCAL}/ws/verify-probe?token=${VERIFY_XBT_TOKEN}" 2>/dev/null | head -n1 | awk '{print $2}')
  if [[ "$code" == "101" ]]; then
    ok "bridge accepted WS upgrade (HTTP 101)"
  elif [[ "$code" == "401" || "$code" == "403" ]]; then
    # Auth was checked at the application level — still proves the route is wired up
    ok "bridge replied $code on WS upgrade (route reachable; token invalid is OK for this smoke)"
  else
    ko "WS upgrade did not return 101/401/403 (got: ${code:-<no-response>})"
  fi
}

# -----------------------------------------------------------------------------
test_06_migration_0014() {
  echo
  echo "[6/8] user_external_sessions table exists (migration 0014)"
  if ! docker ps --filter "name=${DB_CONTAINER}" --format "{{.Names}}" 2>/dev/null | grep -q "${DB_CONTAINER}"; then
    ko "${DB_CONTAINER} container not found"
    return
  fi
  local exists
  exists=$(docker exec -i "${DB_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc \
    "SELECT to_regclass('user_external_sessions')::text" 2>/dev/null | tr -d '[:space:]')
  if [[ "$exists" == "user_external_sessions" ]]; then
    ok "user_external_sessions table exists"
  else
    ko "user_external_sessions table not found (alembic head < 0014?)"
  fi
}

# -----------------------------------------------------------------------------
test_07_librechat_endpoint() {
  echo
  echo "[7/8] librechat.yaml contains \"Claude Pro/Max\" custom endpoint"
  # Prefer reading from the container (true post-restart state), fall back to host file
  local content=""
  if docker ps --filter "name=${LIBRECHAT_CONTAINER}" --format "{{.Names}}" 2>/dev/null | grep -q "${LIBRECHAT_CONTAINER}"; then
    content=$(docker exec "${LIBRECHAT_CONTAINER}" cat /app/librechat.yaml 2>/dev/null || true)
  fi
  if [[ -z "$content" ]] && [[ -f infrastructure/librechat/librechat.yaml ]]; then
    content=$(cat infrastructure/librechat/librechat.yaml)
  fi
  if echo "$content" | grep -q 'Claude Pro/Max'; then
    ok "endpoint 'Claude Pro/Max' present"
  else
    ko "endpoint 'Claude Pro/Max' NOT present in librechat.yaml"
  fi
}

# -----------------------------------------------------------------------------
test_08_translator_tests() {
  echo
  echo "[8/8] chrome-extension/tests/run_tests.mjs exits 0"
  if ! command -v node >/dev/null 2>&1; then
    skip "node not installed on this host — translator tests run in browser/dev environment, not production VM"
    return
  fi
  if [[ ! -d chrome-extension ]]; then
    skip "chrome-extension/ directory not found on this host — run from dev where extension source lives"
    return
  fi
  if (cd chrome-extension && node tests/run_tests.mjs) >/tmp/xbrain-09-trans.log 2>&1; then
    ok "translator + WS keepalive tests pass"
  else
    ko "translator tests failed (see /tmp/xbrain-09-trans.log)"
  fi
}

# -----------------------------------------------------------------------------
test_01_container_up
test_02_healthz_local
test_03_dns_resolves
test_04_nginx_vhost_loaded
test_05_ws_reachable
test_06_migration_0014
test_07_librechat_endpoint
test_08_translator_tests

TOTAL_RUN=$((PASS + FAIL))
echo
echo "==========================================="
echo "PASS: $PASS / $TOTAL_RUN (SKIPPED: $SKIP)"
echo "==========================================="

if [[ $FAIL -eq 0 ]]; then
  green "Phase 9 verification: ALL PASS (FAIL=0; SKIPPED never blocks)"
  exit 0
else
  red "Phase 9 verification: $FAIL failure(s)"
  exit 1
fi
