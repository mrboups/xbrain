#!/usr/bin/env bash
#
# verify-phase17-full.sh — Phase 17 (CI Lockstep) FULL-PROFILE gate.
#
# WHAT THIS IS NOT: a 32-container boot. Per D-17-03 the full profile may not fit a hosted
# runner's ~14 GB disk, so "test the full profile" is defined here as a GRAPH + OVERRIDE
# validation at the config layer. That is a deliberate scope decision, not a shortcut — and it
# is stated out loud in check (d) so nobody reads a green run as "the full stack booted".
#
# WHY IT IS STILL A REAL GATE: every check below is a `docker compose config` resolve, which is
# a PURE PARSE — no daemon, no images, no network. Verified for this repo by re-running the same
# commands with DOCKER_HOST pointed at a dead socket (tcp://127.0.0.1:1): identical output. That
# is what makes SKIP=FAIL honest here — these checks CAN always run, so a SKIP would only ever
# mean the gate was dodged.
#
# Checks:
#   (a) FULL GRAPH  — the full-profile service set resolves, and its size is DERIVED, not
#                     hardcoded: two independent readings of the compose graph must agree.
#                       reading 1: `config --services` counted with profiles on vs. off
#                       reading 2: the per-service `profiles:` key in the resolved config
#                     i.e. listed_full == listed_core + profile_tagged, and the core set is a
#                     SUBSET of the full set. Today that is 10 + 24 = 34, but the numbers are
#                     re-derived every run: adding a service updates both sides and the check
#                     keeps holding, while a service that is tagged/untagged INCONSISTENTLY (the
#                     failure that silently drops a service out of CI) breaks it.
#   (b) PROFILES    — `config --profiles` == exactly `board integrations ops saas` (no `pro`).
#   (c) OVERRIDE    — layering docker-compose.ci-images.yml resolves, and EVERY `build:` service
#                     COMPLETENESS  carries a `ghcr.io/<owner>/xbrain-*` image. The expected count is
#                     derived from the BASE file (count of services with a `build:` key = 20
#                     today), never hardcoded. This is the check that catches a service missing
#                     from the override — the exact defect that would make a downstream job
#                     silently `--build` one service instead of pulling what CI tested (D-17-02).
#                     Also asserts no `xbrain-xbrain-*` double-prefix, and that `xbrain-backup`
#                     keeps its single prefix + carries its amd64-only note.
#   (d) SCOPE NOTE  — prints what this gate does NOT cover (the boot, and the memory-api pytest
#                     suite under EDITION=saas, which the CI `test-full-profile` job runs).
#
# Exit code: 0 when FAIL == 0, 1 when FAIL > 0.
#
# Usage:
#   bash infrastructure/scripts/verify-phase17-full.sh
# Run from anywhere inside the repo (the script cd's to the repo root itself).
#
# Requires: bash, docker compose v2+, python3 (or python). No daemon. No jq.
#
# Host note (load-bearing, learned in Phase 16): on Git-Bash the `python` on PATH is a WINDOWS
# python, which cannot open an MSYS `/tmp/...` path. So every python helper below takes its DATA
# ON STDIN and its script inline via `-c` — no temp-file path is ever handed to python. By
# contrast `--env-file` / `-f` are HOST paths passed to docker and MUST stay MSYS-converted
# (no MSYS_NO_PATHCONV here).

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

echo "=== Phase 17 Verification — FULL PROFILE graph + GHCR override (REL-01, REL-03) ==="

PROJECT="xbrain-p17"
BASE_FILE="infrastructure/docker-compose.yml"
CI_IMAGES_FILE="infrastructure/docker-compose.ci-images.yml"

HERMETIC_ENV="$(mktemp)"
cleanup() { rm -f "$HERMETIC_ENV"; }
trap cleanup EXIT

# --- hermetic env (phase15/16 pattern) --------------------------------------------------------
# `config` only cares about the service GRAPH, never the values — but an unset interpolated var
# makes compose warn on stderr and can abort on a required var. So the interpolated vars are
# stripped and re-appended as clean KEY=value lines (NO inline comments — Phase 16 Pitfall 5).
# SEARXNG_SECRET is included on top of the Phase-16 list: it is only reachable once the
# `integrations` profile is on, which is exactly what this gate turns on.
build_hermetic_env() {
  grep -vE '^(OAUTH_ISSUER_URL|OAUTH_RESOURCE_URL|POSTGRES_PASSWORD|DATABASE_URL|NEO4J_PASSWORD|MINIO_ROOT_PASSWORD|BRIDGE_SHARED_SECRET|XBRAIN_BASE_DOMAIN|EDITION|COMPOSE_PROFILES|SEARXNG_SECRET)=' \
    .env.example > "$HERMETIC_ENV"
  cat >> "$HERMETIC_ENV" <<'EOF'
OAUTH_ISSUER_URL=http://api.p17.test
OAUTH_RESOURCE_URL=http://mcp.p17.test/mcp
POSTGRES_PASSWORD=p17testpassword
DATABASE_URL=postgresql+asyncpg://xbrain:p17testpassword@postgres:5432/xbrain
NEO4J_PASSWORD=p17testpassword
MINIO_ROOT_PASSWORD=p17testpassword
BRIDGE_SHARED_SECRET=p17testbridgesecret
XBRAIN_BASE_DOMAIN=p17.test
SEARXNG_SECRET=p17testsearxngsecret
EOF
}

if [[ ! -f "$BASE_FILE" ]]; then
  ko "base compose file missing: $BASE_FILE"
  echo; echo "=== Summary ==="; echo "PASS: $PASS / $((PASS+FAIL))  (SKIP: $SKIP)"; exit 1
fi
if [[ -z "$PY" ]]; then
  # SKIP=FAIL: the config parse needs no daemon, so "no python" is a broken host, not a valid skip.
  ko "no python3/python on PATH — the config assertions cannot run (SKIP=FAIL)"
  echo; echo "=== Summary ==="; echo "PASS: $PASS / $((PASS+FAIL))  (SKIP: $SKIP)"; exit 1
fi

build_hermetic_env
DC_BASE=(docker compose -p "$PROJECT" -f "$BASE_FILE" --env-file "$HERMETIC_ENV")
DC_OVERRIDE=(docker compose -p "$PROJECT" -f "$BASE_FILE" -f "$CI_IMAGES_FILE" --env-file "$HERMETIC_ENV")

# =============================================================================================
# (a) FULL GRAPH — derived, not hardcoded.
# =============================================================================================
test_a_full_graph() {
  echo
  echo "(a) FULL GRAPH — full == core + profile-tagged (DERIVED both ways), core is a subset of full"

  local core_list full_list
  core_list=$(env -u COMPOSE_PROFILES "${DC_BASE[@]}" config --services 2>/dev/null | sort)
  full_list=$(COMPOSE_PROFILES=integrations,saas,ops,board "${DC_BASE[@]}" config --services 2>/dev/null | sort)

  if [[ -z "$core_list" || -z "$full_list" ]]; then
    ko "(a) 'config --services' returned nothing — the compose graph did not resolve (SKIP=FAIL)"
    COMPOSE_PROFILES=integrations,saas,ops,board "${DC_BASE[@]}" config --services 2>&1 | tail -5 | sed 's/^/        /'
    return 1
  fi

  local n_core n_full
  n_core=$(printf '%s\n' "$core_list" | wc -l | tr -d '[:space:]')
  n_full=$(printf '%s\n' "$full_list" | wc -l | tr -d '[:space:]')

  # Reading 2 — INDEPENDENT of the two listings above: count services by their own `profiles:`
  # key in the resolved full config. If a service is tagged with a profile it must NOT appear in
  # the bare-core listing, and vice versa; that is what makes this a cross-check and not a
  # tautology like "full - core == full - core".
  local keyed
  keyed=$(COMPOSE_PROFILES=integrations,saas,ops,board "${DC_BASE[@]}" config --format json 2>/dev/null \
    | PYTHONIOENCODING=utf-8 "$PY" -c '
import json, sys
try:
    svc = json.load(sys.stdin)["services"]
except Exception as exc:
    print("ERR %s" % exc)
    sys.exit(0)
tagged = sorted(n for n, v in svc.items() if v.get("profiles"))
untagged = sorted(n for n, v in svc.items() if not v.get("profiles"))
print("%d %d" % (len(untagged), len(tagged)))
')

  if [[ "$keyed" == ERR* || -z "$keyed" ]]; then
    ko "(a) could not parse the resolved full config as JSON: $keyed"
    return 1
  fi

  local n_untagged n_tagged
  n_untagged=$(echo "$keyed" | awk '{print $1}')
  n_tagged=$(echo "$keyed" | awk '{print $2}')

  echo "      derived: core(listed)=$n_core  profile-tagged(by profiles: key)=$n_tagged  full(listed)=$n_full"
  echo "      cross-check: untagged(by profiles: key)=$n_untagged"

  local bad=""
  [[ "$n_core" -eq "$n_untagged" ]] || bad="$bad core_listed($n_core)!=untagged_by_key($n_untagged)"
  [[ "$n_full" -eq $((n_core + n_tagged)) ]] || bad="$bad full($n_full)!=core($n_core)+tagged($n_tagged)"

  local not_subset
  not_subset=$(comm -23 <(echo "$core_list") <(echo "$full_list") | tr '\n' ' ')
  [[ -z "${not_subset// /}" ]] || bad="$bad core_not_subset_of_full[$not_subset]"

  if [[ -z "$bad" ]]; then
    ok "(a) full-profile graph resolves: $n_full services == $n_core core + $n_tagged profile-tagged (derived, agrees with the profiles: keys); core is a subset of full"
    # Informational snapshot only — the ASSERTION above is the derived identity, this line just
    # records what the derivation evaluated to today so drift is visible in CI logs.
    if [[ "$n_full" -ne 34 || "$n_core" -ne 10 || "$n_tagged" -ne 24 ]]; then
      yellow "      NOTE: the graph changed since the Phase-26 board profile landed (was 34 = 10 + 24, now $n_full = $n_core + $n_tagged)."
      yellow "            This is NOT a failure — the identity still holds. Update the CI docs if the change was intended."
    fi
  else
    ko "(a) full-graph derivation failed:$bad
      core listed : $(echo $core_list | tr '\n' ' ')
      full listed : $(echo $full_list | tr '\n' ' ')
      profiles-on-only (full minus core): $(comm -13 <(echo "$core_list") <(echo "$full_list") | tr '\n' ' ')"
    return 1
  fi
}

# =============================================================================================
# (b) PROFILES — exactly board integrations ops saas.
# =============================================================================================
test_b_profiles() {
  echo
  echo "(b) PROFILES — declared profile set is exactly 'board integrations ops saas' (no pro)"

  local profiles
  profiles=$(COMPOSE_PROFILES=integrations,saas,ops,board "${DC_BASE[@]}" config --profiles 2>/dev/null | sort | tr '\n' ' ')
  if [[ "$profiles" == "board integrations ops saas " ]]; then
    ok "(b) profiles == 'board integrations ops saas'"
  else
    ko "(b) profiles='$profiles' (expected 'board integrations ops saas ')"
    return 1
  fi
}

# =============================================================================================
# (c) OVERRIDE COMPLETENESS — every build: service is remapped to GHCR.
# =============================================================================================
test_c_override_completeness() {
  echo
  echo "(c) OVERRIDE — every build: service resolves to ghcr.io/<owner>/xbrain-* under $CI_IMAGES_FILE"

  if [[ ! -f "$CI_IMAGES_FILE" ]]; then
    ko "(c) $CI_IMAGES_FILE is missing — downstream jobs would rebuild instead of pulling (SKIP=FAIL)"
    return 1
  fi

  # Expected count is DERIVED from the base file: however many services carry a `build:` key must
  # be remapped. Add a build service without adding it to the override and this check fails.
  local n_build_base
  n_build_base=$(COMPOSE_PROFILES=integrations,saas,ops,board "${DC_BASE[@]}" config --format json 2>/dev/null \
    | PYTHONIOENCODING=utf-8 "$PY" -c '
import json, sys
try:
    svc = json.load(sys.stdin)["services"]
except Exception:
    print("0")
    sys.exit(0)
print(sum(1 for v in svc.values() if v.get("build")))
')

  if [[ ! "$n_build_base" =~ ^[0-9]+$ ]] || [[ "$n_build_base" -eq 0 ]]; then
    ko "(c) could not count build: services in $BASE_FILE (got '$n_build_base')"
    return 1
  fi
  echo "      derived from $BASE_FILE: $n_build_base services carry a build: key — all $n_build_base must be remapped"

  local verdict
  verdict=$(COMPOSE_PROFILES=integrations,saas,ops,board "${DC_OVERRIDE[@]}" config --format json 2>/dev/null \
    | PYTHONIOENCODING=utf-8 "$PY" -c '
import json, re, sys

expected = int(sys.argv[1])
try:
    svc = json.load(sys.stdin)["services"]
except Exception as exc:
    print("BAD override-layered config did not resolve to JSON: %s" % exc)
    sys.exit(0)

# ghcr.io is HARDCODED in the pattern on purpose (T-17-02-02): the override must never point at
# an arbitrary registry host, only at ghcr.io/<owner>/xbrain-<name>:<tag>.
pat = re.compile(r"^ghcr\.io/[^/]+/xbrain-[a-z0-9._-]+:.+$")

builds = {n: (v.get("image") or "") for n, v in svc.items() if v.get("build")}
bad = sorted("%s->%r" % (n, i) for n, i in builds.items() if not pat.match(i))
dbl = sorted(n for n, i in builds.items() if "xbrain-xbrain-" in i)

if bad:
    print("BAD %d/%d build services are NOT remapped to a ghcr.io/<owner>/xbrain-* image: %s"
          % (len(bad), len(builds), ", ".join(bad)))
    sys.exit(0)
if dbl:
    print("BAD double-prefixed image name (xbrain-xbrain-*) on: %s" % ", ".join(dbl))
    sys.exit(0)
if len(builds) != expected:
    print("BAD override-layered config has %d build services, base has %d" % (len(builds), expected))
    sys.exit(0)

backup = builds.get("xbrain-backup", "")
if backup and not backup.split("/")[-1].startswith("xbrain-backup:"):
    print("BAD xbrain-backup maps to %r (expected .../xbrain-backup:<tag>)" % backup)
    sys.exit(0)

owners = sorted({i.split("/")[1] for i in builds.values()})
tags = sorted({i.rsplit(":", 1)[1] for i in builds.values()})
print("OK %d/%d build services remapped; owner(s)=%s tag(s)=%s"
      % (len(builds), expected, owners, tags))
' "$n_build_base")

  if [[ "$verdict" == OK* ]]; then
    ok "(c) ${verdict#OK }"
  else
    ko "(c) ${verdict#BAD }"
    return 1
  fi

  # xbrain-backup is amd64-only (D-17-02): its base image publishes no arm64 manifest, so the
  # BUILD job must restrict its platforms. The image REFERENCE is arch-agnostic, so that
  # constraint cannot be asserted from the resolved config — it lives as a documented contract in
  # the override file, and this check asserts the documentation is present so the build job's
  # single-platform exception can never become folklore.
  if grep -q 'xbrain-backup' "$CI_IMAGES_FILE" && grep -qi 'amd64' "$CI_IMAGES_FILE"; then
    ok "(c) xbrain-backup carries its single (non-doubled) prefix AND the override documents the amd64-only constraint (D-17-02)"
  else
    ko "(c) $CI_IMAGES_FILE does not document xbrain-backup's amd64-only constraint (D-17-02) — the build job's platform exception would be undocumented"
    return 1
  fi
}

# =============================================================================================
# (d) SCOPE NOTE — say out loud what a green run here does and does NOT prove.
# =============================================================================================
test_d_scope_note() {
  echo
  echo "(d) SCOPE — what this gate covers, and what it deliberately does not"
  echo "      COVERED (daemon-free, pure 'docker compose config' parse — no images, no network):"
  echo "        - the full-profile service graph resolves and its size is derived, not asserted from memory"
  echo "        - the GHCR override remaps every build: service, so downstream jobs PULL (D-17-02)"
  echo "      NOT COVERED HERE (by design, D-17-03 — a 32-container boot may not fit a hosted runner's disk):"
  echo "        - booting the full profile. The boot-fit MEASUREMENT is a workflow_dispatch follow-up, not a gate."
  echo "        - the memory-api pytest suite under EDITION=saas + the saas migration test: the CI"
  echo "          'test-full-profile' job (Plan 17-03) runs those alongside this script."
  echo "        - the real 10-core boot + SC#3 HTTP walk: that is verify-phase16.sh (CI 'test-oss-subset',"
  echo "          which reuses it via VERIFY16_NO_BUILD=1 against these same CI-built images)."
}

test_a_full_graph
test_b_profiles
test_c_override_completeness
test_d_scope_note

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi
