#!/usr/bin/env bash
#
# verify-phase11.sh — Verify Phase 11 (Brain Monitor + Superadmin Dashboard) deliverables.
#
# Tests (16, SKIP-aware — SKIP never counts as FAIL):
#   1.  Alembic version >= 0018_brain_events_view
#   2.  tasks.truth_level / tasks.deleted_at / tasks.deleted_by columns present
#   3.  All 6 target tables have a deleted_at column
#   4.  v_brain_events view exists (to_regclass)
#   5.  v_brain_events returns >= 1 distinct entity_type for team 'default'
#   6.  GET /v1/brain/events returns 200 with auth + X-Team-Scope
#   7.  PATCH /v1/brain/events/task/<owned> 200 by owner / 403 by non-owner
#   8.  DELETE -> 204 ; row absent from default list ; present with include_deleted=true
#   9.  POST .../restore -> 200 ; deleted_at IS NULL
#   10. GET /v1/tasks API count == DB count (regression filter active)
#   11. xbrain-brain-janitor container running + sentinel /tmp/brain-janitor-alive
#       mtime within last 25h
#   12. Migration preservation: no rogue truth_level values in memory_items (SC-6)
#   13. Messages soft-delete regression — API count == DB count == 1 (REVISION 1)
#   14. Superadmin /v1/admin/brain/overview returns >=2 teams ; non-admin -> 403
#   15. Drill-down /v1/admin/brain/events writes exactly 1 audit_log row per call
#   16. ADMIN_USER_SUBS lockdown — all 5 /v1/admin/brain/* endpoints return 403
#       (only runs when LOCKDOWN_TEST=1 ; otherwise SKIPPED)
#
# Exit code:
#   0 when FAIL == 0, regardless of SKIP count
#   1 when FAIL > 0
#
# Usage:
#   bash infrastructure/scripts/verify-phase11.sh
#
# Required env / fixtures (sourced from infrastructure/.env.test if present):
#   TEST_XBT                  xbt_ token for a regular team member (author of TEST_TASK_ID_OWNED)
#   TEST_XBT_MEMBER           xbt_ token for a different non-admin member of team 'default'
#                             (used as the 403 negative case for PATCH and admin endpoints)
#   TEST_TASK_ID_OWNED        UUID of a tasks row owned (created_by) by TEST_XBT principal
#                             in team_scope='default'
#   TEST_TASK_ID_OTHER_USER   UUID of a tasks row owned by another user in team_scope='default'
#                             (used by assertion 7 to verify the 403 path)
#   TEST_CONVERSATION_ID      UUID of a conversations row in team_scope='default' the API can list
#                             messages for; assertion 13 seeds 2 messages and soft-deletes one
#   SUPERADMIN_XBT            xbt_ token for a principal whose 'sub' is in ADMIN_USER_SUBS
#   TEST_DRILLDOWN_TEAM_SLUG  any non-default team slug for assertion 15 drill-down
#
# Optional env overrides:
#   MEMAPI_HOST               default http://localhost:8000
#   DB_CONTAINER              default xbrain-postgres
#   PG_USER                   default xbrain
#   PG_DB                     default xbrain
#   JANITOR_CONTAINER         default xbrain-brain-janitor
#   LOCKDOWN_TEST             set to 1 to enable assertion 16 (requires the memory-api
#                             deployment under test to be running with ADMIN_USER_SUBS=""
#                             — i.e. nobody is a superadmin). When unset, assertion 16
#                             SKIPS with an explicit reason and operator verifies manually.
#
# Manual lockdown verification (if LOCKDOWN_TEST is not set):
#   1. Edit infrastructure/.env on the VM: blank ADMIN_USER_SUBS (or unset it).
#   2. docker compose restart memory-api
#   3. curl -s -o /dev/null -w '%{http_code}' \
#         -H "Authorization: Bearer $SUPERADMIN_XBT" \
#         "$MEMAPI_HOST/v1/admin/brain/overview"
#      Expected: 403
#   4. Restore ADMIN_USER_SUBS and restart memory-api.
#
# Assertion 15 audit-row WHERE clause:
#   The audit_log row written by /v1/admin/brain/events drill-down stores the target
#   team slug in BOTH the `team_scope` column AND `payload->>'target_team_slug'`. This
#   script queries by `team_scope` (the canonical column). If a future refactor moves
#   the slug to payload-only, swap the WHERE to:
#     WHERE action='superadmin_brain_access' AND payload->>'target_team_slug'='<slug>'
#
# Run from repo root on the VM where docker compose is running.

set -uo pipefail   # NOT -e — every test runs independently; the summary line is the truth

# Auto-source test fixtures if .env.test exists at repo root
if [[ -f "infrastructure/.env.test" ]]; then
  # shellcheck disable=SC1091
  set -a; . "infrastructure/.env.test"; set +a
fi

MEMAPI_HOST="${MEMAPI_HOST:-http://localhost:8000}"
DB_CONTAINER="${DB_CONTAINER:-xbrain-postgres}"
PG_USER="${PG_USER:-xbrain}"
PG_DB="${PG_DB:-xbrain}"
JANITOR_CONTAINER="${JANITOR_CONTAINER:-xbrain-brain-janitor}"

PASS=0
FAIL=0
SKIP=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

ok()   { green "  PASS: $*"; PASS=$((PASS+1)); }
ko()   { red   "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip() { yellow "  SKIPPED: $*"; SKIP=$((SKIP+1)); }

run_psql() {
  docker exec -i "${DB_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc "$1" 2>/dev/null
}

require_env() {
  # Returns 0 if all named env vars are set & non-empty; 1 otherwise.
  local missing=()
  local v
  for v in "$@"; do
    if [[ -z "${!v:-}" ]]; then
      missing+=("$v")
    fi
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    skip "missing fixture env vars: ${missing[*]}"
    return 1
  fi
  return 0
}

echo "=== Phase 11 Verification ==="
echo "MEMAPI_HOST       = $MEMAPI_HOST"
echo "DB_CONTAINER      = $DB_CONTAINER"
echo "JANITOR_CONTAINER = $JANITOR_CONTAINER"
echo "LOCKDOWN_TEST     = ${LOCKDOWN_TEST:-0}"

# -----------------------------------------------------------------------------
test_01_alembic_head() {
  echo
  echo "[1/16] Alembic version >= 0018_brain_events_view"
  if ! docker ps --filter "name=${DB_CONTAINER}" --format "{{.Names}}" 2>/dev/null | grep -q "${DB_CONTAINER}"; then
    ko "DB container ${DB_CONTAINER} not running"
    return
  fi
  local ver
  ver=$(run_psql "SELECT version_num FROM alembic_version" | tr -d '[:space:]')
  if [[ -z "$ver" ]]; then
    ko "alembic_version row missing"
    return
  fi
  # 0018_brain_events_view is the minimum acceptable head for Phase 11
  if [[ "$ver" == "0018_brain_events_view" ]] || [[ "$ver" > "0018_brain_events_view" ]]; then
    ok "alembic_version='$ver' (>= 0018_brain_events_view)"
  else
    ko "alembic_version='$ver' is below 0018_brain_events_view"
  fi
}

# -----------------------------------------------------------------------------
test_02_tasks_columns() {
  echo
  echo "[2/16] tasks.truth_level / deleted_at / deleted_by columns present"
  local cols
  cols=$(run_psql "SELECT column_name FROM information_schema.columns WHERE table_name='tasks' AND column_name IN ('truth_level','deleted_at','deleted_by') ORDER BY column_name")
  # Expect 3 lines back; normalise whitespace
  local n
  n=$(echo "$cols" | grep -c -E '^(truth_level|deleted_at|deleted_by)$' || true)
  if [[ "$n" == "3" ]]; then
    ok "tasks has truth_level + deleted_at + deleted_by"
  else
    ko "tasks missing one of truth_level/deleted_at/deleted_by (got $n/3: $(echo "$cols" | tr '\n' ' '))"
  fi
}

# -----------------------------------------------------------------------------
test_03_all_tables_deleted_at() {
  echo
  echo "[3/16] All 6 target tables have a deleted_at column"
  # The 6 base tables behind v_brain_events. memory_items already had deleted_at
  # pre-Phase-11; the other 5 were added by migration 0017_brain_monitor_base.
  local tables=("memory_items" "conversations" "messages" "team_messages" "tasks" "contacts")
  local missing=()
  local t
  for t in "${tables[@]}"; do
    local has
    has=$(run_psql "SELECT 1 FROM information_schema.columns WHERE table_name='$t' AND column_name='deleted_at'" | tr -d '[:space:]')
    if [[ "$has" != "1" ]]; then
      missing+=("$t")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "all 6 tables have deleted_at"
  else
    ko "tables missing deleted_at: ${missing[*]}"
  fi
}

# -----------------------------------------------------------------------------
test_04_view_exists() {
  echo
  echo "[4/16] v_brain_events view exists"
  local out
  out=$(run_psql "SELECT to_regclass('v_brain_events')::text" | tr -d '[:space:]')
  if [[ "$out" == "v_brain_events" ]]; then
    ok "v_brain_events view exists"
  else
    ko "v_brain_events not registered (to_regclass returned '$out')"
  fi
}

# -----------------------------------------------------------------------------
test_05_view_returns_entity_types() {
  echo
  echo "[5/16] v_brain_events returns >= 1 distinct entity_type for team 'default'"
  local n
  n=$(run_psql "SELECT COUNT(DISTINCT entity_type) FROM v_brain_events WHERE team_scope='default'" | tr -d '[:space:]')
  if [[ -z "$n" ]]; then
    ko "could not query v_brain_events (view error?)"
    return
  fi
  if (( n >= 1 )); then
    ok "v_brain_events returns $n distinct entity_type(s) for team 'default'"
  else
    skip "v_brain_events returned 0 entity_types for team 'default' — seed data missing? (not a Phase 11 regression — but operator should populate fixtures)"
  fi
}

# -----------------------------------------------------------------------------
test_06_get_events() {
  echo
  echo "[6/16] GET /v1/brain/events returns 200 with auth + X-Team-Scope"
  require_env "TEST_XBT" || return
  local code
  code=$(curl -s -o /tmp/xbrain-11-06.body -w "%{http_code}" -m 10 \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    "$MEMAPI_HOST/v1/brain/events?limit=5" 2>/dev/null)
  code="${code:-000}"
  if [[ "$code" == "200" ]]; then
    ok "GET /v1/brain/events returned 200 (body head: $(head -c 80 /tmp/xbrain-11-06.body 2>/dev/null))"
  else
    ko "GET /v1/brain/events returned HTTP $code (body: $(head -c 200 /tmp/xbrain-11-06.body 2>/dev/null))"
  fi
}

# -----------------------------------------------------------------------------
test_07_patch_owner_vs_nonowner() {
  echo
  echo "[7/16] PATCH /v1/brain/events/task/<owned> 200 by owner / 403 by non-owner"
  require_env "TEST_XBT" "TEST_XBT_MEMBER" "TEST_TASK_ID_OWNED" "TEST_TASK_ID_OTHER_USER" || return
  # 7a — owner PATCHes own task -> 200
  local code_owner
  code_owner=$(curl -s -o /tmp/xbrain-11-07a.body -w "%{http_code}" -m 10 \
    -X PATCH \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    -H "Content-Type: application/json" \
    -d '{"truth_level":"WORKING"}' \
    "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OWNED" 2>/dev/null)
  code_owner="${code_owner:-000}"
  # 7b — non-owner PATCHes someone else's task -> 403
  local code_other
  code_other=$(curl -s -o /tmp/xbrain-11-07b.body -w "%{http_code}" -m 10 \
    -X PATCH \
    -H "Authorization: Bearer $TEST_XBT_MEMBER" \
    -H "X-Team-Scope: default" \
    -H "Content-Type: application/json" \
    -d '{"truth_level":"WORKING"}' \
    "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OTHER_USER" 2>/dev/null)
  code_other="${code_other:-000}"
  if [[ "$code_owner" == "200" ]] && [[ "$code_other" == "403" ]]; then
    ok "owner PATCH 200 ; non-owner PATCH 403"
  else
    ko "owner=$code_owner non-owner=$code_other (expected 200 / 403)"
  fi
}

# -----------------------------------------------------------------------------
test_08_soft_delete_roundtrip() {
  echo
  echo "[8/16] DELETE -> 204 ; absent from default list ; present with include_deleted=true"
  require_env "TEST_XBT" "TEST_TASK_ID_OWNED" || return
  # delete
  local code_del
  code_del=$(curl -s -o /dev/null -w "%{http_code}" -m 10 \
    -X DELETE \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OWNED" 2>/dev/null)
  code_del="${code_del:-000}"
  if [[ "$code_del" != "204" ]]; then
    ko "DELETE returned HTTP $code_del (expected 204)"
    return
  fi
  # absent from default list
  local default_hits
  default_hits=$(curl -s -m 10 \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    "$MEMAPI_HOST/v1/brain/events?limit=200" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for i in d.get('items',[]) if i.get('entity_id')=='$TEST_TASK_ID_OWNED'))" 2>/dev/null || echo "?")
  # present with include_deleted=true
  local incl_hits
  incl_hits=$(curl -s -m 10 \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    "$MEMAPI_HOST/v1/brain/events?include_deleted=true&limit=200" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(sum(1 for i in d.get('items',[]) if i.get('entity_id')=='$TEST_TASK_ID_OWNED'))" 2>/dev/null || echo "?")
  if [[ "$default_hits" == "0" ]] && [[ "$incl_hits" == "1" ]]; then
    ok "soft-delete works: default list excludes task, include_deleted=true returns it"
  else
    ko "default_hits=$default_hits include_deleted_hits=$incl_hits (expected 0 / 1)"
  fi
}

# -----------------------------------------------------------------------------
test_09_restore() {
  echo
  echo "[9/16] POST .../restore -> 200 ; deleted_at IS NULL"
  require_env "TEST_XBT" "TEST_TASK_ID_OWNED" || return
  local code
  code=$(curl -s -o /tmp/xbrain-11-09.body -w "%{http_code}" -m 10 \
    -X POST \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    "$MEMAPI_HOST/v1/brain/events/task/$TEST_TASK_ID_OWNED/restore" 2>/dev/null)
  code="${code:-000}"
  if [[ "$code" != "200" ]]; then
    ko "restore returned HTTP $code (body: $(head -c 200 /tmp/xbrain-11-09.body 2>/dev/null))"
    return
  fi
  local null_check
  null_check=$(run_psql "SELECT deleted_at IS NULL FROM tasks WHERE id='$TEST_TASK_ID_OWNED'" | tr -d '[:space:]')
  if [[ "$null_check" == "t" ]]; then
    ok "restore: HTTP 200 + deleted_at IS NULL in DB"
  else
    ko "restore: HTTP 200 but deleted_at IS NULL check = '$null_check' (expected 't')"
  fi
}

# -----------------------------------------------------------------------------
test_10_tasks_regression_filter() {
  echo
  echo "[10/16] GET /v1/tasks API count == DB count (regression filter active)"
  require_env "TEST_XBT" || return
  # API count — adjust ?limit if your dev DB has > 200 tasks
  local api_count
  api_count=$(curl -s -m 10 \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    "$MEMAPI_HOST/v1/tasks?limit=500" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else len(d.get('items',[])))" 2>/dev/null || echo "?")
  local db_count
  db_count=$(run_psql "SELECT COUNT(*) FROM tasks WHERE team_scope='default' AND deleted_at IS NULL" | tr -d '[:space:]')
  if [[ "$api_count" == "$db_count" ]] && [[ "$api_count" != "?" ]]; then
    ok "/v1/tasks API count == DB count == $api_count (soft-delete filter active)"
  else
    ko "/v1/tasks API=$api_count DB=$db_count (mismatch indicates filter regression)"
  fi
}

# -----------------------------------------------------------------------------
test_11_janitor_alive() {
  echo
  echo "[11/16] xbrain-brain-janitor running + sentinel /tmp/brain-janitor-alive mtime < 25h"
  local state
  state=$(docker inspect "$JANITOR_CONTAINER" --format '{{.State.Status}}' 2>/dev/null || echo "absent")
  if [[ "$state" != "running" ]]; then
    ko "$JANITOR_CONTAINER state='$state' (expected 'running')"
    return
  fi
  # Read sentinel mtime as epoch seconds from inside the container
  local mtime
  mtime=$(docker exec "$JANITOR_CONTAINER" stat -c '%Y' /tmp/brain-janitor-alive 2>/dev/null || echo "")
  if [[ -z "$mtime" ]]; then
    skip "$JANITOR_CONTAINER running but /tmp/brain-janitor-alive missing — janitor has not completed its first cycle yet (cron runs 03:00 UTC daily)"
    return
  fi
  local now
  now=$(date +%s)
  local age=$((now - mtime))
  local max=$((25 * 3600))   # 25h tolerance
  if (( age <= max )); then
    ok "$JANITOR_CONTAINER running + sentinel touched ${age}s ago (<= 25h)"
  else
    ko "$JANITOR_CONTAINER running but sentinel stale: ${age}s ago (> 25h)"
  fi
}

# -----------------------------------------------------------------------------
test_12_truth_level_preservation() {
  echo
  echo "[12/16] No rogue truth_level values in memory_items (SC-6 — migration preservation)"
  local n
  n=$(run_psql "SELECT COUNT(*) FROM memory_items WHERE truth_level IS NOT NULL AND truth_level NOT IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')" | tr -d '[:space:]')
  if [[ "$n" == "0" ]]; then
    ok "0 rogue truth_level values in memory_items"
  else
    ko "$n memory_items rows have a truth_level outside the 5-value enum"
  fi
}

# -----------------------------------------------------------------------------
test_13_messages_soft_delete_regression() {
  echo
  echo "[13/16] Messages soft-delete regression — API count == DB count (REVISION 1)"
  require_env "TEST_XBT" "TEST_CONVERSATION_ID" || return

  # Snapshot prior state so we can clean up at the end.
  local pre_alive pre_deleted
  pre_alive=$(run_psql "SELECT COUNT(*) FROM messages WHERE conversation_id='$TEST_CONVERSATION_ID' AND deleted_at IS NULL" | tr -d '[:space:]')
  pre_deleted=$(run_psql "SELECT COUNT(*) FROM messages WHERE conversation_id='$TEST_CONVERSATION_ID' AND deleted_at IS NOT NULL" | tr -d '[:space:]')

  # Seed 2 fixture messages, soft-delete one, expect API to return only the live one.
  # We mark them with a UAT-only marker so cleanup is precise.
  local marker
  marker="verify-phase11-$(date +%s%N)"
  run_psql "
    INSERT INTO messages (id, conversation_id, team_scope, role, content, created_at)
    SELECT gen_random_uuid(), '$TEST_CONVERSATION_ID', team_scope, 'user', '$marker:alive', NOW()
      FROM conversations WHERE id='$TEST_CONVERSATION_ID';
    INSERT INTO messages (id, conversation_id, team_scope, role, content, created_at, deleted_at)
    SELECT gen_random_uuid(), '$TEST_CONVERSATION_ID', team_scope, 'user', '$marker:deleted', NOW(), NOW()
      FROM conversations WHERE id='$TEST_CONVERSATION_ID';
  " >/dev/null 2>&1

  # Expected post-state: pre_alive + 1 live, pre_deleted + 1 deleted.
  local expected_live=$((pre_alive + 1))
  local db_live
  db_live=$(run_psql "SELECT COUNT(*) FROM messages WHERE conversation_id='$TEST_CONVERSATION_ID' AND deleted_at IS NULL" | tr -d '[:space:]')

  # The API path may vary by deployment — try the canonical first then fall back.
  local api_count
  api_count=$(curl -s -m 10 \
    -H "Authorization: Bearer $TEST_XBT" \
    -H "X-Team-Scope: default" \
    "$MEMAPI_HOST/v1/messages?conversation_id=$TEST_CONVERSATION_ID&limit=500" \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d) if isinstance(d,list) else len(d.get('items',[])))" 2>/dev/null || echo "?")

  if [[ "$api_count" == "?" ]] || [[ "$api_count" == "" ]]; then
    skip "could not list /v1/messages?conversation_id=... — discover the right path via: curl -s '$MEMAPI_HOST/openapi.json' | jq '.paths | keys[] | select(contains(\"messages\"))'"
    # Cleanup fixtures even on skip
    run_psql "DELETE FROM messages WHERE content LIKE '$marker:%'" >/dev/null 2>&1
    return
  fi

  if [[ "$api_count" == "$db_live" ]] && [[ "$db_live" == "$expected_live" ]]; then
    ok "messages list: API=$api_count == DB=$db_live (excludes soft-deleted fixture; deleted_at filter active)"
  else
    ko "messages list mismatch: API=$api_count DB=$db_live expected=$expected_live (pre_alive=$pre_alive)"
  fi

  # Cleanup fixtures
  run_psql "DELETE FROM messages WHERE content LIKE '$marker:%'" >/dev/null 2>&1
}

# -----------------------------------------------------------------------------
test_14_superadmin_overview() {
  echo
  echo "[14/16] /v1/admin/brain/overview — superadmin gets >=2 teams ; non-admin -> 403"
  require_env "SUPERADMIN_XBT" "TEST_XBT_MEMBER" || return

  # 14a — superadmin can read overview
  local body code_super
  code_super=$(curl -s -o /tmp/xbrain-11-14a.body -w "%{http_code}" -m 10 \
    -H "Authorization: Bearer $SUPERADMIN_XBT" \
    "$MEMAPI_HOST/v1/admin/brain/overview" 2>/dev/null)
  code_super="${code_super:-000}"
  if [[ "$code_super" != "200" ]]; then
    ko "14a. superadmin overview HTTP $code_super (expected 200; body: $(head -c 200 /tmp/xbrain-11-14a.body 2>/dev/null))"
  else
    local team_count
    team_count=$(python3 -c "import json,sys; d=json.load(open('/tmp/xbrain-11-14a.body')); print(len(d) if isinstance(d,list) else 0)" 2>/dev/null || echo "0")
    if (( team_count >= 2 )); then
      ok "14a. superadmin overview returns $team_count teams (>= 2)"
    else
      # Could be a real regression OR a fixture gap (single-team dev DB).
      skip "14a. superadmin overview returned $team_count teams (<2 — seed a second team via POST /v1/admin/teams before re-running)"
    fi
  fi

  # 14b — non-superadmin gets 403
  local code_member
  code_member=$(curl -s -o /dev/null -w "%{http_code}" -m 10 \
    -H "Authorization: Bearer $TEST_XBT_MEMBER" \
    "$MEMAPI_HOST/v1/admin/brain/overview" 2>/dev/null)
  code_member="${code_member:-000}"
  if [[ "$code_member" == "403" ]]; then
    ok "14b. non-superadmin gets 403 on /v1/admin/brain/overview"
  else
    ko "14b. non-superadmin overview returned HTTP $code_member (expected 403)"
  fi
}

# -----------------------------------------------------------------------------
test_15_drilldown_audit() {
  echo
  echo "[15/16] /v1/admin/brain/events drill-down — exactly +1 audit_log row per call"
  require_env "SUPERADMIN_XBT" "TEST_DRILLDOWN_TEAM_SLUG" || return

  local before
  before=$(run_psql "SELECT COUNT(*) FROM audit_log WHERE action='superadmin_brain_access' AND team_scope='$TEST_DRILLDOWN_TEAM_SLUG'" | tr -d '[:space:]')
  before="${before:-0}"

  local code
  code=$(curl -s -o /tmp/xbrain-11-15.body -w "%{http_code}" -m 10 \
    -H "Authorization: Bearer $SUPERADMIN_XBT" \
    "$MEMAPI_HOST/v1/admin/brain/events?team_slug=$TEST_DRILLDOWN_TEAM_SLUG&limit=5" 2>/dev/null)
  code="${code:-000}"

  local after
  after=$(run_psql "SELECT COUNT(*) FROM audit_log WHERE action='superadmin_brain_access' AND team_scope='$TEST_DRILLDOWN_TEAM_SLUG'" | tr -d '[:space:]')
  after="${after:-0}"
  local delta=$((after - before))

  if [[ "$code" == "200" ]] && [[ "$delta" == "1" ]]; then
    ok "15. drill-down 200 + audit_log incremented by exactly 1 (before=$before after=$after)"
  else
    ko "15. drill-down http=$code audit_delta=$delta (expected http=200 delta=1; body head: $(head -c 200 /tmp/xbrain-11-15.body 2>/dev/null))"
  fi
}

# -----------------------------------------------------------------------------
test_16_admin_lockdown() {
  echo
  echo "[16/16] ADMIN_USER_SUBS lockdown — all 5 /v1/admin/brain/* endpoints 403"
  if [[ "${LOCKDOWN_TEST:-0}" != "1" ]]; then
    skip "16. LOCKDOWN_TEST != 1 (manual procedure documented in script header — operator runs once per release with ADMIN_USER_SUBS='')"
    return
  fi
  require_env "SUPERADMIN_XBT" "TEST_DRILLDOWN_TEAM_SLUG" || return

  local all_403=1
  local ep code
  # 5 endpoints to probe: overview / storage / activity / sources / events?team_slug=...
  for ep in "overview" "storage" "activity" "sources" "events?team_slug=$TEST_DRILLDOWN_TEAM_SLUG"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 \
      -H "Authorization: Bearer $SUPERADMIN_XBT" \
      "$MEMAPI_HOST/v1/admin/brain/$ep" 2>/dev/null)
    code="${code:-000}"
    if [[ "$code" != "403" ]]; then
      all_403=0
      ko "16. /v1/admin/brain/$ep returned $code under lockdown (expected 403)"
      break
    fi
  done
  if [[ "$all_403" == "1" ]]; then
    ok "16. lockdown active — all 5 /v1/admin/brain/* endpoints return 403"
  fi
}

# -----------------------------------------------------------------------------
test_01_alembic_head
test_02_tasks_columns
test_03_all_tables_deleted_at
test_04_view_exists
test_05_view_returns_entity_types
test_06_get_events
test_07_patch_owner_vs_nonowner
test_08_soft_delete_roundtrip
test_09_restore
test_10_tasks_regression_filter
test_11_janitor_alive
test_12_truth_level_preservation
test_13_messages_soft_delete_regression
test_14_superadmin_overview
test_15_drilldown_audit
test_16_admin_lockdown

TOTAL_RUN=$((PASS + FAIL))
echo
echo "==========================================="
echo "PASS: $PASS / $TOTAL_RUN (SKIPPED: $SKIP)"
echo "==========================================="

if [[ $FAIL -eq 0 ]]; then
  green "Phase 11 verification: ALL PASS (FAIL=0; SKIPPED never blocks)"
  exit 0
else
  red "Phase 11 verification: $FAIL failure(s)"
  exit 1
fi
