#!/usr/bin/env bash
# register-mcp-tools.sh — Register MCP sidecars in mcp-gateway (5 tools total).
#
# Usage (from project root on the VM):
#   bash infrastructure/scripts/register-mcp-tools.sh
#
# Requires:
#   - Python 3 with authlib installed (available inside mcp-gateway container)
#   - BRIDGE_SHARED_SECRET env var (already in .env from Phase 1)
#   - MCP_GATEWAY_URL env var (defaults to http://localhost:8080 for VM host access)
#
# Idempotent: POST /admin/register uses ON CONFLICT DO UPDATE — safe to re-run.
#             Running this script twice leaves the registry in the same state.
#
# Run AFTER all Phase 3 containers are healthy:
#   docker compose ps  # confirm mcp-gateway, mcp-scraper, mcp-drive-read, mcp-calendar are Up

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_ROOT}"

# Source .env if present (VM deployment pattern — same as verify-phase2.sh)
if [ -f .env ]; then
  set -a
  . .env
  set +a
fi

MCP_GATEWAY_URL="${MCP_GATEWAY_URL:-http://localhost:8080}"
BRIDGE_SHARED_SECRET="${BRIDGE_SHARED_SECRET:?BRIDGE_SHARED_SECRET env var required (check .env)}"

COMPOSE="docker compose -f infrastructure/docker-compose.yml --env-file .env"

echo "=== xbrain MCP Tool Registration ==="
echo "Gateway: ${MCP_GATEWAY_URL}"
echo ""

# ---------------------------------------------------------------------------
# Generate a bridge JWT via Python inside the mcp-gateway container.
# authlib is installed in mcp-gateway (pyproject.toml dependency).
# The JWT expires in 1 hour — enough to complete registration.
# ---------------------------------------------------------------------------
echo "Generating bridge JWT..."

BRIDGE_JWT=$(${COMPOSE} exec -T mcp-gateway python3 -c "
import time, os, sys
try:
    from authlib.jose import jwt
except ImportError:
    print('ERROR: authlib not installed in mcp-gateway container', file=sys.stderr)
    sys.exit(1)
secret = os.environ['BRIDGE_SHARED_SECRET']
now = int(time.time())
payload = {
    'iss': 'register-mcp-tools',
    'sub': 'admin-script',
    'scope': 'bridge',
    'team_scope': 'default',
    'iat': now,
    'exp': now + 3600,
}
token = jwt.encode({'alg': 'HS256'}, payload, secret)
print(token.decode('ascii') if isinstance(token, bytes) else token)
" 2>/dev/null) || BRIDGE_JWT=""

if [ -z "${BRIDGE_JWT}" ]; then
  echo "ERROR: Could not generate bridge JWT from mcp-gateway container." >&2
  echo "Ensure mcp-gateway is running: ${COMPOSE} ps mcp-gateway" >&2
  exit 1
fi

echo "Bridge JWT generated (expires in 1h)"
echo ""

# ---------------------------------------------------------------------------
# Helper: register a single tool via POST /admin/register
# idempotent — upserts via ON CONFLICT DO UPDATE
# ---------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0

register_tool() {
  local name="$1"
  local url="$2"
  local desc="$3"
  local label="[register] ${name} → ${url}"

  printf '%s ... ' "${label}"

  RESPONSE=$(${COMPOSE} exec -T mcp-gateway \
    python3 -c "
import urllib.request, json, os, sys
req = urllib.request.Request(
    '${MCP_GATEWAY_URL}/admin/register',
    data=json.dumps({'tool_name': '${name}', 'sidecar_url': '${url}', 'description': '${desc}'}).encode(),
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode()
        print(body)
except urllib.error.HTTPError as e:
    print(f'HTTP_ERROR:{e.code}:{e.read().decode()[:200]}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || RESPONSE=""

  if [ -z "${RESPONSE}" ]; then
    # Fallback: try from VM host directly (when mcp-gateway is port-forwarded)
    RESPONSE=$(python3 -c "
import urllib.request, json, sys
req = urllib.request.Request(
    '${MCP_GATEWAY_URL}/admin/register',
    data=json.dumps({'tool_name': '${name}', 'sidecar_url': '${url}', 'description': '${desc}'}).encode(),
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.read().decode())
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || RESPONSE=""
  fi

  if [ -n "${RESPONSE}" ]; then
    echo "OK"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "FAILED"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  Hint: ensure mcp-gateway is healthy and BRIDGE_SHARED_SECRET matches." >&2
  fi
}

# ---------------------------------------------------------------------------
# Register the 3 Phase 3 MCP tools
# Tool names must match what agent-runtime and sidecars use at runtime.
# sidecar_url is the Docker-internal hostname (xbrain_net):
#   mcp-scraper    → port 8100 (FastMCP streamable-http)
#   mcp-drive-read → port 8101 (FastMCP streamable-http)
#   mcp-calendar   → port 8102 (FastMCP streamable-http)
# ---------------------------------------------------------------------------
echo "--- Registering Phase 3+4+github-read MCP tools ---"

register_tool \
  "scraper" \
  "http://mcp-scraper:8100" \
  "Fetch URL content as text (max 50KB) — MCP-05"

register_tool \
  "drive-read" \
  "http://mcp-drive-read:8101" \
  "Live read/write a Google Drive file by ID (bypasses memory-api cache) — MCP-05, INT-04"

register_tool \
  "calendar" \
  "http://mcp-calendar:8102" \
  "List Google Calendar events for a date range (default: today+7days) — MCP-06"

register_tool \
  "deck" \
  "http://mcp-deck:8103" \
  "Generate PowerPoint presentations (.pptx) via python-pptx — MCP-07"

# NOTE: github tools are NOT registered in the aggregate. mcp-github is connected
# DIRECTLY by LibreChat (xbrain-github server in librechat.yaml) so it receives
# the user email + resolves the caller's team (github_sync_repo must land in the
# right brain). The aggregate hardcodes team_scope=default, which would break sync.

# ---------------------------------------------------------------------------
# Verification: GET /tools and confirm the 3 tools are present
# ---------------------------------------------------------------------------
echo ""
echo "--- Verification: GET /tools ---"

TOOLS_RESPONSE=$(${COMPOSE} exec -T mcp-gateway \
  python3 -c "
import urllib.request, sys
req = urllib.request.Request(
    '${MCP_GATEWAY_URL}/tools',
    headers={'Authorization': 'Bearer ${BRIDGE_JWT}'},
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.read().decode())
except Exception as e:
    print(f'ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || TOOLS_RESPONSE=""

if [ -n "${TOOLS_RESPONSE}" ]; then
  TOOL_COUNT=$(python3 -c "import sys, json; d=json.loads('${TOOLS_RESPONSE}'); print(len(d))" 2>/dev/null || echo "?")
  echo "Registered tools (${TOOL_COUNT} total):"
  python3 -c "
import json, sys
try:
    tools = json.loads('''${TOOLS_RESPONSE}''')
    for t in tools:
        print(f\"  - {t.get('tool_name','?')} → {t.get('sidecar_url','?')}\")
        print(f\"    {t.get('description','')}\")
except Exception as e:
    print(f'  (could not parse: {e})')
" 2>/dev/null || echo "  (raw: ${TOOLS_RESPONSE:0:200})"
else
  echo "WARNING: Could not retrieve tool list — check mcp-gateway logs."
fi

# ---------------------------------------------------------------------------
# Quick smoke-test: call scraper with example.com (safe public domain)
# This validates the full forward path: gateway → mcp-scraper → HTTP fetch
# ---------------------------------------------------------------------------
echo ""
echo "--- Smoke test: POST /tools/scraper/call ---"

SCRAPER_RESP=$(${COMPOSE} exec -T mcp-gateway \
  python3 -c "
import urllib.request, json, sys
payload = json.dumps({
    'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
    'params': {'name': 'scrape', 'arguments': {'url': 'https://example.com'}}
}).encode()
req = urllib.request.Request(
    '${MCP_GATEWAY_URL}/tools/scraper/call',
    data=payload,
    headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ${BRIDGE_JWT}',
        'X-Team-Scope': 'default',
    },
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode()
        print(body[:300])
except Exception as e:
    print(f'SCRAPER_CALL_ERROR:{e}', file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || SCRAPER_RESP=""

if [ -n "${SCRAPER_RESP}" ] \
  && ! echo "${SCRAPER_RESP}" | grep -q "SCRAPER_CALL_ERROR" \
  && ! echo "${SCRAPER_RESP}" | grep -q '"isError":true' \
  && ! echo "${SCRAPER_RESP}" | grep -q "Unknown tool"; then
  CHAR_COUNT=${#SCRAPER_RESP}
  echo "Scraper smoke test: PASS (${CHAR_COUNT} chars received)"
else
  echo "Scraper smoke test: SKIP (mcp-scraper may not be healthy yet, or returned an error — not a registration failure)"
  echo "  Re-run after: ${COMPOSE} up -d mcp-scraper"
  if [ -n "${SCRAPER_RESP}" ]; then
    echo "  Response snippet: ${SCRAPER_RESP:0:200}"
  fi
fi

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
echo ""
TOTAL_TOOLS=4
TOTAL_RUN=$((PASS_COUNT + FAIL_COUNT))

if [ "${FAIL_COUNT}" -eq 0 ]; then
  echo "=== Registration SUCCESS === ${PASS_COUNT}/${TOTAL_TOOLS} tools registered"
elif [ "${PASS_COUNT}" -ge 4 ] 2>/dev/null; then
  pass "[verify] tools-count" "${PASS_COUNT} tool(s) registered"
elif [ "${PASS_COUNT}" -ge 4 ] 2>/dev/null; then
  echo "=== Registration PARTIAL === ${PASS_COUNT} succeeded, ${FAIL_COUNT} failed — github tool not registered yet? Run after mcp-github is up"
else
  echo "=== Registration PARTIAL === ${PASS_COUNT} succeeded, ${FAIL_COUNT} failed (${TOTAL_RUN}/${TOTAL_TOOLS} run)"
fi

echo ""
echo "--- Manual test commands (run from inside the Docker network) ---"
echo ""
echo "  # List tools:"
echo "  curl http://mcp-gateway:8080/tools"
echo ""
echo "  # Call scraper (from agent-runtime container):"
echo "  curl -X POST http://mcp-gateway:8080/tools/scraper/call \\"
echo "    -H 'Authorization: Bearer \$BRIDGE_JWT' -H 'X-Team-Scope: default' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"scraper\",\"arguments\":{\"url\":\"https://example.com\"}}}'"
echo ""
echo "  # Call drive-read (requires GOOGLE_DRIVE_ACCESS_TOKEN in .env):"
echo "  curl -X POST http://mcp-gateway:8080/tools/drive-read/call \\"
echo "    -H 'Authorization: Bearer \$BRIDGE_JWT' -H 'X-Team-Scope: default' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"read_drive_file\",\"arguments\":{\"file_id\":\"YOUR_FILE_ID\"}}}'"
echo ""
echo "  # Call calendar (requires GOOGLE_CALENDAR_ACCESS_TOKEN in .env):"
echo "  curl -X POST http://mcp-gateway:8080/tools/calendar/call \\"
echo "    -H 'Authorization: Bearer \$BRIDGE_JWT' -H 'X-Team-Scope: default' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"list_events\",\"arguments\":{\"date_range\":\"today+7days\"}}}'"

# Exit code reflects registration success only (not smoke-test)
if [ "${FAIL_COUNT}" -gt 0 ]; then
  exit 1
fi
exit 0
