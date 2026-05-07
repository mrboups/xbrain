---
phase: "07"
plan: "07-05"
subsystem: "granola-sync"
tags: ["granola", "skeleton", "memory-client", "claude-extraction", "bridge-jwt"]
dependency_graph:
  requires: ["07-01", "07-04"]
  provides: ["07-08"]
  affects: []
tech_stack:
  added:
    - "anthropic>=0.50.0 (Claude Messages API for extraction)"
    - "authlib>=1.3.0 (bridge JWT encoding — jose_jwt)"
    - "httpx>=0.28.0 (async HTTP client to memory-api)"
    - "pydantic-settings>=2.6 (Settings class)"
    - "structlog>=25.0.0 (structured logging)"
  patterns:
    - "Repo-root Docker build context (mirroring drive-sync pattern)"
    - "Bridge JWT service-to-service auth (sub=granola-sync)"
    - "Fail-soft async clients — return None / fallback dict, never raise"
    - "Claude markdown-fence stripping for robust JSON extraction"
key_files:
  created:
    - "apps/granola-sync/Dockerfile"
    - "apps/granola-sync/pyproject.toml"
    - "apps/granola-sync/app/__init__.py"
    - "apps/granola-sync/app/main.py"
    - "apps/granola-sync/app/config.py"
    - "apps/granola-sync/app/memory_client.py"
    - "apps/granola-sync/app/extractor.py"
  modified: []
decisions:
  - "granola_poller.py and docker-compose service block delegated to 07-08 (depends on this plan)"
  - "main.py intentionally imports from granola_poller before it exists — expected ImportError resolved by 07-08"
  - "ANTHROPIC_MODEL defaulted to claude-3-5-haiku-20241022 (cost-efficient for extraction at polling cadence)"
  - "summary_text capped at 20000 chars before Claude call (T-07-05-10 DoS mitigation)"
metrics:
  duration: "2 minutes"
  completed_date: "2026-05-07"
  tasks_completed: 3
  tasks_total: 3
  files_created: 7
  files_modified: 0
---

# Phase 07 Plan 05: granola-sync Service Skeleton Summary

## One-liner

granola-sync Python service skeleton: Dockerfile (repo-root context), pyproject, config, asyncio entry, bridge-JWT memory client POSTing to `/v1/integrations/granola/ingest`, and Claude-based fail-soft extractor for participants/action_items/decisions/project_scope.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Service skeleton (Dockerfile, pyproject, config, main, __init__) | 4fcf5dd | apps/granola-sync/Dockerfile, pyproject.toml, app/__init__.py, app/main.py, app/config.py |
| 2 | memory_client.py — bridge JWT + fail-soft POST | a87a634 | apps/granola-sync/app/memory_client.py |
| 3 | extractor.py — Claude structured extraction | c07f49a | apps/granola-sync/app/extractor.py |

## What Was Built

**Dockerfile** mirrors `apps/drive-sync/` exactly: `FROM python:3.12-slim`, copies `packages/memory-models/` first (repo-root build context), then installs `granola-sync` package. No Google SDK dependency.

**pyproject.toml** declares 8 dependencies: httpx, asyncpg, structlog, cryptography, authlib, pydantic-settings, anthropic, xbrain-memory. Notably absent: google-api-python-client (drive-only), fastapi/uvicorn (no webhook server — polling only).

**config.py** (Settings via pydantic-settings):
- `GRANOLA_API_BASE = "https://api.granola.ai"` — polled by granola_poller (07-08)
- `GRANOLA_POLL_INTERVAL_SECONDS = 300` — 5 min default
- `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"` — extraction
- `FERNET_KEY` — for decrypting stored Granola API keys from DB
- `BRIDGE_SHARED_SECRET` — JWT signing

**main.py** — asyncio entry: boots structlog, calls `await run_poll_loop(settings.DATABASE_URL)`. Imports `from app.granola_poller import run_poll_loop` which does not exist yet — this is intentional and will be resolved by 07-08.

**memory_client.py** — `_make_bridge_jwt()` generates HS256 JWT (sub=granola-sync, 1h exp). `post_ingest(team_scope, payload)` POSTs to `/v1/integrations/granola/ingest` with Authorization + X-Team-Scope headers. Returns None on any error (never raises).

**extractor.py** — `extract_from_summary(summary_text, fallback_attendees)` calls Claude Messages API (async). SYSTEM_PROMPT instructs strict JSON with 4 keys: participants, action_items, decisions, project_scope. Strips markdown fences if present. Returns structured fallback dict on empty input, JSONDecodeError, or Anthropic exception.

## What 07-08 Delivers (Out of Scope for This Plan)

- `apps/granola-sync/app/granola_poller.py` — the polling loop that uses memory_client + extractor
- `infrastructure/docker-compose.yml` — granola-sync service block
- Container build validation and healthcheck

## Deviations from Plan

None — plan executed exactly as written. All 7 files created, no poller, no docker-compose touch.

## Known Stubs

None — no stub data or placeholder values flow to any rendering layer. `main.py` import of `granola_poller` is an expected structural stub documented in the plan.

## Threat Flags

No new threat surface beyond what was in the plan's threat model. All T-07-05-xx mitigations applied:
- T-07-05-01: log.error calls log only `team_scope` and `error=str(exc)` — no API key, no Authorization header
- T-07-05-10: `summary_text[:20000]` cap applied in `extract_from_summary` before Claude call

## Self-Check: PASSED
