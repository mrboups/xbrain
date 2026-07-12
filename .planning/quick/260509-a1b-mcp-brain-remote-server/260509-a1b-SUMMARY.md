---
quick_id: "260509-a1b"
slug: mcp-brain-remote-server
date: "2026-05-09"
status: complete
one_liner: "Remote MCP server (FastMCP port 8104) with Bearer API token auth for Claude.ai + ChatGPT web access to team brain"
tags: [mcp, fastmcp, api-tokens, auth, nginx, docker]
key_files:
  created:
    - apps/mcp-brain/Dockerfile
    - apps/mcp-brain/pyproject.toml
    - apps/mcp-brain/app/__init__.py
    - apps/mcp-brain/app/config.py
    - apps/mcp-brain/app/memory_client.py
    - apps/mcp-brain/app/main.py
    - apps/mcp-brain/chatgpt-actions.json
    - apps/memory-api/alembic/versions/0013_api_tokens.py
    - infrastructure/nginx/conf.d/40-mcp.conf
  modified:
    - apps/memory-api/app/routes/me.py
    - apps/memory-api/app/deps.py
    - infrastructure/docker-compose.yml
decisions:
  - "xbt_ prefix used for personal API tokens — recognizable prefix enables prefix-based auth routing in deps.py without extra DB query"
  - "SHA-256 hash stored, plaintext returned once only — standard secure token storage pattern"
  - "get_team_scope validates API token team_scope equals X-Team-Scope header — tokens are single-team scoped"
  - "_touch_token uses fire-and-forget asyncio.create_task — avoids blocking the auth path on last_used_at update"
  - "chatgpt-actions.json points to api.example.com directly — ChatGPT Custom GPT Actions use OpenAPI (not MCP protocol)"
metrics:
  duration: "~25 minutes"
  completed: "2026-05-09"
  tasks_completed: 3
  files_created: 10
  files_modified: 3
---

# Quick Task 260509-a1b: mcp-brain Remote MCP Server — Summary

**One-liner:** Remote MCP server (FastMCP port 8104) with Bearer API token auth (xbt_ prefix, SHA-256 hashed) exposing 9 team brain tools for Claude.ai and ChatGPT web access.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1 | Personal API token system in memory-api | 707127a |
| 2 | mcp-brain FastMCP sidecar | 551446b |
| 3 | Infrastructure: nginx + docker-compose + OpenAPI spec | f9051ae |

## What Was Built

### Task 1: Personal API token system

- **Migration 0013** (`user_api_tokens` table): UUID PK, user_id FK CASCADE, token_hash (SHA-256, UNIQUE), team_scope, name, timestamps, revoked_at for soft-revoke. Indexes on hash (partial, WHERE revoked_at IS NULL) and user_id.
- **GET /v1/me** extended: returns `api_token_team_scope` when `kind=user_api_token` — required by mcp-brain `_resolve()` to know which team to use.
- **POST /v1/me/api-token**: generates `xbt_` + `secrets.token_urlsafe(32)`, stores SHA-256 hash, returns plaintext ONCE.
- **GET /v1/me/api-token**: lists non-revoked tokens (no plaintext exposed).
- **DELETE /v1/me/api-token/{token_id}**: soft-revoke via `revoked_at=now()`.
- **deps.py 4th auth path**: detects `xbt_` prefix → SHA-256 hash lookup → `user_api_token` principal kind.
- **get_team_scope**: API token team_scope validated against X-Team-Scope header — tokens are single-team.
- **`_touch_token`**: fire-and-forget `asyncio.create_task` to update `last_used_at` without blocking auth.

### Task 2: mcp-brain FastMCP sidecar

- **Dockerfile**: mirrors mcp-calendar pattern, `python:3.11-slim`, single worker, `EXPOSE 8104`.
- **pyproject.toml**: `mcp>=1.27.0`, `httpx>=0.28.0`, `structlog>=25.0.0`, `pydantic-settings>=2.0.0`.
- **config.py**: Pydantic Settings — `MEMORY_API_URL`, `FASTMCP_HOST`, `FASTMCP_PORT`.
- **memory_client.py**: async httpx wrapper for all 8 memory-api endpoints (memory_search, memory_add, tasks_list, task_create, task_update, contacts_search, contact_add, agent_invoke, team_context).
- **main.py**: 9 `@mcp.tool()` decorators. `_resolve(ctx)` extracts Bearer token from `ctx.request_context.request.headers`, calls `GET /v1/me` to get `api_token_team_scope`. All tools delegate to `memory_client`. FastMCP import: `from mcp.server.fastmcp import FastMCP, Context`.

### Task 3: Infrastructure

- **nginx/conf.d/40-mcp.conf**: `mcp.example.com` server block with CORS preflight (OPTIONS → 204), `Access-Control-Allow-*` headers for browser MCP clients, `proxy_buffering off` + `proxy_http_version 1.1` for SSE/streaming, 120s read timeout.
- **docker-compose.yml**: `mcp-brain` service added after `mcp-deck`. Port 8104, `nc -z localhost 8104` healthcheck, `mem_limit: 128m`, `depends_on: memory-api`.
- **chatgpt-actions.json**: Valid OpenAPI 3.0 spec for ChatGPT Custom GPT Actions pointing to `api.example.com`. 5 paths: `/v1/memory/search`, `/v1/memory/upsert`, `/v1/tasks` (GET+POST), `/v1/crm/contacts`, `/v1/me`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed unbalanced braces in chatgpt-actions.json**
- **Found during:** Task 3 verification
- **Issue:** `/v1/memory/upsert` requestBody had 14 opening braces and 13 closing braces — invalid JSON.
- **Fix:** Added missing closing `}` for the outer `content` object.
- **Files modified:** `apps/mcp-brain/chatgpt-actions.json`
- **Commit:** 9f21d52

## Known Stubs

None — all endpoints delegate to real memory-api routes. mcp-brain is a thin proxy; no data is stubbed.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: new_auth_path | apps/memory-api/app/deps.py | New `xbt_` token auth path — 4th principal kind bypasses Google/GitHub OIDC. Tokens are long-lived (no expiry in v1). Consider adding expiry or rotation mechanism in a future plan. |
| threat_flag: cors_wildcard | infrastructure/nginx/conf.d/40-mcp.conf | `Access-Control-Allow-Origin: *` on mcp.example.com — mitigated by Bearer token requirement, but wildcard is broader than needed for Claude.ai-only use. |

## Self-Check: PASSED

All 10 created/modified files exist on disk. All 4 commits verified in git log (707127a, 551446b, f9051ae, 9f21d52).
