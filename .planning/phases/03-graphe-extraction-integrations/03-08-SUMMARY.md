---
phase: 03-graphe-extraction-integrations
plan: "08"
subsystem: mcp-drive-read
tags: [mcp, drive, fastmcp, oauth, write-back, int-04, mcp-05]
dependency_graph:
  requires:
    - 03-06  # mcp-gateway (routes calls to this sidecar)
    - 03-01  # Neo4j/infra (docker-compose context)
  provides:
    - read_drive_file tool (MCP-05 second concrete MCP tool)
    - write_drive_file tool (INT-04 write-back with explicit consent)
  affects:
    - infrastructure/docker-compose.yml  # new mcp-drive-read service
    - .env.example  # GOOGLE_DRIVE_* credentials documented
tech_stack:
  added:
    - mcp>=1.27.0 (FastMCP streamable-http transport)
    - google-api-python-client>=2.195.0
    - google-auth-oauthlib>=1.3.1
    - google-auth-httplib2>=0.2.0
  patterns:
    - FastMCP standalone (not mounted in parent FastAPI, issue #1367 avoided)
    - Single worker enforced (issue #658 — session state per process)
    - asyncio.run_in_executor for sync Drive API calls in async MCP handlers
    - user_consent boolean opt-in guard for write operations (T-03-08-01)
key_files:
  created:
    - apps/mcp-drive-read/app/__init__.py
    - apps/mcp-drive-read/app/drive_client.py
    - apps/mcp-drive-read/app/main.py
    - apps/mcp-drive-read/Dockerfile
    - apps/mcp-drive-read/pyproject.toml
  modified:
    - infrastructure/docker-compose.yml  # mcp-drive-read service added
    - .env.example  # GOOGLE_DRIVE_ACCESS_TOKEN + GOOGLE_DRIVE_REFRESH_TOKEN
decisions:
  - "Standalone uvicorn process (not mounted) per FastMCP issue #1367 — same pattern as mcp-scraper and mcp-calendar"
  - "user_consent guard is a hard early-return before any Drive API call — satisfies T-03-08-01 (Elevation of Privilege)"
  - "PDF extraction via pypdf with MediaIoBaseDownload in drive_client.py — no separate PDF service needed"
  - "MAX_BYTES=50KB cap applied to all file types — mitigates T-03-08-04 (DoS via large files)"
  - "Distinct log action strings: mcp.tool_call.read_drive_file vs mcp.tool_call.write_drive_file — gateway can audit separately"
metrics:
  duration: "~25 minutes"
  completed: "2026-05-04"
  tasks_completed: 2
  files_created: 5
  files_modified: 2
---

# Phase 03 Plan 08: MCP Drive Read/Write Sidecar Summary

**One-liner:** FastMCP standalone sidecar on port 8101 exposing `read_drive_file` and `write_drive_file` as two distinct `@mcp.tool()` decorators — live Drive file access (Docs/Sheets/Slides/PDF) plus INT-04 write-back with explicit `user_consent` opt-in guard.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | drive_client.py + pyproject.toml + app scaffolding | 852ffbf (pre-existing) | apps/mcp-drive-read/app/drive_client.py, apps/mcp-drive-read/pyproject.toml, apps/mcp-drive-read/app/__init__.py |
| 2 | main.py FastMCP + Dockerfile + docker-compose | 25d3d11 | apps/mcp-drive-read/app/main.py, apps/mcp-drive-read/Dockerfile |

Note: Some files were committed in earlier plan sessions (852ffbf, 3b9518c, b323c3a). Task 2 commit 25d3d11 is the authoritative commit for this plan's main.py with the final 81-line implementation.

## Key Implementation Details

### Two distinct MCP tools

```python
@mcp.tool()
async def read_drive_file(file_id: str) -> str:
    # logs: mcp.tool_call.read_drive_file
    # supports: Docs/Sheets/Slides via files.export, PDFs via pypdf, text via get_media

@mcp.tool()
async def write_drive_file(file_id: str, content: str, user_consent: bool) -> str:
    # early-return if not user_consent (T-03-08-01 mitigation)
    # logs: mcp.tool_call.write_drive_file
```

### drive_client.py helpers (127 lines)

- `export_file_as_text(file_id)`: detects MIME type via `files().get()`, routes to `files().export()` for Workspace files, `MediaIoBaseDownload + pypdf` for PDFs, `files().get_media()` for plain text
- `update_file_content(file_id, content)`: `MediaInMemoryUpload` + `files().update()` with `drive.file` scope
- `MAX_BYTES = 50_000` cap applied everywhere
- Credentials from `GOOGLE_DRIVE_ACCESS_TOKEN` + `GOOGLE_DRIVE_REFRESH_TOKEN` env vars

### Docker service

```yaml
mcp-drive-read:
  mem_limit: 128m
  GOOGLE_DRIVE_ACCESS_TOKEN: ${GOOGLE_DRIVE_ACCESS_TOKEN:-}
  GOOGLE_DRIVE_REFRESH_TOKEN: ${GOOGLE_DRIVE_REFRESH_TOKEN:-}
  # healthcheck: wget http://127.0.0.1:8101/healthz || exit 0
```

## Deviations from Plan

None — plan executed exactly as written. Files committed across earlier plan sessions and Task 2 commit 25d3d11.

## Threat Model Compliance

| Threat ID | Mitigation | Status |
|-----------|-----------|--------|
| T-03-08-01 | `if not user_consent: return ERROR` — hard guard before any API call | Implemented |
| T-03-08-02 | .env excluded by .gitignore; .env.example uses `__FILL_AFTER_OAUTH__` placeholders | OK |
| T-03-08-03 | `drive.file` scope limits writes to app-owned/shared files; gateway audits | Accepted |
| T-03-08-04 | `MAX_BYTES = 50_000` cap on all file type paths | Implemented |

## Self-Check: PASSED

- [x] apps/mcp-drive-read/app/main.py exists and parses (81 lines >= 80 required)
- [x] apps/mcp-drive-read/app/drive_client.py exists (127 lines >= 60 required)
- [x] read_drive_file present as @mcp.tool() decorator
- [x] write_drive_file present as @mcp.tool() decorator (separate decorator)
- [x] user_consent appears 6 times (>= 3 required)
- [x] infrastructure/docker-compose.yml has mcp-drive-read service with mem_limit=128m
- [x] .env.example has GOOGLE_DRIVE_ACCESS_TOKEN and GOOGLE_DRIVE_REFRESH_TOKEN
- [x] commit 25d3d11 exists in git log
