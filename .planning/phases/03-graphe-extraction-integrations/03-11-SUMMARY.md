---
phase: 03-graphe-extraction-integrations
plan: 11
subsystem: drive-sync
tags: [google-drive, polling, ingestion, soft-archive, fernet, docker, integration]

# Dependency graph
requires:
  - phase: 03-graphe-extraction-integrations
    plan: 02
    provides: team_drive_mappings table (change_token, oauth_credentials_enc)
  - phase: 03-graphe-extraction-integrations
    plan: 10
    provides: admin endpoints + Fernet-encrypted oauth_credentials_enc
  - phase: 02-memoire-intelligente-agents
    plan: 07
    provides: POST /v1/agents/ingest endpoint (ingestion-agent LangGraph graph)
provides:
  - apps/drive-sync/ service: incremental Google Drive poller every 5min
  - poll_team(): changes.list + newStartPageToken persist + export + archive + delegate
  - send_to_ingestion_agent(): POST /v1/agents/ingest with bridge JWT
  - soft_archive_drive_file(): search + patch/delete per truth_level
  - docker-compose drive-sync service with context: .. (repo root)
affects:
  - memory-api: receives ingested facts via agent-runtime with source="drive:{file_id}"
  - team_drive_mappings: change_token updated per tick

# Tech tracking
tech-stack:
  added:
    - "google-api-python-client>=2.195.0 (Drive changes.list + files.export + MediaIoBaseDownload)"
    - "google-auth-oauthlib>=1.3.1 (OAuth token refresh)"
    - "google-auth-httplib2>=0.2.0 (transport adapter)"
    - "asyncpg>=0.30.0 (async Postgres for token persistence)"
    - "pypdf>=5.0.0 (PDF text extraction in drive-sync)"
    - "pydantic-settings>=2.6 (Settings class)"
  patterns:
    - "Token-persist-before-process: newStartPageToken written to DB before iterating changes (RISK-04 idempotency)"
    - "Backoff decorator: _with_backoff() wraps Drive API calls, max 64s wait for 429/500/503"
    - "Sentinel file healthcheck: /tmp/drive-sync-alive touched after each successful poll tick"
    - "Soft-archive pattern: WORKING+ facts get validation_status=archived; EPHEMERAL facts hard-deleted"
    - "Bridge JWT: drive-sync authenticates to agent-runtime and memory-api with short-lived bridge JWT (sub=drive-sync, scope=bridge)"
    - "Internal Docker DNS: MEMORY_API_URL=http://memory-api:8000, AGENT_RUNTIME_URL=http://agent-runtime:9100 -- never Cloudflare public subdomain"

key-files:
  created:
    - apps/drive-sync/app/__init__.py
    - apps/drive-sync/app/config.py
    - apps/drive-sync/app/ingestion_client.py
    - apps/drive-sync/app/drive_poller.py
    - apps/drive-sync/app/main.py
    - apps/drive-sync/pyproject.toml
    - apps/drive-sync/Dockerfile
  modified:
    - infrastructure/docker-compose.yml
    - .env.example

key-decisions:
  - "MEMORY_API_URL hardcoded to http://memory-api:8000 in docker-compose environment (not via ${MEMORY_API_URL}) to prevent Cloudflare 502 regression (Phase 2 lesson)"
  - "Sentinel file healthcheck chosen over HTTP healthcheck: drive-sync is a daemon asyncio process with no HTTP server; /tmp/drive-sync-alive touched per tick with 600s tolerance"
  - "Token persist BEFORE iterating changes: guarantees idempotency on crash-restart (RISK-04). On restart, changes are re-sent to ingestion-agent which upserts on UNIQUE(source, team_scope)"
  - "AGENT_RUNTIME_URL also hardcoded to http://agent-runtime:9100 in docker-compose: same Cloudflare bypass rationale"
  - "pydantic-settings added to pyproject.toml: absent from plan spec but required for BaseSettings import (Rule 3 auto-fix)"

# Metrics
duration: 15min
completed: 2026-05-04
---

# Phase 3 Plan 11: Google Drive Sync Service Summary

**New `apps/drive-sync/` Python sidecar that polls Google Drive changes.list every 5 minutes, persists the newStartPageToken before processing, delegates extraction to agent-runtime ingestion-agent, and soft-archives deleted files — completing the INT-01/INT-02 sync loop.**

## Performance

- **Duration:** ~15 min
- **Completed:** 2026-05-04
- **Tasks:** 2
- **Files created:** 7 | **Files modified:** 2

## Accomplishments

- `apps/drive-sync/` service fully scaffolded and containerized
- `drive_poller.py` (252 lines): `poll_team()` with incremental polling, exponential backoff, 410 re-baseline, Google Docs/Sheets/Slides/PDF/Markdown export, soft-archive on deletion
- `ingestion_client.py` (145 lines): `send_to_ingestion_agent()` (bridge JWT, POST /v1/agents/ingest) + `soft_archive_drive_file()` (search + WORKING+/EPHEMERAL differential handling)
- `config.py`: all settings with pydantic-settings, internal Docker URLs as defaults
- `Dockerfile`: repo-root build context (`context: ..`) to access `packages/memory-models`
- `docker-compose.yml`: drive-sync service with `context: ..`, `MEMORY_API_URL=http://memory-api:8000` (internal), `mem_limit=192m`, `depends_on: [postgres, memory-api, agent-runtime]`
- Sentinel file healthcheck: `/tmp/drive-sync-alive` touched after each successful tick

## Task Commits

1. **Task 1: scaffolding** — `208dc46` (feat)
2. **Task 2: drive_poller + main + docker-compose** — `f1d0a56` (feat)

## Files Created/Modified

| File | Change |
|------|--------|
| `apps/drive-sync/app/__init__.py` | Created — empty module marker |
| `apps/drive-sync/app/config.py` | Created — Settings with internal Docker URLs |
| `apps/drive-sync/app/ingestion_client.py` | Created — send_to_ingestion_agent + soft_archive_drive_file |
| `apps/drive-sync/app/drive_poller.py` | Created — poll_team() + run_poll_loop() + backoff + export |
| `apps/drive-sync/app/main.py` | Created — asyncio.run entry point |
| `apps/drive-sync/pyproject.toml` | Created — Drive + asyncpg + pypdf + authlib deps |
| `apps/drive-sync/Dockerfile` | Created — repo-root context for packages/memory-models |
| `infrastructure/docker-compose.yml` | Modified — added drive-sync service block |
| `.env.example` | Modified — added DRIVE_SYNC_POLL_INTERVAL=300 |

## Polling Logic Location

`apps/drive-sync/app/drive_poller.py`:
- `run_poll_loop()` (line ~195): main async loop — acquires DB pool, fetches all `team_drive_mappings` rows, calls `poll_team()` per row, sleeps `POLL_INTERVAL_SECONDS`
- `poll_team()` (line ~120): per-team logic — decrypt Fernet creds, call `changes.list`, persist `newStartPageToken` BEFORE iterating, dispatch to `send_to_ingestion_agent` or `soft_archive_drive_file`

## Decisions Made

- `MEMORY_API_URL=http://memory-api:8000` hardcoded in docker-compose environment (not delegated to `${MEMORY_API_URL}`) — prevents accidental routing through Cloudflare public subdomain (Phase 2 502 regression)
- Sentinel healthcheck over HTTP: daemon process with no HTTP port; `/tmp/drive-sync-alive` is the simplest correct approach
- `pydantic-settings` added to pyproject.toml: plan spec omitted it but `BaseSettings` requires it (auto-fix Rule 3)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added pydantic-settings to pyproject.toml**
- **Found during:** Task 1
- **Issue:** Plan spec listed dependencies but omitted `pydantic-settings` which is required for `BaseSettings` in config.py. Without it, `pip install -e apps/drive-sync/` would install but `import app.config` would fail at runtime with `ModuleNotFoundError`.
- **Fix:** Added `pydantic-settings>=2.6` to `pyproject.toml` dependencies
- **Files modified:** apps/drive-sync/pyproject.toml
- **Commit:** 208dc46

---

**Total deviations:** 1 auto-fixed (1 blocking missing dep)
**Impact on plan:** Strictly additive — pyproject.toml only. All must_haves satisfied.

## Known Stubs

None — all paths are fully wired:
- Fernet decryption reads from `OAUTH_CREDENTIALS_ENCRYPTION_KEY` env var (same key used by 03-10)
- `send_to_ingestion_agent()` POSTs to real `agent-runtime:9100` endpoint
- `soft_archive_drive_file()` calls real `memory-api:8000` endpoints
- `team_drive_mappings` table exists (migration 0004 from plan 03-02)

## Threat Surface Scan

| Flag | File | Description |
|------|------|-------------|
| threat_flag: credential_in_memory | apps/drive-sync/app/drive_poller.py | creds_dict decrypted in-memory during poll tick — T-03-11-01 accepted: credentials never logged, only in scope for milliseconds |

All other threats from plan threat model addressed:
- T-03-11-03 (429 rate limit): mitigated by `_with_backoff()` with max 64s cap
- T-03-11-04 (file content in logs): mitigated — `poll.file_changed` logs only file_id+mime, never content
- T-03-11-05 (Cloudflare 502): mitigated — MEMORY_API_URL and AGENT_RUNTIME_URL hardcoded to internal Docker DNS in docker-compose

## User Setup Required

1. `OAUTH_CREDENTIALS_ENCRYPTION_KEY` must be set in `.env` (same Fernet key from plan 03-10)
2. `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` must be set (same Google OAuth client from Phase 1)
3. Create a `team_drive_mappings` row via `POST /v1/admin/drive-mapping` (plan 03-10 admin endpoint) and complete the OAuth consent flow
4. `drive-sync` container will start polling on next `docker compose up`

## Next Phase Readiness

- drive-sync is the last service in Phase 3 wave 4 — all 11 plans complete
- Facts arrive in memory-api with `source="drive:{file_id}"` and `truth_level=WORKING`
- `change_token` is updated per tick; idempotent re-processing on crash via `UNIQUE(source, team_scope)` index (migration 0004)
- INT-01 and INT-02 requirements are now fully implemented end-to-end

---
*Phase: 03-graphe-extraction-integrations*
*Completed: 2026-05-04*
