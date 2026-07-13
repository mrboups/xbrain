#!/usr/bin/env bash
#
# preflight-env.sh — pre-deploy crashloop guard (B3).
#
# 14-03b removed the docker-compose fallback that previously supplied
# OAUTH_ISSUER_URL/OAUTH_RESOURCE_URL, and 14-01 made memory-api + mcp-brain
# REJECT an empty value at boot (Pydantic field_validator). Deploying without
# these — plus the other vars below, each with its own distinct silent or
# fatal failure mode — WILL break the deployment. This script hard-fails
# BEFORE a deploy touches the target, naming the exact missing var and why.
#
# Usage:
#   bash infrastructure/scripts/preflight-env.sh [path-to-env-file]
#   (defaults to .env in the current directory)
#
# Exit code:
#   0 — all 5 vars present and non-empty ("PREFLIGHT OK")
#   1 — at least one var missing or empty (FATAL message names it)
#
# This script SHIPS TO SELF-HOSTERS. It must never name a production domain —
# only generic placeholders. The actual production values for this project
# live exclusively in 14-06-SUMMARY.md, which is not a shipped file.

set -euo pipefail

ENV_FILE="${1:-.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "FATAL: env file '$ENV_FILE' does not exist. Cannot preflight a deploy without it." >&2
  exit 1
fi

fail() {
  local var="$1"
  local why="$2"
  local example="$3"
  echo "FATAL: ${var} is not set in ${ENV_FILE}. ${why}" >&2
  echo "  Set it to your own deployment's value, e.g." >&2
  echo "    ${example}" >&2
  exit 1
}

has_var() {
  # Missing OR present-but-empty both count as absent.
  grep -qE "^$1=.+" "$ENV_FILE" 2>/dev/null
}

if ! has_var OAUTH_ISSUER_URL; then
  fail "OAUTH_ISSUER_URL" \
    "This deployment removed the docker-compose fallback that previously supplied it, and memory-api/mcp-brain now REJECT an empty value at boot (Pydantic field_validator). Deploying without this var WILL crashloop both services." \
    "OAUTH_ISSUER_URL=https://api.yourdomain.example"
fi

if ! has_var OAUTH_RESOURCE_URL; then
  fail "OAUTH_RESOURCE_URL" \
    "Same as OAUTH_ISSUER_URL — the compose fallback is gone, and memory-api/mcp-brain now REJECT an empty value at boot (Pydantic field_validator). Deploying without this var WILL crashloop both services." \
    "OAUTH_RESOURCE_URL=https://mcp.yourdomain.example/mcp"
fi

if ! has_var CORS_ALLOWED_ORIGIN_REGEX; then
  fail "CORS_ALLOWED_ORIGIN_REGEX" \
    "Missing this var means the neutral default (chrome-extension + localhost only) stays in effect in production — your browser extension and web app origins will be silently CORS-blocked." \
    "CORS_ALLOWED_ORIGIN_REGEX=https://app\\.yourdomain\\.example"
fi

if ! has_var XBRAIN_BASE_DOMAIN; then
  fail "XBRAIN_BASE_DOMAIN" \
    "Missing this var means every nginx vhost template renders with its compose default (*.localhost) instead of your real domain — this is a TOTAL INGRESS OUTAGE, not a degraded feature." \
    "XBRAIN_BASE_DOMAIN=yourdomain.example"
fi

if ! has_var AGENT_MENTION_ALIASES; then
  fail "AGENT_MENTION_ALIASES" \
    "Missing this var means the agent mention trigger falls back to the neutral default (agent only) — any additional @mention aliases your users rely on will SILENTLY stop working." \
    "AGENT_MENTION_ALIASES=agent,ai,assistant"
fi

# --- Phase 15: COMPOSE_PROFILES / EDITION consistency ------------------------------------------
# This is the FIRST check in this script of a RELATIONSHIP between two vars (everything above only
# checks that a var is present-and-non-empty).
#
# `session-bridge` lives in the `saas` compose profile and POSTs to memory-api's
# /v1/me/external-sessions. That router is SAAS-ONLY: under EDITION=oss (the default) it is NOT
# mounted, so the register frame gets a 404 — and there is NO other symptom anywhere. The Pro/Max
# routing bridge just silently stops working.
#
# This is enforced HERE, in the ops layer, and NOT as a field_validator inside memory-api:
# COMPOSE_PROFILES is an orchestrator concept, and teaching the application to read it would invert
# the dependency (the app would refuse to boot on a variable that means nothing outside Compose, and
# would break anyone running memory-api standalone with COMPOSE_PROFILES merely exported in their
# shell). preflight runs on every `make deploy`, so a real deploy cannot ship this combination.
PROFILES="$(grep -E '^COMPOSE_PROFILES=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
EDITION_VAL="$(grep -E '^EDITION=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
EDITION_VAL="${EDITION_VAL:-oss}"     # same default as apps/memory-api/app/config.py
case ",${PROFILES}," in
  *,saas,*)
    if [[ "$EDITION_VAL" != "saas" ]]; then
      echo "FATAL: COMPOSE_PROFILES includes 'saas' but EDITION is '${EDITION_VAL}' in ${ENV_FILE}." >&2
      echo "  The 'saas' profile starts session-bridge, which POSTs /v1/me/external-sessions — a" >&2
      echo "  router that EDITION=oss does not mount. It would 404 silently, with no other symptom." >&2
      echo "  Set:" >&2
      echo "    EDITION=saas" >&2
      exit 1
    fi
    ;;
esac

echo "PREFLIGHT OK — all 5 required vars are set in ${ENV_FILE}, and COMPOSE_PROFILES/EDITION are consistent."
exit 0
