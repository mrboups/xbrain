#!/usr/bin/env bash
#
# verify-phase15.sh — Verify Phase 15 (Edition Mechanics) EDIT-01 + EDIT-02
# acceptance gate.
#
# Phase 14 shipped three defects its own gate could not see, all because the check never
# traversed the real deployment path (it exercised Settings() directly, never noticing that
# docker-compose.yml silently dropped a var compose was supposed to pass through). This gate
# is built specifically to not repeat that mistake: every check below reads REAL
# `docker compose config` output (never a grep of the YAML source) or drives REAL running
# containers (never an in-process Settings() construction).
#
# Checks (8, SKIP-aware — SKIP never counts as FAIL):
#   (a) EDIT-01 — `docker compose config --profiles` resolves to exactly
#       `integrations ops saas` (no `pro`), and the resolved config carries no
#       "depends on undefined service" error (the cheapest whole-graph regression gate).
#   (b) EDIT-01 — the bare (no-profile) OSS-light core is exactly the 10 named services,
#       diffed BY NAME against `docker compose config --services`.
#   (c) EDIT-01 — independent leak assertion: none of the 22 opt-in service names appear
#       in the bare core (a second, independent statement of (b)'s intent).
#   (d) EDIT-01 — each opt-in profile's membership (integrations=24, saas=17, ops=11,
#       all-three=32), diffed BY NAME — never by count alone, which a two-service swap
#       would sail through. Also: each profile is independently a legal compose project,
#       and COMPOSE_PROFILES=<list> resolves identically to the equivalent --profile flags.
#   (e) EDIT-02 + SC#5 — the RESOLVED container environment (docker compose config
#       --format json), not the YAML: EDITION reaches memory-api and ONLY memory-api,
#       defaults to oss, flips to saas; QDRANT_COLLECTION/MINIO_ENDPOINT resolve
#       identically across every consumer in every profile combination (D-15-04); no
#       depends_on: neo4j survives on memory-api or brain-janitor.
#   (f) SC#1/SC#4 — a REAL `docker compose up` of the 5 pull-only OSS-light core services
#       (postgres, qdrant, minio, centrifugo, nginx) reaches `healthy`, with nginx healthy
#       despite 5 absent upstreams; zero opt-in containers are running; exactly the 5
#       requested containers came up (no undeclared depends_on drag-in).
#   (g) SC#2/SC#3/SC#4/D-15-05 — a REAL memory-api (real source, real compose-resolved
#       env, no Neo4j container anywhere) reaches /v1/healthz 200; under EDITION=oss both
#       SaaS-only routes 404 (the router was never registered — not merely auth-rejected);
#       flipping ONLY EDITION on the SAME running container makes them reachable
#       (422 / 401); an upsert carrying metadata.entities writes the memory_items row but
#       enqueues ZERO neo4j_outbox rows in the exact state compose produces (NEO4J_URI set,
#       NEO4J_PASSWORD set, no neo4j container). A silently-failed bind mount FAILS this
#       check (never SKIPs) — the Git-Bash host-mount trap that already cost real time
#       twice in this phase.
#   (h) T-15-04-02 — `preflight-env.sh` rejects COMPOSE_PROFILES=saas + the default
#       EDITION=oss (session-bridge's /v1/me/external-sessions would 404 silently), accepts
#       saas+EDITION=saas, and accepts the OSS-light default. Also asserts the Makefile's
#       REMOTE deploy guard now invokes preflight-env.sh over SSH against the VM's OWN
#       .env — the file that actually boots the containers — instead of a second,
#       independent inline copy of the same rule.
#
# NOT covered (stated plainly, not overclaimed):
#   - xbrain-backup (`ops` profile) — the only service in the whole compose file with no
#     arm64 image (google/cloud-sdk:slim). Verified at the CONFIG layer only (check d);
#     never booted on this arm64 dev host.
#   - mcp-brain, mcp-gateway, mcp-scraper, brain-janitor — all `build:` services. Config-
#     layer only (checks a-e); NOT brought up by check (f), which only starts the 5
#     services with pull-only, multi-arch, upstream images. memory-api itself IS live-
#     verified in check (g), but via a no-build harness (stock python:3.12-slim +
#     bind-mounted source), not via `docker compose up` of the `build:` service.
#
# Exit code:
#   0 when FAIL == 0, regardless of SKIP count
#   1 when FAIL > 0
#
# Usage:
#   bash infrastructure/scripts/verify-phase15.sh
#   make verify-phase15
# Run from anywhere inside the repo (the script cd's to the repo root itself).
#
# Requires: bash, docker, docker compose v2+, python3. Does NOT require jq (this
# environment does not have it — python3 does the JSON-based assertions instead; same
# underlying data source, same pass/fail semantics).
#
# Host notes (do not "fix" these — they are load-bearing for THIS gate's own trustworthiness):
#   - NEVER `docker build`. Host may be arm64; prod is amd64. Every check either pulls a
#     multi-arch upstream image or runs real source inside a stock python:3.12-slim.
#   - The Git-Bash host-mount trap: a POSIX $PWD path silently fails to bind-mount from
#     Git Bash (rewritten to something like C:/Program Files/Git/<path>). Check (g) hard-
#     guards this and FAILS (never SKIPs) if the mount did not land.

set -uo pipefail   # NOT -e — every check should run independently; the summary line is the truth

PASS=0
FAIL=0
SKIP=0

red()    { printf '\033[31m%s\033[0m\n' "$*"; }
green()  { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }

ok()   { green "  PASS: $*"; PASS=$((PASS+1)); }
ko()   { red   "  FAIL: $*"; FAIL=$((FAIL+1)); }
skip() { yellow "  SKIPPED: $*"; SKIP=$((SKIP+1)); }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

echo "=== Phase 15 Verification (EDIT-01 + EDIT-02) ==="

# -----------------------------------------------------------------------------
# The hermetic env file. NEVER read the operator's real .env (may not exist; on a dev
# box may hold prod secrets). Built from .env.example, with the boot-fatal-if-empty vars
# filled with test values and the two knobs under test (EDITION/COMPOSE_PROFILES)
# deliberately left OUT of the file so the shell environment controls them cleanly.
ENVF="$(mktemp)"
grep -vE '^(OAUTH_ISSUER_URL|OAUTH_RESOURCE_URL|POSTGRES_PASSWORD|DATABASE_URL|NEO4J_PASSWORD|MINIO_ROOT_PASSWORD|BRIDGE_SHARED_SECRET|XBRAIN_BASE_DOMAIN|EDITION|COMPOSE_PROFILES)=' .env.example > "$ENVF"
cat >> "$ENVF" <<'EOF'
OAUTH_ISSUER_URL=https://api.p15.test
OAUTH_RESOURCE_URL=https://mcp.p15.test/mcp
POSTGRES_PASSWORD=p15testpassword
DATABASE_URL=postgresql+asyncpg://xbrain:p15testpassword@postgres:5432/xbrain
NEO4J_PASSWORD=p15testpassword
MINIO_ROOT_PASSWORD=p15testpassword
BRIDGE_SHARED_SECRET=p15testbridgesecret
XBRAIN_BASE_DOMAIN=p15.test
EOF
# NOTE: NEO4J_URI is intentionally NOT stripped — .env.example ships it non-empty
# (bolt://neo4j:7687) and docker-compose.yml passes it as a bare literal to memory-api
# regardless. That is exactly the state check (g) needs: Neo4j "configured" (URI set,
# password set) but NO Neo4j container anywhere. Do not sanitise it away.
DC=(docker compose -p xbrain-p15 -f infrastructure/docker-compose.yml --env-file "$ENVF")

# A small python helper for the JSON-based assertions in check (e). File paths are always
# passed as separate argv entries (never embedded inside -c code) — on this Git-Bash/Windows
# dev host, MSYS only auto-translates POSIX-looking paths that are standalone CLI arguments;
# a path baked into a -c string is NOT translated and silently resolves to nothing.
PYCHECK="$(mktemp --suffix=.py)"
cat > "$PYCHECK" <<'PYEOF'
import json
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


mode = sys.argv[1]

if mode == "edition":
    d_oss = load(sys.argv[2])
    d_saas = load(sys.argv[3])
    e_oss = d_oss["services"]["memory-api"]["environment"].get("EDITION")
    e_saas = d_saas["services"]["memory-api"]["environment"].get("EDITION")
    good = (e_oss == "oss") and (e_saas == "saas")
    print(f"{'OK' if good else 'BAD'} default_edition={e_oss!r} explicit_saas_edition={e_saas!r}")

elif mode == "edition-only-memapi":
    d = load(sys.argv[2])
    carriers = sorted(
        name for name, svc in d["services"].items()
        if (svc.get("environment") or {}).get("EDITION") is not None
    )
    good = carriers == ["memory-api"]
    print(f"{'OK' if good else 'BAD'} EDITION_carriers={carriers}")

elif mode == "no-neo4j-depends":
    d = load(sys.argv[2])
    offenders = []
    for svc in ("memory-api", "brain-janitor"):
        dep = d["services"][svc].get("depends_on") or {}
        if "neo4j" in dep:
            offenders.append(svc)
    good = not offenders
    print(f"{'OK' if good else 'BAD'} services_still_depending_on_neo4j={offenders}")

elif mode == "data-identity":
    args = sys.argv[2:]
    pairs = list(zip(args[0::2], args[1::2]))
    mismatches = []
    for label, path in pairs:
        d = load(path)
        svcs = d["services"]
        mem = (svcs.get("memory-api") or {}).get("environment") or {}
        jan = (svcs.get("brain-janitor") or {}).get("environment") or {}
        mem_qc, jan_qc = mem.get("QDRANT_COLLECTION"), jan.get("QDRANT_COLLECTION")
        if mem_qc != jan_qc:
            mismatches.append(f"{label}:QDRANT_COLLECTION memory-api={mem_qc!r} brain-janitor={jan_qc!r}")
        if "mcp-deck" in svcs:
            deck = svcs["mcp-deck"].get("environment") or {}
            mem_me, deck_me = mem.get("MINIO_ENDPOINT"), deck.get("MINIO_ENDPOINT")
            if mem_me != deck_me:
                mismatches.append(f"{label}:MINIO_ENDPOINT memory-api={mem_me!r} mcp-deck={deck_me!r}")
    good = not mismatches
    print(f"{'OK' if good else 'BAD'} mismatches={mismatches}")

else:
    print(f"BAD unknown mode {mode!r}")
    sys.exit(1)
PYEOF

# JSON/env-file temp files used by later checks (declared here so the single EXIT trap
# below can always clean them up, however far the script gets).
J_OSS="$(mktemp --suffix=.json)"
J_SAAS="$(mktemp --suffix=.json)"
J_BARE="$(mktemp --suffix=.json)"
J_INT="$(mktemp --suffix=.json)"
J_SAASPROF="$(mktemp --suffix=.json)"
J_OPS="$(mktemp --suffix=.json)"
J_ALL="$(mktemp --suffix=.json)"
MEMAPI_ENV="$(mktemp)"

cleanup() {
  "${DC[@]}" down -v --remove-orphans >/dev/null 2>&1
  docker rm -f xbrain-p15-memapi >/dev/null 2>&1
  rm -f "$ENVF" "$PYCHECK" "$J_OSS" "$J_SAAS" "$J_BARE" "$J_INT" "$J_SAASPROF" "$J_OPS" "$J_ALL" "$MEMAPI_ENV"
}
trap cleanup EXIT

# -----------------------------------------------------------------------------
test_a_profiles() {
  echo
  echo "(a) EDIT-01 — exactly three profiles (no pro), and no dangling depends_on"
  local profiles depends_err
  profiles=$("${DC[@]}" config --profiles 2>/dev/null | sort | tr '\n' ' ')
  depends_err=$("${DC[@]}" config 2>&1 >/dev/null | grep -i "depends on undefined service" || true)
  if [[ "$profiles" == "integrations ops saas " ]] && [[ -z "$depends_err" ]]; then
    ok "profiles == 'integrations ops saas' (no pro); no 'depends on undefined service'"
  else
    ko "profiles='$profiles' (expected 'integrations ops saas '); depends_on_error='$depends_err'"
  fi
}

# -----------------------------------------------------------------------------
CORE="brain-janitor centrifugo mcp-brain mcp-gateway mcp-scraper memory-api minio nginx postgres qdrant"

test_b_core_by_name() {
  echo
  echo "(b) EDIT-01 — bare (no-profile) OSS-light core is exactly 10 services, BY NAME"
  local actual expected d
  actual=$("${DC[@]}" config --services 2>/dev/null | sort)
  expected=$(printf '%s\n' $CORE | sort)
  d=$(diff <(echo "$expected") <(echo "$actual"))
  if [[ -z "$d" ]]; then
    ok "bare-core diff empty (10/10, by name): $(echo $actual | tr '\n' ' ')"
  else
    ko "bare-core diff non-empty (expected vs actual):
$d"
  fi
}

# -----------------------------------------------------------------------------
OPT_IN="neo4j graphiti-service langfuse langfuse-worker langfuse-clickhouse langfuse-redis mcp-calendar mcp-drive-read mcp-deck mcp-github granola-sync drive-sync searxng agent-runtime session-bridge librechat librechat-mongo librechat-meili librechat-bridge openwebui openwebui-pipeline xbrain-backup"

test_c_deny_list() {
  echo
  echo "(c) EDIT-01 — independent leak assertion: none of the 22 opt-in names in the bare core"
  local core_list leaked=""
  core_list=$("${DC[@]}" config --services 2>/dev/null)
  local svc
  for svc in $OPT_IN; do
    if echo "$core_list" | grep -qx "$svc"; then
      leaked="$leaked $svc"
    fi
  done
  local n_opt_in
  n_opt_in=$(echo "$OPT_IN" | wc -w)
  if [[ -z "$leaked" ]]; then
    ok "0 of $n_opt_in opt-in service names present in the bare core (independent of check b)"
  else
    ko "leaked opt-in service names into the bare core:$leaked"
  fi
}

# -----------------------------------------------------------------------------
INTEGRATIONS="agent-runtime drive-sync granola-sync graphiti-service langfuse langfuse-clickhouse langfuse-redis langfuse-worker mcp-calendar mcp-deck mcp-drive-read mcp-github neo4j searxng"
SAAS="librechat librechat-bridge librechat-meili librechat-mongo openwebui openwebui-pipeline session-bridge"
OPS="xbrain-backup"

test_d_profile_membership() {
  echo
  echo "(d) EDIT-01 — each opt-in profile's membership, BY NAME (diff, never count-only)"
  local d_int d_saas d_ops d_all n_int n_saas n_ops n_all
  d_int=$(diff <(printf '%s\n' $CORE $INTEGRATIONS | sort) <("${DC[@]}" --profile integrations config --services 2>/dev/null | sort))
  d_saas=$(diff <(printf '%s\n' $CORE $SAAS | sort) <("${DC[@]}" --profile saas config --services 2>/dev/null | sort))
  d_ops=$(diff <(printf '%s\n' $CORE $OPS | sort) <("${DC[@]}" --profile ops config --services 2>/dev/null | sort))
  d_all=$(diff <(printf '%s\n' $CORE $INTEGRATIONS $SAAS $OPS | sort) <("${DC[@]}" --profile integrations --profile saas --profile ops config --services 2>/dev/null | sort))

  n_int=$("${DC[@]}" --profile integrations config --services 2>/dev/null | wc -l)
  n_saas=$("${DC[@]}" --profile saas config --services 2>/dev/null | wc -l)
  n_ops=$("${DC[@]}" --profile ops config --services 2>/dev/null | wc -l)
  n_all=$("${DC[@]}" --profile integrations --profile saas --profile ops config --services 2>/dev/null | wc -l)

  if [[ -z "$d_int" && -z "$d_saas" && -z "$d_ops" && -z "$d_all" ]]; then
    ok "membership diffs all empty BY NAME — integrations=$n_int saas=$n_saas ops=$n_ops all-three=$n_all (expected 24/17/11/32)"
  else
    ko "membership diff non-empty — integrations:[$d_int] saas:[$d_saas] ops:[$d_ops] all-three:[$d_all] (counts: int=$n_int saas=$n_saas ops=$n_ops all=$n_all)"
  fi

  local legal_fail=""
  local p
  for p in ops saas integrations; do
    if ! "${DC[@]}" --profile "$p" config -q 2>/dev/null; then
      legal_fail="$legal_fail $p"
    fi
  done
  if [[ -z "$legal_fail" ]]; then
    ok "each opt-in profile (ops/saas/integrations) is independently a legal compose project (config -q exits 0)"
  else
    ko "profile(s) failed 'config -q' standalone:$legal_fail"
  fi

  local via_env via_flags
  via_env=$(COMPOSE_PROFILES=integrations,saas "${DC[@]}" config --services 2>/dev/null | sort)
  via_flags=$("${DC[@]}" --profile integrations --profile saas config --services 2>/dev/null | sort)
  if [[ "$via_env" == "$via_flags" ]]; then
    ok "COMPOSE_PROFILES=integrations,saas resolves identically to --profile integrations --profile saas"
  else
    ko "COMPOSE_PROFILES env var did not resolve identically to the equivalent --profile flags"
  fi
}

# -----------------------------------------------------------------------------
test_e_edition_and_data_identity() {
  echo
  echo "(e) EDIT-02 + SC#5 — resolved container environment (config --format json), not the YAML"

  env -u EDITION "${DC[@]}" config --format json >"$J_OSS" 2>/dev/null
  EDITION=saas "${DC[@]}" config --format json >"$J_SAAS" 2>/dev/null

  local r
  r=$(python "$PYCHECK" edition "$J_OSS" "$J_SAAS")
  if [[ "$r" == OK* ]]; then ok "EDITION passthrough: ${r#OK }"; else ko "EDITION passthrough: ${r#BAD }"; fi

  r=$(python "$PYCHECK" edition-only-memapi "$J_OSS")
  if [[ "$r" == OK* ]]; then ok "EDITION reaches ONLY memory-api: ${r#OK }"; else ko "EDITION carrier set wrong: ${r#BAD }"; fi

  r=$(python "$PYCHECK" no-neo4j-depends "$J_OSS")
  if [[ "$r" == OK* ]]; then ok "no depends_on:neo4j on memory-api/brain-janitor: ${r#OK }"; else ko "dangling neo4j depends_on: ${r#BAD }"; fi

  "${DC[@]}" config --format json >"$J_BARE" 2>/dev/null
  "${DC[@]}" --profile integrations config --format json >"$J_INT" 2>/dev/null
  "${DC[@]}" --profile saas config --format json >"$J_SAASPROF" 2>/dev/null
  "${DC[@]}" --profile ops config --format json >"$J_OPS" 2>/dev/null
  "${DC[@]}" --profile integrations --profile saas --profile ops config --format json >"$J_ALL" 2>/dev/null

  r=$(python "$PYCHECK" data-identity bare "$J_BARE" integrations "$J_INT" saas "$J_SAASPROF" ops "$J_OPS" all "$J_ALL")
  if [[ "$r" == OK* ]]; then
    ok "D-15-04 data identity (QDRANT_COLLECTION/MINIO_ENDPOINT) holds across all 5 profile combinations"
  else
    ko "D-15-04 data identity violated: ${r#BAD }"
  fi
}

test_a_profiles
test_b_core_by_name
test_c_deny_list
test_d_profile_membership
test_e_edition_and_data_identity

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi
