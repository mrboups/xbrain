---
phase: 4
plan: "04-08"
subsystem: scripts/verification
tags: [mcp, deck, verification, smoke-test, register-tools]
dependency_graph:
  requires: [04-01, 04-02, 04-03, 04-04, 04-05, 04-06, 04-07]
  provides: [verify-phase4, register-deck-tool]
  affects: [mcp-gateway, agent-runtime, mcp-deck]
tech_stack:
  added: []
  patterns: [bash-smoke-test, structlog-stdout-filter]
key_files:
  modified:
    - infrastructure/scripts/register-mcp-tools.sh
  created:
    - infrastructure/scripts/verify-phase4.sh
decisions:
  - "Register deck tool before verify so test 4 (>=4 tools) passes cleanly"
  - "structlog in agent-runtime writes to stdout — filter with grep -E '^[0-9]+$' to extract count"
metrics:
  duration: "~20 min"
  completed: "2026-05-05"
---

# Phase 4 Plan 08: register-mcp-tools.sh update + verify-phase4.sh + UAT Summary

## One-liner

Scripts updated to register 4 MCP tools (scraper, drive-read, calendar, deck) with 8-test E2E verify passing 8/8 on the production VM.

## What Was Done

### Task 1 — register-mcp-tools.sh updated

`infrastructure/scripts/register-mcp-tools.sh` modifié :

- Section header : `--- Registering Phase 3 MCP tools ---` → `--- Registering Phase 3+4 MCP tools ---`
- Ajout de l'appel `register_tool "deck" "http://mcp-deck:8103" "Generate PowerPoint presentations (.pptx) via python-pptx — MCP-07"`
- `TOTAL_TOOLS=3` → `TOTAL_TOOLS=4`
- Résumé final étendu avec un message spécifique quand 3/4 (deck absent)

Résultat VM : `4/4 tools registered` + smoke test scraper OK.

### Task 2 — verify-phase4.sh créé

`infrastructure/scripts/verify-phase4.sh` créé : 8 tests E2E, seuil 6/8.

| Test | Description | Résultat |
|------|-------------|----------|
| 1/8 | mcp-deck container health | PASS (health=healthy) |
| 2/8 | POST /v1/messages upsert silencieux (04-01) | PASS (HTTP 201) |
| 3/8 | mcp-gateway aggregate port 8081 (04-02) | PASS (up:406) |
| 4/8 | /tools >= 4 tools incl. deck (04-02+04-08) | PASS (4 tools) |
| 5/8 | LibreChat librechat.yaml mcpServers (04-03) | PASS (port 8081 present) |
| 6/8 | agent-runtime MCP tools import (04-04) | PASS (4 tools discoverable) |
| 7/8 | drive-sync webhook port 8200 (04-05) | PASS (status=ok) |
| 8/8 | team_drive_mappings.project_scope column (04-06) | PASS (migration 0005 applied) |

**Résultat final :** `[verify-phase4] === SUCCESS === 8 passed, 0 skipped (8/8)`

### Task 3 — Exécution VM

Exécutés avec succès sur VM `__VM_HOST__` (disque 61% — blocker précédent résolu).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] structlog stdout pollution dans test 6**

- **Found during:** Task 3 (première exécution verify)
- **Issue:** `get_mcp_tools()` dans agent-runtime utilise structlog qui écrit les logs INFO sur stdout (pas stderr). La variable `AGENT_TOOLS` capturait `"2026-05-04 23:32:47 [info] mcp_gateway_client.tools_loaded count=4\n4"` — la comparaison `-ge 1` échouait car bash ne parse pas les strings multi-lignes.
- **Fix:** Ajout de `logging.disable(logging.CRITICAL)` avant l'import + filtrage post-capture : `grep -E '^[0-9]+$' | tail -1` pour extraire uniquement la ligne numérique.
- **Files modified:** `infrastructure/scripts/verify-phase4.sh`
- **Commit:** `a84d90e`

## Commits

| Hash | Type | Description |
|------|------|-------------|
| `4c71401` | feat | Add mcp-deck to register-mcp-tools.sh + create verify-phase4.sh |
| `a84d90e` | fix | verify-phase4.sh test 6 — extract numeric result past structlog stdout lines |

## Self-Check

- [x] `infrastructure/scripts/verify-phase4.sh` existe
- [x] `infrastructure/scripts/register-mcp-tools.sh` modifié (TOTAL_TOOLS=4, deck enregistré)
- [x] Commits `4c71401` et `a84d90e` présents
- [x] verify-phase4.sh : 8/8 PASS sur VM

## Self-Check: PASSED

## Known Stubs

None — scripts de maintenance, pas de UI data flow.

## Threat Flags

None — scripts locaux VM, pas de nouvelle surface réseau.
