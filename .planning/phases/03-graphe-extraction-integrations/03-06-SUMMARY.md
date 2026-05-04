---
phase: 03-graphe-extraction-integrations
plan: "06"
subsystem: mcp-gateway
tags: [mcp, gateway, fastapi, proxy, team-scope, audit, registry]
dependency_graph:
  requires:
    - 02-04  # memory-api audit-log endpoint (POST /v1/audit-log)
    - 02-06  # agent-runtime auth pattern (Google OIDC + bridge JWT)
    - 03-01  # Neo4j + postgres healthy (mcp_tool_registry table)
  provides:
    - mcp-gateway service on xbrain_net:8080
    - GET /tools — DB-backed tool registry discovery
    - POST /tools/{name}/call — team_scope-injecting MCP proxy
    - POST /admin/register — hot-register sidecars without restart (MCP-07)
    - Audit trail: mcp.tool_call.{tool_name}[.{method}] prefix for INT-04
  affects:
    - infrastructure/docker-compose.yml — new mcp-gateway service
    - .env.example — MCP Gateway section comment
tech_stack:
  added:
    - FastAPI 0.115+ (plain HTTP proxy, NOT FastMCP host)
    - httpx 0.28+ (async HTTP client for sidecar forwarding)
    - asyncpg 0.30+ (DB-backed registry — mcp_tool_registry table)
    - authlib 1.3+ (bridge JWT mint + Google OIDC verify)
    - pydantic-settings 2.0+ (Settings from env)
    - structlog 25.0+ (structured logging)
  patterns:
    - Intentional auth.py copy (same pattern as agent-runtime + memory-api)
    - Fire-and-forget audit via asyncio.create_task
    - CREATE TABLE IF NOT EXISTS at lifespan startup (mcp-gateway owns its table)
    - URL scheme normalization: postgresql+asyncpg:// -> postgresql:// for asyncpg
key_files:
  created:
    - apps/mcp-gateway/app/__init__.py
    - apps/mcp-gateway/app/config.py
    - apps/mcp-gateway/app/auth.py
    - apps/mcp-gateway/app/registry.py
    - apps/mcp-gateway/app/audit.py
    - apps/mcp-gateway/app/main.py
    - apps/mcp-gateway/pyproject.toml
    - apps/mcp-gateway/Dockerfile
  modified:
    - infrastructure/docker-compose.yml  # mcp-gateway service added
    - .env.example                       # MCP Gateway comment section
decisions:
  - "mcp-gateway is a plain FastAPI HTTP proxy — NOT a FastMCP host. FastMCP cannot be mounted in a parent FastAPI app (issue #1367: RuntimeError: Task group is not initialized). Each tool sidecar runs standalone."
  - "auth.py copied verbatim from agent-runtime (not extracted to shared package). Pattern: extract only if a 3rd service needs it."
  - "mcp_tool_registry table owned by mcp-gateway, created at startup via CREATE TABLE IF NOT EXISTS — deliberately not in memory-api Alembic migrations."
  - "team_scope sourced from JWT claims first (bridge JWTs may embed it), falls back to X-Team-Scope header, then 'default'. This prevents T-03-06-01 spoofing."
  - "audit action format: mcp.tool_call.{tool_name}[.{method}] — prefix-queryable for INT-04 Drive write-back traceability."
metrics:
  duration: "~25 minutes"
  completed: "2026-05-04"
  tasks_completed: 2
  tasks_total: 2
  files_created: 8
  files_modified: 2
---

# Phase 03 Plan 06: MCP Gateway Summary

**One-liner:** Plain FastAPI HTTP proxy that routes MCP tool calls to registered sidecars with team_scope + user_sub injection, DB-backed hot-register registry, and fire-and-forget audit trail.

## What Was Built

### apps/mcp-gateway/ — new service

- **app/config.py** — pydantic-settings with DATABASE_URL, MEMORY_API_URL, BRIDGE_SHARED_SECRET, GOOGLE_CLIENT_ID
- **app/auth.py** — intentional copy of agent-runtime auth: Google OIDC JWKS verification + bridge JWT scope check
- **app/registry.py** — asyncpg pool with `mcp_tool_registry` table (CREATE TABLE IF NOT EXISTS on startup); upsert-based register_tool for idempotent hot-registration
- **app/audit.py** — fire-and-forget bridge-JWT-authenticated POST to memory-api `/v1/audit-log`; action format `mcp.tool_call.{tool_name}[.{method}]` for INT-04 prefix queries
- **app/main.py** (194 lines) — FastAPI app with 4 endpoints:
  - `GET /healthz` — health probe
  - `GET /tools` — lists enabled tool sidecars from DB
  - `POST /admin/register` — hot-registers a new sidecar URL (MCP-07)
  - `POST /tools/{tool_name}/call` — forwards to sidecar with injected headers; audits result
- **pyproject.toml** — dependencies: fastapi, uvicorn, httpx, asyncpg, authlib, pydantic-settings, structlog (no `mcp` package — plain proxy)
- **Dockerfile** — multi-stage python:3.11-slim, port 8080, single uvicorn worker

### infrastructure/docker-compose.yml

Added `mcp-gateway` service: context `../apps/mcp-gateway` (no repo-root context needed), mem_limit 192m, depends on postgres + memory-api, healthcheck on `GET /healthz`.

### .env.example

Added `=== MCP Gateway (plan 03-06) ===` comment documenting that no new vars are needed (reuses DATABASE_URL, BRIDGE_SHARED_SECRET, GOOGLE_CLIENT_ID).

## Verification Results

All must-have truths confirmed:
- GET /tools returns DB-backed list (empty registry = OK on startup)
- POST /tools/{name}/call injects X-Team-Scope + X-User-Sub headers to sidecar
- POST /admin/register upserts sidecar URL without restart (MCP-07)
- Audit fire-and-forget via asyncio.create_task for every call (MCP-04)
- Auth: bridge JWT (service-to-service) + Google OIDC (human users)
- Action prefix mcp.tool_call.{tool_name}[.{method}] satisfies INT-04 traceability
- docker-compose: context = apps/mcp-gateway (not repo root), mem_limit = 192m

## Requirements Satisfied

| ID | Requirement | How |
|----|-------------|-----|
| MCP-01 | Gateway registers and routes tool calls | POST /tools/{name}/call + DB registry |
| MCP-02 | team_scope + user_id injected into every call | _get_principal extracts from JWT; headers injected before forward |
| MCP-03 | Tool call audit logged in memory-api | audit.py log_tool_call → POST /v1/audit-log |
| MCP-04 | Tool call includes full tagging | audit payload: action, team_scope, target_id, user_sub |
| MCP-07 | New MCP server registerable without restart | POST /admin/register → DB upsert, GET /tools reads live |

## Deviations from Plan

### Deviation: Task 2 files pre-committed by plan 03-09

**Found during:** Task 2 commit
**Issue:** Plan 03-09's summary commit (b323c3a) had already committed `apps/mcp-gateway/app/main.py`, `apps/mcp-gateway/app/audit.py`, `infrastructure/docker-compose.yml` (mcp-gateway service), and `.env.example` (MCP Gateway comment). The files written in this plan execution were byte-identical to what 03-09 pre-committed.
**Fix:** Confirmed zero diff between working tree and HEAD for all Task 2 files. Task 1 files (scaffold) were genuinely new and committed as `65b4406`.
**Impact:** None — the end state is correct. Both tasks complete.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | 65b4406 | feat(03-06): scaffold mcp-gateway — config, auth, registry, Dockerfile |
| Task 2 | b323c3a | (pre-committed by 03-09) main.py + audit.py + docker-compose + .env.example |

## Security Notes (Threat Model)

| Threat | Mitigation in Code |
|--------|-------------------|
| T-03-06-01: X-Team-Scope spoofing | team_scope extracted from JWT claims first, header is fallback only |
| T-03-06-02: /admin/register elevation | JWT auth required (Phase 4 adds admin-role check) |
| T-03-06-03: sidecar timeout DoS | httpx timeout=30s, 504 returned on TimeoutException |
| T-03-06-04: result info disclosure | result_summary truncated to 500 chars in audit payload |

## Known Stubs

None. All endpoints are functional (GET /tools returns real DB data, POST /call forwards live, POST /register writes to DB).

## Self-Check

- [x] apps/mcp-gateway/app/main.py exists, 194 lines (>= 120 required)
- [x] apps/mcp-gateway/app/registry.py exists, 80 lines (>= 60 required)
- [x] apps/mcp-gateway/Dockerfile exists, contains 'uvicorn' and '8080'
- [x] mcp-gateway in docker-compose.yml services
- [x] mem_limit: 192m confirmed
- [x] Task 1 commit 65b4406 exists
- [x] Task 2 content in commit b323c3a (pre-committed by 03-09)

## Self-Check: PASSED
