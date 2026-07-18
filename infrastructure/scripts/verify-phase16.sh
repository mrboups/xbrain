#!/usr/bin/env bash
#
# verify-phase16.sh — Phase 16 (OSS Light Packaging) acceptance gate.
#
# The gate lesson (hard): seven-plus defects across P14/15/18 and the Phase-19 CR-01
# blocker all shared ONE cause — a check that never traversed the REAL deployment path.
# So this gate does a REAL `make oss-init` -> `docker compose up -d --build` of ALL 10
# OSS-light core services (the FIRST time the four `build:` core services — mcp-gateway,
# mcp-scraper, mcp-brain, brain-janitor — are compose-built and booted TOGETHER, which
# Phase 15's gate deliberately never did), asserts ACTUAL docker health state, verifies the
# documented `make env-check` DEPLOY-LAYER prerequisite passes zero-key, then walks the whole
# SC#3 flow (register -> keyless doc ingest+retrieval+truth-level -> connector consent via
# local auth -> clip) over REAL HTTP through nginx. A `docker compose config` parse, a mocked
# boot, or a SKIP does NOT satisfy this gate: SKIP is treated as FAIL for the deploy-layer +
# live-boot + SC#3 checks.
#
# Checks:
#   (a) CONFIG      — `docker compose config --services` (COMPOSE_PROFILES unset) == exactly
#                     the 10 core BY NAME; `--profiles` == `integrations ops saas`.
#   (b) ENV-DRIFT   — static audit of .env.example (D-16-05 regression guard): MinIO root
#                     password is [required] not [optional], the stale SaaS-only header is gone,
#                     the REQUIRED — core boot header exists, and the saas-only secrets no longer
#                     carry [required].
#   (c) OSS-INIT    — `oss-init.sh --force` writes a bootable ZERO-external-key env
#                     (EDITION=oss, EMBEDDINGS_PROVIDER=local, UVICORN_WORKERS=1, MinIO pw >= 8,
#                     no OpenAI/Google/GitHub/Anthropic key, DATABASE_URL pw == POSTGRES_PASSWORD,
#                     no __FILL__ placeholders).
#   (d0) DEPLOY     — the documented `make env-check` deploy prerequisite passes zero-key
#                     (COMPOSE_PROFILES unset) and still fails a saas-without-saas-creds profile.
#   (d) REAL BOOT   — `up -d --build` the 10 core from the generated zero-key env; assert healthy
#                     (postgres/qdrant/minio/centrifugo/memory-api), nginx via its own vhost, and
#                     the 4 build services Up + not restarting.
#   (e) SC#3 auth   — register -> xbt_ + solo- team_scope; login -> xbt_.
#   (f) SC#3 doc    — media upload + KEYLESS semantic retrieval with a truth_level.
#   (g) SC#3 connector — .well-known 200; DCR; /oauth/authorize renders the LOCAL login form
#                     (GITHUB_APP_CLIENT_ID empty, NO 302 to github.com); POST /oauth/authorize/local
#                     -> 302 to the registered redirect_uri carrying a minted ?code=.
#   (h) SC#3 clip   — POST /v1/memory/upsert (source=manual-clip) -> 201 + a memory_items row.
#   (i) SC#2 bound  — zero opt-in containers running with COMPOSE_PROFILES unset.
#
# Exit code: 0 when FAIL == 0 (regardless of SKIP), 1 when FAIL > 0.
#
# Usage:
#   bash infrastructure/scripts/verify-phase16.sh
#   make verify-phase16
# Run from anywhere inside the repo (the script cd's to the repo root itself).
#
# Requires: bash, docker, docker compose v2+, python3 (or python), openssl. Does NOT require jq
# (python does the JSON assertions) and does NOT require make (if `make` is absent the env-check
# recipe body is run directly, byte-equivalently).
#
# Host notes (load-bearing for THIS gate's trustworthiness):
#   - Git-Bash docker host-mount / path trap: use cygpath + MSYS_NO_PATHCONV=1 for any path passed
#     into a container. A ~8.5s Neo4j DNS timeout at boot is EXPECTED (Neo4j is NOT in core), not a
#     failure. Only nginx publishes a host port (:80); memory-api is reached THROUGH nginx.

set -uo pipefail   # NOT -e — every check runs independently; the summary line is the truth

PASS=0
FAIL=0
SKIP=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

ok()   { green "  PASS: $*"; PASS=$((PASS+1)); }
ko()   { red   "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip() { yellow "  SKIPPED: $*"; SKIP=$((SKIP+1)); }

PY="$(command -v python3 || command -v python || true)"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

echo "=== Phase 16 Verification (OSS Light Packaging — PKG-01) ==="

PROJECT="xbrain-p16"

# The 10 OSS-light core services (COMPOSE_PROFILES unset).
CORE="brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant"

# Container-name deny-list — copied verbatim from verify-phase15.sh:408. container_name: is EXPLICIT
# and GLOBAL in docker-compose.yml (NOT namespaced to the -p project), and it is `xbrain-<service>`
# for every opt-in service EXCEPT xbrain-backup, whose service name already carries the prefix.
OPT_IN_CONTAINERS="xbrain-neo4j xbrain-graphiti-service xbrain-langfuse xbrain-langfuse-worker xbrain-langfuse-clickhouse xbrain-langfuse-redis xbrain-mcp-calendar xbrain-mcp-drive-read xbrain-mcp-deck xbrain-mcp-github xbrain-granola-sync xbrain-drive-sync xbrain-searxng xbrain-agent-runtime xbrain-session-bridge xbrain-librechat xbrain-librechat-mongo xbrain-librechat-meili xbrain-librechat-bridge xbrain-openwebui xbrain-openwebui-pipeline xbrain-backup"

# --- Temp files (declared up front so the single EXIT trap always cleans them) ----------------
HERMETIC_ENV="$(mktemp)"       # phase15-style hermetic env for the config-layer check (a)
OSS_ENV="$(mktemp)"            # the generated zero-external-key env (checks c/d0/d/e/f/g/h)
ENV_BACKUP="$(mktemp)"         # backup slot for the operator's real .env (d0, non-destructive)
BOOT_LOG="$(mktemp)"
JSON_HELPER="$(mktemp).py"     # stdin JSON -> a dotted key (python, not jq)
STATE_HELPER="$(mktemp).py"    # stdin HTML -> the hidden `state` input value
SEARCH_HELPER="$(mktemp).py"   # stdin search JSON -> OK/BAD content+truth_level assertion
DOCFILE="$(mktemp).txt"        # the small doc uploaded in check (f)

ENV_BACKED_UP=0
ENV_INSTALLED=0

# host_path <posix-path> — echo a Windows path for a bind-mount/@upload on Git-Bash, else the path
# unchanged. Callers that pass it into `docker` prefix the command with MSYS_NO_PATHCONV=1 so the
# in-container target path is not rewritten by MSYS either (the Git-Bash host-mount trap).
host_path() {
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) cygpath -m "$1" 2>/dev/null || echo "$1" ;;
    *)                    echo "$1" ;;
  esac
}

# restore_env — idempotent: remove the throwaway .env installed by (d0) and put the operator's real
# .env back if we moved it. Called at the end of (d0) AND from the EXIT trap (T-16-04-05).
restore_env() {
  if [[ "$ENV_INSTALLED" == "1" ]]; then rm -f .env; ENV_INSTALLED=0; fi
  if [[ "$ENV_BACKED_UP" == "1" ]]; then mv -f "$ENV_BACKUP" .env; ENV_BACKED_UP=0; fi
}

cleanup() {
  MSYS_NO_PATHCONV=1 docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml \
    --env-file "$OSS_ENV" down -v --remove-orphans >/dev/null 2>&1
  restore_env
  rm -f "$HERMETIC_ENV" "$OSS_ENV" "$ENV_BACKUP" "$BOOT_LOG" \
        "$JSON_HELPER" "$STATE_HELPER" "$SEARCH_HELPER" "$DOCFILE"
}
trap cleanup EXIT

# --- python helpers (data via stdin, keys via argv — never a path baked into -c) --------------
cat > "$JSON_HELPER" <<'PYEOF'
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    print("")
    sys.exit(0)
cur = data
for part in sys.argv[1].split("."):
    if isinstance(cur, list):
        try:
            cur = cur[int(part)]
        except (ValueError, IndexError):
            cur = None
    elif isinstance(cur, dict):
        cur = cur.get(part)
    else:
        cur = None
    if cur is None:
        break
print("" if cur is None else cur)
PYEOF

cat > "$STATE_HELPER" <<'PYEOF'
import re
import sys

html = sys.stdin.read()
m = re.search(r'name=["\']state["\']\s+value=["\']([^"\']+)["\']', html)
if not m:
    m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']state["\']', html)
print(m.group(1) if m else "")
PYEOF

# search assertion: argv[1..] are words that MUST all appear (case-insensitive) in some hit's
# item.content, and that hit MUST carry a non-empty truth_level. Prints OK/BAD.
cat > "$SEARCH_HELPER" <<'PYEOF'
import json
import sys

words = [w.lower() for w in sys.argv[1:]]
try:
    hits = json.load(sys.stdin)
except Exception as exc:
    print(f"BAD could not parse search JSON: {exc}")
    sys.exit(0)
if not isinstance(hits, list) or not hits:
    print(f"BAD no hits returned (got {type(hits).__name__})")
    sys.exit(0)
for h in hits:
    item = h.get("item") or {}
    content = (item.get("content") or "").lower()
    if all(w in content for w in words):
        tl = item.get("truth_level")
        if tl:
            print(f"OK matched content={item.get('content')!r} truth_level={tl!r} score={h.get('score')}")
            sys.exit(0)
        print(f"BAD content matched but truth_level missing: {item!r}")
        sys.exit(0)
print(f"BAD no hit contained all of {words}; top hit content="
      f"{(hits[0].get('item') or {}).get('content')!r}")
PYEOF

# =============================================================================================
# (a) CONFIG — the bare (no-profile) core is exactly the 10 named services, and the only profiles
#     are integrations/ops/saas. Built from a phase15-style hermetic env (grep -vE + clean KEY=value,
#     NO inline comments — Pitfall 5). Values are irrelevant to `config`; only the service graph is.
# =============================================================================================
build_hermetic_env() {
  grep -vE '^(OAUTH_ISSUER_URL|OAUTH_RESOURCE_URL|POSTGRES_PASSWORD|DATABASE_URL|NEO4J_PASSWORD|MINIO_ROOT_PASSWORD|BRIDGE_SHARED_SECRET|XBRAIN_BASE_DOMAIN|EDITION|COMPOSE_PROFILES)=' \
    .env.example > "$HERMETIC_ENV"
  cat >> "$HERMETIC_ENV" <<'EOF'
OAUTH_ISSUER_URL=http://api.p16.test
OAUTH_RESOURCE_URL=http://mcp.p16.test/mcp
POSTGRES_PASSWORD=p16testpassword
DATABASE_URL=postgresql+asyncpg://xbrain:p16testpassword@postgres:5432/xbrain
NEO4J_PASSWORD=p16testpassword
MINIO_ROOT_PASSWORD=p16testpassword
BRIDGE_SHARED_SECRET=p16testbridgesecret
XBRAIN_BASE_DOMAIN=p16.test
EOF
}

test_a_config() {
  echo
  echo "(a) CONFIG — bare core is exactly the 10 services BY NAME; profiles == integrations ops saas"
  build_hermetic_env
  local DC=(docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml --env-file "$HERMETIC_ENV")

  local actual expected d
  actual=$(env -u COMPOSE_PROFILES "${DC[@]}" config --services 2>/dev/null | sort)
  expected=$(printf '%s\n' $CORE | sort)
  d=$(diff <(echo "$expected") <(echo "$actual"))
  if [[ -z "$d" ]]; then
    ok "bare-core diff empty (10/10, by name): $(echo $actual | tr '\n' ' ')"
  else
    ko "bare-core diff non-empty (expected vs actual):
$d"
  fi

  local profiles
  profiles=$(env -u COMPOSE_PROFILES "${DC[@]}" config --profiles 2>/dev/null | sort | tr '\n' ' ')
  if [[ "$profiles" == "integrations ops saas " ]]; then
    ok "profiles == 'integrations ops saas' (no pro)"
  else
    ko "profiles='$profiles' (expected 'integrations ops saas ')"
  fi
}

# =============================================================================================
# (b) ENV-DRIFT AUDIT — static assertions against .env.example (D-16-05 regression guard).
# =============================================================================================
test_b_env_drift() {
  echo
  echo "(b) ENV-DRIFT — .env.example: MinIO pw [required], no stale SaaS-only header, saas secrets un-[required]"

  local mline
  mline=$(grep -E '^MINIO_ROOT_PASSWORD=' .env.example || true)
  if echo "$mline" | grep -q '\[required\]' && ! echo "$mline" | grep -q '\[optional\]'; then
    ok "MINIO_ROOT_PASSWORD is tagged [required] (not [optional]) — the core minio 8-char floor is honored"
  else
    ko "MINIO_ROOT_PASSWORD tag wrong (must be [required], not [optional]): '$mline'"
  fi

  local n_stale
  n_stale=$(grep -c 'SaaS-only' .env.example || true)
  if [[ "${n_stale:-0}" -eq 0 ]]; then
    ok "stale 'SaaS-only' header is gone (grep -c == 0)"
  else
    ko "stale 'SaaS-only' header still present ($n_stale occurrences)"
  fi

  if grep -q 'REQUIRED — core boot' .env.example; then
    ok "the 'REQUIRED — core boot' section header exists"
  else
    ko "the 'REQUIRED — core boot' section header is missing"
  fi

  local v line still_required=""
  for v in MEILI_MASTER_KEY JWT_SECRET CREDS_KEY CREDS_IV MONGO_URI PIPELINE_API_KEY; do
    line=$(grep -E "^$v=" .env.example || true)
    if echo "$line" | grep -q '\[required\]'; then
      still_required="$still_required $v"
    fi
  done
  if [[ -z "$still_required" ]]; then
    ok "MEILI_MASTER_KEY/JWT_SECRET/CREDS_KEY/CREDS_IV/MONGO_URI/PIPELINE_API_KEY no longer carry [required]"
  else
    ko "these saas-only secrets still carry [required]:$still_required"
  fi
}

# =============================================================================================
# (c) OSS-INIT — run the generator and assert it wrote a bootable ZERO-external-key env.
# =============================================================================================
test_c_oss_init() {
  echo
  echo "(c) OSS-INIT — oss-init.sh writes a bootable zero-external-key env"

  if ! bash infrastructure/scripts/oss-init.sh --force "$OSS_ENV" >/dev/null 2>&1; then
    ko "oss-init.sh --force '$OSS_ENV' failed to run"
    return
  fi

  local fails=""
  grep -q '^EDITION=oss$'               "$OSS_ENV" || fails="$fails EDITION!=oss"
  grep -q '^EMBEDDINGS_PROVIDER=local$' "$OSS_ENV" || fails="$fails EMBEDDINGS_PROVIDER!=local"
  grep -q '^UVICORN_WORKERS=1$'         "$OSS_ENV" || fails="$fails UVICORN_WORKERS!=1"

  local mp
  mp=$(grep -E '^MINIO_ROOT_PASSWORD=' "$OSS_ENV" | head -1 | cut -d= -f2-)
  [[ "${#mp}" -ge 8 ]] || fails="$fails MINIO_ROOT_PASSWORD<8"

  # Zero external keys: none of these may be present with a non-empty value.
  if grep -qE '^(OPENAI_API_KEY|GOOGLE_CLIENT_ID|GITHUB_APP_CLIENT_ID|ANTHROPIC_API_KEY)=.+' "$OSS_ENV"; then
    fails="$fails external_key_populated"
  fi

  local pw
  pw=$(grep -E '^POSTGRES_PASSWORD=' "$OSS_ENV" | head -1 | cut -d= -f2-)
  grep -q "postgresql+asyncpg://xbrain:${pw}@postgres" "$OSS_ENV" \
    || fails="$fails DATABASE_URL_pw_mismatch"

  local n_fill
  n_fill=$(grep -c '__FILL__' "$OSS_ENV" || true)
  [[ "${n_fill:-0}" -eq 0 ]] || fails="$fails ${n_fill}x__FILL__"

  if [[ -z "$fails" ]]; then
    ok "generated env is zero-key + bootable (EDITION=oss, local embeddings, 1 worker, MinIO pw >= 8, DATABASE_URL pw == POSTGRES_PASSWORD, no __FILL__)"
  else
    ko "generated env failed:$fails"
  fi
}

# --- Task 2 (deploy-layer + boot + SC#3 HTTP walk) checks are appended below ---
# TASK2_CHECKS_ANCHOR

test_a_config
test_b_env_drift
test_c_oss_init

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi
