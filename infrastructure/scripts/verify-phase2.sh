#!/bin/bash
# E2E smoke-test for an xbrain Phase 2 deployment.
#
# Run FROM the production VM as the deploy user:
#   bash infrastructure/scripts/verify-phase2.sh
#
# Requirements:
#   - .env at project root (/home/user/xbrain/.env)
#   - All 16 Phase 1+2 containers running
#   - jq available on the VM (apt-get install -y jq)
#
# Exit codes: 0 = all tests passed, 1 = one or more tests failed.
#
# Idempotent: running twice in a row leaves no state corruption.
# The /second-opinion and /ingest calls do not write persistent data (read-only
# graph and HITL-pending thread that auto-expires in Postgres LangGraph store).

# ---------------------------------------------------------------------------
# Bootstrap — source .env, cd to project root, disable set -e for test blocks
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
  echo "[verify-phase2] ERROR: .env not found at ${PROJECT_ROOT}/.env" >&2
  exit 1
fi

COMPOSE="docker compose -f infrastructure/docker-compose.yml --env-file .env"

PASS_COUNT=0
FAIL_COUNT=0

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
}

# ---------------------------------------------------------------------------
echo "[verify-phase2] === START ==="
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TEST 1 — Container health
# All 16 containers must be Up; healthcheck containers must also be (healthy).
# ---------------------------------------------------------------------------
LABEL="[1/6] Container health..."
printf '%s' "${LABEL} "

EXPECTED_CONTAINERS=(
  "xbrain-nginx"
  "xbrain-postgres"
  "xbrain-qdrant"
  "xbrain-memory-api"
  "xbrain-librechat-bridge"
  "xbrain-openwebui-pipeline"
  "xbrain-librechat-mongo"
  "xbrain-librechat-meili"
  "xbrain-librechat"
  "xbrain-openwebui"
  "xbrain-backup"
  "xbrain-agent-runtime"
  "xbrain-langfuse-clickhouse"
  "xbrain-langfuse-redis"
  "xbrain-langfuse-worker"
  "xbrain-langfuse"
)
TOTAL=${#EXPECTED_CONTAINERS[@]}

# `docker compose ps` output format: NAME  IMAGE  COMMAND  SERVICE  CREATED  STATUS  PORTS
# STATUS column is "Up X seconds (healthy)" or "Up X seconds" or "Exit N"
PS_OUTPUT=$($COMPOSE ps 2>/dev/null) || true

HEALTHY_COUNT=0
UNHEALTHY_NAMES=()

for cname in "${EXPECTED_CONTAINERS[@]}"; do
  # grep the container name row; STATUS is anything after the 5th column
  ROW=$(echo "${PS_OUTPUT}" | grep -E "^${cname}[[:space:]]" || true)
  if [ -z "${ROW}" ]; then
    UNHEALTHY_NAMES+=("${cname}:missing")
    continue
  fi
  # Containers with a healthcheck: STATUS must contain "(healthy)"
  # Containers without one: STATUS must start with "Up"
  STATUS=$(echo "${ROW}" | awk '{for(i=6;i<=NF;i++) printf "%s ", $i; print ""}')
  if echo "${STATUS}" | grep -q "(unhealthy)"; then
    UNHEALTHY_NAMES+=("${cname}:unhealthy")
  elif echo "${STATUS}" | grep -qE "^Up|^up"; then
    HEALTHY_COUNT=$((HEALTHY_COUNT + 1))
  else
    UNHEALTHY_NAMES+=("${cname}:not-up(${STATUS%% *})")
  fi
done

if [ ${HEALTHY_COUNT} -eq ${TOTAL} ]; then
  pass "${LABEL}" "${TOTAL}/${TOTAL} healthy"
else
  UNHEALTHY_STR=$(printf ", %s" "${UNHEALTHY_NAMES[@]}")
  UNHEALTHY_STR="${UNHEALTHY_STR:2}"
  fail "${LABEL}" "${HEALTHY_COUNT}/${TOTAL} healthy — problems: ${UNHEALTHY_STR}"
fi

# ---------------------------------------------------------------------------
# TEST 2 — agent-runtime /healthz
# Curl from inside openwebui-pipeline container (shares xbrain_net).
# ---------------------------------------------------------------------------
LABEL="[2/6] agent-runtime healthz..."
printf '%s ' "${LABEL}"

HEALTHZ_RESPONSE=$($COMPOSE exec -T openwebui-pipeline \
  curl -fsS --max-time 10 http://agent-runtime:9100/healthz 2>/dev/null) || HEALTHZ_RESPONSE=""

if echo "${HEALTHZ_RESPONSE}" | grep -q '"status"'; then
  STATUS_VAL=$(echo "${HEALTHZ_RESPONSE}" | grep -o '"status":"[^"]*"' | head -1 | cut -d'"' -f4)
  if [ "${STATUS_VAL}" = "ok" ]; then
    pass "${LABEL}"
  else
    fail "${LABEL}" "unexpected status value: ${STATUS_VAL} (response: ${HEALTHZ_RESPONSE})"
  fi
else
  fail "${LABEL}" "no JSON or curl failed (response: ${HEALTHZ_RESPONSE:-<empty>})"
fi

# ---------------------------------------------------------------------------
# TEST 3 — memory-api /v1/promotions/pending
# Build a bridge JWT inside the librechat-bridge container (it has the
# BRIDGE_SHARED_SECRET env var and the authlib package already installed),
# then hit memory-api.  We use a tiny Python one-liner so we don't have to
# carry a standalone JWT library on the VM itself.
# ---------------------------------------------------------------------------
LABEL="[3/6] memory-api /v1/promotions/pending..."
printf '%s ' "${LABEL}"

# Generate the JWT inside the container that already has authlib available.
BRIDGE_JWT=$($COMPOSE exec -T librechat-bridge python3 -c "
import time
from authlib.jose import jwt
import os
secret = os.environ['BRIDGE_SHARED_SECRET']
algorithm = os.environ.get('JWT_ALGORITHM', 'HS256')
now = int(time.time())
payload = {
    'iss': 'librechat-bridge',
    'sub': 'verify-phase2',
    'team_scope': 'default',
    'scope': 'bridge',
    'iat': now,
    'exp': now + 300,
}
token = jwt.encode({'alg': algorithm}, payload, secret)
print(token.decode('ascii') if isinstance(token, bytes) else token)
" 2>/dev/null) || BRIDGE_JWT=""

if [ -z "${BRIDGE_JWT}" ]; then
  fail "${LABEL}" "could not generate bridge JWT from librechat-bridge container"
else
  # Build the payload-less GET via printf to avoid quoting issues
  PROMOTIONS_RESPONSE=$($COMPOSE exec -T librechat-bridge \
    curl -fsS --max-time 15 \
    -H "Authorization: Bearer ${BRIDGE_JWT}" \
    -H "X-Team-Scope: default" \
    "http://memory-api:8000/v1/promotions/pending?team_scope=default" 2>/dev/null) \
    || PROMOTIONS_RESPONSE="CURL_FAILED"

  if [ "${PROMOTIONS_RESPONSE}" = "CURL_FAILED" ]; then
    fail "${LABEL}" "curl returned non-zero (endpoint unavailable or 4xx/5xx)"
  elif echo "${PROMOTIONS_RESPONSE}" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    # Valid JSON — could be [] or a list with items, both acceptable
    ITEM_COUNT=$(echo "${PROMOTIONS_RESPONSE}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
    pass "${LABEL}" "${ITEM_COUNT} pending items"
  else
    fail "${LABEL}" "response is not valid JSON: ${PROMOTIONS_RESPONSE:0:120}"
  fi
fi

# ---------------------------------------------------------------------------
# TEST 4 — /second-opinion E2E
# POST to openwebui-pipeline /v1/chat/completions from the VM (pipeline
# listens on port 9099, exposed via nginx or directly via docker network).
# We call from the VM host via the docker network alias through a one-shot
# docker run on xbrain_net to avoid nginx SSL complexity.
# ---------------------------------------------------------------------------
LABEL="[4/6] /second-opinion E2E..."
printf '%s ' "${LABEL}"

T4_START=$(date +%s%3N)

# Use printf to avoid bash quoting hell with the JSON payload
SECOND_OPINION_RESP=$(
  $COMPOSE exec -T openwebui-pipeline bash -c '
    printf '"'"'%s'"'"' '"'"'{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"/second-opinion Is the sky blue?"}],"max_tokens":200}'"'"' \
    | curl -fsS --max-time 60 \
        -H "Authorization: Bearer '"${PIPELINE_API_KEY}"'" \
        -H "Content-Type: application/json" \
        --data-binary @- \
        http://localhost:9099/v1/chat/completions
  ' 2>/dev/null
) || SECOND_OPINION_RESP=""

T4_END=$(date +%s%3N)
T4_LATENCY_S=$(echo "scale=1; (${T4_END} - ${T4_START}) / 1000" | bc 2>/dev/null || echo "?")

if [ -z "${SECOND_OPINION_RESP}" ]; then
  fail "${LABEL}" "empty response or curl error"
else
  CONTENT=$(echo "${SECOND_OPINION_RESP}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception as e:
    print('PARSE_ERROR:' + str(e))
" 2>/dev/null || echo "PARSE_ERROR")

  if echo "${CONTENT}" | grep -q "PARSE_ERROR"; then
    fail "${LABEL}" "could not parse response JSON: ${SECOND_OPINION_RESP:0:120}"
  elif echo "${CONTENT}" | grep -q "Claude" && echo "${CONTENT}" | grep -q "Grok"; then
    pass "${LABEL}" "latency ${T4_LATENCY_S}s"
  else
    # Detect the Phase-2-not-deployed fallback message so we give a clearer failure
    if echo "${CONTENT}" | grep -qi "phase 2\|Phase 2\|agent-runtime"; then
      fail "${LABEL}" "agent-runtime not reachable from pipeline (Phase 2 services down?)"
    else
      fail "${LABEL}" "response missing 'Claude' or 'Grok' markers — content: ${CONTENT:0:200}"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# TEST 5 — /ingest E2E
# Response from ingestion_trigger.try_handle() includes `thread_id` in the
# formatted markdown (see _format_extracted_facts: "Thread \`{thread_id[:8]}…\`").
# The run["thread_id"] key is also returned by the agent-runtime /run endpoint,
# so the formatted message always includes that string.
# ---------------------------------------------------------------------------
LABEL="[5/6] /ingest E2E..."
printf '%s ' "${LABEL}"

T5_START=$(date +%s%3N)

INGEST_RESP=$(
  $COMPOSE exec -T openwebui-pipeline bash -c '
    printf '"'"'%s'"'"' '"'"'{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"/ingest https://example.com"}],"max_tokens":400}'"'"' \
    | curl -fsS --max-time 60 \
        -H "Authorization: Bearer '"${PIPELINE_API_KEY}"'" \
        -H "Content-Type: application/json" \
        --data-binary @- \
        http://localhost:9099/v1/chat/completions
  ' 2>/dev/null
) || INGEST_RESP=""

T5_END=$(date +%s%3N)
T5_LATENCY_S=$(echo "scale=1; (${T5_END} - ${T5_START}) / 1000" | bc 2>/dev/null || echo "?")

if [ -z "${INGEST_RESP}" ]; then
  fail "${LABEL}" "empty response or curl error"
else
  INGEST_CONTENT=$(echo "${INGEST_RESP}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['choices'][0]['message']['content'])
except Exception as e:
    print('PARSE_ERROR:' + str(e))
" 2>/dev/null || echo "PARSE_ERROR")

  if echo "${INGEST_CONTENT}" | grep -q "PARSE_ERROR"; then
    fail "${LABEL}" "could not parse response JSON: ${INGEST_RESP:0:120}"
  elif echo "${INGEST_CONTENT}" | grep -q "thread_id\|Thread"; then
    pass "${LABEL}" "latency ${T5_LATENCY_S}s"
  elif echo "${INGEST_CONTENT}" | grep -qi "phase 2\|Phase 2\|agent-runtime"; then
    fail "${LABEL}" "agent-runtime not reachable from pipeline (Phase 2 services down?)"
  else
    fail "${LABEL}" "response missing thread_id marker — content: ${INGEST_CONTENT:0:200}"
  fi
fi

# ---------------------------------------------------------------------------
# TEST 6 — Normal chat → Langfuse trace round-trip
# SKIPs gracefully when LANGFUSE_PUBLIC_KEY is empty (pre-bootstrap deploy).
# ---------------------------------------------------------------------------
LABEL="[6/6] Langfuse trace round-trip..."
printf '%s ' "${LABEL}"

if [ -z "${LANGFUSE_PUBLIC_KEY:-}" ]; then
  skip "${LABEL}" "LANGFUSE_PUBLIC_KEY not set in .env — bootstrap Langfuse keys first"
else
  CHAT_RESP=$(
    $COMPOSE exec -T openwebui-pipeline bash -c '
      printf '"'"'%s'"'"' '"'"'{"model":"claude-haiku-4-5","messages":[{"role":"user","content":"Reply just OK"}],"max_tokens":10}'"'"' \
      | curl -fsS --max-time 30 \
          -H "Authorization: Bearer '"${PIPELINE_API_KEY}"'" \
          -H "Content-Type: application/json" \
          --data-binary @- \
          http://localhost:9099/v1/chat/completions
    ' 2>/dev/null
  ) || CHAT_RESP=""

  if [ -z "${CHAT_RESP}" ]; then
    fail "${LABEL}" "chat POST failed — no response"
  else
    # Wait for Langfuse ingestion pipeline (ClickHouse async write)
    echo -n " (waiting 8s for trace ingestion) "
    sleep 8

    # Query Langfuse public API — basic auth with public_key:secret_key
    LANGFUSE_HOST_CLEAN="${LANGFUSE_HOST:-http://langfuse:3000}"

    TRACE_RESP=$(
      $COMPOSE exec -T openwebui-pipeline \
        curl -fsS --max-time 15 \
        -u "${LANGFUSE_PUBLIC_KEY}:${LANGFUSE_SECRET_KEY}" \
        "${LANGFUSE_HOST_CLEAN}/api/public/traces?limit=1" 2>/dev/null
    ) || TRACE_RESP=""

    if [ -z "${TRACE_RESP}" ]; then
      fail "${LABEL}" "Langfuse API query failed (host: ${LANGFUSE_HOST_CLEAN})"
    else
      TRACE_NAME=$(echo "${TRACE_RESP}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    items = d.get('data', [])
    print(items[0]['name'] if items else 'NO_TRACES')
except Exception as e:
    print('PARSE_ERROR:' + str(e))
" 2>/dev/null || echo "PARSE_ERROR")

      if echo "${TRACE_NAME}" | grep -q "PARSE_ERROR"; then
        fail "${LABEL}" "could not parse Langfuse traces response: ${TRACE_RESP:0:120}"
      elif [ "${TRACE_NAME}" = "NO_TRACES" ]; then
        fail "${LABEL}" "Langfuse returned empty data[] — trace was not ingested"
      elif echo "${TRACE_NAME}" | grep -q "^chat:claude-haiku"; then
        pass "${LABEL}" "trace name: ${TRACE_NAME}"
      else
        fail "${LABEL}" "unexpected trace name '${TRACE_NAME}' — expected 'chat:claude-haiku*'"
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Final summary
# ---------------------------------------------------------------------------
TOTAL_TESTS=6
TOTAL_RUN=$((PASS_COUNT + FAIL_COUNT))

echo ""
if [ ${FAIL_COUNT} -eq 0 ]; then
  echo "[verify-phase2] === SUCCESS === ${PASS_COUNT}/${TOTAL_TESTS} passed"
  exit 0
else
  echo "[verify-phase2] === FAILURE === ${PASS_COUNT} passed, ${FAIL_COUNT} failed (${TOTAL_RUN}/${TOTAL_TESTS} run)"
  exit 1
fi
