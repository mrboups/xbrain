---
phase: 260509-a1b-mcp-brain-remote-server
verified: 2026-05-09T00:00:00Z
status: passed
score: 9/9 must-haves verified
overrides_applied: 0
---

# Quick Task 260509-a1b: mcp-brain Remote MCP Server — Verification Report

**Task Goal:** Build mcp-brain remote MCP server for Claude.ai + ChatGPT web access to team brain — new FastMCP sidecar (port 8104) with 9 tools, personal API token endpoint in memory-api, nginx vhost mcp.grooveos.app, docker-compose entry, OpenAPI spec for ChatGPT Actions.
**Verified:** 2026-05-09
**Status:** PASSED
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | POST /mcp on mcp.grooveos.app routes to FastMCP sidecar (mcp-brain:8104) | COVERED | `40-mcp.conf` proxies all requests to `http://mcp-brain:8104`; docker-compose exposes port 8104 on `xbrain-mcp-brain` |
| 2 | Bearer API token validates against user_api_tokens table in postgres | COVERED | `deps.py` lines 103-127: `xbt_` prefix detected, SHA-256 hash lookup against `user_api_tokens` table, 401 on miss |
| 3 | 9 MCP tools: memory_search, memory_add, tasks_list, task_create, task_update, contacts_search, contact_add, agent_invoke, team_context | COVERED | `app/main.py` contains exactly 9 `@mcp.tool()` decorators at lines 35, 56, 77, 96, 117, 136, 157, 182, 203 — all 9 named tools present |
| 4 | Personal API token CRUD at /v1/me/api-token in memory-api | COVERED | `me.py` lines 219-310: `POST /me/api-token`, `GET /me/api-token`, `DELETE /me/api-token/{token_id}` — full CRUD with real DB queries |
| 5 | Migration 0013 creates user_api_tokens table | COVERED | `0013_api_tokens.py` revision="0013", down_revision="0012", creates `user_api_tokens` with all specified columns and indexes |
| 6 | deps.py validates API token as 4th auth path (kind=user_api_token) | COVERED | `deps.py` lines 102-127: `xbt_` check is placed after Google OIDC and GitHub OAuth paths, before bridge JWT fallback; returns `kind=user_api_token` principal |
| 7 | docker-compose includes mcp-brain service | COVERED | `docker-compose.yml` lines 680-701: `mcp-brain` service block with correct image, port 8104, memory-api healthcheck dependency |
| 8 | nginx 40-mcp.conf serves mcp.grooveos.app with CORS headers | COVERED | `40-mcp.conf` has OPTIONS preflight returning 204, `Access-Control-Allow-Origin: *`, `Access-Control-Allow-Headers` including `Mcp-Session-Id`, `proxy_buffering off` for SSE |
| 9 | OpenAPI spec chatgpt-actions.json in apps/mcp-brain/ | COVERED | `apps/mcp-brain/chatgpt-actions.json` is valid OpenAPI 3.0 JSON with `servers: [{url: "https://api.grooveos.app"}]`, bearerAuth scheme, 5 paths (memory/search, memory/upsert, tasks, crm/contacts, me) |

**Score:** 9/9 truths verified

---

### Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `apps/mcp-brain/Dockerfile` | VERIFIED | Exists, substantive — python:3.11-slim, EXPOSE 8104, CMD python -m app.main |
| `apps/mcp-brain/pyproject.toml` | VERIFIED | Exists — declares `mcp>=1.27.0`, `httpx>=0.28.0`, `structlog`, `pydantic-settings` |
| `apps/mcp-brain/app/main.py` | VERIFIED | 217 lines, 9 tools, real _resolve() calling memory_client.get_me(), mcp.run(transport="streamable-http") |
| `apps/mcp-brain/app/config.py` | VERIFIED | Settings with MEMORY_API_URL, FASTMCP_HOST, FASTMCP_PORT=8104 |
| `apps/mcp-brain/app/memory_client.py` | VERIFIED (not listed but present) | Glob confirms file exists at apps/mcp-brain/app/memory_client.py |
| `apps/memory-api/alembic/versions/0013_api_tokens.py` | VERIFIED | revision=0013, creates user_api_tokens with all 8 columns + 2 indexes, includes downgrade() |
| `apps/memory-api/app/routes/me.py (extended)` | VERIFIED | 311 lines — has api-token CRUD (POST/GET/DELETE) plus GET /me returning api_token_team_scope when kind=user_api_token |
| `apps/memory-api/app/deps.py (extended)` | VERIFIED | imports asyncio, hashlib, types at top; _touch_token() helper; xbt_ auth block at lines 102-127; get_team_scope validates api_token_team_scope at lines 191-193 |
| `infrastructure/nginx/conf.d/40-mcp.conf` | VERIFIED | 37 lines, mcp.grooveos.app server block, CORS preflight, proxy to mcp-brain:8104, SSE support |
| `infrastructure/docker-compose.yml (extended)` | VERIFIED | mcp-brain service block present with context ../apps/mcp-brain, port 8104, mem_limit 128m |
| `apps/mcp-brain/chatgpt-actions.json` | VERIFIED | OpenAPI 3.0.0 with servers=[api.grooveos.app], bearerAuth, 5 paths covering memory/tasks/contacts/crm/me |

---

### Key Links

| From | To | Via | Status |
|------|----|-----|--------|
| nginx 40-mcp.conf | mcp-brain:8104 | `proxy_pass $mcp_brain_upstream` | WIRED |
| docker-compose mcp-brain | apps/mcp-brain | `build.context: ../apps/mcp-brain` | WIRED |
| mcp-brain/app/main.py | memory_client | `from app import memory_client` | WIRED |
| main.py _resolve() | GET /v1/me | `memory_client.get_me(token)` → `{_BASE}/v1/me` | WIRED |
| deps.py xbt_ path | user_api_tokens | SQL JOIN users via token_hash WHERE revoked_at IS NULL | WIRED |
| me.py POST /me/api-token | user_api_tokens | INSERT with hash, returns plaintext once | WIRED |
| me.py GET /v1/me | api_token_team_scope | returns field when principal kind=user_api_token | WIRED |
| get_team_scope | api_token_team_scope | validates `principal["api_token_team_scope"] != x_team_scope` → 403 | WIRED |

---

### Anti-Patterns Found

None. No TODO/FIXME/placeholder patterns. No empty return stubs. All handlers perform real DB queries or real HTTP calls.

Notable: `_touch_token()` in deps.py uses `asyncio.create_task()` (fire-and-forget) and the helper opens its own session via `async_session_factory()` — the plan specified `_touch_token(str(row["id"]), session)` with a session parameter, but the implementation uses a standalone session instead. This is functionally equivalent (and actually safer — avoids session reuse across task boundary). Not a defect.

---

### Human Verification Required

1. **DNS for mcp.grooveos.app**
   - Test: `curl -I https://mcp.grooveos.app/nginx-health` from external
   - Expected: 200 OK — confirms DNS is pointed to VM and nginx terminates TLS (or redirects to HTTPS)
   - Why human: nginx config only listens on port 80; TLS/HTTPS termination (Cloudflare proxy or certbot) is not visible in these files

2. **MCP protocol handshake with Claude.ai**
   - Test: Add `https://mcp.grooveos.app` as a remote MCP server in Claude.ai settings with a valid xbt_ token
   - Expected: Claude.ai connects, lists 9 tools, invokes memory_search successfully
   - Why human: StreamableHTTP transport handshake and SSE keep-alive behavior cannot be verified statically

3. **Migration 0013 applied on live DB**
   - Test: `docker exec xbrain-postgres psql -U $POSTGRES_USER -d $POSTGRES_DB -c '\d user_api_tokens'`
   - Expected: Table exists with all 8 columns
   - Why human: migration file exists but runtime application on the VM cannot be confirmed from the codebase

---

## Summary

All 9 must-have truths are fully implemented and wired. Every artifact exists with substantive content. The auth chain is complete: xbt_ token is hashed → looked up in user_api_tokens → returns principal with api_token_team_scope → mcp-brain _resolve() calls GET /v1/me to extract team_scope → all 9 tools use team_scope correctly. The nginx vhost forwards Authorization and Mcp-Session-Id headers and has SSE support (proxy_buffering off). The OpenAPI spec targets api.grooveos.app directly (correct for ChatGPT Actions). No stubs, no placeholder implementations found.

---

_Verified: 2026-05-09_
_Verifier: Claude (gsd-verifier)_
