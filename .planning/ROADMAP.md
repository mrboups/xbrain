# Roadmap: xbrain — AI Cognitive OS

## Overview

xbrain est construit en trois phases qui correspondent aux trois invariants du produit : (1) tout le monde peut chatter en multi-modèle et chaque donnée atterrit dans une mémoire centrale taguée — le socle sans lequel rien d'autre n'a de sens ; (2) la mémoire devient intelligente — agents, versioning, workflow de promotion truth-level, RAG scopé — c'est là que xbrain devient différenciant ; (3) le graphe, l'extraction automatique, Drive sync et les outils MCP ferment la boucle entre le monde extérieur et la mémoire d'équipe. Chaque phase livre un système cohérent et démontrable avant que la suivante commence.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Socle Infra + Frontends + memory-api** - GCP VM, Docker Compose multi-service, LibreChat + Open WebUI branchés sur une memory-api qui enforce le contrat de tagging dès le premier write — **DONE 2026-05-03** (https://x.dejavu.cat + https://ai.dejavu.cat)
- [ ] **Phase 2: Mémoire Intelligente + Agents** - VM upgrade, mem0 + MemoryProvider, truth-level promotion workflow, LangGraph agents avec HITL, RAG permission-aware
- [ ] **Phase 3: Graphe + Extraction + Intégrations** - Neo4j, extraction structurée (Claude NER), Drive sync, MCP gateway + 3 premiers outils

## Phase Details

### Phase 1: Socle Infra + Frontends + memory-api
**Goal**: Un membre de l'équipe peut ouvrir LibreChat, chatter avec au moins deux modèles (Claude + GPT), et cette conversation est stockée dans memory-api avec les 7 champs de tagging. Team A ne peut pas voir les conversations de Team B.
**Depends on**: Nothing (first phase)
**Entry gate**: Compte GCP `team@grooveos.app`, projet `xbrain-prod` créé, VM `e2-medium` (4 GB) provisionnée, Docker installé, firewall configuré.
**Requirements**: AUTH-01, AUTH-02, AUTH-03, AUTH-04, AUTH-05, AUTH-06, TEAM-01, TEAM-02, TEAM-03, TEAM-04, TEAM-05, TEAM-06, MEM-01, MEM-02, MEM-03, MEM-04, MEM-05, CHAT-01, CHAT-02, CHAT-03, CHAT-04, CHAT-05, CHAT-08, SRCH-01, SRCH-02, OBS-01, OBS-04, ADMIN-01, ADMIN-02, ADMIN-03, ADMIN-04, ADMIN-05, ADMIN-06
**Success Criteria** (what must be TRUE):
  1. Un membre de l'équipe peut se connecter via Google SSO depuis LibreChat et Open WebUI — les deux frontends reconnaissent la même identité (même `source_user_id` OIDC `sub` dans memory-api)
  2. Un utilisateur peut lancer une conversation dans LibreChat avec Claude, puis une autre avec GPT ou Grok, et les deux conversations sont retrievables depuis memory-api avec tous les 7 champs de tagging présents (`team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level=EPHEMERAL`, `source`, `validation_status`) — un write sans l'un de ces champs retourne 422
  3. Un admin peut créer une équipe, inviter un membre, et les données de Team A sont invisibles à un utilisateur authentifié de Team B — vérifié via query directe à memory-api et non seulement via l'UI
  4. L'intégralité du stack (LibreChat + Open WebUI + PostgreSQL + Qdrant + memory-api) démarre avec un seul `docker compose up` depuis le repo et les healthchecks passent
  5. Un test de restore depuis backup a été exécuté sur un environnement clean et tous les services redémarrent avec les données correctes (gate obligatoire avant de déclarer Phase 1 terminée)
**Plans**: TBD
**UI hint**: yes

### Phase 2: Mémoire Intelligente + Agents
**Goal**: La mémoire d'équipe devient intelligente — les faits sont versionnés, les promotions truth-level passent par un workflow humain, les agents ingèrent et valident via LangGraph avec HITL, et chaque réponse de chat est enrichie avec les faits CANONICAL de l'équipe.
**Depends on**: Phase 1
**Entry gate**: VM upgradée vers `e2-standard-2` (8 GB, ~38-49€/mo) AVANT d'ajouter le moindre service Phase 2. POC 1-jour mem0 vs native réalisé et résultat documenté. `MemoryProvider` interface créée dans `/packages/memory-models`.
**Requirements**: MEM-06, MEM-07, MEM-08, MEM-09, MEM-10, CHAT-06, CHAT-07, SRCH-03, SRCH-04, TRUTH-01, TRUTH-02, TRUTH-03, TRUTH-04, TRUTH-05, TRUTH-06, TRUTH-07, TRUTH-08, TRUTH-09, AGENT-01, AGENT-02, AGENT-03, AGENT-04, AGENT-05, AGENT-06, AGENT-07, OBS-02, OBS-03, OBS-05
**Success Criteria** (what must be TRUE):
  1. Un membre peut proposer la promotion d'un fait de `WORKING` à `VALIDATED` depuis Open WebUI, un admin reçoit la demande, approuve, et l'événement apparaît dans le log immuable avec promoteur, approbateur, date et justification — un `PATCH /facts/{id}` direct sur `truth_level` retourne 405
  2. Un agent LangGraph peut être mis en pause à un checkpoint d'approbation, un humain valide dans Open WebUI, et l'agent reprend à partir de l'état checkpointé sans perte de contexte
  3. Une conversation dans LibreChat avec Claude inclut automatiquement dans son system prompt les faits `CANONICAL` de l'équipe/projet pertinents, récupérés depuis Qdrant avec filtre `team_scope` + `truth_level >= CANONICAL`
  4. L'agent d'ingestion de documents convertit automatiquement un PDF uploadé en faits structurés avec provenance back-linkée à la conversation/upload source, déposés à `EPHEMERAL` ou `WORKING` seulement — aucun chemin ne permet d'atterrir directement à `VALIDATED+` via import
  5. Un admin voit le dashboard de coût par équipe (token spend, breakdown modèle, contribution agent-vs-human) dans Langfuse
**Plans**: TBD
**UI hint**: yes

### Phase 3: Graphe + Extraction + Intégrations
**Goal**: La mémoire d'équipe est connectée au monde extérieur — Drive sync incrémental, outils internes en MCP, et le graphe Neo4j rend les relations entre entités et le lineage des faits queryables.
**Depends on**: Phase 2
**Entry gate**: Décision VM Phase 3 prise : `e2-standard-4` (16 GB, ~98€/mo) OU Langfuse migré sur VM séparée `e2-small` (~62€/mo total) selon charge observée en fin de Phase 2. POC Memori BYODB réalisé avant planning Phase 3.
**Requirements**: SRCH-05, MCP-01, MCP-02, MCP-03, MCP-04, MCP-05, MCP-06, MCP-07, INT-01, INT-02, INT-03, INT-04
**Success Criteria** (what must be TRUE):
  1. Un utilisateur peut demander depuis LibreChat "qu'est-ce qui dépend de l'entité X ?" et obtenir une réponse graph-traversal depuis Neo4j via memory-api — sans requête Cypher directe ni accès DB
  2. Un dossier Google Drive mappé à une équipe est synchronisé incrementalement dans memory-api (seuls les fichiers modifiés sont re-traités), chaque document indexé avec le contrat de tagging complet, déposé à `WORKING`
  3. Un développeur peut enregistrer un nouveau service MCP (scraper, calendar, deck-service) via le gateway sans modifier l'infra centrale, et chaque appel outil depuis LibreChat ou un agent inclut le `team_scope` et `user_id` injectés par le gateway
  4. Les résumés produits par un agent peuvent être écrits en retour dans un document Drive avec opt-in utilisateur explicite, et cette action est tracée dans l'audit log memory-api
**Plans**: 12 plans
Plans:
- [ ] 03-01-PLAN.md — Neo4j compose service + volume + .env.example
- [ ] 03-02-PLAN.md — Alembic migration 0004 (neo4j_outbox + team_drive_mappings + UNIQUE source)
- [ ] 03-03-PLAN.md — Google OAuth scope upgrade runbook (docs/google-oauth-scope-upgrade.md)
- [ ] 03-04-PLAN.md — Neo4j async driver + outbox background worker in memory-api
- [ ] 03-05-PLAN.md — /v1/graph/* endpoints + extract_facts NER extension
- [ ] 03-06-PLAN.md — mcp-gateway service (FastAPI proxy, DB registry, audit)
- [ ] 03-07-PLAN.md — mcp-scraper sidecar (FastMCP, URL → text, port 8100)
- [ ] 03-08-PLAN.md — mcp-drive-read sidecar (FastMCP, Drive read/write, port 8101)
- [ ] 03-09-PLAN.md — mcp-calendar sidecar (FastMCP, Calendar read-only, port 8102)
- [ ] 03-10-PLAN.md — Drive admin endpoint in memory-api (OAuth flow, Fernet encrypt)
- [ ] 03-11-PLAN.md — drive-sync service (incremental poll, ingestion delegate, soft-archive)
- [ ] 03-12-PLAN.md — MCP tool registration script + E2E validation

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Socle Infra + Frontends + memory-api | 6/6 | ✅ Complete | 2026-05-03 |
| 2. Mémoire Intelligente + Agents | 0/TBD | Not started | - |
| 3. Graphe + Extraction + Intégrations | 0/12 | Not started | - |

---

## Coverage Map (73 requirements → 3 phases)

| Category | Phase 1 | Phase 2 | Phase 3 |
|----------|---------|---------|---------|
| AUTH (6) | AUTH-01..06 | — | — |
| TEAM (6) | TEAM-01..06 | — | — |
| MEM (10) | MEM-01..05 | MEM-06..10 | — |
| CHAT (8) | CHAT-01..05, CHAT-08 | CHAT-06, CHAT-07 | — |
| SRCH (5) | SRCH-01, SRCH-02 | SRCH-03, SRCH-04 | SRCH-05 |
| TRUTH (9) | — | TRUTH-01..09 | — |
| AGENT (7) | — | AGENT-01..07 | — |
| MCP (7) | — | — | MCP-01..07 |
| INT (4) | — | — | INT-01..04 |
| OBS (5) | OBS-01, OBS-04 | OBS-02, OBS-03, OBS-05 | — |
| ADMIN (6) | ADMIN-01..06 | — | — |
| **Total** | **33** | **28** | **12** |

**Coverage: 73/73 requirements mapped. No orphans.**

*Note: REQUIREMENTS.md header states "65 total" but the file contains 73 v1 REQ-IDs as counted by category. Traceability table below maps all 73.*

---
*Roadmap created: 2026-05-02*
*Granularity: coarse (3 phases)*
*VM strategy: e2-medium (P1) → e2-standard-2 (P2 entry gate) → e2-standard-4 or split Langfuse VM (P3 entry gate)*
