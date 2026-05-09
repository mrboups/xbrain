---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: "04"
subsystem: api
tags: [granola, polling, per-user, agent, auto-trigger, asyncpg, httpx, fernet]

# Dependency graph
requires:
  - phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
    plan: "01"
    provides: granola_user_connections table (migration 0012) with user_id, api_key_enc, last_polled_at, team_scope, enabled
  - phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
    plan: "02"
    provides: POST /v1/agents/{id}/invoke endpoint — bridge JWT accepted, returns memory_item_id + recap
  - phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
    plan: "03"
    provides: granola_user_connections rows populated by users via POST /v1/me/granola-key

provides:
  - granola-sync polls granola_user_connections (WHERE enabled=true) at every tick alongside team integrations
  - _process_user_connection() — at-most-once delivery pattern (UPDATE last_polled_at BEFORE _fetch_notes)
  - _get_meeting_recap_agent() — queries agent_definitions once per tick, cached as dict
  - _maybe_invoke_recap() — fail-soft auto-trigger of meeting-recap agent after each successful ingest
  - post_agent_invoke() in memory_client.py — bridge JWT call to /v1/agents/{id}/invoke

affects:
  - 08-08-verify (rebuild granola-sync required: docker compose up -d --build granola-sync)
  - any future plan adding quota enforcement on agent invocations

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-user poll loop cohabiting with team poll loop in same service container"
    - "Agent cached per-tick not per-note (one SELECT per tick vs N SELECTs per note)"
    - "_maybe_invoke_recap() fail-soft pattern — exception never bubbles to ingest loop"
    - "post_agent_invoke no X-Team-Scope header — team_scope passed in body only (Pitfall 1)"

key-files:
  created: []
  modified:
    - apps/granola-sync/app/memory_client.py
    - apps/granola-sync/app/granola_poller.py

key-decisions:
  - "UPDATE last_polled_at BEFORE _fetch_notes in BOTH loops (at-most-once, Pitfall 5) — combined with note dedup = exactly-once-effective"
  - "_get_meeting_recap_agent queried once per poll tick, not per note — avoids N DB queries for M notes"
  - "No X-Team-Scope header on post_agent_invoke — Pitfall 1: bridge JWT carries no team_scope, body is the only source of truth"
  - "content[:50000] cap on invoke matches agents.py Anthropic call limit — conservative cut at 50k not 200k to reduce cost"
  - "401/403 Granola per-user → log.warning + return, never exception — T-08-04-02 (one bad user never blocks others)"

patterns-established:
  - "At-most-once + dedup = exactly-once-effective: same pattern Team loop (Phase 7) now applied to User loop"
  - "Fail-soft helper pattern: _maybe_invoke_recap wraps all exceptions, always returns None on error"
  - "Separate log namespaces: granola.user.* vs granola.* for dashboard filtering across two cohabiting loops"

requirements-completed: []

# Metrics
duration: 2min
completed: 2026-05-09
---

# Phase 08 Plan 04: Per-User Granola Poll Loop + Meeting-Recap Auto-Trigger Summary

**granola-sync extended with a per-user poll loop (granola_user_connections) and post_agent_invoke() bridge helper, auto-triggering the meeting-recap agent after each successful ingest via exactly-once-effective delivery (UPDATE-before-fetch + note dedup)**

## Performance

- **Duration:** 2 min
- **Started:** 2026-05-09T15:28:54Z
- **Completed:** 2026-05-09T15:31:15Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments

- Added `post_agent_invoke()` to `memory_client.py` — bridge JWT call to `/v1/agents/{id}/invoke`, 120s timeout, no X-Team-Scope header (Pitfall 1), fail-soft
- Extended `granola_poller.py` with `_process_user_connection()` — exact mirror of `_process_team_integration` for `granola_user_connections`, UPDATE-before-fetch pattern preserved
- `run_poll_loop()` now iterates two tables per tick: team integrations (Phase 7) + per-user connections (Phase 8 D2)
- `_get_meeting_recap_agent()` queries `agent_definitions` once per tick — result passed to both processing functions
- `_maybe_invoke_recap()` auto-triggers meeting-recap after every successful ingest in both loops (D5)

## Task Commits

Each task was committed atomically:

1. **Task 1: post_agent_invoke helper in memory_client.py** - `4057211` (feat)
2. **Task 2: per-user poll loop + meeting-recap auto-trigger in granola_poller.py** - `bc513c7` (feat)

## Files Created/Modified

- `apps/granola-sync/app/memory_client.py` — Added `post_agent_invoke()` — bridge JWT helper for POST /v1/agents/{id}/invoke, fail-soft, 120s timeout
- `apps/granola-sync/app/granola_poller.py` — Full rewrite: added `_get_meeting_recap_agent`, `_maybe_invoke_recap`, `_process_user_connection`, extended `run_poll_loop` with second loop, updated `_process_team_integration` signature to accept `recap_agent`

## Decisions Made

**At-most-once + dedup = exactly-once-effective:** UPDATE `last_polled_at` BEFORE `_fetch_notes` in both loops (Pitfall 5 RESEARCH.md). On crash, the cursor advances but the note dedup (Phase 7 note_id UNIQUE constraint in ingest route) ensures no duplicate memory items. The combination is exactly-once-effective.

**_get_meeting_recap_agent cached per tick:** The meeting-recap agent row is queried once per poll cycle (not once per note). For a tick with 20 notes across 5 users, this means 1 SELECT on `agent_definitions` instead of 100. The result dict is passed as a parameter to both processing functions.

**No X-Team-Scope header on post_agent_invoke (Pitfall 1):** The bridge JWT carries no team_scope — the `invoke_agent` handler in `agents.py` reads `team_scope` exclusively from the request body. Adding an X-Team-Scope header would be misleading since the handler ignores it. The body's `team_scope` field is the single source of truth for authorization scope.

**content[:50000] cap:** The agents.py Anthropic call already trims content to `body.content[:50000]` before sending to Claude. The cap at the caller side is redundant but provides defense-in-depth and avoids sending 200k characters over the internal network unnecessarily.

## Deviations from Plan

None — plan executed exactly as written. The `grep -c "/v1/agents/"` and `grep -c "/invoke"` acceptance criteria mentioned "retourne 1" but the module/function docstrings also match these patterns. The implementation is correct: 1 actual URL construction line and 1 HTTP call. This is a benign pattern-count discrepancy in the acceptance check, not an implementation issue.

## Issues Encountered

None.

## User Setup Required

**Rebuild required on the VM after deploying these changes:**

```bash
docker compose up -d --build granola-sync
```

The granola-sync container must be rebuilt to load the updated `memory_client.py` and `granola_poller.py`. Without rebuild, the per-user poll loop and meeting-recap auto-trigger will not be active.

No new environment variables required — existing `FERNET_KEY`, `MEMORY_API_URL`, `BRIDGE_SHARED_SECRET`, `JWT_ALGORITHM` are reused.

## Next Phase Readiness

- granola-sync is ready to poll `granola_user_connections` — the table was created in plan 08-01 and populated via 08-03
- meeting-recap auto-trigger is wired end-to-end: granola-sync (this plan) → memory-api POST /v1/agents/{id}/invoke (08-02) → Anthropic Claude → memory_items
- The agent `meeting-recap` must be seeded in `agent_definitions` with `auto_trigger=true` and `enabled=true` for the trigger to fire (seeded via plan 08-02 migration)
- Rebuild `granola-sync` container on the VM before running 08-08 verify

## Known Stubs

None — both loops are fully wired with real DB queries and HTTP calls. No hardcoded empty values or placeholder data flows to rendering.

## Threat Flags

No new network endpoints or trust boundaries introduced in this plan. All calls are service-to-service (granola-sync → memory-api internal network) using the existing bridge JWT pattern. See threat model in 08-04-PLAN.md for STRIDE analysis of this plan's surface.

## Self-Check

- FOUND: apps/granola-sync/app/memory_client.py
- FOUND: apps/granola-sync/app/granola_poller.py
- FOUND: 08-04-SUMMARY.md
- FOUND commit 4057211 (Task 1)
- FOUND commit bc513c7 (Task 2)

## Self-Check: PASSED

---
*Phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents*
*Completed: 2026-05-09*
