---
phase: "07"
plan: "07-07"
subsystem: "frontend-dashboard + nginx-routing + ops-tooling"
tags: ["dashboard", "tasks", "nginx", "granola", "crm", "env-config", "verify-script"]
dependency_graph:
  requires: ["07-02", "07-03", "07-05", "07-08"]
  provides: ["tasks-dashboard", "nginx-routes-phase7", "verify-phase7", "env-phase7"]
  affects: ["projects-dashboard", "infrastructure/nginx", "infrastructure/scripts"]
tech_stack:
  added: ["TailwindCSS CDN (tasks.html)", "curl-based http testing (verify-phase7.sh)"]
  patterns: ["static HTML + vanilla fetch polling", "nginx prefix location matching", "set -uo pipefail test script"]
key_files:
  created:
    - "projects-dashboard/public/tasks.html"
    - "infrastructure/scripts/verify-phase7.sh"
  modified:
    - "projects-dashboard/public/index.html"
    - "infrastructure/nginx/conf.d/10-xbrain.conf"
    - ".env.example"
decisions:
  - "ANTHROPIC_API_KEY not duplicated in .env.example — comment reference added pointing to existing var at line 20"
  - "Nginx blocks placed in x.dejavu.cat server block (port 80) alongside existing /memapi/ and /v1/admin/ routes"
  - "location /v1/tasks (no trailing slash) chosen for tasks to capture /v1/tasks and /v1/tasks/{id} via nginx prefix match"
  - "tasks.html uses escapeHtml() on all API-returned string fields to prevent XSS (T-07-07-01)"
  - "verify-phase7.sh uses set -uo pipefail (not -e) so all 8 tests run even when early ones fail"
metrics:
  duration: "167s (~3 minutes)"
  completed: "2026-05-07T03:21:18Z"
  tasks_completed: 4
  files_modified: 5
---

# Phase 07 Plan 07: Deployment Completion — Tasks Dashboard, Nginx Routes, Verify Script, Env Config Summary

**One-liner:** Static tasks dashboard with 30s fetch polling + 4 nginx proxy routes + 8-test verify script + Phase 7 env documentation.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Tasks dashboard + nav link | 3118750 | projects-dashboard/public/tasks.html (create), index.html (add nav) |
| 2 | Nginx routes for CRM/Tasks/Granola | 907b046 | infrastructure/nginx/conf.d/10-xbrain.conf |
| 3 | verify-phase7.sh (8 tests) | 5cbb4dc | infrastructure/scripts/verify-phase7.sh (create) |
| 4 | .env.example Phase 7 section | 8df6823 | .env.example |

## Deliverables

### Task 1 — tasks.html

Static HTML page at `projects-dashboard/public/tasks.html`:
- TailwindCSS CDN, no build step
- `fetch(API_BASE + '/v1/tasks?' + params)` with `Authorization: Bearer` + `X-Team-Scope` headers
- `POLL_INTERVAL_MS = 30000` — `setInterval(loadTasks, POLL_INTERVAL_MS)` active once credentials entered
- Filters: status (todo/in_progress/done/cancelled), project (auto-populated from API), team scope input
- `escapeHtml()` applied to all API-returned string fields before DOM insertion (title, description, project_scope, due_date, source, priority, assigned_to)
- `PATCH /v1/tasks/{id}` for "Mark done" and "Start" actions
- `localStorage` persistence for token + team (convenience — acknowledged in threat model T-07-07-02)
- Nav link `<a href="/tasks.html">Tasks</a>` added to `projects-dashboard/public/index.html` header

### Task 2 — Nginx location blocks

Four new `location` blocks added to `x.dejavu.cat` server block (port 80), after existing `/v1/admin/drive-mapping/oauth-callback`:

| Block | Path | X-Team-Scope |
|-------|------|--------------|
| CRM endpoints | `/v1/crm/` | Yes |
| Tasks endpoints | `/v1/tasks` (no trailing slash) | Yes |
| Granola admin | `/v1/admin/granola-integration` | No |
| Granola ingest | `/v1/integrations/granola/` | No |

All 4 blocks: `proxy_pass $memory_api_upstream` (http://memory-api:8000), `proxy_set_header Authorization $http_authorization`, `proxy_read_timeout 60s`. Existing blocks (`/memapi/`, `/v1/admin/drive-mapping/oauth-callback`, `/`, Open WebUI, Langfuse) preserved.

### Task 3 — verify-phase7.sh

`infrastructure/scripts/verify-phase7.sh` — 8 independent tests:

1. `alembic_version = 0010` via `docker exec psql`
2. `teams.plan` column + `teams_plan_check` constraint
3. `contacts` table + `contacts_type_check` constraint
4. `tasks` table + FK to `contacts`
5. `granola_integrations` table
6. `xbrain-granola-sync` container `running` + `healthy`
7. `GET /v1/tasks` → 401 or 403 (auth gate, not 404)
8. `GET /v1/crm/contacts` → 401 or 403 (auth gate, not 404)

Uses `set -uo pipefail` (not `-e`) so all tests run even if early ones fail. `PASS/FAIL` helper functions, final `PASS: N / 8` summary, exits 0 on full pass / 1 otherwise. Configurable via `DB_CONTAINER`, `GRANOLA_CONTAINER`, `MEMAPI_HOST` env vars.

### Task 4 — .env.example Phase 7 section

New section appended at end of `.env.example`:
- `GRANOLA_API_BASE=https://api.granola.ai`
- `GRANOLA_POLL_INTERVAL_SECONDS=300`
- `ANTHROPIC_MODEL=claude-3-5-haiku-20241022` (comment notes existing `ANTHROPIC_API_KEY` above — not duplicated)
- `FERNET_KEY=` (with comment: falls back to `OAUTH_CREDENTIALS_ENCRYPTION_KEY` if empty)
- `SMTP_HOST`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM=noreply@dejavu.cat`, `SMTP_TLS=true`

## Deviations from Plan

None — plan executed exactly as written.

The only minor adaptation: in Task 4, `ANTHROPIC_API_KEY` already exists at line 20 of `.env.example` with `__FILL__` value. Per plan instruction, it was not duplicated. Instead, a comment reference `# Note: ANTHROPIC_API_KEY is already set above (shared with LibreChat + memory-api)` was added in the Phase 7 section. This satisfies the intent (documentation of the dependency) without introducing a duplicate.

## Threat Surface Scan

No new network endpoints, auth paths, or schema changes introduced in this plan. All surfaces introduced are documented in the plan's `<threat_model>` (T-07-07-01 through T-07-07-10). Key mitigations applied:
- T-07-07-01 (XSS): `escapeHtml()` applied to all user-controlled fields before innerHTML insertion
- T-07-07-04 (nginx logs): no `$http_authorization` in log_format (default nginx access log format does not include request headers)

## Known Stubs

None. `tasks.html` fetches live data from `/v1/tasks` API — no hardcoded/mock data. The page shows "Enter team + token then click Refresh." as initial state, which is correct behavior before credentials are provided.

## Self-Check: PASSED

- All 5 files created/modified confirmed present on disk
- All 4 task commits verified in git log: 3118750, 907b046, 5cbb4dc, 8df6823
- Content checks: 30s polling, escapeHtml, nginx X-Team-Scope, TEST_TOTAL=8, GRANOLA vars — all PASS
