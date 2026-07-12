#!/usr/bin/env bash
#
# verify-phase8.sh — Verify Phase 8 (Granola per-user + Universal extraction + Platform agents) deliverables.
#
# Tests:
#   1. Migration 0012 applied + tables granola_user_connections + agent_definitions present (criterion 5)
#   2. granola_user_connections has FK to users(id) (criterion 1)
#   3. agent_definitions seeded with meeting-recap (criteria 5+6)
#   4. POST /v1/admin/agents responds with auth error (route registered) (criterion 5)
#   5. POST /v1/agents/{id}/invoke responds with auth error (route registered) (criterion 6)
#   6. POST /v1/me/granola-key responds with auth error (route registered) (criterion 1)
#   7. GET /v1/github/repos responds with auth error (route registered) (criterion 7)
#
# Usage: bash infrastructure/scripts/verify-phase8.sh
# Run from repo root on the VM where docker compose is running.

set -uo pipefail

TEST_TOTAL=7
TEST_PASSED=0
FAILURES=()

DB_CONTAINER="${DB_CONTAINER:-xbrain-postgres}"
MEMAPI_HOST="${MEMAPI_HOST:-http://localhost:8000}"

pass() { echo "  PASS: $1"; TEST_PASSED=$((TEST_PASSED + 1)); }
fail() { echo "  FAIL: $1 — $2"; FAILURES+=("$1: $2"); }

run_psql() {
  docker exec -i "$DB_CONTAINER" psql -U xbrain -d xbrain -tAc "$1" 2>/dev/null
}

echo "=== Phase 8 Verification ==="

# Test 1: Migration 0012 applied + 2 tables present
echo
echo "[1/7] Migration 0012 applied + tables present"
ver=$(run_psql "SELECT version_num FROM alembic_version" || true)
guc_tbl=$(run_psql "SELECT to_regclass('granola_user_connections')::text" || true)
ad_tbl=$(run_psql "SELECT to_regclass('agent_definitions')::text" || true)
if { [[ "$ver" > "0011" ]] || [[ "$ver" == "0012" ]]; } \
   && [ "$guc_tbl" = "granola_user_connections" ] \
   && [ "$ad_tbl" = "agent_definitions" ]; then
  pass "alembic_version >= 0012 (got: $ver) + granola_user_connections + agent_definitions"
else
  fail "Migration 0012 incomplete" "version='$ver' guc='$guc_tbl' ad='$ad_tbl'"
fi

# Test 2: granola_user_connections has FK to users(id)
echo
echo "[2/7] granola_user_connections FK to users"
guc_fk=$(run_psql "SELECT conname FROM pg_constraint WHERE conrelid='granola_user_connections'::regclass AND confrelid='users'::regclass LIMIT 1" || true)
if [ -n "$guc_fk" ]; then
  pass "FK present (conname='$guc_fk')"
else
  fail "FK granola_user_connections → users missing" "no row in pg_constraint"
fi

# Test 3: agent_definitions seeded with meeting-recap
echo
echo "[3/7] agent_definitions seeded with meeting-recap"
seed_count=$(run_psql "SELECT count(*) FROM agent_definitions WHERE name='meeting-recap'" || true)
if [ "$seed_count" = "1" ]; then
  pass "meeting-recap row present"
else
  fail "meeting-recap not seeded" "count='$seed_count' (expected 1)"
fi

# Test 4: POST /v1/admin/agents responds with auth error (route registered)
echo
echo "[4/7] POST /v1/admin/agents endpoint registered"
http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MEMAPI_HOST/v1/admin/agents" 2>/dev/null || echo "000")
case "$http" in
  401|403|422) pass "/v1/admin/agents responds ($http — route exists)";;
  *) fail "/v1/admin/agents routing" "got HTTP $http (expected 401/403/422 — route should exist)";;
esac

# Test 5: POST /v1/agents/{id}/invoke responds with auth error
echo
echo "[5/7] POST /v1/agents/{id}/invoke endpoint registered"
DUMMY_UUID="00000000-0000-0000-0000-000000000000"
http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MEMAPI_HOST/v1/agents/${DUMMY_UUID}/invoke" 2>/dev/null || echo "000")
case "$http" in
  401|403|404|422) pass "/v1/agents/{id}/invoke responds ($http — route exists)";;
  *) fail "/v1/agents/{id}/invoke routing" "got HTTP $http (expected 401/403/404/422 — route should exist)";;
esac

# Test 6: POST /v1/me/granola-key responds with auth error
echo
echo "[6/7] POST /v1/me/granola-key endpoint registered"
http=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$MEMAPI_HOST/v1/me/granola-key" 2>/dev/null || echo "000")
case "$http" in
  401|403|422) pass "/v1/me/granola-key responds ($http — route exists)";;
  *) fail "/v1/me/granola-key routing" "got HTTP $http (expected 401/403/422 — route should exist)";;
esac

# Test 7: GET /v1/github/repos responds with auth error
echo
echo "[7/7] GET /v1/github/repos endpoint registered"
http=$(curl -s -o /dev/null -w "%{http_code}" "$MEMAPI_HOST/v1/github/repos" 2>/dev/null || echo "000")
case "$http" in
  401|403|422|503) pass "/v1/github/repos responds ($http — route exists)";;
  *) fail "/v1/github/repos routing" "got HTTP $http (expected 401/403/422/503 — route should exist)";;
esac

# Summary
echo
echo "==========================================="
echo "PASS: $TEST_PASSED / $TEST_TOTAL"
echo "==========================================="

if [ "$TEST_PASSED" -eq "$TEST_TOTAL" ]; then
  echo "Phase 8 verification: ALL PASS"
  exit 0
else
  echo "Phase 8 verification: FAILURES"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
