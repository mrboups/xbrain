---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: 02
subsystem: api
tags: [fastapi, anthropic, agents, memory-api, platform-agents, postgres, audit]

# Dependency graph
requires:
  - phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
    provides: "Migration 0012 (agent_definitions table + seed meeting-recap)"
provides:
  - "POST /v1/admin/agents — create platform agent (admin)"
  - "GET /v1/admin/agents — list platform agents (admin)"
  - "PATCH /v1/admin/agents/{id} — update platform agent (admin)"
  - "DELETE /v1/admin/agents/{id} — hard-delete platform agent (admin)"
  - "POST /v1/agents/{id}/invoke — synchronous Anthropic invocation (user or bridge)"
  - "memory_item insertion with source='agent', truth_level='WORKING' per invocation"
  - "Audit log on all mutations (create/update/delete/invoke)"
affects:
  - granola-sync (will call /v1/agents/{id}/invoke after meeting ingest — D5)
  - 08-03 (granola per-user loop triggers meeting-recap via bridge JWT)
  - phase-09 (tools_json currently stored only; Phase 9 adds real MCP tool execution)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Anthropic singleton per-module (lazy-init, independent — avoids cross-module coupling)"
    - "Bridge JWT cross-team guard on invoke: bridge.team_scope must match body.team_scope (Pitfall 1)"
    - "tools_json stored as JSONB, validated via Pydantic list[dict], never passed to Anthropic (Pitfall 6)"
    - "Admin CRUD via _is_admin() with audit_log team_scope='_platform' (platform-global resource)"
    - "Invoke audit_log uses body.team_scope (actual team scope of the memory_item)"

key-files:
  created:
    - apps/memory-api/app/routes/agents.py
  modified:
    - apps/memory-api/app/main.py

key-decisions:
  - "tools_json stored as JSONB but never passed to client.messages.create in Phase 8 (Pitfall 6 RESEARCH.md) — Phase 9 adds real tool execution via mcp-gateway"
  - "team_scope comes from invoke body (not JWT header) — bridge JWT has no fixed team_scope per invocation (Pitfall 1 option A)"
  - "Admin CRUD uses team_scope='_platform' in audit_log — agents are global platform resources, not team-scoped"
  - "Hard delete (not soft) — soft-disable via enabled=false in PATCH; hard delete removes from agent_definitions entirely"
  - "Anthropic singleton is module-local to agents.py (not shared with memory.py) — avoids coupling between routers"
  - "invoke endpoint accepts both user (kind=user) and bridge (kind=bridge) — granola-sync uses bridge JWT to auto-trigger meeting-recap (D5)"

patterns-established:
  - "Platform-agent invoke: bridge JWT + body.team_scope + memory_item with source='agent'"
  - "Admin CRUD with audit_log team_scope='_platform' for cross-team platform resources"

requirements-completed: []

# Metrics
duration: 15min
completed: 2026-05-09
---

# Phase 8 Plan 02: Platform Agents Registry — Admin CRUD + Anthropic Invoke Summary

**FastAPI router for platform agent CRUD (admin) and synchronous Anthropic invocation (user/bridge), storing recaps as team-scoped memory_items with full audit trail**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-05-09T15:10:00Z
- **Completed:** 2026-05-09T15:27:21Z
- **Tasks:** 2/2
- **Files modified:** 2

## Accomplishments
- Created `apps/memory-api/app/routes/agents.py` with 5 endpoints: 4 admin CRUD + 1 invoke
- Admin endpoints guarded by `_is_admin()`: POST/GET/PATCH/DELETE `/v1/admin/agents`
- Invoke endpoint accepts user OR bridge principal — enables granola-sync D5 auto-trigger
- `tools_json` accepted and stored as JSONB in `agent_definitions` but never passed to Anthropic (Pitfall 6)
- Bridge JWT cross-team guard on invoke: if bridge carries `team_scope`, must match body (Pitfall 1)
- Recap stored as `memory_item` with `source='agent'`, `truth_level='WORKING'`, and 7-field tagging contract
- Audit log written for all mutations (create/update/delete) and invocations, with correct `team_scope` per context
- Registered router in `apps/memory-api/app/main.py` — exposes `/v1/admin/agents/*` and `/v1/agents/{id}/invoke`

## Task Commits

Each task was committed atomically:

1. **Task 1: Create platform agents router (agents.py)** - `6ea8b47` (feat)
2. **Task 2: Register agents router in main.py** - `e20ada8` (feat)

## Files Created/Modified
- `apps/memory-api/app/routes/agents.py` — New router: 5 endpoints, Anthropic singleton, Pydantic models, audit, 7-field memory_item insertion
- `apps/memory-api/app/main.py` — Added `agents` import + `app.include_router(agents.router, prefix="/v1", tags=["agents"])`

## Decisions Made
- `tools_json` is stored as JSONB and validated by Pydantic but never passed to `client.messages.create` in Phase 8. Phase 9 will add real MCP tool execution via mcp-gateway delegation to agent-runtime LangGraph.
- The `team_scope` on invoke comes from the request body (not a JWT claim) — bridge JWT has no fixed team_scope per invocation. Cross-team forgery is blocked by checking if bridge JWT carries a `team_scope` claim that differs from the body.
- Hard delete is used for `DELETE /v1/admin/agents/{id}` — soft-disable is achieved via `PATCH enabled=false`. This avoids phantom rows in the registry.
- The Anthropic client singleton is instantiated per-module (local to `agents.py`) rather than imported from `memory.py` — prevents cross-module coupling and allows independent configuration per use case.
- Admin CRUD audit logs use `team_scope='_platform'` — agent definitions are global platform resources, not tied to any one team.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

Python on Windows defaults to `cp1252` encoding for file I/O. The file contains UTF-8 characters (em dash in French comments). Verification script was adjusted to use `encoding='utf-8'` explicitly. No code change needed — file was correctly written and parsed by Python's AST with `encoding='utf-8'`.

## User Setup Required

None — no external service configuration required. `ANTHROPIC_API_KEY` was already configured in Phase 7. The `agent_definitions` table is created by migration `0012` (plan 08-01).

## Known Stubs

None — all endpoints are fully wired. The `tools_json` field is intentionally not executed (Pitfall 6) — this is documented behavior, not a stub. Phase 9 will add real tool execution.

## Next Phase Readiness
- `/v1/agents/{id}/invoke` is ready for granola-sync to call after each meeting ingest (D5, plan 08-03)
- `meeting-recap` agent is seeded in migration 0012 with `auto_trigger=true`
- Admin UI (dashboard or curl) can now create/edit/disable agents via `/v1/admin/agents`
- No blockers — all ANTHROPIC_API_KEY dependencies already satisfied

## Self-Check

- `apps/memory-api/app/routes/agents.py` — created and parsed OK
- `apps/memory-api/app/main.py` — modified and parsed OK
- Commit `6ea8b47` — feat(08-02): add platform agents router
- Commit `e20ada8` — feat(08-02): register agents router in main.py

## Self-Check: PASSED

---
*Phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents*
*Completed: 2026-05-09*
