---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: "03"
subsystem: api
tags: [fastapi, granola, fernet, encryption, user-auth, me-endpoint]

# Dependency graph
requires:
  - phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
    plan: "01"
    provides: granola_user_connections table with UNIQUE(user_id) constraint enabling UPSERT
provides:
  - "POST /v1/me/granola-key — user stores personal Granola API key (Fernet-encrypted)"
  - "GET /v1/me/granola-key — returns connection status (connected, team_scope, last_polled_at) — never the key"
  - "DELETE /v1/me/granola-key — soft delete via enabled=false, idempotent"
affects:
  - plan 08-04 (granola-sync per-user loop reads granola_user_connections)
  - plan 08-07 (LibreChat onboarding calls POST /me/granola-key)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "_require_user() guard pattern: rejects kind=bridge JWTs with 403 on user-owned resource endpoints"
    - "Import _require_granola_fernet from granola_integration.py (DRY — single Fernet source)"
    - "Soft delete pattern: enabled=false instead of hard DELETE — preserves audit trail, idempotent DELETE"
    - "GET returns connected=False (not 404) for missing/disabled connections — UI-friendly status pattern"

key-files:
  created: []
  modified:
    - apps/memory-api/app/routes/me.py

key-decisions:
  - "Soft delete (enabled=false) over hard DELETE: encrypted key stays stored, poller skips it (filter enabled=true in plan 08-04), POST re-enables atomically"
  - "DELETE is idempotent: returns 204 even if no row to disable — avoids 404 on double-disconnect from UI"
  - "GET returns connected=False (not 404) for missing connection — friendly for onboarding UI polling status"
  - "_require_user() local guard rejects bridge JWTs (kind=bridge) — a service principal has no personal Granola key"
  - "api_key_enc never SELECTed in GET response — T-08-03-02 mitigated by design (column not in query)"
  - "write_audit on POST and DELETE only — GET is read-only, no audit needed"

patterns-established:
  - "_require_user() guard: raise HTTPException(403) if principal.get('kind') != 'user' — use for any user-owned resource endpoint"

requirements-completed: []

# Metrics
duration: 8min
completed: 2026-05-09
---

# Phase 8 Plan 03: /me/granola-key Endpoints Summary

**Three user-facing Granola key management endpoints (POST/GET/DELETE /v1/me/granola-key) added to me.py with Fernet encryption, UPSERT, soft delete, and bridge JWT rejection**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-05-09T15:18:00Z
- **Completed:** 2026-05-09T15:26:35Z
- **Tasks:** 1
- **Files modified:** 1

## Accomplishments

- Added `POST /v1/me/granola-key`: encrypts api_key via Fernet, UPSERT ON CONFLICT(user_id), writes audit log
- Added `GET /v1/me/granola-key`: returns `{connected, team_scope, last_polled_at}` — api_key_enc never selected (T-08-03-02)
- Added `DELETE /v1/me/granola-key`: soft delete (enabled=false), idempotent 204, writes audit log
- All three endpoints guard against bridge JWTs via `_require_user()` — service principals rejected with 403 (T-08-03-01)
- Original `/me` endpoint preserved intact

## Task Commits

Each task was committed atomically:

1. **Task 1: Ajouter les 3 endpoints granola-key à me.py (POST/GET/DELETE)** - `15eb09f` (feat)

## Files Created/Modified

- `apps/memory-api/app/routes/me.py` - Extended with POST/GET/DELETE /me/granola-key endpoints; original /me endpoint unchanged

## Decisions Made

- **Soft delete over hard DELETE**: `enabled=false` preserves the encrypted key row. The poller in plan 08-04 will filter `enabled=true`. A subsequent POST atomically re-enables with a new key. Audit trail intact.
- **Idempotent DELETE (204 not 404)**: If the connection is already disabled or doesn't exist, returns 204 silently. UI can call DELETE freely without error handling for "already disconnected" case.
- **connected=False (not 404) for GET**: If no row or `enabled=false`, returns `{connected: false, team_scope: null, last_polled_at: null}`. The onboarding UI polls this to decide whether to show the "connect Granola" prompt — a 200 with `connected=false` is cleaner than a 404.
- **DRY Fernet**: `_require_granola_fernet()` imported from `granola_integration.py` — no duplication of the key-loading logic or fallback behavior.

## Deviations from Plan

None - plan executed exactly as written.

## Threat Surface Scan

All threats in the plan's threat register (T-08-03-01 through T-08-03-07) are mitigated as designed:

| Threat | Status |
|--------|--------|
| T-08-03-01 Spoofing bridge JWTs | Mitigated: `_require_user()` rejects kind!=user with 403 |
| T-08-03-02 api_key_enc in GET response | Mitigated: GET query does not SELECT api_key_enc (verified via grep: 0 matches) |
| T-08-03-03 api_key in logs | Mitigated: log.info only logs user_id and team_scope |
| T-08-03-04 Cross-user UPSERT | Mitigated: WHERE user_id = principal.user.id on all mutations |
| T-08-03-05 Repudiation | Mitigated: write_audit on POST (upserted) and DELETE (disabled) |
| T-08-03-06 Spam POST DoS | Accepted: UPSERT is atomic, no amplification possible |
| T-08-03-07 FERNET_KEY rotation | Mitigated: plan 08-04 poller handles InvalidToken gracefully |

No new threat surface introduced beyond what the plan's threat model covers.

## Known Stubs

None — all endpoints are fully wired to the database. The `granola_user_connections` table was created in plan 08-01 migration 0012.

## Issues Encountered

None.

## Next Phase Readiness

- `POST /v1/me/granola-key` is ready for plan 08-07 (LibreChat onboarding UI) to call during user setup
- `GET /v1/me/granola-key` is ready for onboarding status polling
- `granola_user_connections` rows with `enabled=true` are ready for plan 08-04 (granola-sync per-user loop) to query and decrypt

---
*Phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents*
*Completed: 2026-05-09*

## Self-Check: PASSED

- FOUND: apps/memory-api/app/routes/me.py
- FOUND: .planning/phases/08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents/08-03-SUMMARY.md
- FOUND: commit 15eb09f
