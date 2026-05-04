---
phase: 03-graphe-extraction-integrations
plan: "12"
subsystem: mcp-gateway
tags: [mcp, registration, idempotent, scraper, drive-read, calendar, e2e, smoke-test]
dependency_graph:
  requires:
    - 03-06  # mcp-gateway service + POST /admin/register endpoint
    - 03-07  # mcp-scraper sidecar (port 8100)
    - 03-08  # mcp-drive-read sidecar (port 8101)
    - 03-09  # mcp-calendar sidecar (port 8102)
  provides:
    - infrastructure/scripts/register-mcp-tools.sh — idempotent registration of 3 MCP tools
    - infrastructure/scripts/verify-phase3.sh — 7-test E2E smoke test for Phase 3
  affects:
    - .env.example — MCP_GATEWAY_URL documented with registration instructions
tech_stack:
  added: []
  patterns:
    - Bridge JWT minted inside mcp-gateway container (authlib already installed — no host dependency)
    - Idempotent tool registration via POST /admin/register (ON CONFLICT DO UPDATE in registry.py)
    - SKIP guard pattern for OAuth-dependent smoke tests (mirrors verify-phase2 LANGFUSE_PUBLIC_KEY pattern)
key_files:
  created:
    - infrastructure/scripts/register-mcp-tools.sh
    - infrastructure/scripts/verify-phase3.sh
  modified:
    - .env.example  # MCP_GATEWAY_URL + registration instructions
decisions:
  - "Bridge JWT generated inside mcp-gateway container via docker compose exec (not on VM host) — avoids installing authlib on the VM and ensures BRIDGE_SHARED_SECRET is available from container env"
  - "register_tool() uses urllib.request (stdlib) inside container, no curl dependency — consistent with verify-phase2 exec pattern"
  - "verify-phase3.sh tests 7 scenarios; drive-read + calendar SKIP gracefully when OAuth tokens absent — same pattern as verify-phase2 LANGFUSE_PUBLIC_KEY guard"
  - "verify-phase3.sh scopes to Phase 3 new services only (6 containers) — Phase 1+2 coverage is verify-phase2.sh's responsibility"
metrics:
  duration: "~15 minutes"
  completed: "2026-05-04"
  tasks_completed: 2
  tasks_total: 1
  files_created: 2
  files_modified: 1
---

# Phase 03 Plan 12: MCP Tool Registration + Verify-Phase3 Summary

**One-liner:** Idempotent bash script registers scraper/drive-read/calendar sidecars in mcp-gateway DB registry via bridge JWT, plus 7-test E2E smoke test covering Neo4j, gateway registry, scraper call, and OAuth-graceful drive/calendar checks.

## What Was Built

### infrastructure/scripts/register-mcp-tools.sh

Idempotent registration script for the 3 Phase 3 MCP tools:

| Tool name | Sidecar URL | Description |
|-----------|------------|-------------|
| `scraper` | `http://mcp-scraper:8100` | Fetch URL content as text (max 50KB) — MCP-05 |
| `drive-read` | `http://mcp-drive-read:8101` | Live read/write a Google Drive file by ID — MCP-05, INT-04 |
| `calendar` | `http://mcp-calendar:8102` | List Google Calendar events for a date range — MCP-06 |

Key properties:
- Bridge JWT minted inside the `mcp-gateway` container (authlib is already installed there)
- Registration via `POST /admin/register` — idempotent upsert (ON CONFLICT DO UPDATE)
- Post-registration verification: `GET /tools` to confirm count, `POST /tools/scraper/call` smoke test
- Manual test commands for all 3 tools documented at script end
- Auto-sources `.env` from project root (same bootstrap as verify-phase2.sh)

### infrastructure/scripts/verify-phase3.sh

7-test E2E smoke test:

| Test | What it checks |
|------|---------------|
| 1/7 | 6 Phase 3 containers Up (neo4j, mcp-gateway, mcp-scraper, mcp-drive-read, mcp-calendar, drive-sync) |
| 2/7 | Neo4j HTTP reachability (`neo4j:7474` from inside mcp-gateway container) |
| 3/7 | `GET /tools` returns ≥ 3 registered tools |
| 4/7 | `/admin/register` idempotency — second scraper POST returns 200/201 |
| 5/7 | Scraper E2E: gateway → mcp-scraper → `https://example.com` (expects >0 chars) |
| 6/7 | drive-read: if `GOOGLE_DRIVE_ACCESS_TOKEN` not set → verifies tool registered, SKIPs call |
| 7/7 | calendar: if `GOOGLE_CALENDAR_ACCESS_TOKEN` not set → verifies tool registered, SKIPs call |

### .env.example

Added under `=== MCP Tool Registration (plan 03-12) ===`:
```
MCP_GATEWAY_URL=http://mcp-gateway:8080
```
With instructions to run `register-mcp-tools.sh` post-deploy.

## Requirements Satisfied

| ID | Requirement | How |
|----|-------------|-----|
| MCP-01 | Gateway registers and routes tool calls | register-mcp-tools.sh wires all 3 sidecars |
| MCP-04 | At least 3 MCP tools registered | scraper + drive-read + calendar |
| MCP-05 | Tool call from agent-runtime works | scraper E2E test in both scripts |
| MCP-06 | Tool call from agent-runtime for calendar | calendar registered + verified |
| MCP-07 | Register new MCP server without restart | POST /admin/register is the only mechanism used |

## Deviations from Plan

### Deviation: verify-phase3.sh added (not in plan task list)

**Type:** Rule 2 (missing critical functionality)
**Found during:** Task 1 — prompt explicitly requested verify-phase3.sh as a critical deliverable alongside register-mcp-tools.sh.
**Fix:** Created `infrastructure/scripts/verify-phase3.sh` as a second task, following verify-phase2.sh style exactly (set -uo pipefail, .env auto-source, pass/fail/skip helpers, COMPOSE variable, exit code 0=success).
**Impact:** Additive only — no existing files affected.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| Task 1 | c4bb3bd | feat(03-12): register-mcp-tools.sh + .env.example MCP_GATEWAY_URL |
| Task 2 | e56673a | feat(03-12): verify-phase3.sh — 7-test E2E smoke test |

## Security Notes

| Threat | Mitigation |
|--------|-----------|
| T-03-12-01: bridge JWT exposure | JWT minted inside container, never written to disk, expires in 1h |
| T-03-12-02: arbitrary sidecar_url | Accepted for Phase 3 (admin-only script on VM); Phase 4 adds URL scheme validation |

## Known Stubs

None. register-mcp-tools.sh makes real HTTP calls to mcp-gateway. verify-phase3.sh makes real docker exec + HTTP calls to live containers.

## Self-Check

- [x] infrastructure/scripts/register-mcp-tools.sh exists
- [x] infrastructure/scripts/verify-phase3.sh exists
- [x] bash -n on both scripts: no syntax errors
- [x] register-mcp-tools.sh contains: scraper, drive-read, calendar, /admin/register, ON CONFLICT DO UPDATE, bridge JWT
- [x] verify-phase3.sh contains 7 labeled tests
- [x] commit c4bb3bd exists in git log
- [x] commit e56673a exists in git log

## Self-Check: PASSED
