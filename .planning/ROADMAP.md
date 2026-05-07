# Roadmap: xbrain — AI Cognitive OS

## Overview

xbrain est construit en trois phases qui correspondent aux trois invariants du produit : (1) tout le monde peut chatter en multi-modèle et chaque donnée atterrit dans une mémoire centrale taguée — le socle sans lequel rien d'autre n'a de sens ; (2) la mémoire devient intelligente — agents, versioning, workflow de promotion truth-level, RAG scopé — c'est là que xbrain devient différenciant ; (3) le graphe, l'extraction automatique, Drive sync et les outils MCP ferment la boucle entre le monde extérieur et la mémoire d'équipe. Chaque phase livre un système cohérent et démontrable avant que la suivante commence.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Socle Infra + Frontends + memory-api** - GCP VM, Docker Compose multi-service, LibreChat + Open WebUI branchés sur une memory-api qui enforce le contrat de tagging dès le premier write — **DONE 2026-05-03** (https://x.dejavu.cat + https://ai.dejavu.cat)
- [x] **Phase 2: Mémoire Intelligente + Agents** - VM upgrade, mem0 + MemoryProvider, truth-level promotion workflow, LangGraph agents avec HITL, RAG permission-aware — **DONE 2026-05-04**
- [x] **Phase 3: Graphe + Extraction + Intégrations** - Neo4j, extraction structurée (Claude NER), Drive sync, MCP gateway + 3 premiers outils — **DONE 2026-05-04**
- [x] **Phase 3.5: MCP Gateway Fix + Corrections Phase 3** (INSERTED) - Réécriture mcp-gateway client MCP stateful (Bug 1 critique : tool-call E2E cassé), fix verify-phase3.sh parser (Bug 2 cosmetic) — **DONE 2026-05-05**
- [x] **Phase 4: Consolidation MCP Frontends + Intégrations Avancées** - LibreChat & agent-runtime branchés sur la gateway MCP (MCP-05/06 réellement câblés), fix logging Open WebUI conversations (MEM-04 résiduel), Drive push webhooks + multi-folder mapping, deck-service MCP tool (MCP-07 déféré) — **DONE 2026-05-05**
- [x] **Phase 5: Plateforme Projets Équipe** - Pipeline GitOps (GitHub Actions → Cloud Run + Firebase), Graphiti extraction temporelle, extension Chrome truth-level, auth GitHub Org + Google + account linking, dashboard projets déployés — **DONE 2026-05-06**
- [ ] **Phase 7: CRM + Granola + Task Intelligence** - CRM auto-populé depuis le brain (contacts extraits automatiquement), intégration Granola → mémoire (notes de réunion → faits taguées), task tracking automatique (tout output brain qui implique une action génère une tâche assignée + notification team)
- [x] **Phase 6: Marketing Site + Documentation** - Site marketing statique en anglais (fond blanc, cible startup teams), documentation complète de toutes les features, déploiement Firebase Hosting — **DONE 2026-05-07** (https://xbrain-marketing.web.app)

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

### Phase 4: Consolidation MCP Frontends + Intégrations Avancées
**Goal**: Fermer la boucle multi-frontend des MCP tools (LibreChat + agent-runtime appellent réellement la gateway), supprimer les frictions résiduelles (Open WebUI logging conversations, latence Drive sync), livrer le dernier MCP tool requirement (deck-service), et ouvrir le mapping Drive multi-dossier par équipe — sans ajouter de service lourd.
**Depends on**: Phase 3 + Phase 3.5
**Entry gate**: Phase 3.5 fully shipped (mcp-gateway tool-call E2E PASS) ; verify-phase1/2/3 PASS ; `docker stats` confirme ≥ 2 GB headroom sur la VM e2-standard-2.
**Requirements**: MEM-04 (résiduel Phase 2), MCP-05, MCP-06, MCP-07, INT-02 (élargi push webhooks), INT-03 (élargi multi-folder)
**Success Criteria** (what must be TRUE):
  1. Un user qui chatte dans LibreChat peut écrire "scrape https://example.com" et le LLM appelle réellement `mcp-scraper` via la gateway, retourne le contenu, et l'appel apparaît dans `audit_log` avec `team_scope` + `user_sub` corrects (MCP-05 réel).
  2. Un user qui chatte dans Open WebUI dans une conversation neuve voit le chat correctement loggé dans memory-api (`POST /v1/messages` retourne 201, conversation row créée silencieusement) — vérifié via SELECT direct sur la table `conversations` (MEM-04 résiduel).
  3. Un agent LangGraph peut appeler un MCP tool via le wrapper `mcp_gateway_client.py` et l'output atterrit dans memory-api avec le tagging contract complet (MCP-06 réel).
  4. Un fichier sauvegardé dans Drive devient queryable dans memory-api en moins de 30 secondes en cas nominal (push webhook), avec le polling 5min comme fallback (INT-02 amélioré).
  5. Un admin peut mapper 2+ dossiers Drive distincts à la même équipe avec project_scope distinct par mapping (INT-03 élargi multi-folder) ; un user peut prompter "génère un deck pour X" et obtenir un `.pptx` dans MinIO indexé en mémoire (MCP-07).
**Plans**: 8 plans
Plans:
- [x] 04-01-PLAN.md — memory-api upsert silencieux conversations sur POST /v1/messages
- [ ] 04-02-PLAN.md — mcp-gateway endpoint GET /mcp/aggregate (serveur MCP agrégé pour LibreChat)
- [ ] 04-03-PLAN.md — LibreChat config mcpServers + smoke E2E
- [ ] 04-04-PLAN.md — agent-runtime mcp_gateway_client.py (LangGraph Tool wrapper)
- [ ] 04-05-PLAN.md — Drive push webhooks (migration 0005 + endpoint + channel renewal)
- [ ] 04-06-PLAN.md — Multi-folder Drive mapping (migration 0006 + admin endpoints + scheduler)
- [ ] 04-07-PLAN.md — mcp-deck sidecar (deck_create/deck_update via python-pptx + MinIO)
- [ ] 04-08-PLAN.md — register-mcp-tools.sh update + verify-phase4.sh + UAT

### Phase 5: Plateforme Projets Équipe
**Goal**: Transformer xbrain en plateforme complète pour les équipes : pipeline de déploiement GitOps (GitHub Actions → Cloud Run + Firebase), extraction intelligente temporelle (Graphiti), extension Chrome pour validation truth-level, modèle d'authentification unifié (GitHub Org + Google + account linking), et dashboard des projets déployés.
**Depends on**: Phase 4
**Entry gate**: Phase 4 fully shipped (verify-phase4.sh PASS 8/8) ; VM e2-standard-2 avec headroom suffisant (graphiti-service ajoute ~512m).
**Requirements**: TEAM-01, TEAM-03, AUTH-01, AUTH-03, MEM-06, MEM-07, MEM-08, CHAT-08
**Success Criteria** (what must be TRUE):
  1. `GET http://graphiti-service:8300/v1/healthz` retourne `{"status": "ok", "graphiti": true}` et `POST /v1/ingest` retourne 202 en moins de 500ms.
  2. LibreChat affiche deux boutons de connexion (Google + GitHub) — un user peut se connecter avec son compte GitHub de l'org `your-github-org`.
  3. La table `users` PostgreSQL a les colonnes `github_username` et `github_id` (migration 0007 appliquée).
  4. Un repo GitHub avec `brain.yaml` peut déclencher le workflow `deploy-cloudrun.yml` ou `deploy-firebase.yml` et indexer son contenu dans `api.dejavu.cat/v1/memory` via `brain-index.sh`.
  5. L'extension Chrome (Manifest V3) peut envoyer du contenu sélectionné sur une page web vers `api.dejavu.cat/v1/memory` avec le truth_level choisi par l'utilisateur — memory-api retourne 201.
  6. `projects-dashboard/public/index.html` est généré par `generate_dashboard.py` et déployé sur Firebase Hosting (ou testable localement).
  7. `bash infrastructure/scripts/verify-phase5.sh` retourne `PASS: 8 / 8`.
**Plans**: 7 plans
Plans:
- [ ] 05-01-PLAN.md — graphiti-service container (FastAPI wrapper graphiti-core, port 8300, Neo4j backend)
- [ ] 05-02-PLAN.md — GitHub OAuth LibreChat + migration 0007 github_username + membership middleware
- [ ] 05-03-PLAN.md — brain.yaml schema + GitHub Actions templates Cloud Run / Firebase + POST /v1/admin/projects
- [ ] 05-04-PLAN.md — Extension Chrome MV3 (web clipper, auth launchWebAuthFlow, CORS memory-api)
- [ ] 05-05-PLAN.md — projects.dejavu.cat dashboard statique (generate_dashboard.py + Firebase deploy)
- [ ] 05-06-PLAN.md — nginx 30-projects.conf + Cloudflare Access runbook + .env.example Phase 5
- [ ] 05-07-PLAN.md — register-mcp-tools.sh vérification + verify-phase5.sh (8 tests)

### Phase 6: Marketing Site + Documentation
**Goal**: Livrer un site marketing statique en anglais (fond blanc, cible startup teams implémentant l'AI) + documentation complète de toutes les features Phase 1-5, déployés en ligne via Firebase Hosting.
**Depends on**: Phase 5
**Entry gate**: Phase 5 fully shipped.
**Requirements**: (non-requirements phase — pure content/docs delivery)
**Success Criteria** (what must be TRUE):
  1. `https://xbrain-marketing.web.app` accessible (HTTP 200) avec la landing page complète (7 sections : Hero, Problem, Solution, Features, How it works, Technical overview, Footer)
  2. Les 13 pages de documentation sont accessibles via leurs URLs Firebase et la sidebar 14 liens est fonctionnelle sur toutes les pages
  3. `docs/memory.html` contient les 5 truth levels et les 7 champs de tagging avec exemples curl
  4. `docs/api-reference.html` documente >= 15 endpoints memory-api avec exemples request/response
  5. `docs/deployment.html` couvre les Phases 1 à 5 avec les commandes exactes (docker compose, firebase, gcloud)
  6. Le design est cohérent : fond blanc, accent violet #7C3AED, TailwindCSS CDN, aucun build step
**Plans**: 8 plans
Plans:
- [x] 06-01-PLAN.md — Firebase config + CSS foundation (style.css + docs.css)
- [ ] 06-02-PLAN.md — Landing page index.html (7 sections, TailwindCSS CDN)
- [ ] 06-03-PLAN.md — Docs home + Architecture + Memory System pages
- [ ] 06-04-PLAN.md — Teams + Chat + MCP Tools pages
- [ ] 06-05-PLAN.md — Drive Sync + Chrome Extension + GitHub Auth pages
- [ ] 06-06-PLAN.md — Agents + Graphiti + API Reference pages
- [ ] 06-07-PLAN.md — Deployment + Configuration pages
- [ ] 06-08-PLAN.md — Firebase deploy + checkpoint human verification

### Phase 7: CRM + Granola + Task Intelligence
**Goal**: Le brain devient actif. Une équipe peut (1) consulter un CRM populé automatiquement depuis tout ce qui passe par le brain (chats, agents, meetings Granola), (2) voir chaque réunion Granola ingérée comme source de mémoire de premier ordre (résumé + participants + actions + décisions), et (3) gérer un backlog de tâches auto-générées depuis les action items Granola et les outputs agents — chaque tâche assignée à un contact CRM déclenche une notification email.
**Depends on**: Phase 6
**Entry gate**: Phases 1-6 SHIPPED. VM disque < 80% (rebuild containers possible). Granola API key (Business/Enterprise) disponible pour au moins une team — sinon System 2 (granola-sync) reste enregistrable mais ne polle rien.
**Requirements**: D1, D2, D3, D4, D5, D6 (decisions Phase 7 — ce phase est hors scope v1 requirements AUTH/MEM/etc.)
**Success Criteria** (what must be TRUE):
  1. Un admin peut créer une team avec `plan='team'` et un user de cette team peut faire CRUD sur `/v1/crm/contacts` ; un user d'une team `starter` reçoit 403
  2. POST `/v1/admin/granola-integration` enregistre une API key Granola Fernet-chiffrée pour une team ; le container `xbrain-granola-sync` poll Granola et ingère chaque nouvelle note via `/v1/integrations/granola/ingest` (1 memory_item résumé + N contacts + M tasks créées)
  3. POST `/v1/memory/upsert` avec un content contenant des mentions de personnes déclenche en background l'extraction Claude → upsert dans `contacts` (fail-soft)
  4. Un memory_item avec `metadata.contains_action=true` (ou regex TODO/à faire/...) déclenche en background la création d'une tâche dans `tasks` avec `source='agent'|'chat'`, `source_ref=memory_item.id`
  5. Une tâche assignée à un contact avec email déclenche un email via aiosmtplib (skip silencieux si `SMTP_HOST` vide)
  6. Le dashboard `/tasks.html` (Firebase) liste les tâches d'une team avec filtres status/projet et polling 30s ; bouton "Mark done" → PATCH 200
  7. `bash infrastructure/scripts/verify-phase7.sh` retourne `PASS: 8 / 8`
**Plans**: 9 plans
Plans:
- [x] 07-01-PLAN.md — Migrations 0008 (teams.plan) + 0009 (contacts + granola_integrations) + 0010 (tasks, created_by NULLABLE)
- [ ] 07-02-PLAN.md — require_paid_tier + _user_id_from_principal dans deps.py + router CRM /v1/crm/contacts (CRUD + audit)
- [ ] 07-03-PLAN.md — Router /v1/tasks (CRUD + filtres + polling since)
- [ ] 07-04-PLAN.md — Router granola_integration (admin Fernet + ingest atomic memory_item+contacts+tasks, dedup note_id, created_by=NULL)
- [ ] 07-05-PLAN.md — Squelette apps/granola-sync/ (Dockerfile, pyproject, main, config, memory_client, extractor — pas de poller, pas de docker-compose)
- [ ] 07-06-PLAN.md — Background tasks memory.py (extract contacts + auto-task via async_session_factory) + service notifications email
- [ ] 07-07-PLAN.md — Dashboard tasks.html + Nginx routes + verify-phase7.sh + .env.example
- [ ] 07-08-PLAN.md — Boucle polling granola_poller.py + service granola-sync dans docker-compose.yml (split de 07-05)
- [ ] 07-09-PLAN.md — D5 Trigger 3 : librechat-bridge task_intent_detector.py + hook mongo_watcher (chat → contains_action → task auto via 07-06)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 3.5 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Socle Infra + Frontends + memory-api | 6/6 | ✅ Complete | 2026-05-03 |
| 2. Mémoire Intelligente + Agents | 9/9 | ✅ Complete | 2026-05-04 |
| 3. Graphe + Extraction + Intégrations | 12/12 | ✅ Complete | 2026-05-04 |
| 3.5. MCP Gateway Fix + Corrections Phase 3 (INSERTED) | 2/2 | ✅ Complete | 2026-05-05 |
| 4. Consolidation MCP Frontends + Intégrations Avancées | 8/8 | ✅ Complete | 2026-05-05 |
| 5. Plateforme Projets Équipe | 7/7 | ✅ Complete | 2026-05-06 |
| 6. Marketing Site + Documentation | 8/8 | ✅ Complete | 2026-05-07 |
| 7. CRM + Granola + Task Intelligence | 0/7 | 🟡 Planned | — |

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
