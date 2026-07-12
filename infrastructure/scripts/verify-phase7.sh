#!/usr/bin/env bash
#
# verify-phase7.sh — Verify Phase 7 (CRM + Granola + Task Intelligence) deliverables.
#
# Tests:
#   1. Migration 0010 applied (alembic_version = 0010)
#   2. teams.plan column exists with check constraint
#   3. contacts table exists with type CHECK
#   4. tasks table exists with FK to contacts
#   5. granola_integrations table exists
#   6. Container xbrain-granola-sync is running and healthy
#   7. Endpoint /v1/tasks responds (expects 401/403 — auth required)
#   8. Endpoint /v1/crm/contacts responds (expects 401/403 — auth required)
#
# Usage: bash infrastructure/scripts/verify-phase7.sh
# Run from repo root on the VM where docker compose is running.

set -uo pipefail   # NOT -e — we want all tests to run even if some fail

TEST_TOTAL=8
TEST_PASSED=0
FAILURES=()

DB_CONTAINER="${DB_CONTAINER:-xbrain-postgres}"
GRANOLA_CONTAINER="${GRANOLA_CONTAINER:-xbrain-granola-sync}"
MEMAPI_HOST="${MEMAPI_HOST:-http://localhost:8000}"

pass() { echo "  PASS: $1"; TEST_PASSED=$((TEST_PASSED + 1)); }
fail() { echo "  FAIL: $1 — $2"; FAILURES+=("$1: $2"); }

run_psql() {
  docker exec -i "$DB_CONTAINER" psql -U xbrain -d xbrain -tAc "$1" 2>/dev/null
}

echo "=== Phase 7 Verification ==="

# Test 1: alembic_version = 0010
echo
echo "[1/8] Migration 0010 applied"
ver=$(run_psql "SELECT version_num FROM alembic_version" || true)
# Accept 0010 or any later migration (DB may have advanced in subsequent phases)
if [[ "$ver" > "0009" ]] || [[ "$ver" == "0010" ]]; then
  pass "alembic_version >= 0010 (got: $ver)"
else
  fail "Migration not at 0010" "got: '$ver'"
fi

# Test 2: teams.plan column with CHECK constraint
echo
echo "[2/8] teams.plan column + CHECK"
plan_col=$(run_psql "SELECT column_name FROM information_schema.columns WHERE table_name='teams' AND column_name='plan'" || true)
plan_chk=$(run_psql "SELECT conname FROM pg_constraint WHERE conname='teams_plan_check'" || true)
if [ "$plan_col" = "plan" ] && [ "$plan_chk" = "teams_plan_check" ]; then
  pass "teams.plan column + CHECK present"
else
  fail "teams.plan missing" "col='$plan_col' chk='$plan_chk'"
fi

# Test 3: contacts table with type CHECK
echo
echo "[3/8] contacts table + type CHECK"
contacts_tbl=$(run_psql "SELECT to_regclass('contacts')::text" || true)
contacts_chk=$(run_psql "SELECT conname FROM pg_constraint WHERE conname='contacts_type_check'" || true)
if [ "$contacts_tbl" = "contacts" ] && [ "$contacts_chk" = "contacts_type_check" ]; then
  pass "contacts table + CHECK present"
else
  fail "contacts table missing" "tbl='$contacts_tbl' chk='$contacts_chk'"
fi

# Test 4: tasks table with FK to contacts
echo
echo "[4/8] tasks table + FK to contacts"
tasks_tbl=$(run_psql "SELECT to_regclass('tasks')::text" || true)
tasks_fk=$(run_psql "SELECT conname FROM pg_constraint WHERE conrelid='tasks'::regclass AND confrelid='contacts'::regclass LIMIT 1" || true)
if [ "$tasks_tbl" = "tasks" ] && [ -n "$tasks_fk" ]; then
  pass "tasks table + FK contacts present"
else
  fail "tasks table or FK missing" "tbl='$tasks_tbl' fk='$tasks_fk'"
fi

# Test 5: granola_integrations table
echo
echo "[5/8] granola_integrations table"
gi_tbl=$(run_psql "SELECT to_regclass('granola_integrations')::text" || true)
if [ "$gi_tbl" = "granola_integrations" ]; then
  pass "granola_integrations present"
else
  fail "granola_integrations missing" "tbl='$gi_tbl'"
fi

# Test 6: granola-sync container running + healthy
echo
echo "[6/8] xbrain-granola-sync running + healthy"
status=$(docker inspect -f '{{.State.Status}}' "$GRANOLA_CONTAINER" 2>/dev/null || echo "absent")
health=$(docker inspect -f '{{.State.Health.Status}}' "$GRANOLA_CONTAINER" 2>/dev/null || echo "none")
if [ "$status" = "running" ] && [ "$health" = "healthy" ]; then
  pass "granola-sync running + healthy"
else
  fail "granola-sync state" "status='$status' health='$health'"
fi

# Test 7: /v1/tasks endpoint responds with auth error (401/403)
echo
echo "[7/8] /v1/tasks endpoint registered"
http=$(curl -s -o /dev/null -w "%{http_code}" "$MEMAPI_HOST/v1/tasks" 2>/dev/null || echo "000")
case "$http" in
  401|403|422) pass "/v1/tasks responds ($http — route exists)";;
  *) fail "/v1/tasks routing" "got HTTP $http (expected 401/403/422 — route should exist)";;
esac

# Test 8: /v1/crm/contacts endpoint responds with auth error (401/403)
echo
echo "[8/8] /v1/crm/contacts endpoint registered"
http=$(curl -s -o /dev/null -w "%{http_code}" "$MEMAPI_HOST/v1/crm/contacts" 2>/dev/null || echo "000")
case "$http" in
  401|403|422) pass "/v1/crm/contacts responds ($http — route exists)";;
  *) fail "/v1/crm/contacts routing" "got HTTP $http (expected 401/403/422 — route should exist)";;
esac

# Summary
echo
echo "==========================================="
echo "PASS: $TEST_PASSED / $TEST_TOTAL"
echo "==========================================="

if [ "$TEST_PASSED" -eq "$TEST_TOTAL" ]; then
  echo "Phase 7 verification: ALL PASS"
  exit 0
else
  echo "Phase 7 verification: FAILURES"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
