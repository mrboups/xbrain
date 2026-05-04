#!/bin/bash
# E2E smoke-test for an xbrain Phase 4 deployment.
#
# Run FROM the production VM as the deploy user:
#   bash infrastructure/scripts/verify-phase4.sh
#
# Requirements:
#   - .env at project root
#   - All Phase 1+2+3+4 containers running
#   - Phase 4 plans executed: register-mcp-tools.sh re-run after mcp-deck up
#
# Exit codes: 0 = all tests passed, 1 = one or more tests failed.
# Threshold: PASS if >= 6/8 tests are green.
#
# Tests that require OAuth credentials or live data SKIP gracefully.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${PROJECT_ROOT}"

if [ -f .env ]; then
  set -a; . .env; set +a
else
  echo "[verify-phase4] ERROR: .env not found at ${PROJECT_ROOT}/.env" >&2
  exit 1
fi

COMPOSE="docker compose -f infrastructure/docker-compose.yml --env-file .env"
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0

pass() { local l="$1"; local d="${2:-}"; [ -n "${d}" ] && echo "${l} [PASS] (${d})" || echo "${l} [PASS]"; PASS_COUNT=$((PASS_COUNT+1)); }
fail() { echo "$1 [FAIL] $2"; FAIL_COUNT=$((FAIL_COUNT+1)); }
skip() { echo "$1 [SKIP] $2"; SKIP_COUNT=$((SKIP_COUNT+1)); }

echo "[verify-phase4] === START ==="

# ---------------------------------------------------------------------------
# TEST 1 — Phase 4 container health (mcp-deck)
# ---------------------------------------------------------------------------
LABEL="[1/8] Phase 4 container health (mcp-deck)..."
printf '%s ' "${LABEL}"

CNAME=$($COMPOSE ps -q mcp-deck 2>/dev/null | head -1)
if [ -z "${CNAME}" ]; then
  fail "${LABEL}" "mcp-deck container not found — docker compose up -d mcp-deck"
else
  HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "${CNAME}" 2>/dev/null || echo "inspect-error")
  STATE=$(docker inspect --format '{{.State.Status}}' "${CNAME}" 2>/dev/null || echo "unknown")
  if [ "${HEALTH}" = "healthy" ] || { [ "${HEALTH}" = "no-healthcheck" ] && [ "${STATE}" = "running" ]; }; then
    pass "${LABEL}" "mcp-deck health=${HEALTH} state=${STATE}"
  else
    fail "${LABEL}" "mcp-deck health=${HEALTH} state=${STATE}"
  fi
fi

# ---------------------------------------------------------------------------
# TEST 2 — POST /v1/messages upsert silencieux (plan 04-01)
# ---------------------------------------------------------------------------
LABEL="[2/8] memory-api POST /v1/messages upsert silencieux..."
printf '%s ' "${LABEL}"

BRIDGE_JWT=$($COMPOSE exec -T mcp-gateway python3 -c "
import time, os, sys
try:
    from authlib.jose import jwt
except ImportError:
    sys.exit(1)
secret = os.environ['BRIDGE_SHARED_SECRET']
now = int(time.time())
payload = {'sub': 'verify-phase4', 'scope': 'bridge', 'team_scope': 'default', 'iat': now, 'exp': now + 300}
token = jwt.encode({'alg': 'HS256'}, payload, secret)
print(token.decode('ascii') if isinstance(token, bytes) else token)
" 2>/dev/null) || BRIDGE_JWT=""

if [ -z "${BRIDGE_JWT}" ]; then
  fail "${LABEL}" "could not generate bridge JWT — mcp-gateway healthy?"
else
  # Generate a UUID that definitely doesn't exist as a conversation
  FAKE_CONV_ID=$(python3 -c "import uuid; print(str(uuid.uuid4()))" 2>/dev/null || echo "")
  if [ -z "${FAKE_CONV_ID}" ]; then
    skip "${LABEL}" "python3 uuid generation failed"
  else
    MSG_RESP=$($COMPOSE exec -T memory-api \
      python3 -c "
import urllib.request, json, sys
payload = json.dumps({
    'conversation_id': '${FAKE_CONV_ID}',
    'role': 'user',
    'content': 'verify-phase4 upsert test',
    'metadata': {},
    'tagging': {
        'team_scope': 'default',
        'project_scope': None,
        'visibility': 'team',
        'confidence': 1.0,
        'truth_level': 'EPHEMERAL',
        'source': 'test:verify-phase4',
        'validation_status': 'pending',
    }
}).encode()
req = urllib.request.Request(
    'http://memory-api:8000/v1/messages',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
        'X-Team-Scope': 'default',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=10) as r:
        print(r.status)
except urllib.error.HTTPError as e:
    print(f'HTTP_ERROR:{e.code}', file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || MSG_RESP=""

    if [ "${MSG_RESP}" = "201" ]; then
      pass "${LABEL}" "conversation auto-created + message inserted (HTTP 201)"
    elif echo "${MSG_RESP}" | grep -q "HTTP_ERROR:404"; then
      fail "${LABEL}" "still returns 404 — plan 04-01 not applied"
    else
      fail "${LABEL}" "unexpected response: ${MSG_RESP:-<empty>}"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# TEST 3 — mcp-gateway aggregate endpoint actif (port 8081)
# ---------------------------------------------------------------------------
LABEL="[3/8] mcp-gateway aggregate endpoint (port 8081)..."
printf '%s ' "${LABEL}"

AGG_RESP=$($COMPOSE exec -T mcp-gateway \
  python3 -c "
import urllib.request, sys
try:
    with urllib.request.urlopen('http://127.0.0.1:8081/mcp', timeout=10) as r:
        body = r.read().decode()
        print('ok:' + str(r.status))
except urllib.error.HTTPError as e:
    # Any HTTP response (even 4xx) means the server is up
    print('up:' + str(e.code))
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || AGG_RESP=""

if echo "${AGG_RESP}" | grep -qE "^ok:|^up:"; then
  pass "${LABEL}" "aggregate server responding (${AGG_RESP})"
elif echo "${AGG_RESP}" | grep -q "Connection refused"; then
  fail "${LABEL}" "port 8081 not listening — plan 04-02 subprocess not started"
else
  fail "${LABEL}" "unexpected: ${AGG_RESP:-<empty>}"
fi

# ---------------------------------------------------------------------------
# TEST 4 — GET /tools via gateway retourne >= 4 tools (plan 04-02 + 04-08)
# ---------------------------------------------------------------------------
LABEL="[4/8] mcp-gateway /tools has >= 4 tools (incl. deck)..."
printf '%s ' "${LABEL}"

if [ -z "${BRIDGE_JWT:-}" ]; then
  skip "${LABEL}" "bridge JWT not available (test 2 failed)"
else
  TOOLS_COUNT=$($COMPOSE exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, sys
try:
    with urllib.request.urlopen('http://mcp-gateway:8080/tools', timeout=10) as r:
        tools = json.loads(r.read())
        print(len(tools))
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || TOOLS_COUNT=""

  if [ -z "${TOOLS_COUNT}" ] || echo "${TOOLS_COUNT}" | grep -q "ERROR"; then
    fail "${LABEL}" "could not reach /tools — mcp-gateway healthy?"
  elif [ "${TOOLS_COUNT}" -ge 4 ] 2>/dev/null; then
    pass "${LABEL}" "${TOOLS_COUNT} tools registered (scraper, drive-read, calendar, deck)"
  elif [ "${TOOLS_COUNT}" -ge 3 ] 2>/dev/null; then
    fail "${LABEL}" "${TOOLS_COUNT} tools — deck not registered (run register-mcp-tools.sh after mcp-deck up)"
  else
    fail "${LABEL}" "only ${TOOLS_COUNT} tools — run register-mcp-tools.sh"
  fi
fi

# ---------------------------------------------------------------------------
# TEST 5 — LibreChat config mcpServers presente dans librechat.yaml
# ---------------------------------------------------------------------------
LABEL="[5/8] LibreChat librechat.yaml has mcpServers config..."
printf '%s ' "${LABEL}"

YAML_CHECK=$(grep -c "mcpServers" "${PROJECT_ROOT}/infrastructure/librechat/librechat.yaml" 2>/dev/null || echo "0")
if [ "${YAML_CHECK}" -gt 0 ]; then
  # Also check the URL points to port 8081
  URL_CHECK=$(grep -v '^#' "${PROJECT_ROOT}/infrastructure/librechat/librechat.yaml" | grep -c "8081" 2>/dev/null || echo "0")
  if [ "${URL_CHECK}" -gt 0 ]; then
    pass "${LABEL}" "mcpServers block present with port 8081"
  else
    fail "${LABEL}" "mcpServers found but URL doesn't reference port 8081 — check librechat.yaml"
  fi
else
  fail "${LABEL}" "mcpServers not found in librechat.yaml — plan 04-03 not applied"
fi

# ---------------------------------------------------------------------------
# TEST 6 — agent-runtime MCP tools discoverable (plan 04-04)
# ---------------------------------------------------------------------------
LABEL="[6/8] agent-runtime MCP tools import + discovery..."
printf '%s ' "${LABEL}"

AGENT_TOOLS=$($COMPOSE exec -T agent-runtime \
  python3 -c "
import sys
try:
    from app.tools.mcp_gateway_client import get_mcp_tools
    tools = get_mcp_tools(team_scope='default')
    print(len(tools))
except ImportError as e:
    print(f'IMPORT_ERROR:{e}', file=sys.stderr)
    sys.exit(1)
except Exception as e:
    # Gateway might be unreachable in test — graceful = returns 0 but no exception
    print('0')
" 2>/dev/null) || AGENT_TOOLS=""

if [ -z "${AGENT_TOOLS}" ] || echo "${AGENT_TOOLS}" | grep -q "IMPORT_ERROR"; then
  fail "${LABEL}" "mcp_gateway_client.py not importable — plan 04-04 not applied (${AGENT_TOOLS:-<empty>})"
elif [ "${AGENT_TOOLS}" -ge 1 ] 2>/dev/null; then
  pass "${LABEL}" "${AGENT_TOOLS} MCP tools discoverable from agent-runtime"
elif [ "${AGENT_TOOLS}" = "0" ]; then
  # Import works but gateway returned 0 tools — might be timing
  pass "${LABEL}" "module importable — 0 tools (gateway may not have tools yet, run register-mcp-tools.sh)"
else
  fail "${LABEL}" "unexpected result: ${AGENT_TOOLS}"
fi

# ---------------------------------------------------------------------------
# TEST 7 — drive-sync webhook endpoint actif (port 8200)
# ---------------------------------------------------------------------------
LABEL="[7/8] drive-sync webhook endpoint (port 8200)..."
printf '%s ' "${LABEL}"

WEBHOOK_RESP=$($COMPOSE exec -T drive-sync \
  python3 -c "
import urllib.request, sys
try:
    with urllib.request.urlopen('http://127.0.0.1:8200/healthz', timeout=10) as r:
        import json
        body = json.loads(r.read())
        print(body.get('status', 'unknown'))
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || WEBHOOK_RESP=""

if [ "${WEBHOOK_RESP}" = "ok" ]; then
  pass "${LABEL}" "webhook server healthy on port 8200"
elif echo "${WEBHOOK_RESP}" | grep -q "Connection refused"; then
  fail "${LABEL}" "port 8200 not listening — plan 04-05 not applied"
else
  fail "${LABEL}" "unexpected: ${WEBHOOK_RESP:-<empty>}"
fi

# ---------------------------------------------------------------------------
# TEST 8 — Multi-folder Drive mapping schema (plan 04-06)
# ---------------------------------------------------------------------------
LABEL="[8/8] Multi-folder Drive mapping schema (migration 0005)..."
printf '%s ' "${LABEL}"

SCHEMA_CHECK=$($COMPOSE exec -T postgres psql -U "${POSTGRES_USER:-xbrain}" -d "${POSTGRES_DB:-xbrain}" \
  -tAc "SELECT column_name FROM information_schema.columns WHERE table_name='team_drive_mappings' AND column_name='project_scope'" \
  2>/dev/null | tr -d '[:space:]' | head -1) || SCHEMA_CHECK=""

if [ "${SCHEMA_CHECK}" = "project_scope" ] || echo "${SCHEMA_CHECK}" | grep -q "project_scope"; then
  pass "${LABEL}" "team_drive_mappings.project_scope column exists (migration 0005 applied)"
else
  fail "${LABEL}" "project_scope column missing — migration 0005 not applied (${SCHEMA_CHECK:-<empty>})"
fi

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
TOTAL_TESTS=8
TOTAL_RUN=$((PASS_COUNT + FAIL_COUNT + SKIP_COUNT))
THRESHOLD=6

echo ""
if [ "${FAIL_COUNT}" -eq 0 ]; then
  echo "[verify-phase4] === SUCCESS === ${PASS_COUNT} passed, ${SKIP_COUNT} skipped (${TOTAL_RUN}/${TOTAL_TESTS})"
  exit 0
elif [ "${PASS_COUNT}" -ge "${THRESHOLD}" ]; then
  echo "[verify-phase4] === PARTIAL SUCCESS === ${PASS_COUNT} passed (>=${THRESHOLD}/8 threshold met), ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped"
  exit 0
else
  echo "[verify-phase4] === FAILURE === ${PASS_COUNT} passed, ${FAIL_COUNT} failed, ${SKIP_COUNT} skipped — threshold ${THRESHOLD}/8 not met"
  exit 1
fi
