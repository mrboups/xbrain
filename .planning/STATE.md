# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-02)

**Core value:** Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.
**Current focus:** Phase 1 — Socle Infra + Frontends + memory-api

## Current Position

Phase: 4 (Consolidation MCP Frontends + Intégrations Avancées)
Plan: 1 of 8 in current phase
Status: In progress
Last activity: 2026-05-05 — Plan 04-04 terminé : mcp_gateway_client.py — LangGraph StructuredTool wrapper pour MCP gateway (MCP-06)

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| — | — | — | — |

**Recent Trend:**
- Last 5 plans: —
- Trend: —

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- 2026-05-02: Couche mémoire = mem0 + memory-api natif (Memstate/Remembra/Memori retirés comme dépendances directes)
- 2026-05-02: VM strategy confirmée : e2-medium (P1) → e2-standard-2 (P2 entry gate) → e2-standard-4 ou split Langfuse (P3 entry gate)
- 2026-05-02: MinIO via Chainguard image (`cgr.dev/chainguard/minio:latest`) — images Docker Hub discontinuées oct 2025
- 2026-05-02: `MemoryProvider` interface dans `/packages/memory-models` obligatoire avant toute intégration mem0 (Phase 2)
- 2026-05-02: Langfuse sur e2-medium Phase 1 : déployé en config légère sans ClickHouse complet — surveiller RAM ; si OOM, migrer Langfuse à Phase 2 start post-VM-upgrade

### Pending Todos

None yet.

### Blockers/Concerns

- **Phase 2 entry gate** : POC 1-jour mem0 vs native doit être réalisé AVANT le planning Phase 2 — résultat détermine le chemin `MemoryProvider` implémentation
- **Phase 3 entry gate** : POC Memori BYODB (Alpha) doit être réalisé avant planning Phase 3 — fallback = LangGraph + LLM structured output
- **OOM Risk Phase 1** : e2-medium (4 GB) est serré avec LibreChat + MongoDB + Qdrant + memory-api. Surveiller `docker stats` total. Ne pas ajouter Langfuse complet (ClickHouse) sans upgrade VM.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-05-05
Stopped at: Plan 04-04 complet — mcp_gateway_client.py LangGraph wrapper MCP gateway. get_mcp_tools() retourne 3 tools validés en container (calendar, drive-read, scraper). Commit 22e93c7 (concurrent 04-01). VM disque 97% après builder prune.
Resume file: None
