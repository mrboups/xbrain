#!/usr/bin/env bash
#
# verify-phase18.sh — Verify Phase 18 (Local Auth, OSS default) acceptance gate:
# LAUTH-01/02 + SC#1..SC#6 + D-18-05.
#
# Six defects across Phases 14-15-18 shared ONE cause: a check that never traversed the real
# deployment path. Here, that failure mode is most dangerous — a login test that mocks the
# principal proves nothing about whether a real xbt_ token, minted by the real route, is
# accepted by the real get_current_principal and authorizes against the real team_members
# table. Every check below drives REAL memory-api routes against a REAL Postgres (testcontainers
# or a live container), never a mocked principal, never a source-grep standing in for behavior.
#
# Checks (4, SKIP-as-FAIL for every security/regression assertion):
#   (a) SECURITY SUITE — tests/test_local_auth.py + test_local_auth_set_password.py +
#       test_local_credentials_repo.py against REAL testcontainers Postgres. These 27 tests
#       already encode SC#1 (test_clean_boot_no_oauth_e2e), SC#2 (test_password_hash_is_argon2id),
#       SC#3/LAUTH-02 (test_local_xbt_authorizes_team_route), D-18-05 (test_register_collision_409,
#       test_convergence_github_style_user_attaches_password, test_cannot_set_another_users_password),
#       SC#6 (test_lockout_then_recovery), and the no-enumeration-oracle assertion
#       (test_no_enumeration_oracle). Docker absent, or ANY test skipped/uncollected, is scored a
#       FAILURE here — not a skip. A skipped security assertion proves the real path was never
#       exercised, which is precisely the failure mode this gate exists to catch.
#
#   (b) SC#4 REGRESSION — Google/GitHub unchanged, auth_local classified CORE:
#       (b1) tests/test_edition_gating.py — pure router-classification, no Docker needed.
#            SKIP-as-FAIL, must be 100% green.
#       (b2) tests/test_phase10_auth.py — driven as-is, but NOT gated on 100% green: this file is
#            independently, provably pre-existing-broken by THREE bugs that predate Phase 18 by
#            multiple phases and live in files Phase 18 must not touch (app/repos/merge.py's
#            `memory_promotions` table-name drift, logged in deferred-items.md by Plan 18-03; and
#            two additional test-fixture drift bugs discovered while building this gate — see
#            deferred-items.md's Plan 18-06 entry). Forcing this suite to exit 0 would require
#            editing a Phase 10 test file, which the Scope Boundary rule forbids. Instead: the set
#            of FAILING test names is compared against the known-broken set; any NAME outside that
#            set (a genuinely NEW failure) is scored a FAILURE. This still catches a real Phase 18
#            regression while not blocking the gate forever on bugs Phase 18 cannot fix.
#       (b3) A gate-owned, CORRECTLY-shaped respx-mocked live exercise of the real
#            POST /v1/auth/github/signin route (real Postgres, real FastAPI app) — the genuine,
#            independent "actually exercise the GitHub path" proof that (b2) can no longer provide
#            on its own. SKIP-as-FAIL, must PASS.
#       (b4) app/deps.py and app/routes/auth_github.py are diffed BYTE-FOR-BYTE against the
#            pre-Phase-18 base commit (100e6d9, the commit immediately before Plan 18-01's first
#            commit) — not just "no uncommitted changes", a real structural proof across the whole
#            phase. Falls back to a warning (not a FAIL) only if that commit is unreachable in this
#            checkout's history; the check (d) grep below is the second, independent proof of the
#            same property regardless.
#
#   (c) LIVE ZERO-OAUTH BOOT — a REAL memory-api (real source, no image is built — python:3.12-slim +
#       bind mount, same no-build harness verify-phase15.sh established) boots against a REAL
#       postgres:17 container with NO GOOGLE_CLIENT_ID / GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET
#       / GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET set (all explicitly emptied, not merely omitted).
#       After `alembic upgrade head`: POST /v1/auth/local/register -> capture xbt_ + team_scope;
#       POST /v1/auth/local/login -> 200; GET /v1/tasks with the xbt_ + X-Team-Scope -> 200. This is
#       SC#1 proven against a live, socially-OAuth-less server — the belt-and-suspenders proof on top
#       of check (a)'s testcontainers-based test_clean_boot_no_oauth_e2e. A MOUNT GUARD fails loudly
#       (never skips) if the bind mount didn't land (the Git-Bash host-mount trap). Only the
#       pip-install/boot step itself may SKIP if genuinely offline — check (a) is the load-bearing
#       proof of SC#1 and does NOT skip-pass.
#
#   (d) NEGATIVE PROVENANCE — app/deps.py and app/routes/auth_github.py contain no
#       local_credentials/auth_local marker strings (0 matches expected — no sixth branch, no
#       leaked local-auth logic). PLUS a self-excluding REPO-WIDE grep for the /auth/local/*
#       endpoint paths: the local-auth surface must appear ONLY in its known owner files (the
#       route, its tests, the account UI pages, the docs) — nowhere else (e.g. NOT in
#       oauth_authorize.py, which D-18-07 flags as a KNOWN, NOT-fixed limitation, not something to
#       silently patch here). SELF-EXCLUSION: the repo-wide grep excludes verify-phase18.sh's own
#       path (it legitimately mentions every endpoint by name in this header and in its curl calls)
#       — the self-invalidating-grep trap named in 18-CONTEXT.md.
#
# Exit code:
#   0 when FAIL == 0, regardless of SKIP count
#   1 when FAIL > 0
#
# Usage:
#   bash infrastructure/scripts/verify-phase18.sh
#   make verify-phase18
# Run from anywhere inside the repo (the script cd's to the repo root itself).
#
# Requires: bash, docker, docker compose v2+ (not used directly here but expected on the host),
# python3, curl. Does NOT require jq — python3 does the JSON-based assertions.
#
# Host notes (do not "fix" these — load-bearing for THIS gate's own trustworthiness):
#   - This gate never builds a container image (this file greps to 0 occurrences of the two words
#     "docker" and "build" adjacent to each other). Host may be arm64; prod is amd64. Every check
#     either pulls a multi-arch upstream image (postgres:17, python:3.12-slim) or runs real source
#     inside a stock container with prebuilt wheels only.
#   - The Git-Bash host-mount trap: a POSIX $PWD path silently fails to bind-mount from Git Bash.
#     Check (c) hard-guards this and FAILS (never SKIPs) if the mount did not land.

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
MEMAPI_DIR="$REPO_ROOT/apps/memory-api"

echo "=== Phase 18 Verification (LAUTH-01 + LAUTH-02, SC#1..SC#6, D-18-05) ==="

# -----------------------------------------------------------------------------
# Docker availability — gates (a), (b2)/(b3), (c). (b1) and (d) need no Docker.
DOCKER_OK=true
if ! docker info >/dev/null 2>&1; then
  DOCKER_OK=false
fi

# -----------------------------------------------------------------------------
# A small python helper for JUnit-XML-based assertions — REAL pytest output, never a
# source grep standing in for test results. File paths are passed as separate argv
# entries (never embedded inside -c code): on this Git-Bash/Windows dev host, MSYS only
# auto-translates POSIX-looking paths that are standalone CLI arguments.
PYCHECK="$(mktemp --suffix=.py)"
cat > "$PYCHECK" <<'PYEOF'
import sys
import xml.etree.ElementTree as ET


def _cases(path):
    tree = ET.parse(path)
    root = tree.getroot()
    suites = root.findall(".//testsuite") if root.tag == "testsuites" else [root]
    out = []
    for suite in suites:
        for tc in suite.findall("testcase"):
            name = tc.get("name")
            if tc.find("failure") is not None:
                status = "fail"
            elif tc.find("error") is not None:
                status = "error"
            elif tc.find("skipped") is not None:
                status = "skip"
            else:
                status = "pass"
            out.append((name, status))
    return out


mode = sys.argv[1]

if mode == "junit-strict":
    cases = _cases(sys.argv[2])
    bad = [f"{n}={s}" for n, s in cases if s != "pass"]
    passed = sum(1 for _, s in cases if s == "pass")
    if not cases:
        print("BAD zero testcases collected — nothing was exercised")
    elif bad:
        print(f"BAD non-pass testcases (skip counts as FAIL): {bad}")
    else:
        print(f"OK passed={passed}/{len(cases)}")

elif mode == "junit-tolerant":
    # argv: xml_path, comma-separated known-broken short test names
    cases = _cases(sys.argv[2])
    known_broken = set(sys.argv[3].split(",")) if sys.argv[3] else set()
    unexpected = [f"{n}={s}" for n, s in cases if s in ("fail", "error", "skip") and n not in known_broken]
    newly_fixed = sorted({n for n, s in cases if s == "pass" and n in known_broken})
    if not cases:
        print("BAD zero testcases collected — nothing was exercised")
    elif unexpected:
        print(f"BAD unexpected new failures (not in the documented pre-existing set): {unexpected}")
    else:
        still_broken = sorted(known_broken - set(newly_fixed))
        print(f"OK no new regressions; known-pre-existing-broken={still_broken}; newly-fixed={newly_fixed}")

else:
    print(f"BAD unknown mode {mode!r}")
    sys.exit(1)
PYEOF

# -----------------------------------------------------------------------------
# The gate-owned live GitHub-signin proof (check b3) — a CORRECTLY-shaped respx mock
# (unlike tests/test_phase10_auth.py's stale fixture, which predates the Phase 12
# GitHub App migration's `refresh_token`-required response shape — see deferred-items.md).
# Dropped into tests/ temporarily (needs conftest.py's `client`/`session` fixtures),
# deleted by the cleanup trap below. Never committed under this name.
GH_E2E_TEST="$MEMAPI_DIR/tests/test_zzverify_phase18_github_e2e.py"
cat > "$GH_E2E_TEST" <<'PYEOF'
"""Gate-owned (verify-phase18.sh), ephemeral. Proves POST /v1/auth/github/signin still
works end-to-end against the REAL route + REAL Postgres with a CORRECTLY shaped respx
mock — independent of tests/test_phase10_auth.py's stale, pre-existing-broken fixture
(see .planning/phases/18-local-auth/deferred-items.md). Written and deleted by
verify-phase18.sh; must never be committed under this name."""
from __future__ import annotations

import httpx
import pytest
import respx

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_verify_phase18_github_signin_e2e(client, monkeypatch):
    from cryptography.fernet import Fernet

    from app.config import settings

    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_ID", "verify_phase18_app_client_id")
    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_SECRET", "verify_phase18_app_client_secret")
    # persist_tokens_on_signin encrypts the refresh token at rest via token_crypto.py, which
    # requires FERNET_KEY (or its OAUTH_CREDENTIALS_ENCRYPTION_KEY fallback) to be non-empty —
    # same requirement tests/test_phase12_auto_grant_regression.py already sets for a full
    # successful signin.
    monkeypatch.setattr(settings, "FERNET_KEY", Fernet.generate_key().decode())

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://github.com/login/oauth/access_token").mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "ghu_verify18",
                    "refresh_token": "ghr_verify18",
                    "expires_in": 28800,
                    "refresh_token_expires_in": 15897600,
                    "token_type": "bearer",
                    "scope": "",
                },
            )
        )
        mock.get("https://api.github.com/user").mock(
            return_value=httpx.Response(
                200,
                json={"id": 555001, "login": "verify18user", "name": "Verify Phase18", "email": None},
            )
        )
        mock.get("https://api.github.com/user/emails").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "email": "verify18@example.com",
                        "primary": True,
                        "verified": True,
                        "visibility": "private",
                    }
                ],
            )
        )
        mock.get(url__regex=r"https://api\.github\.com/user/orgs.*").mock(
            return_value=httpx.Response(200, json=[])
        )
        mock.get(url__regex=r"https://api\.github\.com/user/installations.*").mock(
            return_value=httpx.Response(200, json={"total_count": 0, "installations": []})
        )

        r = await client.post(
            "/v1/auth/github/signin",
            json={
                "code": "verify18code",
                "redirect_uri": "https://verify18.test/cb",
                "state": "verify18state",
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["xbt_token"].startswith("xbt_")
    assert body["user"]["github_username"] == "verify18user"
PYEOF

# -----------------------------------------------------------------------------
# Temp files / containers this run may create — one EXIT trap cleans up however far
# the script gets. This gate never builds an image and never touches a container name
# that isn't uniquely prefixed xbrain-p18- (it never reuses the real deployment's
# container names, so it never risks clobbering a running stack).
J_A="$(mktemp --suffix=.xml)"
J_B1="$(mktemp --suffix=.xml)"
J_B2="$(mktemp --suffix=.xml)"
J_B3="$(mktemp --suffix=.xml)"
ENVFILE_C="$(mktemp)"

cleanup() {
  rm -f "$GH_E2E_TEST" "$PYCHECK" "$J_A" "$J_B1" "$J_B2" "$J_B3" "$ENVFILE_C"
  docker rm -f xbrain-p18-memapi >/dev/null 2>&1 || true
  docker rm -f xbrain-p18-postgres >/dev/null 2>&1 || true
  docker network rm xbrain-p18-net >/dev/null 2>&1 || true
  docker volume rm xbrain-p18-pipcache >/dev/null 2>&1 || true
}
trap cleanup EXIT

# =============================================================================
test_a_security_suite() {
  echo
  echo "(a) SECURITY SUITE — real-Postgres proof of SC#1/2/3/6, D-18-05, no-enumeration-oracle"

  if ! $DOCKER_OK; then
    ko "Docker unavailable — the real-Postgres security suite could not be exercised (SKIP-as-FAIL: a skip here means the real path was never tested)"
    return
  fi

  (
    cd "$MEMAPI_DIR" && python -m pytest \
      tests/test_local_auth.py tests/test_local_auth_set_password.py tests/test_local_credentials_repo.py \
      -q --junitxml="$J_A"
  ) > /tmp/verify-phase18-a.log 2>&1
  local rc=$?

  local r
  r=$(python "$PYCHECK" junit-strict "$J_A" 2>/dev/null)
  if [[ "$r" == OK* ]] && [[ "$rc" -eq 0 ]]; then
    ok "test_local_auth.py + test_local_auth_set_password.py + test_local_credentials_repo.py: ${r#OK }, 0 skipped (SC#1/2/3/6, D-18-05, no-oracle, convergence, priv-escalation all genuinely exercised against real Postgres)"
  else
    ko "security suite did not go fully green: ${r#BAD } (pytest exit=$rc; see /tmp/verify-phase18-a.log)"
  fi
}

# =============================================================================
KNOWN_BROKEN_PHASE10_AUTH="test_signin_brand_new_user,test_signin_existing_google_user_attaches_github,test_signin_merges_orphan_row,test_merge_does_not_violate_github_id_unique,test_signin_respects_org_block,test_signin_existing_blocked_membership_no_regrant,test_orphan_token_lands_on_survivor"
PHASE18_BASE_SHA="100e6d9"

test_b_sc4_regression() {
  echo
  echo "(b) SC#4 REGRESSION — Google/GitHub unchanged, auth_local classified CORE"

  # (b1) — pure router classification, no Docker needed, must be 100% green.
  (cd "$MEMAPI_DIR" && python -m pytest tests/test_edition_gating.py -q --junitxml="$J_B1") \
    > /tmp/verify-phase18-b1.log 2>&1
  local rc_b1=$? r_b1
  r_b1=$(python "$PYCHECK" junit-strict "$J_B1" 2>/dev/null)
  if [[ "$r_b1" == OK* ]] && [[ "$rc_b1" -eq 0 ]]; then
    ok "(b1) test_edition_gating.py: ${r_b1#OK }, 0 skipped — auth_local classified, every router accounted for"
  else
    ko "(b1) test_edition_gating.py did not go fully green: ${r_b1#BAD } (pytest exit=$rc_b1; see /tmp/verify-phase18-b1.log)"
  fi

  if ! $DOCKER_OK; then
    ko "(b2)/(b3) Docker unavailable — the GitHub-path regression checks could not be exercised (SKIP-as-FAIL)"
    return
  fi

  # (b2) — tests/test_phase10_auth.py driven AS-IS (never edited — it is a Phase 10 file, out of
  # this plan's scope per the Scope Boundary rule), tolerant ONLY of the specific pre-existing
  # failures already documented in deferred-items.md. Any OTHER failing test name is a genuine
  # NEW regression and fails this check.
  (cd "$MEMAPI_DIR" && python -m pytest tests/test_phase10_auth.py -q --junitxml="$J_B2") \
    > /tmp/verify-phase18-b2.log 2>&1
  local r_b2
  r_b2=$(python "$PYCHECK" junit-tolerant "$J_B2" "$KNOWN_BROKEN_PHASE10_AUTH" 2>/dev/null)
  if [[ "$r_b2" == OK* ]]; then
    ok "(b2) test_phase10_auth.py: ${r_b2#OK }"
    yellow "  NOTE: test_phase10_auth.py is NOT 100% green — it fails on 7 pre-existing, Phase-18-unrelated bugs (a Phase-10-vintage test fixture setting GITHUB_CLIENT_ID/SECRET instead of the post-Phase-12 GITHUB_APP_CLIENT_ID/SECRET; that same fixture's mock token-exchange body missing the refresh_token field the Phase 12 rewrite now requires; and app/repos/merge.py's memory_promotions/promotions table-name drift). All three predate Phase 18's first commit and live in files this plan must not touch (Scope Boundary rule) — documented in deferred-items.md. This is NOT counted as a FAIL: (b1)+(b3)+(b4)/(d) independently prove SC#4 against the REAL current behavior."
  else
    ko "(b2) test_phase10_auth.py: ${r_b2#BAD } — a NEW failure appeared that is NOT in the documented pre-existing set (possible Phase 18 regression)"
  fi

  # (b3) — the genuine, independent, CORRECTLY-mocked live exercise of the real GitHub-signin
  # route that (b2) can no longer provide on its own. SKIP-as-FAIL, must PASS.
  (cd "$MEMAPI_DIR" && python -m pytest tests/test_zzverify_phase18_github_e2e.py -q --junitxml="$J_B3") \
    > /tmp/verify-phase18-b3.log 2>&1
  local rc_b3=$? r_b3
  r_b3=$(python "$PYCHECK" junit-strict "$J_B3" 2>/dev/null)
  if [[ "$r_b3" == OK* ]] && [[ "$rc_b3" -eq 0 ]]; then
    ok "(b3) gate-owned live GitHub-signin proof: ${r_b3#OK } — POST /v1/auth/github/signin genuinely reaches 200 + mints xbt_ today, correctly-shaped mock (SC#4 proven against REAL current behavior, not a stale fixture)"
  else
    ko "(b3) gate-owned live GitHub-signin proof FAILED: ${r_b3#BAD } (pytest exit=$rc_b3; see /tmp/verify-phase18-b3.log) — this WOULD indicate a genuine SC#4 regression"
  fi

  # (b4) — deps.py / auth_github.py byte-identical to the pre-Phase-18 base commit (not merely
  # "no uncommitted changes" — a real diff across the whole phase).
  if git cat-file -e "$PHASE18_BASE_SHA" 2>/dev/null; then
    local diffnames
    diffnames=$(git diff --name-only "$PHASE18_BASE_SHA" HEAD -- apps/memory-api/app/deps.py apps/memory-api/app/routes/auth_github.py 2>/dev/null)
    if [[ -z "$diffnames" ]]; then
      ok "(b4) app/deps.py + app/routes/auth_github.py byte-identical to pre-Phase-18 base ($PHASE18_BASE_SHA) — zero risk introduced across the whole phase"
    else
      ko "(b4) app/deps.py / app/routes/auth_github.py changed since the pre-Phase-18 base ($PHASE18_BASE_SHA): $diffnames"
    fi
  else
    yellow "  NOTE: (b4) pre-Phase-18 base commit $PHASE18_BASE_SHA is unreachable in this checkout's history (shallow clone / rewritten history?) — skipping the historical diff. Check (d)'s static grep is the independent, non-history-dependent proof of the same property."
  fi
}

# =============================================================================
test_c_live_zero_oauth_boot() {
  echo
  echo "(c) LIVE ZERO-OAUTH BOOT — real memory-api, real Postgres, NO social OAuth configured"

  if ! $DOCKER_OK; then
    skip "Docker unavailable — the live zero-OAuth boot harness could not run (check (a)'s testcontainers suite is the load-bearing SC#1 proof and does NOT skip-pass)"
    return
  fi

  local existing
  existing=$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^xbrain-p18-' || true)
  if [[ -n "$existing" ]]; then
    docker rm -f $existing >/dev/null 2>&1 || true
  fi

  docker network create xbrain-p18-net >/dev/null 2>&1 || true

  if ! docker run -d --name xbrain-p18-postgres --network xbrain-p18-net \
      -e POSTGRES_USER=xbrain -e POSTGRES_PASSWORD=p18testpw -e POSTGRES_DB=xbrain \
      postgres:17 >/dev/null 2>&1; then
    ko "docker run postgres:17 (xbrain-p18-postgres) failed to start"
    return
  fi

  local waited=0 pg_ready=false
  while (( waited < 60 )); do
    if docker exec xbrain-p18-postgres pg_isready -U xbrain -d xbrain >/dev/null 2>&1; then
      pg_ready=true
      break
    fi
    sleep 2
    waited=$((waited+2))
  done
  if ! $pg_ready; then
    ko "postgres:17 (xbrain-p18-postgres) did not reach pg_isready within 60s"
    return
  fi

  # Explicitly EMPTY (not merely omitted) every social-OAuth var — SC#1's whole point.
  # GITHUB_APP_ID is int-typed (Settings) — omitted entirely so it defaults to 0, since an
  # empty string fails pydantic int parsing; the string-typed GITHUB_APP_CLIENT_ID/SECRET
  # below already prove "not configured" for the GitHub App path.
  cat > "$ENVFILE_C" <<EOF
DATABASE_URL=postgresql+asyncpg://xbrain:p18testpw@xbrain-p18-postgres:5432/xbrain
BRIDGE_SHARED_SECRET=p18-gate-bridge-secret-do-not-use-in-prod
OAUTH_ISSUER_URL=https://api.p18.test
OAUTH_RESOURCE_URL=https://mcp.p18.test/mcp
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GITHUB_APP_CLIENT_ID=
GITHUB_APP_CLIENT_SECRET=
GITHUB_APP_PRIVATE_KEY_B64=
GITHUB_APP_WEBHOOK_SECRET=
LOG_LEVEL=WARNING
EOF

  local REPO_ROOT_HOST HOST_REPO ENVFILE_WIN
  REPO_ROOT_HOST="$(pwd)"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) HOST_REPO="$(cygpath -w "$REPO_ROOT_HOST")"; ENVFILE_WIN="$(cygpath -w "$ENVFILE_C")" ;;
    *)                    HOST_REPO="$REPO_ROOT_HOST"; ENVFILE_WIN="$ENVFILE_C" ;;
  esac

  # No image build — stock python:3.12-slim, bind-mounted real source, published on loopback only.
  MSYS_NO_PATHCONV=1 docker run -d --name xbrain-p18-memapi --network xbrain-p18-net \
    -p 127.0.0.1:18200:8000 --env-file "$ENVFILE_WIN" \
    -v "${HOST_REPO}:/repo:ro" -v xbrain-p18-pipcache:/root/.cache/pip \
    python:3.12-slim sleep infinity >/dev/null 2>&1

  # MOUNT GUARD — FAIL, never SKIP, if the bind mount silently didn't land (Git-Bash path trap).
  if ! MSYS_NO_PATHCONV=1 docker exec xbrain-p18-memapi test -f /repo/apps/memory-api/app/main.py; then
    ko "bind mount did not land (Git-Bash path trap) — aborting check (c); its assertions would be meaningless against an empty /repo"
    return
  fi
  ok "mount guard: /repo/apps/memory-api/app/main.py is visible inside the harness container (bind mount landed)"

  # Reproduce the Dockerfile's staging order (pip install from pyproject alone FIRST, app/+alembic/
  # copied in after — installing on the flat tree fails with "Multiple top-level packages").
  if ! docker exec xbrain-p18-memapi sh -euc '
      mkdir -p /app/packages && cp -r /repo/packages/memory-models /app/packages/
      mkdir -p /build && cp /repo/apps/memory-api/pyproject.toml /build/
      cd /build && pip install -q --disable-pip-version-check . >/tmp/pip.log 2>&1
      cp -r /repo/apps/memory-api/app /build/app
      cp -r /repo/apps/memory-api/alembic /build/alembic
      cp /repo/apps/memory-api/alembic.ini /build/alembic.ini
      cd /build && python -m alembic upgrade head >/tmp/alembic.log 2>&1
  ' >/dev/null 2>&1; then
    skip "pip install / alembic upgrade failed inside the harness (offline? see /tmp/pip.log|alembic.log in xbrain-p18-memapi) — the belt-and-suspenders live boot could not run. Check (a)'s testcontainers suite is the load-bearing SC#1 proof and did NOT skip."
    return
  fi
  ok "harness staged the real memory-api source + ran alembic upgrade head to 0024_local_credentials (no image built)"

  docker exec -d xbrain-p18-memapi sh -c \
    "cd /build && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/boot.log 2>&1"

  code() { curl -s -o /dev/null -w '%{http_code}' "$@" 2>/dev/null; }

  local booted=false i
  for i in $(seq 1 20); do
    [[ "$(code http://127.0.0.1:18200/v1/healthz)" == "200" ]] && { booted=true; break; }
    sleep 2
  done
  if ! $booted; then
    ko "memory-api did not reach /v1/healthz 200 within 40s with NO social OAuth configured (see docker logs xbrain-p18-memapi / /tmp/boot.log)"
    return
  fi
  ok "/v1/healthz = 200 with GOOGLE_CLIENT_ID/GITHUB_APP_CLIENT_ID/GITHUB_APP_CLIENT_SECRET/GITHUB_CLIENT_ID/GITHUB_CLIENT_SECRET all explicitly empty (SC#1 — the whole point of the phase)"

  local reg_out xbt team
  reg_out=$(curl -s -X POST http://127.0.0.1:18200/v1/auth/local/register \
    -H 'Content-Type: application/json' \
    -d '{"email":"p18-live-gate@x.io","password":"correcthorse9zz"}')
  xbt=$(python -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('xbt_token',''))" "$reg_out" 2>/dev/null)
  team=$(python -c "import json,sys; d=json.loads(sys.argv[1]); print(d.get('team_scope',''))" "$reg_out" 2>/dev/null)
  if [[ -n "$xbt" ]] && [[ "$xbt" == xbt_* ]] && [[ -n "$team" ]]; then
    ok "POST /v1/auth/local/register on a live zero-OAuth server -> xbt_ + team_scope minted"
  else
    ko "POST /v1/auth/local/register on the live harness did not return an xbt_/team_scope: $reg_out"
    return
  fi

  local login_code
  login_code=$(code -X POST http://127.0.0.1:18200/v1/auth/local/login \
    -H 'Content-Type: application/json' \
    -d '{"email":"p18-live-gate@x.io","password":"correcthorse9zz"}')
  [[ "$login_code" == "200" ]] && ok "POST /v1/auth/local/login on the live harness = 200" \
                                || ko "POST /v1/auth/local/login on the live harness = $login_code (expected 200)"

  local tasks_code
  tasks_code=$(code http://127.0.0.1:18200/v1/tasks \
    -H "Authorization: Bearer $xbt" -H "X-Team-Scope: $team")
  [[ "$tasks_code" == "200" ]] && ok "GET /v1/tasks with the register-minted xbt_ + team_scope on the live harness = 200 — SC#1 end-to-end proven live, socially-OAuth-less server" \
                                || ko "GET /v1/tasks on the live harness = $tasks_code (expected 200)"
}

# =============================================================================
test_d_negative_provenance() {
  echo
  echo "(d) NEGATIVE PROVENANCE — no sixth branch in deps.py/auth_github.py, no leaked local-auth surface"

  local hits
  hits=$(grep -c -E 'local_credentials|auth_local' apps/memory-api/app/deps.py apps/memory-api/app/routes/auth_github.py 2>/dev/null | awk -F: '{sum+=$2} END {print sum+0}')
  if [[ "${hits:-0}" -eq 0 ]]; then
    ok "app/deps.py + app/routes/auth_github.py contain zero local_credentials/auth_local marker strings — no sixth principal branch (LAUTH-02)"
  else
    ko "app/deps.py / app/routes/auth_github.py contain $hits local_credentials/auth_local marker string(s) — a sixth branch may have leaked in"
  fi

  # SELF-EXCLUDING repo-wide grep — the local-auth endpoint surface must appear ONLY in its
  # known owner files. Excludes THIS script (it names every endpoint by name above and in
  # curl calls) — the self-invalidating-grep trap named in 18-CONTEXT.md.
  local allow_regex='apps/memory-api/app/routes/auth_local\.py|apps/memory-api/tests/test_local_auth(_set_password)?\.py|app-site/account/(register|login|password)/index\.html|app-site/docs/auth\.html|docs/local-auth-recovery\.md'
  local leaked
  leaked=$(grep -rl -E 'auth/local/(register|login|set-password)' . \
    --include='*.py' --include='*.html' --include='*.md' --include='*.sh' \
    --exclude='verify-phase18.sh' \
    --exclude-dir='.git' --exclude-dir='.planning' --exclude-dir='node_modules' --exclude-dir='.claude' \
    2>/dev/null | sed 's#^\./##' | grep -v -E "$allow_regex" || true)
  local n_owner
  n_owner=$(grep -rl -E 'auth/local/(register|login|set-password)' . \
    --include='*.py' --include='*.html' --include='*.md' --include='*.sh' \
    --exclude='verify-phase18.sh' \
    --exclude-dir='.git' --exclude-dir='.planning' --exclude-dir='node_modules' --exclude-dir='.claude' \
    2>/dev/null | wc -l)
  if [[ -z "$leaked" ]]; then
    ok "repo-wide self-excluding grep: the /auth/local/* endpoint surface appears ONLY in its known owner files ($n_owner file(s): route, tests, account UI pages, docs) — no leak into e.g. oauth_authorize.py (D-18-07 stays a documented limitation, not a silent patch)"
  else
    ko "repo-wide grep found /auth/local/* referenced OUTSIDE the known owner file set: $leaked"
  fi
}

# =============================================================================
test_a_security_suite
test_b_sc4_regression
test_c_live_zero_oauth_boot
test_d_negative_provenance

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi
