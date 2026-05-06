---
phase: 06-marketing-docs
plan: "04"
subsystem: marketing-site/docs
tags: [documentation, html, mcp-tools, teams, chat-interfaces]
dependency_graph:
  requires: [06-01]
  provides: [teams-docs, chat-docs, mcp-tools-docs]
  affects: [marketing-site/docs/]
tech_stack:
  added: []
  patterns: [docs-layout, sidebar-14-links, code-block, callout, docs-table, breadcrumb]
key_files:
  created:
    - marketing-site/docs/teams.html
    - marketing-site/docs/chat.html
    - marketing-site/docs/mcp-tools.html
  modified: []
decisions:
  - "sidebar identical across all 3 pages with 14 links, active link class marks current page"
  - "code examples use $JWT/$ADMIN_JWT/$GATEWAY_JWT placeholders — no real credentials"
  - "mcp-deck port set to 8200 as specified in plan (not 8080 range)"
metrics:
  duration: "12 minutes"
  completed: "2026-05-06"
  tasks_completed: 3
  tasks_total: 3
  files_created: 3
  files_modified: 0
---

# Phase 06 Plan 04: Teams & Scopes + Chat Interfaces + MCP Tools Docs — Summary

**One-liner:** Three documentation pages covering team isolation via X-Team-Scope header, LibreChat/Open WebUI config with librechat-bridge sync architecture, and the 4 MCP tools (scraper/Drive/Calendar/Deck) routed through mcp-gateway with audit trail.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | docs/teams.html — Teams & Scopes | 736ac9e | marketing-site/docs/teams.html |
| 2 | docs/chat.html — Chat Interfaces | 736ac9e | marketing-site/docs/chat.html |
| 3 | docs/mcp-tools.html — MCP Tools | 736ac9e | marketing-site/docs/mcp-tools.html |

All 3 tasks committed in a single atomic commit (736ac9e) — independent pages with no interdependencies.

## What Was Built

### teams.html (409 lines)
- Team isolation concept with X-Team-Scope enforcement explanation
- Code example showing `team_scope` mismatch → `400 Bad Request: item.team_scope must match X-Team-Scope header`
- Visibility table: `team` / `project` / `private` with use cases
- Full tagging contract JSON example
- User management: Google OAuth, source_user_id, ADMIN_USER_SUBS
- Team creation admin API example
- Drive folder mapping per team with properties table
- `callout--info` for "Multiple Folders per Team" note

### chat.html (476 lines)
- "Two Frontends, One Memory" framing with multi-frontend invariant
- LibreChat v0.8.2-rc2 properties table (version, URL, auth, models, integration)
- `librechat.yaml` endpoints config: anthropic / openAI / custom xAI (Grok)
- mcpServers config block connecting to `mcp-gateway:8080/mcp/aggregate`
- `callout--warning` for `allowedDomains` SSRF protection requirement
- Open WebUI v0.9.0 properties table with non-OSI license note
- Memory sync architecture ASCII diagram: librechat-bridge + openwebui-pipeline paths
- Conversation API direct POST example
- CANONICAL facts in system prompts — truth level hierarchy explanation

### mcp-tools.html (654 lines)
- mcp-gateway architecture diagram showing LibreChat → gateway → 4 tools + agent-runtime path
- Tool registration script reference
- Tool 1: mcp-scraper (port 8100, scrape_url) — table, LibreChat prompt example, direct API call
- Tool 2: mcp-drive-read (port 8101, list_files/read_file/write_file) — table, 3 separate code blocks, `callout--warning` for write opt-in
- Tool 3: mcp-calendar (port 8102, list_events/get_event, read-only) — table, 2 code blocks
- Tool 4: mcp-deck (port 8200, create_deck/update_deck, python-pptx + MinIO) — table, full JSON example with 5-slide pitch deck
- Audit trail: SQL query for `audit_log` table filtered by `team_scope`

## Acceptance Criteria Verification

| Check | Result |
|-------|--------|
| teams.html contains "X-Team-Scope" | PASS |
| teams.html contains "400 Bad Request" | PASS |
| teams.html contains "visibility" | PASS |
| teams.html contains "docs-layout" | PASS |
| teams.html contains "code-block" | PASS |
| teams.html min 100 lines | PASS (409) |
| chat.html contains "LibreChat" | PASS |
| chat.html contains "Open WebUI" | PASS |
| chat.html contains "mcp-gateway" | PASS |
| chat.html contains "librechat-bridge" | PASS |
| chat.html contains "CANONICAL" | PASS |
| chat.html contains "code-block" | PASS |
| chat.html min 100 lines | PASS (476) |
| mcp-tools.html contains "mcp-scraper" | PASS |
| mcp-tools.html contains "mcp-drive-read" | PASS |
| mcp-tools.html contains "mcp-calendar" | PASS |
| mcp-tools.html contains "mcp-deck" | PASS |
| mcp-tools.html contains "mcp-gateway" | PASS |
| mcp-tools.html contains "audit" | PASS |
| mcp-tools.html code-block count >= 4 | PASS (11) |
| mcp-tools.html min 150 lines | PASS (654) |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. All pages contain concrete content specific to xbrain's actual implementation. No placeholder text or "coming soon" sections.

## Threat Flags

No new network endpoints, auth paths, or schema changes introduced. Pages are static HTML — no server-side logic. Threat model items T-06-04-01 and T-06-04-02 from plan accepted: code examples use `$JWT`/`$ADMIN_JWT`/`$GATEWAY_JWT` placeholders, not real credentials.

## Self-Check: PASSED

- `marketing-site/docs/teams.html` exists: FOUND
- `marketing-site/docs/chat.html` exists: FOUND
- `marketing-site/docs/mcp-tools.html` exists: FOUND
- Commit 736ac9e exists: FOUND
