---
phase: 04-consolidation-mcp-frontends-et-integrations
plan: "04"
subsystem: agent-runtime
tags: [langgraph, mcp, structured-tool, jwt, httpx, langchain-core]

requires:
  - phase: 03-mcp-services
    provides: mcp-gateway with GET /tools + POST /tools/{name}/call endpoints and registered sidecars

provides:
  - apps/agent-runtime/app/tools/mcp_gateway_client.py — get_mcp_tools(team_scope) returning LangGraph StructuredTools
  - make_bridge_jwt() added to apps/agent-runtime/app/auth.py

affects:
  - agent-runtime LangGraph graphs wanting to call MCP tools (ingestion-agent, future agents)
  - plan 04-07 (mcp-deck — new tool auto-discovered after registration)

tech-stack:
  added: []
  patterns:
    - "MCP tool discovery cached in-memory with configurable TTL (MCP_TOOL_CACHE_TTL_SECS)"
    - "SSRF guard via _TOOL_NAME_RE regex on tool_name before path interpolation"
    - "Graceful degradation — gateway unreachable returns [] not exception"

key-files:
  created:
    - apps/agent-runtime/app/tools/mcp_gateway_client.py
  modified:
    - apps/agent-runtime/app/auth.py
    - infrastructure/docker-compose.yml

key-decisions:
  - "D-04-04-A: make_bridge_jwt added to agent-runtime/auth.py mirroring librechat-bridge/bridge_token.py pattern (Rule 2 — missing critical auth function required by mcp_gateway_client)"
  - "D-04-04-B: Sync httpx used for tool discovery and calls (no async) — LangGraph StructuredTool.from_function expects sync callable"
  - "D-04-04-C: In-memory dict cache keyed by team_scope with monotonic clock TTL — no Redis needed for Phase 4"

patterns-established:
  - "get_mcp_tools(team_scope) pattern: call from any LangGraph graph node to get injectable MCP tools"
  - "Bridge JWT minted per call (not per request) with 300s TTL — acceptable churn vs. security trade-off"

requirements-completed:
  - MCP-06

duration: 25min
completed: 2026-05-05
---

# Phase 4 Plan 04: agent-runtime mcp_gateway_client.py Summary

**LangGraph StructuredTool wrapper for MCP gateway — get_mcp_tools() returns 3 callable tools (calendar, drive-read, scraper) with bridge JWT auto-injection and 5min in-memory cache**

## Performance

- **Duration:** ~25 min
- **Started:** 2026-05-04T22:30:00Z
- **Completed:** 2026-05-04T22:56:00Z
- **Tasks:** 2 (+ 1 Rule 2 deviation)
- **Files modified:** 3

## Accomplishments

- `mcp_gateway_client.py` créé : `get_mcp_tools(team_scope)` retourne des `StructuredTool` LangGraph wrappant chaque outil MCP enregistré dans la gateway
- Bridge JWT auto-injecté à chaque appel via `make_bridge_jwt` (issuer = "agent-runtime")
- Cache in-memory par `team_scope` avec TTL configurable (300s par défaut)
- Dégradation gracieuse : gateway down → `[]` sans crash, log warning seulement
- Test container validé : `3 tools` retournés (calendar, drive-read, scraper)

## Task Commits

Les fichiers avaient déjà été commités dans le commit `22e93c7` (feat(04-01)) par un agent concurrent.

1. **T-04-04-01: mcp_gateway_client.py + auth.py make_bridge_jwt** - `22e93c7` (feat(04-01))
2. **T-04-04-02: docker-compose MCP_GATEWAY_URL env** - `22e93c7` (feat(04-01))

**Plan metadata:** (ce SUMMARY)

## Files Created/Modified

- `apps/agent-runtime/app/tools/mcp_gateway_client.py` — Client MCP gateway : `get_mcp_tools(team_scope)`, `invalidate_cache()`, `_discover_tools_sync()`, `_make_tool_callable()`
- `apps/agent-runtime/app/auth.py` — Ajout `make_bridge_jwt(sub, team_scope, ttl_seconds)` (HS256, issuer=agent-runtime)
- `infrastructure/docker-compose.yml` — Ajout `MCP_GATEWAY_URL` et `MCP_TOOL_CACHE_TTL_SECS` dans le service `agent-runtime`

## Decisions Made

- `make_bridge_jwt` ajouté à `agent-runtime/app/auth.py` plutôt qu'importé depuis un package partagé — cohérent avec le pattern existant (chaque service a sa propre copie, comment librechat-bridge et memory-api font pareil).
- Sync `httpx` choisi pour les callables StructuredTool — LangGraph `StructuredTool.from_function` appelle le callable de façon synchrone dans le graph executor.
- Pas de validation du `inputSchema` MCP à ce stade — `args_schema=None` laisse LangChain inférer depuis les kwargs, suffisant pour Phase 4.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] `make_bridge_jwt` absent de `agent-runtime/app/auth.py`**
- **Found during:** T-04-04-01 — le plan référence `from app.auth import make_bridge_jwt` mais la fonction n'existait pas dans ce module
- **Issue:** `auth.py` n'avait que `verify_bridge_jwt` et `verify_google_id_token` — aucune fonction pour minter des tokens sortants
- **Fix:** Ajout de `make_bridge_jwt(sub, team_scope, ttl_seconds=300)` avec HS256 + `from app.config import settings` (import ajouté)
- **Files modified:** `apps/agent-runtime/app/auth.py`
- **Verification:** Import réussi dans container, `get_mcp_tools()` retourne 3 tools sans erreur d'authentification
- **Committed in:** `22e93c7` (commit concurrent plan 04-01)

---

**Total deviations:** 1 auto-fixed (Rule 2 — missing critical auth function)
**Impact on plan:** Fix essentiel pour que `mcp_gateway_client.py` puisse minter les JWTs sortants. Aucun scope creep.

## Issues Encountered

- **Disque VM plein (98%)** lors du rebuild : `docker builder prune -af` a libéré ~1 GB de build cache, rebuild réussi ensuite
- **Warning authlib deprecated** : `authlib.jose` → `joserfc` migration recommandée. Pré-existant dans le codebase, hors scope de ce plan.
- **Commit concurrent** : les 3 fichiers modifiés ont été commités par le plan 04-01 (agent concurrent). Pas de double commit créé — implémentation identique confirmée in HEAD.

## Next Phase Readiness

- `get_mcp_tools(team_scope)` utilisable immédiatement dans les graphes LangGraph Phase 2 (ingestion-agent) et futurs (plan 04-07 mcp-deck)
- `invalidate_cache()` disponible pour forcer le refresh après enregistrement d'un nouveau tool
- Gateway `mcp-gateway:8080` joignable depuis `agent-runtime` sur le réseau Docker interne `xbrain_net`

---
*Phase: 04-consolidation-mcp-frontends-et-integrations*
*Completed: 2026-05-05*
