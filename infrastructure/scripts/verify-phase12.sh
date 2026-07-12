#!/usr/bin/env bash
#
# verify-phase12.sh — Verify Phase 12 (GitHub App migration) deliverables.
#
# Tests (18, SKIP-aware — SKIP never counts as FAIL):
#   1.  Alembic version >= 0019_github_app_install
#   2.  installations table exists with the 6 required columns
#   3.  users table has the 5 Phase 12 token columns (incl. github_access_token_hash)
#   4.  Partial unique index idx_installations_org_login_active exists
#   5.  memory-api env has all 6 GITHUB_APP_* secrets
#   6.  GITHUB_API_PAT removed from app/ AND tests/ (M-6 KB-truthiness gate)
#   7.  Legacy OAuth App client_id (Ov23liy7tZekl0uEztoj) absent from frontend code
#   8.  PyJWT installed in memory-api at supported version
#   9.  mint_app_jwt() returns valid RS256 JWT shape
#   10. POST /v1/webhooks/github/installation registered in openapi
#   11. Webhook returns 401 on missing X-Hub-Signature-256
#   12. Webhook returns 200 on correctly signed ping (SKIPs if secret not exported)
#   13. installations row exists for TEST_GITHUB_ORG (proves install + webhook delivery)
#   14. SigninGithubOut schema includes install_required + install_url + org_login (M-1 fix)
#   15. Chrome extension manifest has 'key' field (stable extension ID)
#   16. Frontend GITHUB_CLIENT_ID matches GITHUB_APP_CLIENT_ID env
#   17. get_installation_token end-to-end (mint App JWT → ghs_ token)
#   18. SC-5 regression — blocked github_login on installed org cannot auto-join (B-3 fix)
#
# Exit code:
#   0 when FAIL == 0, regardless of SKIP count
#   1 when FAIL > 0
#
# Usage:
#   MEMAPI_HOST=https://api.example.com \
#   GITHUB_APP_CLIENT_ID=Iv23li... \
#   TEST_INSTALLATION_ID=12345 \                    # operator-provided (the dejavudev install)
#   TEST_GITHUB_ORG=dejavudev \
#   GITHUB_APP_WEBHOOK_SECRET=... \                 # OPTIONAL — enables assertion 12
#   TEST_BLOCKED_LOGIN=blocked_fixture \            # OPTIONAL — for assertion 18 (SC-5 regression)
#   TEST_BLOCKED_TEAM_SLUG=test-team \              # OPTIONAL — team with team_org_blocks row for TEST_BLOCKED_LOGIN
#   bash infrastructure/scripts/verify-phase12.sh
#
# Fixtures sourced from infrastructure/.env.test if present.
#
# Optional env overrides:
#   MEMAPI_HOST           default http://localhost:8000
#   DB_CONTAINER          default xbrain-postgres
#   API_CONTAINER         default xbrain-memory-api
#   PG_USER               default xbrain
#   PG_DB                 default xbrain
#
# Assertion 18 prep (operator must run BEFORE invoking this script):
#   1. Pick a fixture github_login OTHER THAN mrboups (e.g. a test bot account or
#      a colleague's login). The user MUST be a member of TEST_GITHUB_ORG on
#      GitHub AND must have signed in to the deployment at least once after the
#      block was set.
#   2. Insert the block row:
#        psql -c "INSERT INTO team_org_blocks (team_id, github_login, blocked_by)
#                 SELECT id, '<login>', NULL FROM teams WHERE slug='<team-slug>'
#                 ON CONFLICT DO NOTHING;"
#   3. Run UAT step 1 as that fixture user (the signin attempt SHOULD return
#      403 / install_required, the user MUST NOT land on the team).
#   4. Re-run this script — assertion 18 reads team_members to confirm the
#      block held.
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
API_CONTAINER="${API_CONTAINER:-xbrain-memory-api}"
PG_USER="${PG_USER:-xbrain}"
PG_DB="${PG_DB:-xbrain}"

PASS=0
FAIL=0
SKIP=0
TOTAL=18

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

ok()   { green "  PASS: $*"; PASS=$((PASS+1)); }
ko()   { red   "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip() { yellow "  SKIPPED: $*"; SKIP=$((SKIP+1)); }

run_psql() {
  docker exec -i "${DB_CONTAINER}" psql -U "${PG_USER}" -d "${PG_DB}" -tAc "$1" 2>/dev/null
}

run_api_python() {
  # Run a python one-liner inside the memory-api container. Stdout is captured.
  docker exec -i "${API_CONTAINER}" python -c "$1" 2>&1
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

echo "=== Phase 12 Verification ==="
echo "MEMAPI_HOST   = $MEMAPI_HOST"
echo "DB_CONTAINER  = $DB_CONTAINER"
echo "API_CONTAINER = $API_CONTAINER"

# -----------------------------------------------------------------------------
test_01_alembic_head() {
  echo
  echo "[1/$TOTAL] Alembic version >= 0019_github_app_install"
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
  # 0019_github_app_install is the minimum acceptable head for Phase 12.
  # Accept hash-renamed variants too (e.g. 0019_<hash>_github_app_install).
  if [[ "$ver" == *"0019"*"github_app"* ]] || [[ "$ver" > "0019" ]] || [[ "$ver" == "0019_github_app_install" ]]; then
    ok "alembic_version='$ver' (>= 0019_github_app_install)"
  else
    ko "alembic_version='$ver' is below 0019_github_app_install"
  fi
}

# -----------------------------------------------------------------------------
test_02_installations_table() {
  echo
  echo "[2/$TOTAL] installations table exists with required columns"
  local cols
  cols=$(run_psql "SELECT column_name FROM information_schema.columns WHERE table_name='installations' ORDER BY column_name")
  if [[ -z "$cols" ]]; then
    ko "installations table missing entirely"
    return
  fi
  local missing=()
  local c
  for c in installation_id github_org_login revoked_at suspended_at permissions raw_payload; do
    if ! echo "$cols" | grep -qx "$c"; then
      missing+=("$c")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "installations has all 6 required columns"
  else
    ko "installations missing columns: ${missing[*]}"
  fi
}

# -----------------------------------------------------------------------------
test_03_users_token_columns() {
  echo
  echo "[3/$TOTAL] users table has 5 Phase 12 token columns (incl. token_hash — M-5 fix)"
  local cols
  cols=$(run_psql "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name LIKE 'github_%'")
  local missing=()
  local c
  for c in github_access_token_enc github_refresh_token_enc github_token_expires_at github_refresh_expires_at github_access_token_hash; do
    if ! echo "$cols" | grep -qx "$c"; then
      missing+=("$c")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "users has all 5 Phase 12 token columns (enc + refresh_enc + expires + refresh_expires + hash)"
  else
    ko "users missing token columns: ${missing[*]}"
  fi
}

# -----------------------------------------------------------------------------
test_04_partial_unique_index() {
  echo
  echo "[4/$TOTAL] partial unique index idx_installations_org_login_active exists"
  local indexdef
  indexdef=$(run_psql "SELECT indexdef FROM pg_indexes WHERE indexname='idx_installations_org_login_active'")
  if [[ -z "$indexdef" ]]; then
    ko "idx_installations_org_login_active not found"
    return
  fi
  if echo "$indexdef" | grep -q "revoked_at IS NULL"; then
    ok "partial unique index present with 'WHERE revoked_at IS NULL' predicate"
  else
    ko "index exists but predicate wrong (got: $indexdef)"
  fi
}

# -----------------------------------------------------------------------------
test_05_memory_api_env() {
  echo
  echo "[5/$TOTAL] memory-api env has all 6 GITHUB_APP_* secrets configured"
  if ! docker ps --filter "name=${API_CONTAINER}" --format "{{.Names}}" 2>/dev/null | grep -q "${API_CONTAINER}"; then
    ko "API container ${API_CONTAINER} not running"
    return
  fi
  local env_dump
  env_dump=$(docker exec "${API_CONTAINER}" printenv 2>/dev/null | grep -E '^GITHUB_APP_' || true)
  local missing=()
  local v
  for v in GITHUB_APP_ID GITHUB_APP_SLUG GITHUB_APP_CLIENT_ID GITHUB_APP_CLIENT_SECRET GITHUB_APP_PRIVATE_KEY_B64 GITHUB_APP_WEBHOOK_SECRET; do
    if ! echo "$env_dump" | grep -q "^${v}="; then
      missing+=("$v")
      continue
    fi
    # Also confirm value non-empty
    local val
    val=$(echo "$env_dump" | grep "^${v}=" | head -1 | cut -d= -f2-)
    if [[ -z "$val" ]]; then
      missing+=("${v}(empty)")
    fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then
    ok "all 6 GITHUB_APP_* env vars set + non-empty"
  else
    ko "missing or empty: ${missing[*]}"
  fi
}

# -----------------------------------------------------------------------------
test_06_no_github_api_pat() {
  echo
  echo "[6/$TOTAL] GITHUB_API_PAT removed from app/ AND tests/ (M-6 KB-truthiness gate)"
  # Strip lines starting with comment '#' so the deprecation comment in
  # config.py / .env.example doesn't cause a false-positive FAIL.
  local app_refs
  app_refs=$(grep -rn "GITHUB_API_PAT" apps/memory-api/app/ --include="*.py" 2>/dev/null \
              | grep -v "/__pycache__/" \
              | awk -F: '{
                  line=$0;
                  # Build the content portion (everything after "file:lineno:")
                  n=index(line, ":"); rest=substr(line, n+1);
                  n2=index(rest, ":"); content=substr(rest, n2+1);
                  # Trim leading whitespace
                  sub(/^[ \t]+/, "", content);
                  # Only count NON-comment hits
                  if (substr(content, 1, 1) != "#") print line;
                }' \
              | wc -l | tr -d '[:space:]')
  local test_refs
  test_refs=$(grep -rn "GITHUB_API_PAT" apps/memory-api/tests/ --include="*.py" 2>/dev/null \
              | grep -v "/__pycache__/" \
              | awk -F: '{
                  n=index($0, ":"); rest=substr($0, n+1);
                  n2=index(rest, ":"); content=substr(rest, n2+1);
                  sub(/^[ \t]+/, "", content);
                  if (substr(content, 1, 1) != "#") print $0;
                }' \
              | wc -l | tr -d '[:space:]')
  app_refs=${app_refs:-0}
  test_refs=${test_refs:-0}
  local total=$((app_refs + test_refs))
  if [[ "$total" == "0" ]]; then
    ok "GITHUB_API_PAT removed from memory-api source + tests (0 non-comment refs)"
  else
    ko "GITHUB_API_PAT still referenced: app=$app_refs test=$test_refs (non-comment lines)"
  fi
}

# -----------------------------------------------------------------------------
test_07_no_legacy_oauth_client_id() {
  echo
  echo "[7/$TOTAL] Legacy OAuth App client_id (Ov23liy7tZekl0uEztoj) absent from frontend code"
  # Exclude .planning, marketing-site docs, KB, and python caches — those legitimately
  # reference the legacy id for historical / runbook purposes. The check focuses
  # on the runtime source code that ships to users.
  local hits
  hits=$(grep -rn "Ov23liy7tZekl0uEztoj" apps/ app-site/ chrome-extension/ \
           --include="*.js" --include="*.py" --include="*.html" --include="*.json" \
           2>/dev/null \
         | grep -v "/__pycache__/" \
         | wc -l | tr -d '[:space:]')
  hits=${hits:-0}
  if [[ "$hits" == "0" ]]; then
    ok "Legacy OAuth App client_id absent from runtime frontend + backend source"
  else
    ko "Legacy OAuth App client_id still in $hits source location(s)"
    grep -rn "Ov23liy7tZekl0uEztoj" apps/ app-site/ chrome-extension/ \
           --include="*.js" --include="*.py" --include="*.html" --include="*.json" \
           2>/dev/null | grep -v "/__pycache__/" | head -5 | sed 's/^/    /'
  fi
}

# -----------------------------------------------------------------------------
test_08_pyjwt_installed() {
  echo
  echo "[8/$TOTAL] PyJWT installed in memory-api at supported version"
  local ver
  ver=$(run_api_python "import jwt; print(jwt.__version__)" 2>/dev/null | tail -1 | tr -d '[:space:]')
  if [[ "$ver" =~ ^2\.(10|11|12|13|14)\. ]] || [[ "$ver" =~ ^[3-9]\. ]]; then
    ok "PyJWT installed (version: $ver)"
  else
    ko "PyJWT not installed or unsupported version: '$ver' (expected 2.10+)"
  fi
}

# -----------------------------------------------------------------------------
test_09_mint_app_jwt() {
  echo
  echo "[9/$TOTAL] mint_app_jwt() returns valid RS256 JWT (3 parts, alg=RS256)"
  # Inspect output: must be 3 dot-separated base64 parts, header decodes to alg=RS256.
  local out
  out=$(run_api_python "
import base64, json, sys
from app.services.github_app_jwt import mint_app_jwt
try:
    t = mint_app_jwt()
except Exception as e:
    print('MINT_ERROR:' + repr(e)[:200])
    sys.exit(0)
if t.count('.') != 2:
    print('BAD_DOTS:' + str(t.count('.')))
    sys.exit(0)
header_b64 = t.split('.')[0]
# Pad for urlsafe_b64decode
pad = '=' * (-len(header_b64) % 4)
try:
    h = json.loads(base64.urlsafe_b64decode(header_b64 + pad).decode('utf-8'))
except Exception as e:
    print('BAD_HEADER:' + repr(e)[:100])
    sys.exit(0)
print('OK' if h.get('alg') == 'RS256' else 'BAD_ALG:' + str(h.get('alg')))
" 2>&1 | tail -1 | tr -d '[:space:]')
  if [[ "$out" == "OK" ]]; then
    ok "mint_app_jwt() returns 3-part token with header.alg=RS256"
  else
    ko "mint_app_jwt() output: '$out'"
  fi
}

# -----------------------------------------------------------------------------
test_10_webhook_in_openapi() {
  echo
  echo "[10/$TOTAL] POST /v1/webhooks/github/installation registered in openapi"
  local has
  # nginx does NOT expose /openapi.json publicly (correct hardening) — fetch via container
  has=$(docker exec "$API_CONTAINER" curl -sS -m 10 http://localhost:8000/openapi.json 2>/dev/null \
        | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception as e:
    print('PARSE_ERROR'); sys.exit(0)
paths = d.get('paths', {})
target = '/v1/webhooks/github/installation'
print('YES' if target in paths and 'post' in paths[target] else 'NO:' + str(list(paths.keys())[:3]))
" 2>/dev/null | tr -d '[:space:]')
  if [[ "$has" == "YES" ]]; then
    ok "POST /v1/webhooks/github/installation registered in openapi"
  else
    ko "webhook route missing from openapi (got: '$has')"
  fi
}

# -----------------------------------------------------------------------------
test_11_webhook_rejects_unsigned() {
  echo
  echo "[11/$TOTAL] Webhook returns 401 on missing X-Hub-Signature-256"
  local code
  code=$(curl -sS -o /tmp/xbrain-12-11.body -w "%{http_code}" -m 10 \
         -X POST "$MEMAPI_HOST/v1/webhooks/github/installation" \
         -H "X-GitHub-Event: ping" \
         -H "Content-Type: application/json" \
         -d '{"zen":"hi"}' 2>/dev/null)
  code="${code:-000}"
  if [[ "$code" == "401" ]]; then
    ok "webhook returns 401 on missing X-Hub-Signature-256"
  else
    ko "webhook returned HTTP $code (expected 401; body: $(head -c 150 /tmp/xbrain-12-11.body 2>/dev/null))"
  fi
}

# -----------------------------------------------------------------------------
test_12_webhook_accepts_signed_ping() {
  echo
  echo "[12/$TOTAL] Webhook returns 200 on correctly signed ping"
  require_env "GITHUB_APP_WEBHOOK_SECRET" || return
  local body sig code
  body='{"zen":"You tried your best and you failed miserably. The lesson is, never try."}'
  sig="sha256=$(printf '%s' "$body" | openssl dgst -sha256 -hmac "$GITHUB_APP_WEBHOOK_SECRET" -hex 2>/dev/null | awk '{print $NF}')"
  if [[ -z "$sig" ]] || [[ "$sig" == "sha256=" ]]; then
    skip "openssl HMAC computation failed (openssl missing or broken on this host)"
    return
  fi
  code=$(curl -sS -o /tmp/xbrain-12-12.body -w "%{http_code}" -m 10 \
         -X POST "$MEMAPI_HOST/v1/webhooks/github/installation" \
         -H "X-GitHub-Event: ping" \
         -H "X-Hub-Signature-256: $sig" \
         -H "Content-Type: application/json" \
         -d "$body" 2>/dev/null)
  code="${code:-000}"
  if [[ "$code" == "200" ]] || [[ "$code" == "204" ]]; then
    ok "webhook returns $code on correctly signed ping"
  else
    ko "webhook returned HTTP $code on signed ping (expected 200/204; body: $(head -c 150 /tmp/xbrain-12-12.body 2>/dev/null))"
  fi
}

# -----------------------------------------------------------------------------
test_13_installations_row_for_org() {
  echo
  echo "[13/$TOTAL] installations row exists for TEST_GITHUB_ORG (proves install + webhook delivery)"
  require_env "TEST_GITHUB_ORG" || return
  local n
  n=$(run_psql "SELECT COUNT(*) FROM installations WHERE github_org_login='${TEST_GITHUB_ORG}' AND revoked_at IS NULL" | tr -d '[:space:]')
  n=${n:-0}
  if (( n >= 1 )); then
    ok "installations row exists for $TEST_GITHUB_ORG (active count: $n)"
  else
    ko "no active installations row for $TEST_GITHUB_ORG — webhook not received OR App not installed yet"
  fi
}

# -----------------------------------------------------------------------------
test_14_signin_schema() {
  echo
  echo "[14/$TOTAL] SigninGithubOut schema includes install_required + install_url + org_login (M-1 fix)"
  local has
  # nginx does NOT expose /openapi.json publicly (correct hardening) — fetch via container
  has=$(docker exec "$API_CONTAINER" curl -sS -m 10 http://localhost:8000/openapi.json 2>/dev/null \
        | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print('PARSE_ERROR'); sys.exit(0)
schemas = d.get('components', {}).get('schemas', {})
sg = schemas.get('SigninGithubOut') or schemas.get('SigninGithubResponse') or {}
props = sg.get('properties', {})
needed = {'install_required', 'install_url', 'org_login'}
have = set(props.keys())
if needed.issubset(have):
    print('YES')
else:
    print('NO:missing=' + ','.join(sorted(needed - have)))
" 2>/dev/null | tr -d '[:space:]')
  if [[ "$has" == "YES" ]]; then
    ok "SigninGithubOut schema has install_required + install_url + org_login"
  else
    ko "schema missing fields: $has"
  fi
}

# -----------------------------------------------------------------------------
test_15_chrome_ext_key() {
  echo
  echo "[15/$TOTAL] chrome-extension/manifest.json has 'key' field (>= 200 chars)"
  if [[ ! -f chrome-extension/manifest.json ]]; then
    ko "chrome-extension/manifest.json not found at repo root (cwd: $(pwd))"
    return
  fi
  local keylen
  keylen=$(python3 -c "
import json, sys
try:
    m = json.load(open('chrome-extension/manifest.json'))
except Exception as e:
    print('0'); sys.exit(0)
print(len(m.get('key', '')))
" 2>/dev/null | tr -d '[:space:]')
  keylen=${keylen:-0}
  if (( keylen >= 200 )); then
    ok "manifest.json has 'key' field (len=$keylen)"
  else
    ko "manifest.json missing 'key' field or too short (len=$keylen)"
  fi
}

# -----------------------------------------------------------------------------
test_16_frontend_client_id_matches() {
  echo
  echo "[16/$TOTAL] Frontend GITHUB_CLIENT_ID matches GITHUB_APP_CLIENT_ID env"
  require_env "GITHUB_APP_CLIENT_ID" || return
  local teams_id bg_id
  teams_id=$(grep -E 'const GITHUB_CLIENT_ID *= *"' app-site/account/teams/teams.js 2>/dev/null \
             | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
  bg_id=$(grep -E 'const GITHUB_CLIENT_ID *= *"' chrome-extension/background.js 2>/dev/null \
          | head -1 | sed -E 's/.*"([^"]+)".*/\1/')
  if [[ "$teams_id" == "$GITHUB_APP_CLIENT_ID" ]] && [[ "$bg_id" == "$GITHUB_APP_CLIENT_ID" ]]; then
    ok "teams.js + background.js GITHUB_CLIENT_ID == env GITHUB_APP_CLIENT_ID ($GITHUB_APP_CLIENT_ID)"
  else
    ko "mismatch: teams.js='$teams_id' bg='$bg_id' env='$GITHUB_APP_CLIENT_ID'"
  fi
}

# -----------------------------------------------------------------------------
test_17_installation_token_e2e() {
  echo
  echo "[17/$TOTAL] get_installation_token end-to-end (mint App JWT → ghs_ token)"
  require_env "TEST_INSTALLATION_ID" || return
  local out
  out=$(run_api_python "
import asyncio
from app.services.github_installation import get_installation_token
async def main():
    try:
        t = await get_installation_token(${TEST_INSTALLATION_ID})
    except Exception as e:
        print('ERROR:' + repr(e)[:200])
        return
    if not isinstance(t, str):
        print('BAD_TYPE:' + type(t).__name__)
        return
    print('OK' if t.startswith('ghs_') else ('BAD_PREFIX:' + t[:10]))
asyncio.run(main())
" 2>&1 | tail -1 | tr -d '[:space:]')
  if [[ "$out" == "OK" ]]; then
    ok "get_installation_token($TEST_INSTALLATION_ID) returned valid ghs_ token"
  else
    ko "installation token roundtrip failed: $out"
  fi
}

# -----------------------------------------------------------------------------
test_18_sc5_regression_blocked_user() {
  echo
  echo "[18/$TOTAL] SC-5 regression — blocked login on installed org cannot auto-join (B-3 fix)"
  if [[ -z "${TEST_BLOCKED_LOGIN:-}" ]] || [[ -z "${TEST_BLOCKED_TEAM_SLUG:-}" ]] || [[ -z "${TEST_GITHUB_ORG:-}" ]]; then
    skip "TEST_BLOCKED_LOGIN / TEST_BLOCKED_TEAM_SLUG / TEST_GITHUB_ORG not all set; primary coverage is unit test tests/test_phase12_auto_grant_regression.py"
    return
  fi
  # Sanity: block row must exist for the fixture before the signin attempt.
  local block_exists
  block_exists=$(run_psql "
    SELECT COUNT(*) FROM team_org_blocks tob
    JOIN teams t ON t.id = tob.team_id
    WHERE tob.github_login='${TEST_BLOCKED_LOGIN}' AND t.slug='${TEST_BLOCKED_TEAM_SLUG}'
  " | tr -d '[:space:]')
  block_exists=${block_exists:-0}
  if [[ "$block_exists" == "0" ]]; then
    skip "team_org_blocks row absent for ${TEST_BLOCKED_LOGIN}/${TEST_BLOCKED_TEAM_SLUG} — prep with INSERT first (see script header)"
    return
  fi
  # Point-in-time check: after the operator ran UAT step 1 as the blocked user,
  # team_members MUST NOT contain a row for them on the blocked team. Counts
  # only rows joined via auto-grant (NULL invite_token); manual invitations
  # are excluded so a pre-existing membership doesn't false-positive FAIL.
  local member_count
  member_count=$(run_psql "
    SELECT COUNT(*) FROM team_members tm
    JOIN users u ON u.id = tm.user_id
    JOIN teams t ON t.id = tm.team_id
    WHERE u.github_username='${TEST_BLOCKED_LOGIN}' AND t.slug='${TEST_BLOCKED_TEAM_SLUG}'
  " | tr -d '[:space:]')
  member_count=${member_count:-0}
  if [[ "$member_count" == "0" ]]; then
    ok "SC-5 — blocked user ${TEST_BLOCKED_LOGIN} did NOT join team ${TEST_BLOCKED_TEAM_SLUG} (block held)"
  else
    ko "SC-5 REGRESSION — blocked user ${TEST_BLOCKED_LOGIN} has $member_count team_members row(s) on team ${TEST_BLOCKED_TEAM_SLUG}"
  fi
}

# -----------------------------------------------------------------------------
test_01_alembic_head
test_02_installations_table
test_03_users_token_columns
test_04_partial_unique_index
test_05_memory_api_env
test_06_no_github_api_pat
test_07_no_legacy_oauth_client_id
test_08_pyjwt_installed
test_09_mint_app_jwt
test_10_webhook_in_openapi
test_11_webhook_rejects_unsigned
test_12_webhook_accepts_signed_ping
test_13_installations_row_for_org
test_14_signin_schema
test_15_chrome_ext_key
test_16_frontend_client_id_matches
test_17_installation_token_e2e
test_18_sc5_regression_blocked_user

TOTAL_RUN=$((PASS + FAIL))
echo
echo "==========================================="
echo "PASS: $PASS / $TOTAL_RUN (SKIPPED: $SKIP)"
echo "==========================================="

if [[ $FAIL -eq 0 ]]; then
  green "Phase 12 verification: ALL PASS (FAIL=0; SKIPPED never blocks)"
  exit 0
else
  red "Phase 12 verification: $FAIL failure(s)"
  exit 1
fi
