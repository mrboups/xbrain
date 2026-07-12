#!/bin/bash
# verify-phase5.sh — Phase 5 Plateforme Projets Equipe verification
# Usage: bash infrastructure/scripts/verify-phase5.sh
# Run from repo root on the VM where docker compose is running.

PASS=0; FAIL=0

run_test() {
    local name="$1"; local cmd="$2"
    if eval "$cmd" &>/dev/null; then
        PASS=$((PASS+1)); echo "PASS [$PASS] $name"
    else
        FAIL=$((FAIL+1)); echo "FAIL [$FAIL] $name"
    fi
}

# Test 1 — graphiti-service healthcheck
run_test "graphiti-service health (port 8300)" \
    "wget -qO- http://localhost:8300/v1/healthz 2>/dev/null | grep -q 'ok'"

# Test 2 — graphiti-service POST /v1/ingest -> 202
run_test "graphiti-service POST /v1/ingest -> 202 Accepted" \
    "curl -sf -o /dev/null -w '%{http_code}' -X POST http://localhost:8300/v1/ingest \
        -H 'Content-Type: application/json' \
        -d '{\"content\":\"test Phase 5 verify\",\"group_id\":\"acme\"}' | grep -q '202'"

# Test 3 — GitHub OAuth dans librechat.yaml
run_test "librechat.yaml a 'github' dans socialLogins" \
    "grep -q 'github' infrastructure/librechat/librechat.yaml.template"

# Test 4 — Migration 0007 : colonne github_username dans users
run_test "table users a la colonne github_username (migration 0007)" \
    "docker compose -f infrastructure/docker-compose.yml exec -T postgres \
        psql -U \"\${POSTGRES_USER:-xbrain}\" -d \"\${POSTGRES_DB:-xbrain}\" -tAc \
        \"SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='github_username';\" \
        2>/dev/null | grep -q github_username"

# Test 5 — brain.yaml schema parseable
run_test "brain.yaml example est parseable (docs/brain-yaml-schema.md)" \
    "python3 -c \"
import yaml, re, sys
content = open('docs/brain-yaml-schema.md').read()
match = re.search(r'\\\`\\\`\\\`yaml(.*?)\\\`\\\`\\\`', content, re.DOTALL)
if not match: sys.exit(1)
d = yaml.safe_load(match.group(1))
assert 'name' in d and 'slug' in d and 'team_scope' in d
\""

# Test 6 — Chrome extension manifest.json valide MV3
run_test "chrome-extension/manifest.json est Manifest V3 valide" \
    "python3 -c \"
import json, sys
d = json.load(open('chrome-extension/manifest.json'))
assert d['manifest_version'] == 3
assert 'background' in d
assert 'identity' in d.get('permissions', [])
\""

# Test 7 — projects dashboard Firebase config
run_test "projects-dashboard/firebase.json existe et est valide JSON" \
    "python3 -c \"import json; d=json.load(open('projects-dashboard/firebase.json')); assert 'hosting' in d\""

# Test 8 — memory-api CORS headers incluent chrome-extension (via docker exec)
run_test "memory-api CORS: Access-Control-Allow-Origin pour chrome-extension" \
    "docker compose -f infrastructure/docker-compose.yml exec -T memory-api \
        curl -sI -X OPTIONS http://127.0.0.1:8000/v1/healthz \
        -H 'Origin: chrome-extension://testextension' \
        -H 'Access-Control-Request-Method: GET' \
        2>/dev/null | grep -i 'access-control-allow-origin'"

echo ""
echo "=== Phase 5 Verification ==="
echo "PASS: $PASS / $((PASS+FAIL))"
echo "FAIL: $FAIL / $((PASS+FAIL))"
[ $FAIL -eq 0 ] && echo "ALL TESTS PASSED" && exit 0 || echo "SOME TESTS FAILED" && exit 1
