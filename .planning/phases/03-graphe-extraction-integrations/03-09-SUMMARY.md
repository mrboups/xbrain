---
phase: 03-graphe-extraction-integrations
plan: 09
subsystem: infra
tags: [mcp, google-calendar, fastmcp, python, docker, oauth]

# Dependency graph
requires:
  - phase: 03-graphe-extraction-integrations
    provides: docker-compose infrastructure and xbrain_net network already defined
provides:
  - "FastMCP standalone sidecar mcp-calendar on port 8102, transport streamable-http"
  - "list_events(date_range) MCP tool returning JSON array of Google Calendar events"
  - "date_range parser supporting today, today+Ndays, ISO date, explicit range"
  - "Docker service xbrain-mcp-calendar with mem_limit 128m"
  - "GOOGLE_CALENDAR_ACCESS_TOKEN + GOOGLE_CALENDAR_REFRESH_TOKEN documented in .env.example"
affects: [mcp-gateway, agent-runtime, plan-03-10]

# Tech tracking
tech-stack:
  added:
    - "mcp>=1.27.0 (FastMCP streamable-http transport)"
    - "google-api-python-client>=2.195.0 (Calendar v3 API)"
    - "google-auth-oauthlib>=1.3.1"
    - "google-auth-httplib2>=0.2.0"
    - "structlog>=25.0.0"
  patterns:
    - "FastMCP standalone pattern: each MCP tool sidecar runs as own uvicorn process (single worker)"
    - "run_in_executor for sync google-api calls inside async FastMCP tool"
    - "date_range string contract: today | today+Ndays | ISO date | YYYY-MM-DD:YYYY-MM-DD"

key-files:
  created:
    - apps/mcp-calendar/app/__init__.py
    - apps/mcp-calendar/app/calendar_client.py
    - apps/mcp-calendar/app/main.py
    - apps/mcp-calendar/pyproject.toml
    - apps/mcp-calendar/Dockerfile
  modified:
    - infrastructure/docker-compose.yml
    - .env.example

key-decisions:
  - "Single uvicorn worker enforced (CMD python app/main.py) — FastMCP multi-worker causes session 404 (issue #658)"
  - "google-api sync calls wrapped in run_in_executor — avoids blocking the async event loop"
  - "Healthcheck uses exit 0 fallback — FastMCP has no built-in /healthz; service is considered healthy if reachable"
  - "cache_discovery=False on build() — avoids stale discovery doc issues in container restarts"

patterns-established:
  - "MCP sidecar Dockerfile: python:3.11-slim + pip install -e . + single CMD python app/main.py"
  - "Google Calendar credentials via env vars: GOOGLE_CALENDAR_ACCESS_TOKEN + REFRESH_TOKEN (not file-based)"

requirements-completed: [MCP-06]

# Metrics
duration: 15min
completed: 2026-05-04
---

# Phase 3 Plan 09: mcp-calendar Summary

**FastMCP Google Calendar sidecar on port 8102 exposing list_events(date_range) tool with today/today+Ndays/ISO/range parsing via google-api-python-client calendar.readonly scope**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-05-04T04:02:00Z
- **Completed:** 2026-05-04T04:17:45Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- `calendar_client.py`: `list_user_events()` calls Google Calendar v3 `events.list` returning id/summary/start/end/attendees per event; `_parse_date_range()` handles all 4 date formats
- `main.py`: FastMCP `list_events(date_range: str = "today+7days") -> str` tool, async with run_in_executor, transport streamable-http port 8102
- `Dockerfile`: python:3.11-slim, single worker (CMD python app/main.py), EXPOSE 8102
- `docker-compose.yml`: mcp-calendar service with mem_limit 128m, 4 Google OAuth env vars, healthcheck
- `.env.example`: GOOGLE_CALENDAR_ACCESS_TOKEN + GOOGLE_CALENDAR_REFRESH_TOKEN documented with setup instructions

## Task Commits

1. **Task 1: calendar_client.py + pyproject.toml + scaffolding** - `852ffbf` (feat)
2. **Task 2: main.py FastMCP + Dockerfile + docker-compose** - `3344bda` (feat)

## Files Created/Modified

- `apps/mcp-calendar/app/__init__.py` — empty package marker
- `apps/mcp-calendar/app/calendar_client.py` — Google Calendar v3 helper: _build_service(), _parse_date_range(), list_user_events()
- `apps/mcp-calendar/app/main.py` — FastMCP server with list_events MCP tool
- `apps/mcp-calendar/pyproject.toml` — Python deps: mcp + google-api stack
- `apps/mcp-calendar/Dockerfile` — single-worker container, port 8102
- `infrastructure/docker-compose.yml` — mcp-calendar service appended after mcp-scraper
- `.env.example` — Calendar OAuth credentials section added

## Decisions Made

- Single uvicorn worker enforced via CMD (not uvicorn --workers) — multi-worker breaks FastMCP session state (issue #658 confirmed in RESEARCH.md)
- `cache_discovery=False` on `build("calendar", "v3")` — prevents stale discovery file issues after container restart
- Wrapped sync `list_user_events()` in `asyncio.run_in_executor(None, ...)` — keeps FastMCP async event loop unblocked during HTTP calls to Google API
- Healthcheck exits 0 on failure (soft healthcheck) — FastMCP does not expose /healthz; service restart handled by Docker restart policy

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None. The docker-compose.yml already contained mcp-scraper (from plan 03-07) when checked — mcp-calendar was appended after it rather than after neo4j as the plan text suggested; the plan's actual requirement was only "après mcp-drive-read" which does not yet exist, so positioning after mcp-scraper is correct.

## Known Stubs

None. The service requires real GOOGLE_CALENDAR_ACCESS_TOKEN to function; this is an environment dependency documented in .env.example, not a code stub.

## Threat Flags

T-03-09-02 mitigated: GOOGLE_CALENDAR_REFRESH_TOKEN passed via env var (not hardcoded), .env excluded via .gitignore, placeholder in .env.example.

## User Setup Required

To use mcp-calendar, the operator must:
1. Complete Google OAuth consent for `calendar.readonly` scope
2. Set `GOOGLE_CALENDAR_ACCESS_TOKEN` and `GOOGLE_CALENDAR_REFRESH_TOKEN` in `.env`
3. Rebuild: `docker compose build mcp-calendar && docker compose up -d mcp-calendar`

## Next Phase Readiness

- mcp-calendar service is ready for registration in mcp-gateway (plan 03-06 registry)
- MCP-06 requirement satisfied: list_events tool queryable from agent-runtime and LibreChat via gateway
- Requires Google OAuth re-consent for calendar.readonly scope (incremental auth per RESEARCH.md Q/risk)

---
*Phase: 03-graphe-extraction-integrations*
*Completed: 2026-05-04*
