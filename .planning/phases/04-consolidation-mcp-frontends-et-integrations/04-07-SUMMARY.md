---
phase: 4
plan: "04-07"
subsystem: mcp-deck
tags: [mcp, pptx, minio, fastmcp, python-pptx]
dependency_graph:
  requires: [langfuse-minio, memory-api]
  provides: [mcp-deck sidecar port 8103]
  affects: [docker-compose.yml, mcp-gateway registrations]
tech_stack:
  added: [python-pptx==1.0.2, boto3==1.43.3, joserfc==1.6.4]
  patterns: [FastMCP standalone sidecar, MinIO bucket auto-create, bridge JWT via joserfc]
key_files:
  created:
    - apps/mcp-deck/app/main.py
    - apps/mcp-deck/app/__init__.py
    - apps/mcp-deck/pyproject.toml
    - apps/mcp-deck/Dockerfile
  modified:
    - infrastructure/docker-compose.yml
decisions:
  - "MinIO vars mapped to MINIO_ROOT_USER/MINIO_ROOT_PASSWORD (Langfuse naming, not ACCESS_KEY/SECRET_KEY)"
  - "deck_id reused as memory_item_id for stable upsert idempotency"
  - "joserfc used directly instead of authlib.jose (deprecated since authlib 1.3)"
metrics:
  duration: "~35 minutes"
  completed: "2026-05-05"
  tasks_completed: 3
  files_changed: 5
---

# Phase 4 Plan 07: mcp-deck sidecar — PowerPoint generator via python-pptx + MinIO

## One-liner

FastMCP sidecar (port 8103) generating PPTX via python-pptx, uploading to MinIO bucket `xbrain-decks`, and indexing in memory-api with full tagging contract (truth_level=WORKING, bridge JWT auth).

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| T-04-07-01 | Create `apps/mcp-deck/app/main.py` with deck_create/deck_update tools | 9eb65de |
| T-04-07-02 | Create `pyproject.toml` and `Dockerfile` | 9eb65de |
| T-04-07-03 | Add mcp-deck service to docker-compose.yml | 9eb65de |
| fix | Fix memory-api 422 (missing id/created_at/updated_at in payload) | e41b2b8 |

## Validation Results

- `docker compose build mcp-deck`: success (python:3.12-slim + libxml2/libxslt + all deps)
- `docker compose up -d mcp-deck`: container healthy, port 8103 bound
- `docker exec xbrain-mcp-deck python3 -c "from pptx import Presentation; print('pptx ok')"`: `pptx ok`
- `deck_create` call: returns `{url, deck_id, memory_item_id, version=1}` with valid presigned MinIO URL
- PPTX download + validation: 2 slides (title slide + content slide), structure correct
- memory-api indexing: HTTP 201 Created, `memory_item_id` populated

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed memory-api 422 on upsert**
- **Found during:** Post-deploy test
- **Issue:** `_index_in_memory_api` payload missing required fields `id`, `created_at`, `updated_at` per MemoryItem schema
- **Fix:** Added `id=deck_id`, `created_at=now_iso`, `updated_at=now_iso` to payload
- **Files modified:** `apps/mcp-deck/app/main.py`
- **Commit:** e41b2b8

**2. [Rule 1 - Warning] Replaced deprecated authlib.jose with joserfc**
- **Found during:** First test run (DeprecationWarning: authlib.jose module deprecated, use joserfc)
- **Issue:** authlib 1.7.1 ships joserfc internally and deprecates its own jose module
- **Fix:** Switched `_mint_bridge_jwt` to use `joserfc.jwt` + `joserfc.jwk.OctKey` directly
- **Files modified:** `apps/mcp-deck/app/main.py`
- **Commit:** e41b2b8

### Notes

- MinIO env vars use `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` (Langfuse naming convention in docker-compose). The PLAN.md template used `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` — harmonized to match the actual stack.
- MinIO endpoint is `langfuse-minio:9000` (not `minio:9000`) — service name in docker-compose.

## Known Stubs

None. Both tools (deck_create, deck_update) are fully wired end-to-end.

## Threat Flags

No new threat surface beyond what is documented in the plan threat model.

## Self-Check: PASSED

- apps/mcp-deck/app/main.py: FOUND
- apps/mcp-deck/Dockerfile: FOUND
- apps/mcp-deck/pyproject.toml: FOUND
- Commit 9eb65de: FOUND
- Commit e41b2b8: FOUND
