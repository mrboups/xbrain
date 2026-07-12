#!/bin/sh
# Phase 14 (14-03b) — entrypoint-envsubst fallback.
#
# Why this exists: LibreChat's native ${VAR} substitution (extractEnvVariable)
# is only applied at specific field-consumption sites (apiKey, custom endpoint
# baseURL, MCP url/env/headers) — NOT to registration.allowedDomains (a plain
# string array read directly off the parsed config) or customUserVars.description
# (a plain z.string() with no transform). Proven by inspecting the shipped
# LibreChat v0.8.2-rc2 image source (packages/api/src/endpoints/custom/config.ts,
# packages/data-provider/src/mcp.ts, api/server/middleware/checkDomainAllowed.js) —
# see 14-03b-SUMMARY.md for the trace. Rendering the template here, once, at
# container start, makes ALL THREE of librechat.yaml's brand strings resolve
# uniformly regardless of which mechanism LibreChat itself uses per-field.
#
# Only the 3 vars below are substituted — every other ${VAR} in the file
# (apiKey, MCP headers, etc.) is left untouched for LibreChat's own native
# per-field substitution to resolve from the same process.env at runtime.
set -e

TEMPLATE=/app/librechat.yaml.template
RENDERED=/app/librechat.yaml

if [ -f "$TEMPLATE" ]; then
  envsubst '$LIBRECHAT_ALLOWED_DOMAINS $BRIDGE_BASE_URL $APP_TEAMS_URL' \
    < "$TEMPLATE" > "$RENDERED"
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
