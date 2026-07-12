#!/bin/sh
# Phase 14 (14-03b) — entrypoint-envsubst fallback.
#
# Why this exists: LibreChat's native ${VAR} substitution (extractEnvVariable)
# is only applied at specific field-consumption sites (apiKey, custom endpoint
# baseURL, MCP url/env/headers) — NOT to registration.allowedDomains (a plain
# string array read directly off the parsed config) or customUserVars.description
# (a plain z.string() with no transform). Proven by inspecting the source of the
# image this Dockerfile actually pins — ghcr.io/danny-avila/librechat:v0.8.5
# (packages/api/src/endpoints/custom/config.ts, packages/data-provider/src/mcp.ts,
# api/server/middleware/checkDomainAllowed.js) — see 14-03b-SUMMARY.md for the trace.
# Rendering the template here, once, at container start, makes every operator-configurable
# string in librechat.yaml resolve uniformly, regardless of which mechanism LibreChat
# itself happens to use per-field.
#
# Only the vars below are substituted — every other ${VAR} in the file
# (apiKey, MCP headers, etc.) is left untouched for LibreChat's own native
# per-field substitution to resolve from the same process.env at runtime.
set -e

# Phase 14 (14-07) — AGENT_MENTION_PRIMARY derivation.
#
# Why derived, not configured separately: the promptPrefix system prompts need
# to NAME a mention alias that actually resolves server-side. The server-side
# detector (memory-api's mention_detector.py, see 14-01) reads the FULL alias
# SET from AGENT_MENTION_ALIASES and treats it as unordered — any listed alias
# resolves. A second "display alias" env var would be a second source of truth
# that could silently drift from the real list (e.g. an operator updates the
# alias list but forgets the display var, and the prompt names an alias that no
# longer resolves). Instead AGENT_MENTION_PRIMARY is always DERIVED as the first
# comma-separated entry of AGENT_MENTION_ALIASES — by construction it is always
# a member of the working alias set. Ordering the list is therefore a free,
# purely-cosmetic lever: AGENT_MENTION_ALIASES=chad,agent makes the prompt show
# "@chad" while both "@chad" and "@agent" still resolve.
# The value is substituted INTO a double-quoted YAML string (the promptPrefix), so it must not
# contain anything that could terminate or corrupt that string. Whitelist the identifier charset
# rather than blacklisting: a stray `"` or `\` in the alias would otherwise render an unparseable
# librechat.yaml and the container would fail to start with a confusing YAML error.
AGENT_MENTION_PRIMARY=$(printf '%s' "${AGENT_MENTION_ALIASES:-agent}" \
  | cut -d, -f1 | tr -cd 'A-Za-z0-9_-')
[ -z "$AGENT_MENTION_PRIMARY" ] && AGENT_MENTION_PRIMARY=agent
export AGENT_MENTION_PRIMARY

TEMPLATE=/app/librechat.yaml.template
RENDERED=/app/librechat.yaml

if [ -f "$TEMPLATE" ]; then
  envsubst '$LIBRECHAT_ALLOWED_DOMAINS $BRIDGE_BASE_URL $APP_TEAMS_URL $AGENT_MENTION_PRIMARY' \
    < "$TEMPLATE" > "$RENDERED"
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
