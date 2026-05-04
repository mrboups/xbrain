---
phase: "04"
plan: "04-02"
subsystem: mcp-gateway
tags: [mcp, aggregate, fastmcp, multiprocessing, librechat]
dependency_graph:
  requires: [mcp-gateway registry DB, bridge JWT auth]
  provides: [MCP aggregate server port 8081, LibreChat-compatible single MCP endpoint]
  affects: [librechat MCP config (plan 04-03), nginx upstream (plan 04-03)]
tech_stack:
  added: [FastMCP subprocess via multiprocessing.Process]
  patterns: [daemon subprocess, exponential backoff retry, dynamic tool registration]
key_files:
  created:
    - apps/mcp-gateway/app/aggregate.py
  modified:
    - apps/mcp-gateway/app/main.py
    - infrastructure/docker-compose.yml
decisions:
  - "D-04-02-01: Utilisation de multiprocessing.Process(daemon=True) pour contourner issue #1367 FastMCP/FastAPI incompatibility"
  - "D-04-02-02: Retry exponentiel 0s→2s→4s→8s pour la découverte des tools au démarrage du subprocess"
  - "D-04-02-03: mem_limit augmenté de 192m à 256m pour le subprocess FastMCP"
  - "D-04-02-04: Image taguée phase4 (précédemment phase3)"
metrics:
  duration: "25min"
  completed: "2026-05-05"
  tasks_completed: 3
  files_created: 1
  files_modified: 2
---

# Phase 04 Plan 02: mcp-gateway — MCP aggregate server Summary

**One-liner:** FastMCP subprocess on port 8081 exposing all registered gateway tools as a single MCP server for LibreChat, with exponential backoff discovery and bridge JWT auth.

## Tasks Completed

| # | Task | Commit | Files |
|---|------|--------|-------|
| 1 | Créer aggregate.py (serveur FastMCP façade) | 68f62d2 | apps/mcp-gateway/app/aggregate.py |
| 2 | Lancer le subprocess dans main.py lifespan | 68f62d2 | apps/mcp-gateway/app/main.py |
| 3 | Ajouter port 8081 et env vars dans docker-compose.yml | cae427d | infrastructure/docker-compose.yml |

## Validation Results

```
# docker exec xbrain-mcp-gateway python3 -c "... list_tools ..."
Tools: ['calendar', 'drive-read', 'scraper']
```

Container status: `Up (healthy)`, ports `8080-8081/tcp` exposés.

Logs de démarrage confirmés :
- `aggregate.discover_success count=3 attempt=0` — découverte immédiate sans retry nécessaire
- `aggregate.tool_registered tool=calendar`
- `aggregate.tool_registered tool=drive-read`
- `aggregate.tool_registered tool=scraper`
- `aggregate.server_ready tools_registered=3 host=0.0.0.0 port=8081`

## Architecture

```
LibreChat → http://mcp-gateway:8081/mcp   (FastMCP aggregate, port 8081)
                    ↓ (bridge JWT)
            http://127.0.0.1:8080/tools/{name}/call   (FastAPI gateway, port 8080)
                    ↓ (MCP SDK)
            http://mcp-{sidecar}:{port}/mcp   (individual sidecars)
```

Le subprocess tourne dans le même container que la gateway FastAPI. `daemon=True` garantit qu'il est tué automatiquement si le process parent (uvicorn) s'arrête.

## Deviations from Plan

None — plan executed exactly as written.

Note: `make_bridge_jwt` n'existe pas dans `app/auth.py` (comme anticipé dans le brief). La fonction `_mint_bridge_jwt()` a été implémentée directement dans `aggregate.py` en utilisant `authlib.jose` (même bibliothèque que `verify_bridge_jwt`).

## Security Mitigations Applied

- **T-04-02-SEC-01**: `team_scope` embedé dans le JWT signé — la gateway extrait `team_scope` du JWT, pas du header. Injection de header impossible.
- **T-04-02-SEC-03**: Validation `^[a-z0-9_-]+$` sur les `tool_name` avant construction URL. `assert` + `log.warning + skip` si nom invalide dans le registre.
- **T-04-02-SEC-02**: Subprocess `daemon=True` — crash loggé, container healthcheck surveille la gateway parent.

## Known Stubs

None.

## Self-Check: PASSED

- [x] `apps/mcp-gateway/app/aggregate.py` — créé et validé
- [x] `apps/mcp-gateway/app/main.py` — modifié, subprocess lancé
- [x] `infrastructure/docker-compose.yml` — port 8081 exposé, env vars ajoutés
- [x] Commit 68f62d2 — vérifié
- [x] Commit cae427d — vérifié
- [x] `list_tools` via MCP SDK retourne `['calendar', 'drive-read', 'scraper']`
- [x] Container status: healthy
