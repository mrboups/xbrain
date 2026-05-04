---
phase: 03-graphe-extraction-integrations
plan: 10
subsystem: memory-api
tags: [drive-oauth, admin-api, fernet, google-oauth, incremental-auth]

# Dependency graph
requires:
  - phase: 03-graphe-extraction-integrations
    plan: 02
    provides: team_drive_mappings table (schema for upsert + credential storage)
provides:
  - POST /v1/admin/drive-mapping (create/update team → folder mapping, returns authorization_url)
  - GET /v1/admin/drive-mapping/{team_scope} (read mapping, omits encrypted creds)
  - GET /v1/admin/drive-mapping/oauth-callback (Google OAuth code exchange + Fernet store)
affects:
  - 03-11-drive-sync (reads oauth_credentials_enc + folder_id + change_token)

# Tech tracking
tech-stack:
  added:
    - "cryptography>=42.0.0 (Fernet AES-128-CBC + HMAC-SHA256 for OAuth credential encryption)"
  patterns:
    - "Admin guard: bridge JWT (kind=bridge) implicitly admin; real users require sub in ADMIN_USER_SUBS CSV"
    - "OAuth callback intentionally unauthenticated — single-use code expires in 10min (T-03-10-01 accepted)"
    - "Fernet lazy import: cryptography only imported when OAUTH_CREDENTIALS_ENCRYPTION_KEY is set"
    - "Upsert pattern: ON CONFLICT(team_scope) DO UPDATE for idempotent mapping creation"

key-files:
  created:
    - apps/memory-api/app/routes/admin_drive.py
  modified:
    - apps/memory-api/app/config.py
    - apps/memory-api/app/main.py
    - apps/memory-api/pyproject.toml
    - infrastructure/docker-compose.yml
    - .env.example

key-decisions:
  - "ADMIN_USER_SUBS already existed as plain str; not duplicated — plan check satisfied"
  - "cryptography>=42.0.0 added to pyproject.toml (was absent — required for Fernet)"
  - "_build_authorization_url extracted as helper for testability"
  - "GET /admin/drive-mapping/{team_scope} returns oauth_configured boolean, not raw creds (T-03-10-02)"
  - "502 error on token exchange failure: surfaces Google error body (first 500 chars) to ease debugging"

# Metrics
duration: 2min
completed: 2026-05-04
---

# Phase 3 Plan 10: Drive Admin Mapping Endpoints Summary

**POST/GET /v1/admin/drive-mapping + OAuth callback with Fernet-encrypted credential storage — gives drive-sync the team→folder configuration it needs and completes the incremental OAuth consent flow.**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-05-04T04:23:57Z
- **Completed:** 2026-05-04T04:25:56Z
- **Tasks:** 1
- **Files modified:** 6 (1 created, 5 modified)

## Accomplishments

- `POST /v1/admin/drive-mapping`: upserts team_drive_mappings row, returns Google OAuth authorization_url with `prompt=consent` and `access_type=offline` for refresh token
- `GET /v1/admin/drive-mapping/{team_scope}`: returns mapping metadata, exposes `oauth_configured` boolean (never exposes raw encrypted creds)
- `GET /v1/admin/drive-mapping/oauth-callback`: exchanges Google auth code for tokens, Fernet-encrypts, stores in `team_drive_mappings.oauth_credentials_enc`
- Admin guard: bridge JWTs (kind=bridge) are implicitly admin; OIDC users require sub in `ADMIN_USER_SUBS`
- Settings extended: `GOOGLE_CLIENT_SECRET`, `OAUTH_CREDENTIALS_ENCRYPTION_KEY`, `MEMORY_API_EXTERNAL_URL`
- `cryptography>=42.0.0` added to pyproject.toml (was missing — required for Fernet)
- Router wired into main.py as last include (tag: admin-drive)
- docker-compose.yml and .env.example updated with new env vars

## Task Commits

1. **Task 1: admin_drive.py + config + wiring** — `5f794b2` (feat)

**Plan metadata:** (docs commit below)

## Files Created/Modified

| File | Change |
|------|--------|
| `apps/memory-api/app/routes/admin_drive.py` | Created — 3 endpoints + Fernet helper + admin guard |
| `apps/memory-api/app/config.py` | Added GOOGLE_CLIENT_SECRET, OAUTH_CREDENTIALS_ENCRYPTION_KEY, MEMORY_API_EXTERNAL_URL |
| `apps/memory-api/app/main.py` | Import + include admin_drive router |
| `apps/memory-api/pyproject.toml` | Added cryptography>=42.0.0 |
| `infrastructure/docker-compose.yml` | Added 3 env vars to memory-api service |
| `.env.example` | Added MEMORY_API_EXTERNAL_URL, OAUTH_CREDENTIALS_ENCRYPTION_KEY documentation block |

## Decisions Made

- `ADMIN_USER_SUBS` was already present as `str = ""` in Settings — not duplicated (plan check passed)
- `cryptography>=42.0.0` added as hard dependency (not optional) because Fernet is in the critical path for the OAuth callback; the lazy import pattern means it is only instantiated when `OAUTH_CREDENTIALS_ENCRYPTION_KEY` is set
- `_build_authorization_url()` extracted to a named helper for clarity and future testability
- 502 on token exchange failure (not 500): semantically correct — the error originates from Google's upstream service

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added 502 response with Google error body on token exchange failure**
- **Found during:** Task 1 (implementing drive_oauth_callback)
- **Issue:** Plan showed `resp.raise_for_status()` with no error context. A failed token exchange (wrong client_secret, unregistered redirect URI) would surface as an opaque 500. Debugging OAuth is notoriously hard without error details.
- **Fix:** Check `resp.status_code != 200`, log `resp.text[:500]`, raise `HTTPException(502, ...)` with actionable message
- **Files modified:** apps/memory-api/app/routes/admin_drive.py
- **Commit:** 5f794b2

---

**Total deviations:** 1 auto-fixed (1 missing critical)
**Impact on plan:** Strictly additive — error handling improvement only, no must_haves changed.

## Known Stubs

None — all endpoints are fully wired. `oauth_credentials_enc` starts as NULL (by design: mapping created before OAuth flow); drive-sync (03-11) reads and decrypts at poll time.

## Threat Surface Scan

| Flag | File | Description |
|------|------|-------------|
| threat_flag: unauthenticated_callback | apps/memory-api/app/routes/admin_drive.py | GET /v1/admin/drive-mapping/oauth-callback is intentionally unauthenticated — documented in plan T-03-10-01 (accepted: code is single-use, 10min TTL) |

All other threats covered:
- T-03-10-02 (oauth_credentials_enc disclosure): mitigated — GET endpoint returns only `oauth_configured` boolean, never raw encrypted bytes
- T-03-10-03 (folder_id tampering): accepted — admin privilege covers this

## User Setup Required

1. In Google Cloud Console → OAuth 2.0 client → Authorized redirect URIs, add:
   `https://x.dejavu.cat/v1/admin/drive-mapping/oauth-callback`
2. Set `OAUTH_CREDENTIALS_ENCRYPTION_KEY` in `.env`:
   ```
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
3. `GOOGLE_CLIENT_SECRET` is the same credential already in `.env` for Phase 1 Google OAuth — no new value needed.

## Next Phase Readiness

- 03-11 (drive-sync) can now read `folder_id` and decrypt `oauth_credentials_enc` using the same `OAUTH_CREDENTIALS_ENCRYPTION_KEY`
- Admin can configure mappings via API before drive-sync is deployed
- `change_token` column is writable by drive-sync (upsert incremental polling token)

---
*Phase: 03-graphe-extraction-integrations*
*Completed: 2026-05-04*
