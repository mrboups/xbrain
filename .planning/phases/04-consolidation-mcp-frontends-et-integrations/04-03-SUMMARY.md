---
phase: 4
plan: "04-03"
subsystem: librechat-mcp-config
tags: [librechat, mcp, streamable-http, mcp-gateway, ssrf]
dependency_graph:
  requires: [04-02]
  provides: [librechat-mcp-tools]
  affects: [infrastructure/librechat/librechat.yaml, infrastructure/docker-compose.yml]
tech_stack:
  added: []
  patterns:
    - "LibreChat mcpSettings.allowedDomains for SSRF bypass on internal Docker hosts"
    - "streamable-http MCP transport (LibreChat v0.8.5+)"
key_files:
  created: []
  modified:
    - infrastructure/librechat/librechat.yaml
    - infrastructure/docker-compose.yml
decisions:
  - "mcpSettings.allowedDomains required to permit http://mcp-gateway:8081 (SSRF protection default-deny)"
metrics:
  duration: "~15 min"
  completed: "2026-05-04T23:28:25Z"
---

# Phase 4 Plan 03: LibreChat config mcpServers + smoke test E2E Summary

**One-liner:** LibreChat v0.8.5 configured with streamable-http MCP pointing to mcp-gateway:8081 — 3 tools (calendar, drive-read, scraper) loaded and confirmed healthy in 334ms.

## What Was Built

- `mcpSettings.allowedDomains: ["http://mcp-gateway:8081"]` added to `infrastructure/librechat/librechat.yaml` to bypass LibreChat's SSRF protection for the internal Docker bridge host.
- `mcpServers.xbrain` bloc added with `type: streamable-http`, `url: http://mcp-gateway:8081/mcp`, and headers `X-Team-Scope: "${LIBRECHAT_DEFAULT_TEAM_SCOPE}"` and `X-LibreChat-User-Email: "{{LIBRECHAT_USER_EMAIL}}"`.
- `LIBRECHAT_DEFAULT_TEAM_SCOPE: ${LIBRECHAT_DEFAULT_TEAM_SCOPE:-default}` added to the `librechat` service environment in `infrastructure/docker-compose.yml`.

## Validation Results

```
[MCP][xbrain] Creating streamable-http transport: http://mcp-gateway:8081/mcp
[MCP][xbrain] Tools: calendar, drive-read, scraper
[MCP][xbrain] Initialized in: 334ms
[MCP] Initialized with 1 configured server and 3 tools.
```

- LibreChat health status: **healthy**
- MCP tools loaded: **3** (calendar, drive-read, scraper)
- Transport: **streamable-http** (no fallback needed)
- Init time: **334ms**

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SSRF protection blocks internal Docker hostname mcp-gateway**

- **Found during:** T-04-03-01 — first restart
- **Issue:** LibreChat v0.8.5 has SSRF protection enabled by default. When no `allowedDomains` is configured, `isDomainAllowedCore()` blocks all non-public domains including Docker bridge hostnames. Error: `Domain "http://mcp-gateway:8081" is not allowed`.
- **Fix:** Added `mcpSettings.allowedDomains: ["http://mcp-gateway:8081"]` to `librechat.yaml`. This is the documented mechanism (`config.ts` Zod schema: `mcpSettings.allowedDomains: z.array(z.string()).optional()`).
- **Files modified:** `infrastructure/librechat/librechat.yaml`
- **Commit:** 3d8fcbb

## Threat Surface Scan

| Flag | File | Description |
|------|------|-------------|
| threat_flag: ssrf-allowlist | infrastructure/librechat/librechat.yaml | allowedDomains explicitly permits http://mcp-gateway:8081 — any compromise of the mcp-gateway container would have direct access from LibreChat's request path. Accepted per T-04-03-SEC-02 (internal Docker bridge, not exposed to internet). |

## Self-Check: PASSED

- [x] `infrastructure/librechat/librechat.yaml` modified with mcpSettings + mcpServers blocs
- [x] `infrastructure/docker-compose.yml` modified with LIBRECHAT_DEFAULT_TEAM_SCOPE
- [x] Commit e4d5098 (feat: configure LibreChat mcpServers) — verified
- [x] Commit 3d8fcbb (fix: add mcpSettings.allowedDomains) — verified
- [x] VM deployment confirmed: librechat healthy, 3 MCP tools loaded
