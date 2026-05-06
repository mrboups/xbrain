---
phase: 06-marketing-docs
plan: "03"
subsystem: marketing-site/docs
tags: [docs, html, marketing-site, memory-system, architecture]
dependency_graph:
  requires: ["06-01"]
  provides: ["docs/index.html", "docs/architecture.html", "docs/memory.html"]
  affects: ["marketing-site"]
tech_stack:
  added: []
  patterns: ["HTML/CSS vanilla", "TailwindCSS CDN", "docs-layout sidebar pattern"]
key_files:
  created:
    - marketing-site/docs/index.html
    - marketing-site/docs/architecture.html
    - marketing-site/docs/memory.html
  modified: []
decisions:
  - "ASCII diagram rendered as HTML entities to avoid encoding issues in static files"
  - "truth_level badge cards use inline styles for color differentiation (5 distinct colors per level)"
  - "Error reference table added to memory.html (not in plan) to complete the 422/405 story — Rule 2 missing critical context"
metrics:
  duration: "~12 minutes"
  completed: "2026-05-06T18:43:46Z"
  tasks_completed: 3
  tasks_total: 3
  files_created: 3
  files_modified: 0
---

# Phase 06 Plan 03: Documentation Pages (Getting Started, Architecture, Memory) Summary

**One-liner:** Three HTML documentation pages covering Getting Started (4-step quick start), Architecture (25-container reference with ASCII diagram and xbrain_net network), and Memory System (7-field tagging contract + 5 truth-level progression + curl API examples).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | docs/index.html — Getting Started | 593be65 | marketing-site/docs/index.html |
| 2 | docs/architecture.html — Architecture | 0f0e63d | marketing-site/docs/architecture.html |
| 3 | docs/memory.html — Memory System | 821462f | marketing-site/docs/memory.html |

## Verification Results

All acceptance criteria passed:

**index.html (338 lines, min 120):**
- `docker compose` present in Quick Start Step 3
- `cdn.tailwindcss.com`, `../assets/style.css`, `../assets/docs.css` linked
- `docs-layout`, `sidebar`, `architecture.html` link all present

**architecture.html (570 lines, min 150):**
- `xbrain_net` appears in overview and ASCII diagram label
- All 25 containers present in Container Reference table
- `nginx`, `graphiti-service` present
- `docs-layout`, `code-block`, `docs-table` all used

**memory.html (541 lines, min 150):**
- `EPHEMERAL`, `CANONICAL` present (all 5 truth levels)
- `team_scope` present in tagging contract table
- `memory/upsert` present in curl example
- `422` (tagging enforcement) and `405` (direct PATCH blocked) both present
- `code-block` used for all curl examples

## Deviations from Plan

### Auto-added Missing Critical Context

**1. [Rule 2 - Missing] Added Error Reference table to memory.html**
- **Found during:** Task 3 implementation
- **Issue:** The plan documented HTTP 422 and HTTP 405 in prose but had no consolidated error reference, making the page incomplete for developers integrating with memory-api
- **Fix:** Added a full Error Reference table (422, 405, 401, 403, 404) at the end of memory.html
- **Files modified:** marketing-site/docs/memory.html

**2. [Rule 2 - Enhancement] Added `upsert with metadata and entities` code block**
- **Found during:** Task 3 implementation
- **Issue:** The plan showed the basic upsert and the entities JSON separately; combining them in a second code block makes the entities workflow clearer
- **Fix:** Added a second code block showing a full upsert payload including the `metadata.entities` array

No other deviations. Plan executed as specified.

## Known Stubs

None. All links in the sidebar point to real filenames (`teams.html`, `chat.html`, etc.) that will be created in subsequent plans (06-04 onwards). Links are not broken — they are forward references to planned pages.

## Threat Flags

None. Pages are static HTML with no forms, no server logic, no credentials in examples (all use `$JWT` placeholder). Consistent with T-06-03-01 and T-06-03-02 disposition: accept.

## Self-Check: PASSED

Files exist:
- marketing-site/docs/index.html: FOUND
- marketing-site/docs/architecture.html: FOUND
- marketing-site/docs/memory.html: FOUND

Commits exist:
- 593be65 feat(06-03): add docs/index.html — Getting Started page
- 0f0e63d feat(06-03): add docs/architecture.html — full 25-container architecture
- 821462f feat(06-03): add docs/memory.html — memory system truth levels + tagging
