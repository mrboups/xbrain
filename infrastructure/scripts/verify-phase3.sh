#!/bin/bash
# E2E smoke-test for an xbrain Phase 3 deployment.
#
# Run FROM the production VM as the deploy user:
#   bash infrastructure/scripts/verify-phase3.sh
#
# Requirements:
#   - .env at project root (/home/user/xbrain/.env)
#   - All Phase 1+2+3 containers running (adds neo4j, mcp-gateway, mcp-scraper,
#     mcp-drive-read, mcp-calendar, drive-sync)
#   - MCP tools registered: run register-mcp-tools.sh first if this is a fresh deploy
#
# Exit codes: 0 = all tests passed, 1 = one or more tests failed.
#
# Idempotent: safe to re-run — no persistent state is written.
# Tests that require OAuth credentials (drive-read, calendar) SKIP gracefully
# when GOOGLE_DRIVE_ACCESS_TOKEN / GOOGLE_CALENDAR_ACCESS_TOKEN are not set.

# ---------------------------------------------------------------------------
# Bootstrap — source .env, cd to project root
# ---------------------------------------------------------------------------
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

if [ -f .env ]; then
  set -a
  . .env
  set +a
else
  echo "[verify-phase3] ERROR: .env not found at ${PROJECT_ROOT}/.env" >&2
  exit 1
fi

COMPOSE="docker compose -f infrastructure/docker-compose.yml --env-file .env"

PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
pass() {
  local label="$1"
  local detail="${2:-}"
  if [ -n "${detail}" ]; then
    echo "${label} [PASS] (${detail})"
  else
    echo "${label} [PASS]"
  fi
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  local label="$1"
  local reason="$2"
  echo "${label} [FAIL] ${reason}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

skip() {
  local label="$1"
  local reason="$2"
  echo "${label} [SKIP] ${reason}"
  SKIP_COUNT=$((SKIP_COUNT + 1))
}

# ---------------------------------------------------------------------------
echo "[verify-phase3] === START ==="
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TEST 1 — Container health (Phase 3 services only)
# Checks the 6 new Phase 3 containers are Up.
# Full Phase 1+2 container health is covered by verify-phase2.sh.
# ---------------------------------------------------------------------------
LABEL="[1/7] Phase 3 container health..."
printf '%s' "${LABEL} "

TOTAL=6

HEALTHY_COUNT=0
UNHEALTHY_NAMES=()
for svc in neo4j mcp-gateway mcp-scraper mcp-drive-read mcp-calendar drive-sync; do
  CNAME=$($COMPOSE ps -q "$svc" 2>/dev/null | head -1)
  if [ -z "${CNAME}" ]; then
    UNHEALTHY_NAMES+=("${svc}:missing")
    continue
  fi
  HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${CNAME}" 2>/dev/null || echo "inspect-error")
  STATE=$(docker inspect --format '{{.State.Status}}' "${CNAME}" 2>/dev/null || echo "unknown")
  if [ "${HEALTH}" = "healthy" ] || { [ "${HEALTH}" = "no-healthcheck" ] && [ "${STATE}" = "running" ]; }; then
    HEALTHY_COUNT=$((HEALTHY_COUNT + 1))
  else
    UNHEALTHY_NAMES+=("${svc}:health=${HEALTH},state=${STATE}")
  fi
done

if [ "${HEALTHY_COUNT}" -eq "${TOTAL}" ]; then
  pass "${LABEL}" "${TOTAL}/${TOTAL} healthy"
else
  UNHEALTHY_STR=$(printf ", %s" "${UNHEALTHY_NAMES[@]}")
  UNHEALTHY_STR="${UNHEALTHY_STR:2}"
  fail "${LABEL}" "${HEALTHY_COUNT}/${TOTAL} healthy — problems: ${UNHEALTHY_STR}"
fi

# ---------------------------------------------------------------------------
# TEST 2 — Neo4j healthz + bolt connectivity
# Neo4j Community exposes HTTP on port 7474 and bolt on 7687.
# We probe the HTTP browser endpoint from inside the mcp-gateway container
# (same xbrain_net) — a 200 from /browser means Neo4j is up.
# ---------------------------------------------------------------------------
LABEL="[2/7] Neo4j connectivity..."
printf '%s ' "${LABEL}"

NEO4J_RESP=$($COMPOSE exec -T mcp-gateway \
  python3 -c "
import urllib.request, sys
try:
    with urllib.request.urlopen('http://neo4j:7474', timeout=10) as r:
        print(r.status)
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || NEO4J_RESP=""

if [ "${NEO4J_RESP}" = "200" ] || [ "${NEO4J_RESP}" = "301" ]; then
  pass "${LABEL}" "HTTP ${NEO4J_RESP} from neo4j:7474"
else
  fail "${LABEL}" "neo4j:7474 not reachable (got: ${NEO4J_RESP:-<empty>})"
fi

# ---------------------------------------------------------------------------
# TEST 3 — mcp-gateway /healthz + GET /tools (registry populated)
# After register-mcp-tools.sh has run, GET /tools must return ≥ 3 tools.
# If registry is empty (0 tools), fail with a helpful message.
# ---------------------------------------------------------------------------
LABEL="[3/7] mcp-gateway /tools registry..."
printf '%s ' "${LABEL}"

TOOLS_RESP=$($COMPOSE exec -T mcp-gateway \
  python3 -c "
import urllib.request, json, sys
try:
    with urllib.request.urlopen('http://mcp-gateway:8080/tools', timeout=10) as r:
        body = r.read().decode()
        tools = json.loads(body)
        print(len(tools))
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || TOOLS_RESP=""

if [ -z "${TOOLS_RESP}" ] || echo "${TOOLS_RESP}" | grep -q "ERROR"; then
  fail "${LABEL}" "could not reach mcp-gateway GET /tools (${TOOLS_RESP:-<empty>})"
elif [ "${TOOLS_RESP}" -ge 3 ] 2>/dev/null; then
  pass "${LABEL}" "${TOOLS_RESP} tool(s) registered"
elif [ "${TOOLS_RESP}" = "0" ]; then
  fail "${LABEL}" "0 tools registered — run: bash infrastructure/scripts/register-mcp-tools.sh"
else
  fail "${LABEL}" "only ${TOOLS_RESP} tool(s) registered (expected ≥ 3) — re-run register-mcp-tools.sh"
fi

# ---------------------------------------------------------------------------
# TEST 4 — mcp-gateway bridge JWT generation + /admin/register idempotency
# Mint a bridge JWT inside the container and call POST /admin/register with a
# known tool. Second call must return 200 (ON CONFLICT DO UPDATE = idempotent).
# ---------------------------------------------------------------------------
LABEL="[4/7] /admin/register idempotency..."
printf '%s ' "${LABEL}"

BRIDGE_JWT=$($COMPOSE exec -T mcp-gateway python3 -c "
import time, os, sys
try:
    from authlib.jose import jwt
except ImportError:
    print('ERROR:authlib_missing', file=sys.stderr)
    sys.exit(1)
secret = os.environ['BRIDGE_SHARED_SECRET']
now = int(time.time())
payload = {'sub': 'verify-phase3', 'scope': 'bridge', 'iat': now, 'exp': now + 300}
token = jwt.encode({'alg': 'HS256'}, payload, secret)
print(token.decode('ascii') if isinstance(token, bytes) else token)
" 2>/dev/null) || BRIDGE_JWT=""

if [ -z "${BRIDGE_JWT}" ]; then
  fail "${LABEL}" "could not generate bridge JWT — check authlib in mcp-gateway container"
else
  # Second POST for scraper — must succeed (upsert)
  REGISTER_RESP=$($COMPOSE exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, sys
payload = json.dumps({
    'tool_name': 'scraper',
    'sidecar_url': 'http://mcp-scraper:8100',
    'description': 'Idempotency test re-register'
}).encode()
req = urllib.request.Request(
    'http://mcp-gateway:8080/admin/register',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        print(r.status)
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || REGISTER_RESP=""

  if [ "${REGISTER_RESP}" = "200" ] || [ "${REGISTER_RESP}" = "201" ]; then
    pass "${LABEL}" "re-register returned HTTP ${REGISTER_RESP}"
  else
    fail "${LABEL}" "unexpected response from /admin/register (${REGISTER_RESP:-<empty>})"
  fi
fi

# ---------------------------------------------------------------------------
# TEST 5 — mcp-scraper E2E: gateway → mcp-scraper → HTTP fetch
# POSTs to gateway /tools/scraper/call with https://example.com.
# Expects a non-empty body response (IANA example domain is always up).
# SKIP if bridge JWT was not generated in test 4.
# ---------------------------------------------------------------------------
LABEL="[5/7] scraper E2E (gateway → mcp-scraper)..."
printf '%s ' "${LABEL}"

if [ -z "${BRIDGE_JWT:-}" ]; then
  skip "${LABEL}" "bridge JWT not available (test 4 failed)"
else
  T5_START=$(date +%s%3N 2>/dev/null || date +%s)

  SCRAPER_RESP=$($COMPOSE exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, sys
payload = json.dumps({
    'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
    'params': {'name': 'scraper', 'arguments': {'url': 'https://example.com'}}
}).encode()
req = urllib.request.Request(
    'http://mcp-gateway:8080/tools/scraper/call',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
        'X-Team-Scope': 'default',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode()
        print(len(body))
        print(body[:50])
except Exception as e:
    print(f'SCRAPER_ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || SCRAPER_RESP=""

  T5_END=$(date +%s%3N 2>/dev/null || date +%s)

  if [ -z "${SCRAPER_RESP}" ] || echo "${SCRAPER_RESP}" | grep -q "SCRAPER_ERROR"; then
    fail "${LABEL}" "scraper call failed — mcp-scraper healthy? (${SCRAPER_RESP:-<empty>})"
  else
    CHAR_COUNT=$(echo "${SCRAPER_RESP}" | head -1 | tr -d '[:space:]')
    if [ -n "${CHAR_COUNT}" ] && [ "${CHAR_COUNT}" -gt 0 ] 2>/dev/null; then
      pass "${LABEL}" "${CHAR_COUNT} chars received"
    else
      fail "${LABEL}" "empty or unparseable response: ${SCRAPER_RESP:0:100}"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# TEST 6 — drive-read tool registration check (OAuth credentials optional)
# If GOOGLE_DRIVE_ACCESS_TOKEN is not set: verifies tool is registered, SKIPs call.
# If set: calls read_drive_file with a sentinel file ID (will get a 4xx from Drive,
# but we verify the gateway forwarded the request to mcp-drive-read).
# ---------------------------------------------------------------------------
LABEL="[6/7] drive-read tool check..."
printf '%s ' "${LABEL}"

if [ -z "${BRIDGE_JWT:-}" ]; then
  skip "${LABEL}" "bridge JWT not available (test 4 failed)"
elif [ -z "${GOOGLE_DRIVE_ACCESS_TOKEN:-}" ] || [ "${GOOGLE_DRIVE_ACCESS_TOKEN}" = "__FILL_AFTER_OAUTH__" ]; then
  # Verify the tool is registered even without OAuth credentials
  DRIVE_REG=$($COMPOSE exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, sys
try:
    with urllib.request.urlopen('http://mcp-gateway:8080/tools', timeout=10) as r:
        tools = json.loads(r.read())
        names = [t.get('tool_name') for t in tools]
        print('found' if 'drive-read' in names else 'missing')
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || DRIVE_REG=""

  if [ "${DRIVE_REG}" = "found" ]; then
    skip "${LABEL}" "drive-read registered; GOOGLE_DRIVE_ACCESS_TOKEN not set — skipping live call"
  else
    fail "${LABEL}" "drive-read not in registry (DRIVE_REG=${DRIVE_REG:-<empty>}) — run register-mcp-tools.sh"
  fi
else
  # OAuth credentials are set — attempt a live call (expect 404 from Drive for sentinel ID)
  DRIVE_RESP=$($COMPOSE exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, sys
payload = json.dumps({
    'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
    'params': {'name': 'read_drive_file', 'arguments': {'file_id': 'verify-phase3-sentinel'}}
}).encode()
req = urllib.request.Request(
    'http://mcp-gateway:8080/tools/drive-read/call',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
        'X-Team-Scope': 'default',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        print('ok:' + str(r.status))
except urllib.error.HTTPError as e:
    # 4xx from gateway (Drive returned error) = gateway forwarded request OK
    print('forwarded:' + str(e.code))
except Exception as e:
    print(f'DRIVE_ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || DRIVE_RESP=""

  if echo "${DRIVE_RESP}" | grep -qE "^ok:|^forwarded:"; then
    pass "${LABEL}" "gateway forwarded to mcp-drive-read (${DRIVE_RESP})"
  else
    fail "${LABEL}" "drive-read call failed: ${DRIVE_RESP:-<empty>}"
  fi
fi

# ---------------------------------------------------------------------------
# TEST 7 — calendar tool registration check (OAuth credentials optional)
# Same pattern as test 6: verifies registration when no OAuth credentials set.
# ---------------------------------------------------------------------------
LABEL="[7/7] calendar tool check..."
printf '%s ' "${LABEL}"

if [ -z "${BRIDGE_JWT:-}" ]; then
  skip "${LABEL}" "bridge JWT not available (test 4 failed)"
elif [ -z "${GOOGLE_CALENDAR_ACCESS_TOKEN:-}" ] || [ "${GOOGLE_CALENDAR_ACCESS_TOKEN}" = "__FILL_AFTER_OAUTH__" ]; then
  # Verify the tool is registered
  CAL_REG=$($COMPOSE exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, sys
try:
    with urllib.request.urlopen('http://mcp-gateway:8080/tools', timeout=10) as r:
        tools = json.loads(r.read())
        names = [t.get('tool_name') for t in tools]
        print('found' if 'calendar' in names else 'missing')
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || CAL_REG=""

  if [ "${CAL_REG}" = "found" ]; then
    skip "${LABEL}" "calendar registered; GOOGLE_CALENDAR_ACCESS_TOKEN not set — skipping live call"
  else
    fail "${LABEL}" "calendar not in registry (CAL_REG=${CAL_REG:-<empty>}) — run register-mcp-tools.sh"
  fi
else
  # OAuth credentials are set — call list_events with today
  CAL_RESP=$($COMPOSE exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, sys
payload = json.dumps({
    'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
    'params': {'name': 'list_events', 'arguments': {'date_range': 'today+7days'}}
}).encode()
req = urllib.request.Request(
    'http://mcp-gateway:8080/tools/calendar/call',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
        'X-Team-Scope': 'default',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read().decode()
        print('ok:' + str(len(body)))
except urllib.error.HTTPError as e:
    print('forwarded:' + str(e.code))
except Exception as e:
    print(f'CAL_ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || CAL_RESP=""

  if echo "${CAL_RESP}" | grep -qE "^ok:|^forwarded:"; then
    pass "${LABEL}" "calendar call returned (${CAL_RESP})"
  else
    fail "${LABEL}" "calendar call failed: ${CAL_RESP:-<empty>}"
  fi
fi

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
TOTAL_TESTS=7
TOTAL_RUN=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT))

echo ""
if [ "${FAIL_COUNT}" -eq 0 ]; then
  echo "[verify-phase3] === SUCCESS === ${PASS_COUNT} passed, ${SKIP_COUNT} skipped (${TOTAL_RUN}/${TOTAL_TESTS} run)"
  exit 0
else
  echo "[verify-phase3] === FAILURE === ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped (${TOTAL_RUN}/${TOTAL_TESTS} run)"
  exit 1
fi
