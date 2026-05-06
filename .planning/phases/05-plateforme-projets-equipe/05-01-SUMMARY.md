---
phase: 05-plateforme-projets-equipe
plan: "05-01"
subsystem: graphiti-service
tags: [graphiti, neo4j, memory, extraction, temporal, fastapi, anthropic, openai]

requires:
  - phase: 03-graphe-extraction-integrations
    provides: [Neo4j container (port 7687), healthcheck, xbrain_net network]
  - phase: 01-socle-infra
    provides: [memory-api container on port 8000, POST /v1/memory/upsert endpoint]

provides:
  - graphiti-service container on port 8300 (internal xbrain_net only)
  - POST /v1/ingest — async temporal fact ingestion (202 Accepted + asyncio.Task)
  - POST /v1/search — semantic search over extracted facts scoped by group_ids
  - GET /v1/healthz — health check with graphiti client status
  - memory-api enrichment hook — POST /v1/memory/upsert triggers graphiti ingest fail-soft

affects:
  - 05-02 (GitHub OAuth — memory-api deps may load graphiti-service env)
  - 05-03 and onwards (graphiti search available for enriched retrieval)

tech-stack:
  added:
    - "graphiti-core[anthropic]>=0.29.0 (temporal fact extraction + contradiction detection)"
    - "fastapi>=0.111 (REST wrapper)"
    - "uvicorn[standard]>=0.29 (single-worker ASGI server)"
  patterns:
    - "FastAPI lifespan context manager for async Neo4j client init (avoids event loop conflict)"
    - "asyncio.create_task for non-blocking background enrichment calls"
    - "SEMAPHORE_LIMIT pattern to respect Anthropic Tier 1 rate limits (50 req/min)"
    - "Fail-soft enrichment: exception logs warning, never propagates to caller"

key-files:
  created:
    - apps/graphiti-service/Dockerfile
    - apps/graphiti-service/pyproject.toml
    - apps/graphiti-service/app/__init__.py
    - apps/graphiti-service/app/main.py
  modified:
    - infrastructure/docker-compose.yml
    - apps/memory-api/app/routes/memory.py

key-decisions:
  - "graphiti_client initialised inside lifespan() only — never at module level (event loop conflict)"
  - "OPENAI_API_KEY obligatoire dans graphiti-service même avec Anthropic LLM — embeddings text-embedding-3-small + reranker gpt-4.1-nano ne supportent pas Anthropic"
  - "SEMAPHORE_LIMIT=3 par défaut pour Claude Haiku Tier 1 (50 req/min Anthropic)"
  - "graphiti-service dégrade gracieusement si Neo4j pas encore prêt (fail-soft init, service démarre en mode dégradé)"
  - "start_period: 60s sur le healthcheck pour laisser Neo4j ~45s démarrer avant build_indices_and_constraints()"
  - "Pas de ports: publics pour graphiti-service — réseau xbrain_net interne uniquement (T-05-01-02)"
  - "asyncio.create_task pour l'enrichissement Graphiti depuis memory-api — ne bloque pas la réponse HTTP"
  - "GRAPHITI_SERVICE_URL configurable via env var dans memory-api (default: http://graphiti-service:8300)"

patterns-established:
  - "Nouveau service Phase 5 : FastAPI lifespan pattern pour init async client (graphiti-core, potentiellement d'autres clients async Phase 5+)"
  - "Enrichissement fail-soft inter-services : asyncio.create_task + try/except + log.warning"

requirements-completed: [MEM-06, MEM-07, MEM-08]

duration: 4min
completed: 2026-05-06
---

# Phase 5 Plan 01: graphiti-service container Summary

**graphiti-service FastAPI wrapper autour de graphiti-core : extraction temporelle de faits bi-temporaux + détection de contradictions via Claude Haiku, branché sur Neo4j existant, avec appel fail-soft depuis memory-api**

## Performance

- **Duration:** 4 min
- **Started:** 2026-05-06T02:00:14Z
- **Completed:** 2026-05-06T02:04:11Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- Container `graphiti-service` complet (Dockerfile, pyproject.toml, app/main.py) avec 3 endpoints REST sur port 8300
- Intégration dans docker-compose.yml avec contraintes de démarrage Neo4j correctes (start_period: 60s)
- Enrichissement fail-soft depuis memory-api : chaque POST /v1/memory/upsert déclenche une tâche background vers graphiti-service, sans bloquer la réponse

## Task Commits

1. **Task 1: Create graphiti-service (Dockerfile, pyproject.toml, app/main.py)** - `d4911e9` (feat)
2. **Task 2: Add graphiti-service to docker-compose.yml** - `3747678` (feat)
3. **Task 3: Fail-soft enrichment call from memory-api** - `d4fb4c7` (feat)

**Plan metadata:** (à venir — commit docs)

## Files Created/Modified

- `apps/graphiti-service/Dockerfile` — python:3.12-slim + libxml2/libxslt1 + uvicorn single worker port 8300
- `apps/graphiti-service/pyproject.toml` — graphiti-core[anthropic]>=0.29.0, fastapi, uvicorn[standard], httpx, structlog, pydantic
- `apps/graphiti-service/app/__init__.py` — fichier vide (package marker)
- `apps/graphiti-service/app/main.py` — FastAPI + lifespan, POST /v1/ingest (202 + asyncio.Task), POST /v1/search, GET /v1/healthz, SEMAPHORE_LIMIT=3, fail-soft init
- `infrastructure/docker-compose.yml` — ajout bloc graphiti-service (après mcp-deck) + GRAPHITI_SERVICE_URL dans memory-api env
- `apps/memory-api/app/routes/memory.py` — ajout _enrich_with_graphiti() + asyncio.create_task() après commit upsert

## Decisions Made

- `graphiti_client` initialisé uniquement dans `lifespan()` — piège event loop documenté dans RESEARCH.md Q1
- `OPENAI_API_KEY` obligatoire même avec Anthropic LLM — graphiti-core n'a pas d'embedder Anthropic (text-embedding-3-small + gpt-4.1-nano via OpenAI)
- `SEMAPHORE_LIMIT=3` pour limiter les appels concurrents et respecter le rate limit Anthropic Haiku Tier 1
- Fail-soft init : le service démarre même si Neo4j n'est pas encore prêt, retourne 503 sur les endpoints jusqu'à ce que le client soit initialisé
- `start_period: 60s` sur le healthcheck pour tenir compte du temps de démarrage Neo4j (~45s) + `build_indices_and_constraints()`

## Deviations from Plan

Aucune — plan exécuté exactement tel qu'écrit.

## Issues Encountered

Aucun.

## Known Stubs

Aucun stub — tous les endpoints sont opérationnels (le graphiti-service requiert Neo4j + clés API en runtime, mais aucune donnée hardcodée ni placeholder dans le code).

## User Setup Required

Pour démarrer graphiti-service en production, les variables suivantes doivent être présentes dans `.env` sur la VM :

```bash
# Déjà présentes si Phase 3 et agent-runtime sont configurés :
NEO4J_PASSWORD=<...>
ANTHROPIC_API_KEY=<...>
OPENAI_API_KEY=<...>    # obligatoire pour les embeddings — partager la clé agent-runtime

# Optionnelles (defaults raisonnables) :
GRAPHITI_LLM_MODEL=claude-haiku-4-5-20251001
GRAPHITI_SEMAPHORE_LIMIT=3
GRAPHITI_SERVICE_URL=http://graphiti-service:8300
```

## Next Phase Readiness

- graphiti-service est disponible pour les plans suivants de la Phase 5
- POST /v1/search utilisable pour enrichir les réponses des agents (agent-runtime peut appeler graphiti-service pour des faits temporels)
- Plan 05-02 (GitHub OAuth) est indépendant de graphiti-service

## Self-Check: PASSED

Fichiers vérifiés :
- FOUND: apps/graphiti-service/Dockerfile
- FOUND: apps/graphiti-service/pyproject.toml
- FOUND: apps/graphiti-service/app/__init__.py
- FOUND: apps/graphiti-service/app/main.py
- FOUND: infrastructure/docker-compose.yml (service graphiti-service présent, sans ports publics)
- FOUND: apps/memory-api/app/routes/memory.py (_enrich_with_graphiti + asyncio.create_task présents)

Commits vérifiés :
- FOUND: d4911e9 (task 1)
- FOUND: 3747678 (task 2)
- FOUND: d4fb4c7 (task 3)

---
*Phase: 05-plateforme-projets-equipe*
*Completed: 2026-05-06*
