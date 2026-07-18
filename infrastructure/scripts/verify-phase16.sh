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
  # NO MSYS_NO_PATHCONV here. `-f` and `--env-file` are HOST paths that MSYS must rewrite
  # (/tmp/x -> C:\...\x); suppressing that makes docker resolve `/tmp/x` as `D:\tmp\x` and abort
  # with "couldn't find env file" — a teardown that silently no-ops and LEAKS the whole stack,
  # because the failure is swallowed by the >/dev/null redirect. MSYS_NO_PATHCONV=1 is only ever
  # correct for IN-CONTAINER paths (see the docker exec in check (h)).
  docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml \
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

# =============================================================================================
# Task 2 shared plumbing — HTTP through nginx, health polling, the env-check runner.
#
# THE HOST-PATH RULE (learned the hard way, do not "simplify" this):
#   `--env-file` / `-f` take HOST paths, so MSYS *must* be allowed to rewrite /tmp/x -> C:\...\x.
#   Setting MSYS_NO_PATHCONV=1 on those commands makes docker resolve `/tmp/x` as `D:\tmp\x` and
#   fail with "couldn't find env file". MSYS_NO_PATHCONV=1 belongs ONLY on commands carrying an
#   IN-CONTAINER path (docker exec <ctr> /path). Both forms appear below, deliberately.
#
# THE INGRESS RULE: only nginx publishes a host port (:80). memory-api is NOT host-bound, so every
#   request is addressed to 127.0.0.1:80 with an explicit `Host:` header matching the api vhost's
#   server_name (api.${XBRAIN_BASE_DOMAIN}, nginx/templates/20-api.conf.template:7). No DNS involved.
# =============================================================================================

RESP="$(mktemp)"   # response body of the last req()
HDRS="$(mktemp)"   # response headers of the last req()

API_HOST=""        # api.<XBRAIN_BASE_DOMAIN>, read from the generated env in (d)
XBT=""             # the xbt_ token minted in (e)
TEAM=""            # the solo-<hex16> team_scope minted in (e)
EMAIL=""
PASSWORD=""
BOOTED=0           # 1 once `up -d` has been issued (so cleanup knows to tear down)

# req METHOD PATH [curl args...] -> echoes the HTTP status; body in $RESP, headers in $HDRS.
# Never follows redirects (the 302 in check (g) is the assertion).
req() {
  local method="$1" path="$2"; shift 2
  curl -sS --max-time 120 -o "$RESP" -D "$HDRS" -w '%{http_code}' \
    -X "$method" -H "Host: $API_HOST" "$@" "http://127.0.0.1${path}" 2>/dev/null
}

# jget KEY — dotted-key lookup on $RESP.
jget() { PYTHONIOENCODING=utf-8 "$PY" "$JSON_HELPER" "$1" < "$RESP" 2>/dev/null; }

# run_env_check PROFILES -> exit code of the documented deploy prerequisite.
#
# `make` is not guaranteed on a dev host (it is absent on this arm64 Windows box), so when it is
# missing the recipe BODY is executed directly — logic-equivalent, not a skip. Two make semantics
# are reproduced exactly:
#   1. The recipe expands `$(COMPOSE_PROFILES)` — a MAKE variable, not the shell env.
#   2. Makefile:5 does `-include .env`, and a makefile assignment BEATS the environment. So the
#      real override form is the COMMAND-LINE one (`make env-check COMPOSE_PROFILES=saas`), which
#      is the only form that outranks the `COMPOSE_PROFILES=` line the generated .env carries.
#      That is what this passes, and what the fallback emulates.
run_env_check() {
  local profiles="$1"
  if command -v make >/dev/null 2>&1; then
    make env-check "COMPOSE_PROFILES=$profiles" >"$RESP" 2>&1
    return $?
  fi
  COMPOSE_PROFILES_ARG="$profiles" bash -c '
    source .env 2>/dev/null
    P="$COMPOSE_PROFILES_ARG"
    REQ="POSTGRES_PASSWORD BRIDGE_SHARED_SECRET OAUTH_ISSUER_URL OAUTH_RESOURCE_URL CORS_ALLOWED_ORIGIN_REGEX XBRAIN_BASE_DOMAIN AGENT_MENTION_ALIASES"
    case ",$P," in *,saas,*) REQ="$REQ GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET MEILI_MASTER_KEY OPENWEBUI_SECRET_KEY";; esac
    for v in $REQ; do
      if [ -z "${!v}" ]; then
        echo "MISSING: $v (required for profile [$P]; GOOGLE_*/MEILI_MASTER_KEY/OPENWEBUI_SECRET_KEY are needed only under the saas profile)"
        exit 1
      fi
    done
    echo "All required env vars present (profile: [$P])."
  ' >"$RESP" 2>&1
}

# =============================================================================================
# (d0) DEPLOY-LAYER env-check — SC#4. The documented deploy path is `make deploy`, whose
#      prerequisite `make env-check` reads `.env` (hardcoded). Proving `docker compose up` works is
#      NOT the same as proving the documented deploy path is unblocked: this is the layer that
#      D-16-01 broke. Non-destructive — the operator's real .env is moved aside and restored by the
#      EXIT trap (T-16-04-05).
# =============================================================================================
test_d0_deploy_env_check() {
  echo
  echo "(d0) DEPLOY-LAYER — 'make env-check' passes zero-key, still fails saas-without-saas-creds"

  if ! command -v make >/dev/null 2>&1; then
    yellow "  NOTE: 'make' is absent on this host — executing the env-check recipe BODY directly"
    yellow "        (logic-equivalent, NOT a skip; see run_env_check)."
  fi

  if [[ ! -s "$OSS_ENV" ]]; then
    ko "(d0) no generated zero-key env available (check (c) must run first)"
    return
  fi

  if [[ -e .env ]]; then
    mv -f .env "$ENV_BACKUP"; ENV_BACKED_UP=1
  fi
  cp -f "$OSS_ENV" .env; ENV_INSTALLED=1

  if run_env_check ""; then
    ok "(d0) zero-key .env passes the deploy prerequisite with COMPOSE_PROFILES unset: $(head -1 "$RESP")"
  else
    ko "(d0) zero-key .env FAILED the deploy prerequisite (SC#4 blocked): $(head -3 "$RESP")"
  fi

  if run_env_check "saas"; then
    ko "(d0) COMPOSE_PROFILES=saas PASSED without saas creds — the saas guard is broken: $(head -1 "$RESP")"
  else
    local msg; msg="$(head -1 "$RESP")"
    if echo "$msg" | grep -qE 'MISSING: (GOOGLE_[A-Z_]+|MEILI_MASTER_KEY|OPENWEBUI_SECRET_KEY)'; then
      ok "(d0) saas profile still fails without saas creds, naming the var: $msg"
    else
      ko "(d0) saas profile failed but did not name a saas var: $msg"
    fi
  fi

  restore_env
}

# =============================================================================================
# (d) REAL BOOT — `up -d --build` the 10 core from the generated zero-key env, then assert ACTUAL
#     docker health state. Not a `config` parse: SKIP here is FAIL (the Phase-19 CR-01 lesson).
# =============================================================================================
test_d_real_boot() {
  echo
  echo "(d) REAL BOOT — up -d --build the 10 core from the zero-key env; assert ACTUAL health state"

  # container_name: is GLOBAL and fixed (xbrain-postgres etc.), NOT namespaced by -p. Booting on
  # top of a live stack would collide on those names and tear down the operator's work (T-16-04-04).
  local running
  running="$(docker ps -a --filter 'name=^xbrain-' --format '{{.Names}}' 2>/dev/null | tr '\n' ' ')"
  if [[ -n "${running// /}" ]]; then
    ko "(d) refusing to boot: xbrain-* containers already exist ($running). container_name: is global —
      booting would clobber a running stack. Bring it down first, then re-run. (SKIP=FAIL: this is
      reported as a FAILURE, not a skip, so the gate can never go green without a real boot.)"
    return 1
  fi

  API_HOST="api.$(grep -E '^XBRAIN_BASE_DOMAIN=' "$OSS_ENV" | head -1 | cut -d= -f2-)"
  echo "      ingress: http://127.0.0.1:80 with Host: $API_HOST (only nginx publishes a host port)"

  BOOTED=1
  # NOTE: no MSYS_NO_PATHCONV here — -f/--env-file are HOST paths and MUST be MSYS-converted.
  if ! env -u COMPOSE_PROFILES docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml \
        --env-file "$OSS_ENV" up -d --build >"$BOOT_LOG" 2>&1; then
    ko "(d) 'up -d --build' failed. Tail:
$(tail -25 "$BOOT_LOG")"
    return 1
  fi
  ok "(d) 'up -d --build' returned 0 (all 10 core services created from the zero-key env)"

  # Poll ACTUAL health. A ~8.5s Neo4j DNS timeout during memory-api startup is EXPECTED here —
  # Neo4j is NOT part of core; the driver simply resolves to nothing and the app carries on.
  local deadline=$(( $(date +%s) + 600 ))
  local svc cid state pending
  while :; do
    pending=""
    for svc in $CORE; do
      cid="$(docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml \
              --env-file "$OSS_ENV" ps -q "$svc" 2>/dev/null | head -1)"
      if [[ -z "$cid" ]]; then pending="$pending $svc(no-container)"; continue; fi
      state="$(docker inspect --format \
        '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null)"
      case "$state" in
        healthy|running) ;;
        *) pending="$pending $svc($state)" ;;
      esac
    done
    [[ -z "$pending" ]] && break
    [[ "$(date +%s)" -ge "$deadline" ]] && break
    sleep 5
  done

  local failed="" line
  for svc in $CORE; do
    cid="$(docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml \
            --env-file "$OSS_ENV" ps -q "$svc" 2>/dev/null | head -1)"
    if [[ -z "$cid" ]]; then failed="$failed $svc=absent"; continue; fi
    state="$(docker inspect --format \
      '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null)"
    line="$(docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$cid" 2>/dev/null)"
    echo "      $svc: $line"
    case "$state" in
      healthy|running) ;;
      *) failed="$failed $svc=$state" ;;
    esac
  done

  if [[ -z "$failed" ]]; then
    ok "(d) all 10 core services reached healthy/running from a REAL build+boot (incl. the four build: services)"
    return 0
  fi
  ko "(d) core services not healthy:$failed"
  for svc in $failed; do
    svc="${svc%%=*}"
    echo "      --- logs $svc (tail 15) ---"
    docker compose -p "$PROJECT" -f infrastructure/docker-compose.yml --env-file "$OSS_ENV" \
      logs --tail=15 "$svc" 2>&1 | sed 's/^/        /'
  done
  return 1
}

# =============================================================================================
# (e) SC#3 register + login — a real account on a real Postgres, through nginx.
# =============================================================================================
test_e_auth() {
  echo
  echo "(e) SC#3 auth — POST /v1/auth/local/register -> xbt_ + solo- team_scope; login -> xbt_"

  EMAIL="p16-$(openssl rand -hex 6)@example.com"
  PASSWORD="p16-$(openssl rand -hex 12)"

  local code tok team
  code=$(req POST /v1/auth/local/register -H 'Content-Type: application/json' \
          --data "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
  tok="$(jget xbt_token)"; team="$(jget team_scope)"
  if [[ "$code" == "200" && "$tok" == xbt_* && "$team" == solo-* ]]; then
    ok "(e) register -> 200, xbt_ token minted, team_scope='$team'"
    XBT="$tok"; TEAM="$team"
  else
    ko "(e) register: code=$code token_prefix='${tok:0:4}' team_scope='$team' body=$(head -c 300 "$RESP")"
    return 1
  fi

  code=$(req POST /v1/auth/local/login -H 'Content-Type: application/json' \
          --data "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
  tok="$(jget xbt_token)"
  if [[ "$code" == "200" && "$tok" == xbt_* ]]; then
    ok "(e) login -> 200 with a fresh xbt_ token (argon2 verify against the live Postgres)"
  else
    ko "(e) login: code=$code body=$(head -c 300 "$RESP")"
  fi
}

# =============================================================================================
# (f) SC#3 doc ingest + KEYLESS semantic retrieval.
#
#     REAL-PATH NOTE (do not "fix" this by moving the phrase out of the caption): media.py:111
#     embeds `caption or filename` — the uploaded BYTES are archived to MinIO but are not the
#     embedded text. So the distinctive phrase is sent as the caption AND written into the file;
#     the retrieval assertion therefore proves what actually runs. File-body text extraction is
#     not implemented on this route and this gate does not pretend otherwise.
# =============================================================================================
test_f_doc_keyless_retrieval() {
  echo
  echo "(f) SC#3 doc — /v1/media/upload then KEYLESS semantic retrieval with a truth_level"

  if [[ -z "$XBT" ]]; then ko "(f) no xbt_ token from (e) — cannot exercise the doc path"; return 1; fi

  local phrase="acme quarterly revenue was forty two million"
  printf '%s\n' "$phrase" > "$DOCFILE"

  local code item_id
  code=$(req POST /v1/media/upload \
          -H "Authorization: Bearer $XBT" -H "X-Team-Scope: $TEAM" \
          -F "file=@$(host_path "$DOCFILE");type=text/plain" \
          -F "caption=$phrase" -F "truth_level=WORKING")
  item_id="$(jget item_id)"
  if [[ "$code" == "201" && -n "$item_id" ]]; then
    ok "(f) upload -> 201 item_id=$item_id (bytes in MinIO, item embedded by the LOCAL provider)"
  else
    ko "(f) upload: code=$code body=$(head -c 300 "$RESP")"
    return 1
  fi

  # Semantic query — deliberately NOT the phrase verbatim: an exact-substring match would prove
  # nothing about embeddings. This wording only retrieves if a real vector search ran, and it ran
  # with EMBEDDINGS_PROVIDER=local and no OPENAI_API_KEY anywhere in the environment.
  local query='how much revenue did acme make in the quarter'
  code=$(req GET "/v1/memory/search?q=$(printf '%s' "$query" | sed 's/ /%20/g')&limit=10" \
          -H "Authorization: Bearer $XBT" -H "X-Team-Scope: $TEAM")
  if [[ "$code" != "200" ]]; then
    ko "(f) search: code=$code body=$(head -c 300 "$RESP")"
    return 1
  fi

  local verdict
  verdict="$(PYTHONIOENCODING=utf-8 "$PY" "$SEARCH_HELPER" acme quarterly revenue forty two million < "$RESP" 2>&1)"
  if [[ "$verdict" == OK* ]]; then
    ok "(f) KEYLESS semantic retrieval (query != stored text) returned the doc WITH a truth_level: ${verdict#OK }"
  else
    ko "(f) keyless retrieval failed: $verdict"
  fi
}

# =============================================================================================
# (g) SC#3 connector consent via LOCAL auth (D-16-02). Asserts BOTH halves of the security
#     property: with GITHUB_APP_CLIENT_ID empty the AS renders its OWN login form instead of
#     302-ing into a client_id-empty github.com URL, AND a code is minted only after a valid
#     credential proof.
# =============================================================================================
test_g_connector_local_consent() {
  echo
  echo "(g) SC#3 connector — .well-known 200; /oauth/authorize renders the LOCAL form; local POST mints a code"

  if [[ -z "$XBT" ]]; then ko "(g) no account from (e) — cannot exercise the connector path"; return 1; fi

  local code
  code=$(req GET /.well-known/oauth-authorization-server)
  if [[ "$code" == "200" ]]; then
    ok "(g) /.well-known/oauth-authorization-server -> 200 (AS discovery is core, never gated)"
  else
    ko "(g) AS discovery: code=$code body=$(head -c 200 "$RESP")"
  fi

  local redirect_uri="http://127.0.0.1:9999/p16-callback"
  code=$(req POST /oauth/register -H 'Content-Type: application/json' \
          --data "{\"client_name\":\"p16-gate\",\"redirect_uris\":[\"$redirect_uri\"]}")
  local client_id; client_id="$(jget client_id)"
  if [[ "$code" == "201" && -n "$client_id" ]]; then
    ok "(g) DCR (RFC 7591) minted a public client: client_id=$client_id"
  else
    ko "(g) DCR: code=$code body=$(head -c 300 "$RESP")"
    return 1
  fi

  local verifier challenge
  verifier="$(openssl rand -hex 32)"
  challenge="$(PYTHONIOENCODING=utf-8 "$PY" -c \
    'import base64,hashlib,sys; print(base64.urlsafe_b64encode(hashlib.sha256(sys.argv[1].encode()).digest()).decode().rstrip("="))' \
    "$verifier")"

  local enc_redirect; enc_redirect="$(PYTHONIOENCODING=utf-8 "$PY" -c \
    'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$redirect_uri")"

  code=$(req GET "/oauth/authorize?response_type=code&client_id=${client_id}&redirect_uri=${enc_redirect}&code_challenge=${challenge}&code_challenge_method=S256&resource=")
  local loc; loc="$(grep -i '^location:' "$HDRS" | head -1 | tr -d '\r')"
  if [[ "$code" == "200" ]] && grep -q 'action="/oauth/authorize/local"' "$RESP"; then
    ok "(g) /oauth/authorize -> 200 rendering the LOCAL login form (action=/oauth/authorize/local), NOT a 302 to github.com"
  else
    ko "(g) /oauth/authorize: code=$code location='$loc' — expected a 200 local form. body=$(head -c 300 "$RESP")"
    return 1
  fi
  if echo "$loc" | grep -qi 'github.com'; then
    ko "(g) /oauth/authorize redirected to github.com with GITHUB_APP_CLIENT_ID empty (D-16-02 regression): $loc"
    return 1
  fi

  local state; state="$(PYTHONIOENCODING=utf-8 "$PY" "$STATE_HELPER" < "$RESP")"
  if [[ -z "$state" ]]; then ko "(g) could not scrape the hidden signed state from the local login form"; return 1; fi

  # Negative half first: a WRONG password must NOT mint a code.
  code=$(req POST /oauth/authorize/local \
          --data-urlencode "email=$EMAIL" --data-urlencode "password=wrong-$PASSWORD" \
          --data-urlencode "state=$state")
  loc="$(grep -i '^location:' "$HDRS" | head -1 | tr -d '\r')"
  if [[ "$code" == "302" ]] || echo "$loc" | grep -q 'code='; then
    ko "(g) a WRONG password minted an authorization code (auth bypass): code=$code location='$loc'"
    return 1
  fi
  ok "(g) wrong password does NOT mint a code (code=$code, no redirect) — consent requires a real credential proof"

  # Positive half: the correct password mints a real code at the REGISTERED redirect_uri.
  code=$(req POST /oauth/authorize/local \
          --data-urlencode "email=$EMAIL" --data-urlencode "password=$PASSWORD" \
          --data-urlencode "state=$state")
  loc="$(grep -i '^location:' "$HDRS" | head -1 | tr -d '\r')"
  if [[ "$code" == "302" ]] && [[ "$loc" == *"$redirect_uri"* ]] && echo "$loc" | grep -q 'code='; then
    ok "(g) POST /oauth/authorize/local -> 302 to the REGISTERED redirect_uri carrying a minted ?code= (zero-key connector consent works)"
  else
    ko "(g) local consent: code=$code location='$loc' body=$(head -c 300 "$RESP")"
  fi
}

# =============================================================================================
# (h) SC#3 clip at the API level (D-16-03) — the backend path the extension uses. Asserted at the
#     ROW level, not on the 201: a 201 only proves the handler returned, the row proves it landed.
# =============================================================================================
test_h_clip() {
  echo
  echo "(h) SC#3 clip — POST /v1/memory/upsert source=manual-clip -> 201 + a real memory_items row"

  if [[ -z "$XBT" ]]; then ko "(h) no xbt_ token from (e) — cannot exercise the clip path"; return 1; fi

  local now item_id code
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  item_id="$(PYTHONIOENCODING=utf-8 "$PY" -c 'import uuid; print(uuid.uuid4())')"

  code=$(req POST /v1/memory/upsert \
          -H "Authorization: Bearer $XBT" -H "X-Team-Scope: $TEAM" \
          -H 'Content-Type: application/json' \
          --data "{\"item\":{\"id\":\"$item_id\",\"team_scope\":\"$TEAM\",\"content\":\"clipped from a web page by the phase-16 gate\",\"source\":\"manual-clip\",\"truth_level\":\"WORKING\",\"visibility\":\"team\",\"confidence\":1.0,\"validation_status\":\"pending\",\"created_at\":\"$now\",\"updated_at\":\"$now\"}}")
  if [[ "$code" == "201" ]]; then
    ok "(h) clip upsert -> 201 (id=$(jget id))"
  else
    ko "(h) clip upsert: code=$code body=$(head -c 300 "$RESP")"
    return 1
  fi

  # container_name: is the literal global `xbrain-postgres` even under -p xbrain-p16.
  # MSYS_NO_PATHCONV=1 here is CORRECT: the args after the container name are in-container tokens.
  local n
  n="$(MSYS_NO_PATHCONV=1 docker exec xbrain-postgres psql -U xbrain -d xbrain -tAc \
        "SELECT count(*) FROM memory_items WHERE source='manual-clip'" 2>/dev/null | tr -d '[:space:]')"
  if [[ "${n:-0}" =~ ^[0-9]+$ ]] && [[ "${n:-0}" -ge 1 ]]; then
    ok "(h) memory_items row landed in Postgres: count(source='manual-clip') = $n"
  else
    ko "(h) no memory_items row for source='manual-clip' (psql returned '$n') — the 201 did not persist"
  fi
}

# =============================================================================================
# (i) SC#2 boundary — COMPOSE_PROFILES unset means the opt-in services are not merely "not in the
#     config", they are NOT RUNNING.
# =============================================================================================
test_i_profile_boundary() {
  echo
  echo "(i) SC#2 boundary — zero opt-in containers running with COMPOSE_PROFILES unset"

  local names leaked="" c
  names="$(docker ps --format '{{.Names}}' 2>/dev/null)"
  for c in $OPT_IN_CONTAINERS; do
    if echo "$names" | grep -qx "$c"; then leaked="$leaked $c"; fi
  done
  if [[ -z "$leaked" ]]; then
    ok "(i) none of the $(echo $OPT_IN_CONTAINERS | wc -w) opt-in containers are running (integrations/ops/saas stayed off)"
  else
    ko "(i) opt-in containers running with COMPOSE_PROFILES unset:$leaked"
  fi
}

test_a_config
test_b_env_drift
test_c_oss_init
test_d0_deploy_env_check

# The live block is strictly ordered: (e)-(i) are meaningless without a booted core, and reporting
# them as SKIP would be exactly the false-green this gate exists to prevent. So if (d) fails, the
# downstream checks are recorded as FAILURES with the boot as their cause — never skipped.
if test_d_real_boot; then
  test_e_auth
  test_f_doc_keyless_retrieval
  test_g_connector_local_consent
  test_h_clip
  test_i_profile_boundary
else
  red "  The core did not boot — (e)-(i) cannot run. Recording them as FAILURES, not SKIPs"
  red "  (SKIP=FAIL: a gate that goes green without a live core is the defect class this gate exists to catch)."
  ko "(e) SC#3 register/login — not exercised: the core never booted"
  ko "(f) SC#3 doc ingest + keyless retrieval — not exercised: the core never booted"
  ko "(g) SC#3 connector local consent — not exercised: the core never booted"
  ko "(h) SC#3 clip -> memory_items row — not exercised: the core never booted"
  ko "(i) SC#2 opt-in boundary — not exercised: the core never booted"
fi

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi
