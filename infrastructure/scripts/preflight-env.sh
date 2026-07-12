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

echo "PREFLIGHT OK — all 5 required vars are set in ${ENV_FILE}."
exit 0
