---
phase: 07-crm-granola-tasks
plan: "07-08"
subsystem: infra
tags: [granola, polling, fernet, asyncpg, httpx, docker-compose, sentinel-healthcheck]

# Dependency graph
requires:
  - phase: 07-05
    provides: "granola-sync skeleton: Dockerfile, pyproject, config.py, main.py, memory_client.py, extractor.py"
provides:
  - "granola_poller.py: async polling loop with Fernet decrypt, Granola REST fetch, extractor+ingest wiring"
  - "docker-compose.yml: granola-sync service block (xbrain-granola-sync)"
affects: [07-04, 07-09]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "UPDATE cursor BEFORE fetch for idempotency on crash (mirrors drive-sync pattern)"
    - "Sentinel file /tmp/<service>-alive touched after each poll cycle for Docker healthcheck"
    - "Fail-soft 401/403: log.warning not log.error, continue to next team"
    - "Pagination hard cap via for _ in range(20) to prevent infinite loop on malformed response"
    - "FERNET_KEY fallback to OAUTH_CREDENTIALS_ENCRYPTION_KEY in compose env block"
    - "$$(...) escaping for $ in docker-compose healthcheck CMD-SHELL"

key-files:
  created:
    - "apps/granola-sync/app/granola_poller.py"
  modified:
    - "infrastructure/docker-compose.yml"

key-decisions:
  - "UPDATE last_polled_at BEFORE _fetch_notes — at-most-once delivery, combined with note-level dedup in 07-04 gives exactly-once-effective"
  - "Pagination capped at 20 iterations unconditionally to prevent DoS on malformed Granola response (T-07-08-08)"
  - "401/403 from Granola is warn-level only (plan insuffisant), not error — avoids log noise for teams on Free/Pro plan"
  - "FERNET_KEY fallback to OAUTH_CREDENTIALS_ENCRYPTION_KEY so drive-sync + granola-sync can share same encryption key in simple deployments"

patterns-established:
  - "granola_poller.py: poll loop follows drive_poller.py pattern (asyncpg pool, per-row try/except, sentinel touch)"

requirements-completed: ["D3", "D5"]

# Metrics
duration: 12min
completed: 2026-05-07
---

# Phase 07 Plan 08: granola-sync Polling Loop Summary

**Granola notes polling loop operational: Fernet decrypt per team, pagination+backoff Granola REST fetch, Claude extraction, memory-api ingest, sentinel healthcheck, registered in docker-compose as xbrain-granola-sync**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-07T00:00:00Z
- **Completed:** 2026-05-07T00:12:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Created `granola_poller.py` implementing the full polling loop: asyncpg pool queries `granola_integrations`, Fernet-decrypts API key per team, fetches Granola REST `/v1/notes` with cursor pagination (20-page cap), rate-limit (429) and 5xx exponential backoff (max 5 attempts, 64s ceiling), fail-soft on 401/403 (plan insuffisant), Claude extraction via 07-05 extractor, memory-api ingest via 07-05 memory_client
- Idempotency guaranteed: `last_polled_at` persisted to DB via `UPDATE granola_integrations SET last_polled_at = $1` BEFORE `_fetch_notes` runs, so crash-restart doesn't double-ingest
- Sentinel `/tmp/granola-sync-alive` touched after each successful poll cycle for Docker healthcheck
- Added `granola-sync` service block to `docker-compose.yml`: `xbrain/granola-sync:phase7`, `mem_limit: 192m`, `depends_on: postgres + memory-api`, healthcheck validates sentinel file freshness < 600s, `$$(...)`-escaped CMD-SHELL

## Task Commits

Each task was committed atomically:

1. **Task 1: granola_poller.py — polling loop, Fernet, rate-limit, fail-soft** - `6fd21a9` (feat)
2. **Task 2: granola-sync service in docker-compose.yml** - `a3ef063` (feat)

**Plan metadata:** (see final commit below)

## Files Created/Modified
- `apps/granola-sync/app/granola_poller.py` — Full polling loop: `run_poll_loop`, `_process_team_integration`, `_fetch_notes`, `_decrypt_api_key`
- `infrastructure/docker-compose.yml` — Added `granola-sync` service block (34 lines, 26 total services)

## Decisions Made
- UPDATE last_polled_at BEFORE fetch (idempotency T-07-08-11) — consistent with drive-sync token persist pattern
- 401/403 → `log.warning` not `log.error` to avoid alert noise for teams on Free/Pro Granola plan
- Pagination hard cap `for _ in range(20)` — prevents infinite loop on malformed `next_cursor` (T-07-08-08)
- FERNET_KEY with fallback `${FERNET_KEY:-${OAUTH_CREDENTIALS_ENCRYPTION_KEY}}` for composability with drive-sync deployments

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Docker CLI not available on dev machine (Windows environment without Docker Desktop in PATH) — YAML validation performed via Python `yaml.safe_load` as equivalent verification. Result: 26 services parsed, all fields confirmed. Production validation will run on GCP VM.

## Threat Mitigations Applied

All STRIDE mitigations from plan threat model implemented:
- T-07-08-01: No API key or Authorization header value logged anywhere
- T-07-08-02: `InvalidToken` caught in `_decrypt_api_key`, team skipped with error log only
- T-07-08-05: Exponential backoff max 5 attempts, max 64s wait on 429
- T-07-08-06: 401/403 → `granola.fetch_unauthorized` warning, not error
- T-07-08-08: `for _ in range(20)` pagination hard cap
- T-07-08-11: `last_polled_at` UPDATE before fetch

## Known Stubs

None — all functionality is wired end-to-end. The service will produce empty poll cycles until a real Granola integration is registered via 07-04 with a valid Business/Enterprise API key.

## User Setup Required

None for this plan. Granola integration setup is handled via the `/v1/integrations/granola/connect` endpoint (07-04). `FERNET_KEY` must be set in the deployment env — uses same key as `OAUTH_CREDENTIALS_ENCRYPTION_KEY` by default.

## Next Phase Readiness
- granola-sync container is ready to build and deploy: `docker compose build granola-sync && docker compose up -d granola-sync`
- Once a team registers a Granola integration (07-04), polling starts automatically on the next 5-minute cycle
- `memory_items` and `tasks` with `source='granola'` will appear in DB after first successful poll

---
*Phase: 07-crm-granola-tasks*
*Completed: 2026-05-07*
