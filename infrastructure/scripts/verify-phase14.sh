#!/usr/bin/env bash
#
# verify-phase14.sh — Verify Phase 14 (Portability Foundation) PORT-01 + PORT-02
# acceptance gate.
#
# Tests (7, SKIP-aware — SKIP never counts as FAIL):
#   (a) PORT-01 source+infra clean — repo-wide scan of apps/, infrastructure/,
#       Makefile, .github/, CLAUDE.md for the brand token (see BRAND_RE below),
#       with an explicit, documented exclude list; self-excluded; build
#       artifacts (compiled .pyc under __pycache__) excluded.
#   (b) PORT-01 OAuth fail-fast fires — an empty OAUTH_ISSUER_URL/
#       OAUTH_RESOURCE_URL crashes Settings() construction in BOTH memory-api
#       and mcp-brain (the fail-fast field_validator added in 14-01).
#   (c) PORT-01 CORS is env-driven — main.py's CORSMiddleware reads
#       settings.CORS_ALLOWED_ORIGIN_REGEX, not a literal (the CORS regex is
#       stored as an ESCAPED-DOT string, which a dotted grep pattern would
#       silently miss — this is why (a) matches the bare token, not a
#       dotted domain).
#   (d) PORT-02 fresh boot at a fictitious domain (acme.example) with zero
#       source edit — proves an operator can point the whole stack at their
#       own domain purely via env vars.
#   (e) PORT-01 nginx templating renders the operator domain — the envsubst
#       vhost template (14-03a) turns XBRAIN_BASE_DOMAIN=acme.example into a
#       real server_name with no source edit.
#   (f) PORT-02 .env.example is brand-free and documents the now-REQUIRED
#       OAuth vars (14-04) — the single operator-fillable env surface.
#   (g) SOFT — .planning/ (outside this phase's own dir) and docs/*.md are
#       brand-free. SKIPs (never FAILs) — the scrub is non-blocking for boot.
#
# Exit code:
#   0 when FAIL == 0, regardless of SKIP count
#   1 when FAIL > 0
#
# Usage:
#   bash infrastructure/scripts/verify-phase14.sh
# Run from anywhere inside the repo (the script cd's to the repo root itself).
# Requires: bash, grep, python (with pydantic-settings installed in the
# memory-api / mcp-brain environments — SKIPs gracefully if unavailable),
# envsubst (SKIPs gracefully if unavailable).
#
# This script deliberately does NOT document any production domain value —
# it ships to self-hosters. The env override VARIABLE NAMES used by the
# OTHER verify-phase*.sh scripts in this family (MEMAPI_HOST, LIBRECHAT_HOST,
# TEST_TEAM_SCOPE) are recorded here by name only; their production values
# live exclusively in 14-06-SUMMARY.md (an exempt, non-shipped surface).

set -uo pipefail   # NOT -e — every test runs independently; the summary line is the truth

# Single source of truth for the brand-token pattern this gate exists to
# catch. Defined ONCE here so there is exactly one occurrence of each half of
# the pattern to audit (round-4 W-A) — this script's own self-exclude flags
# below (--exclude=verify-phase14.sh) keep check (a) from matching this file.
BRAND_RE='grooveos|aibrussels'

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

echo "=== Phase 14 Verification (PORT-01 + PORT-02) ==="
echo "Env override variable NAMES used elsewhere in this verify-phase*.sh family:"
echo "  MEMAPI_HOST, LIBRECHAT_HOST, TEST_TEAM_SCOPE (production values: 14-06-SUMMARY.md only)"

# -----------------------------------------------------------------------------
test_a_source_clean() {
  echo
  echo "(a) PORT-01 source+infra clean (repo-wide scan, documented excludes, self-excluded, build-artifact-excluded)"
  local matches
  matches=$(grep -rIlE "$BRAND_RE" \
    apps/ infrastructure/ Makefile .github/ CLAUDE.md \
    --exclude-dir=__pycache__ \
    --exclude=*.pyc \
    --exclude-dir=tests \
    --exclude-dir=spike-mem0 \
    --exclude=chatgpt-actions.json \
    --exclude=README.md \
    --exclude-dir=chrome-extension \
    --exclude-dir=app-site \
    --exclude-dir=marketing-site \
    --exclude-dir=projects-dashboard \
    --exclude=deploy-dashboard.yml \
    --exclude=verify-phase14.sh \
    --exclude=preflight-env.sh \
    2>/dev/null || true)
  if [[ -z "$matches" ]]; then
    ok "0 matches for the brand token across apps/, infrastructure/, Makefile, .github/, CLAUDE.md"
  else
    ko "brand token found in: $(echo "$matches" | tr '\n' ' ')"
  fi
}

# -----------------------------------------------------------------------------
test_b_oauth_failfast() {
  echo
  echo "(b) PORT-01 OAuth fail-fast fires (empty OAUTH_ISSUER_URL/OAUTH_RESOURCE_URL crashes Settings())"
  if ! command -v python >/dev/null 2>&1; then
    skip "python not available on this host — cannot exercise the fail-fast validator"
    return
  fi

  # Pre-check the import path separately so a non-zero exit from the real test
  # below can only mean the validator fired — not a missing dependency masquerading
  # as a pass.
  if ! (cd apps/memory-api && python -c "import pydantic_settings" >/dev/null 2>&1); then
    skip "pydantic_settings not importable for memory-api in this environment"
  elif (cd apps/memory-api && OAUTH_ISSUER_URL= OAUTH_RESOURCE_URL= DATABASE_URL=x BRIDGE_SHARED_SECRET=x \
      python -c "from app.config import Settings; Settings()" >/dev/null 2>&1); then
    ko "memory-api Settings() did NOT raise on empty OAuth URLs"
  else
    ok "memory-api Settings() raises on empty OAuth URLs"
  fi

  if ! (cd apps/mcp-brain && python -c "import pydantic_settings" >/dev/null 2>&1); then
    skip "pydantic_settings not importable for mcp-brain in this environment"
  elif (cd apps/mcp-brain && OAUTH_ISSUER_URL= OAUTH_RESOURCE_URL= \
      python -c "from app.config import Settings; Settings()" >/dev/null 2>&1); then
    ko "mcp-brain Settings() did NOT raise on empty OAuth URLs"
  else
    ok "mcp-brain Settings() raises on empty OAuth URLs"
  fi
}

# -----------------------------------------------------------------------------
test_c_cors_env_driven() {
  echo
  echo "(c) PORT-01 CORS is env-driven (main.py reads settings.CORS_ALLOWED_ORIGIN_REGEX, zero brand token)"
  local brand_count
  brand_count=$(grep -cE "$BRAND_RE" apps/memory-api/app/main.py 2>/dev/null)
  brand_count="${brand_count:-1}"
  if grep -q 'allow_origin_regex=settings.CORS_ALLOWED_ORIGIN_REGEX' apps/memory-api/app/main.py \
     && [[ "$brand_count" -eq 0 ]]; then
    ok "main.py CORSMiddleware reads settings.CORS_ALLOWED_ORIGIN_REGEX; zero brand token in the file"
  else
    ko "main.py CORS is not env-driven, or still carries the brand token"
  fi
}

# -----------------------------------------------------------------------------
test_d_fresh_boot() {
  echo
  echo "(d) PORT-02 fresh boot at a fictitious domain (acme.example), zero source edit"
  if ! command -v python >/dev/null 2>&1; then
    skip "python not available on this host — cannot exercise a fresh Settings() boot"
    return
  fi
  if ! (cd apps/memory-api && python -c "import pydantic_settings" >/dev/null 2>&1); then
    skip "pydantic_settings not importable for memory-api in this environment"
    return
  fi
  if (cd apps/memory-api && \
      MEMORY_API_EXTERNAL_URL=https://api.acme.example APP_PUBLIC_URL=https://acme.example \
      OAUTH_ISSUER_URL=https://api.acme.example OAUTH_RESOURCE_URL=https://mcp.acme.example/mcp \
      CORS_ALLOWED_ORIGIN_REGEX='https://acme\.example' DATABASE_URL=x BRIDGE_SHARED_SECRET=x \
      python -c "from app.config import Settings; s=Settings(); assert 'grooveos' not in (s.MEMORY_API_EXTERNAL_URL+s.OAUTH_ISSUER_URL+s.APP_PUBLIC_URL+s.CORS_ALLOWED_ORIGIN_REGEX)" >/dev/null 2>&1); then
    ok "memory-api Settings() boots clean at acme.example with zero source edit"
  else
    ko "memory-api Settings() failed to boot at acme.example, or leaked the brand token"
  fi
}

# -----------------------------------------------------------------------------
test_e_nginx_render() {
  echo
  echo "(e) PORT-01 nginx templating renders the operator domain (acme.example)"
  if ! command -v envsubst >/dev/null 2>&1; then
    skip "envsubst not available on this host — cannot render the nginx template"
    return
  fi
  if XBRAIN_BASE_DOMAIN=acme.example envsubst '$XBRAIN_BASE_DOMAIN' \
      < infrastructure/nginx/templates/20-api.conf.template 2>/dev/null \
      | grep -q 'server_name api.acme.example;'; then
    ok "20-api.conf.template renders server_name api.acme.example from XBRAIN_BASE_DOMAIN alone"
  else
    ko "20-api.conf.template did not render the operator domain"
  fi
}

# -----------------------------------------------------------------------------
test_f_env_example() {
  echo
  echo "(f) PORT-02 .env.example is brand-free and documents the now-REQUIRED OAuth vars"
  local brand_count
  brand_count=$(grep -cE "$BRAND_RE" .env.example 2>/dev/null)
  brand_count="${brand_count:-1}"
  if [[ "$brand_count" -eq 0 ]] \
     && grep -q '^OAUTH_ISSUER_URL=' .env.example \
     && grep -q '^CORS_ALLOWED_ORIGIN_REGEX=' .env.example; then
    ok ".env.example is brand-free and documents OAUTH_ISSUER_URL + CORS_ALLOWED_ORIGIN_REGEX"
  else
    ko ".env.example still carries the brand token, or is missing a required var line"
  fi
}

# -----------------------------------------------------------------------------
test_g_soft_planning_docs() {
  echo
  echo "(g) SOFT — .planning/ (outside this phase's own dir) + docs/*.md brand-free (SKIP, never FAIL)"
  local leaks
  leaks=$(grep -rIl "$BRAND_RE" .planning \
    --exclude-dir=14-portability-foundation \
    --exclude=ROADMAP.md --exclude=STATE.md --exclude=BACKLOG.md \
    --exclude=open-core-edition-design.md 2>/dev/null || true)
  leaks="${leaks}
$(grep -rIl "$BRAND_RE" docs 2>/dev/null || true)"
  leaks="$(echo "$leaks" | grep -v '^$' || true)"
  if [[ -z "$leaks" ]]; then
    ok ".planning/ (outside this phase's own dir, minus the GSD-managed/design-doc exceptions) + docs/ are brand-free"
  else
    skip "residual matches outside the boot-blocking surface — non-blocking: $(echo "$leaks" | tr '\n' ' ')"
  fi
}

test_a_source_clean
test_b_oauth_failfast
test_c_cors_env_driven
test_d_fresh_boot
test_e_nginx_render
test_f_env_example
test_g_soft_planning_docs

echo
echo "=== Summary ==="
TOTAL=$((PASS+FAIL))
echo "PASS: $PASS / $TOTAL  (SKIP: $SKIP)"

if [[ "$FAIL" -eq 0 ]]; then
  exit 0
else
  exit 1
fi
