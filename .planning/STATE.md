# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-02)

**Core value:** Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.
**Current focus:** Phase 1 — Socle Infra + Frontends + memory-api

## Current Position

Phase: 4 (Consolidation MCP Frontends + Intégrations Avancées)
Plan: 8 of 8 in current phase
Status: In progress
Last activity: 2026-05-05 — Plan 04-03 terminé : LibreChat v0.8.5 mcpServers streamable-http → mcp-gateway:8081, 3 tools (calendar, drive-read, scraper) chargés, healthy

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
- 2026-05-05 (D-06): OAuth state param changé de team_scope → mapping_id UUID pour supporter N folders/team (T-04-06-SEC-02 accepted)
- 2026-05-05 (04-03): mcpSettings.allowedDomains requis pour débloquer les hosts Docker internes (SSRF protection LibreChat v0.8.5 bloque par défaut)

### Pending Todos

None yet.

### Blockers/Concerns

- **Phase 2 entry gate** : POC 1-jour mem0 vs native doit être réalisé AVANT le planning Phase 2 — résultat détermine le chemin `MemoryProvider` implémentation
- **Phase 3 entry gate** : POC Memori BYODB (Alpha) doit être réalisé avant planning Phase 3 — fallback = LangGraph + LLM structured output
- **OOM Risk Phase 1** : e2-medium (4 GB) est serré avec LibreChat + MongoDB + Qdrant + memory-api. Surveiller `docker stats` total. Ne pas ajouter Langfuse complet (ClickHouse) sans upgrade VM.
- **VM disk 99%** : La VM est à 99% (28G/29G). Le rebuild de drive-sync a échoué par manque d'espace. Nettoyer avant prochain plan avec rebuild de containers. `docker system prune` n'a pas libéré d'espace — probablement des snapshots containerd à nettoyer manuellement ou agrandir le disque.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-05-05
Stopped at: Plan 04-03 complet — LibreChat MCP config deployed. 3 tools loaded (calendar, drive-read, scraper). Commits e4d5098, 3d8fcbb.
Resume file: None
