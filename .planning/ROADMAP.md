# Roadmap: xbrain — AI Cognitive OS

## Overview

xbrain est construit en trois phases qui correspondent aux trois invariants du produit : (1) tout le monde peut chatter en multi-modèle et chaque donnée atterrit dans une mémoire centrale taguée — le socle sans lequel rien d'autre n'a de sens ; (2) la mémoire devient intelligente — agents, versioning, workflow de promotion truth-level, RAG scopé — c'est là que xbrain devient différenciant ; (3) le graphe, l'extraction automatique, Drive sync et les outils MCP ferment la boucle entre le monde extérieur et la mémoire d'équipe. Chaque phase livre un système cohérent et démontrable avant que la suivante commence.

## Phases

**Phase Numbering:**

- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Socle Infra + Frontends + memory-api** - GCP VM, Docker Compose multi-service, LibreChat + Open WebUI branchés sur une memory-api qui enforce le contrat de tagging dès le premier write — **DONE 2026-05-03** (https://chat.grooveos.app + https://adm.grooveos.app)
- [x] **Phase 2: Mémoire Intelligente + Agents** - VM upgrade, mem0 + MemoryProvider, truth-level promotion workflow, LangGraph agents avec HITL, RAG permission-aware — **DONE 2026-05-04**
- [x] **Phase 3: Graphe + Extraction + Intégrations** - Neo4j, extraction structurée (Claude NER), Drive sync, MCP gateway + 3 premiers outils — **DONE 2026-05-04**
- [x] **Phase 3.5: MCP Gateway Fix + Corrections Phase 3** (INSERTED) - Réécriture mcp-gateway client MCP stateful (Bug 1 critique : tool-call E2E cassé), fix verify-phase3.sh parser (Bug 2 cosmetic) — **DONE 2026-05-05**
- [x] **Phase 4: Consolidation MCP Frontends + Intégrations Avancées** - LibreChat & agent-runtime branchés sur la gateway MCP (MCP-05/06 réellement câblés), fix logging Open WebUI conversations (MEM-04 résiduel), Drive push webhooks + multi-folder mapping, deck-service MCP tool (MCP-07 déféré) — **DONE 2026-05-05**
- [x] **Phase 5: Plateforme Projets Équipe** - Pipeline GitOps (GitHub Actions → Cloud Run + Firebase), Graphiti extraction temporelle, extension Chrome truth-level, auth GitHub Org + Google + account linking, dashboard projets déployés — **DONE 2026-05-06**
- [x] **Phase 6: Marketing Site + Documentation** - Site marketing statique en anglais (fond blanc, cible startup teams), documentation complète de toutes les features, déploiement Firebase Hosting — **DONE 2026-05-07** (https://grooveos.app)
- [x] **Phase 7: CRM + Granola + Task Intelligence** - CRM auto-populé depuis le brain (contacts extraits automatiquement), intégration Granola → mémoire (notes de réunion → faits taguées), task tracking automatique (tout output brain qui implique une action génère une tâche assignée + notification team) — **DONE 2026-05-07**
- [x] **Phase 8: Granola Per-User + Universal Extraction Pipeline + Platform Agents** - Clé API Granola per-user (saisie manuelle onboarding, Fernet chiffré), pipeline extraction universel (LibreChat + Chrome ext + Granola → CRM + tasks), registry agent_definitions éditable par les admins avec agent meeting-recap seedé — **DONE 2026-05-09** (PASS: 7/7 verify-phase8.sh)
- [x] **Phase 9: Session Bridge — Pro/Max Routing via Chrome Extension** - Les users xbrain consomment leur propre quota Claude Pro/Max au lieu de la clé API team. Nouveau microservice `session-bridge` (port 8105, OpenAI-compat ↔ WebSocket router), extension xbrain étendue (WebSocket persistant + fetch credentialed claude.ai), nouveau endpoint LibreChat "Claude (mon abonnement)", vhost nginx bridge.grooveos.app, table `user_external_sessions`. **Scope: Claude only — ChatGPT Plus déféré Phase 10.** — **LIVE 2026-05-12** (6/6 plans shipped, verify-phase9.sh PASS 6/6 with 2 acceptable SKIP on VM 2026-05-17)

- [x] **Phase 14: Portability Foundation** - De-hardcode `grooveos.app` / `aibrussels` / `default` team_scope into config; slim fillable OSS `.env.example`
 (completed 2026-07-12)
- [x] **Phase 15: Edition Mechanics** - Compose `profiles:` + `EDITION` flag + router gating so one codebase serves oss/saas. (The Ed25519 license + paid `pro` tier was DROPPED by locked decision Q6 — no product feature is paywalled; only the hosted control plane stays closed.) (completed 2026-07-13)
- [x] **Phase 18: Local Auth (OSS default)** - Native email/password sign-in in memory-api, so a self-hoster needs NO external OAuth setup. **Runs BEFORE Phase 16** — execution order is 14 → 15 → 18 → 16 → 17; it is numbered 18 only to avoid renumbering 16/17.
 (completed 2026-07-14)
- [x] **Phase 19: Local Embeddings (OSS default)** - In-container keyless embedder so semantic retrieval works with NO OpenAI key (OpenAI stays selectable). **Runs BEFORE Phase 16** — order 14 → 15 → 18 → 19 → 16 → 20 → 17. Today `embedders.py` hard-raises without `OPENAI_API_KEY`, so a zero-key OSS install cannot do the "retrieved" half of Phase 16's own SC#3; locked decision Q3 wanted local-by-default. Numbered 19 to avoid renumbering 16/17. (completed 2026-07-18)
- [x] **Phase 16: OSS Light Packaging** - Light compose + install docs + clean-install test on a fresh VM. The stack boots and the brain works end-to-end (chat via the existing surfaces + ChatGPT-web connector + doc analysis + retrieval + clip) with zero external keys. **The standalone web chat frontend moved to Phase 20** (it is a phase-sized build, not a packaging criterion). (completed 2026-07-18)
- [ ] **Phase 20: Standalone Web Chat** - Extract the team group-chat from the Chrome extension (~1125-line popup.js) into a browser-extension-independent web app — THE product per Q4. Wires in Phase 18 auth + clip-to-memory. **Runs AFTER Phase 16** (needs the packaged stack to serve it) and before 17. Numbered 20 to avoid renumbering.
- [ ] **Phase 17: CI Lockstep** - One pipeline builds/tests both profiles, publishes the OSS release and deploys SaaS from the same commit

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
  2. LibreChat affiche deux boutons de connexion (Google + GitHub) — un user peut se connecter avec son compte GitHub de l'org configurée (`GITHUB_ORG`).
  3. La table `users` PostgreSQL a les colonnes `github_username` et `github_id` (migration 0007 appliquée).
  4. Un repo GitHub avec `brain.yaml` peut déclencher le workflow `deploy-cloudrun.yml` ou `deploy-firebase.yml` et indexer son contenu dans `api.grooveos.app/v1/memory` via `brain-index.sh`.
  5. L'extension Chrome (Manifest V3) peut envoyer du contenu sélectionné sur une page web vers `api.grooveos.app/v1/memory` avec le truth_level choisi par l'utilisateur — memory-api retourne 201.
  6. `projects-dashboard/public/index.html` est généré par `generate_dashboard.py` et déployé sur Firebase Hosting (ou testable localement).
  7. `bash infrastructure/scripts/verify-phase5.sh` retourne `PASS: 8 / 8`.

**Plans**: 7 plans
Plans:

- [x] 05-01-PLAN.md — graphiti-service container (FastAPI wrapper graphiti-core, port 8300, Neo4j backend)
- [x] 05-02-PLAN.md — GitHub OAuth LibreChat + migration 0007 github_username + membership middleware
- [x] 05-03-PLAN.md — brain.yaml schema + GitHub Actions templates Cloud Run / Firebase + POST /v1/admin/projects
- [x] 05-04-PLAN.md — Extension Chrome MV3 (web clipper, auth launchWebAuthFlow, CORS memory-api)
- [x] 05-05-PLAN.md — projects.grooveos.app dashboard statique (generate_dashboard.py + Firebase deploy)
- [x] 05-06-PLAN.md — nginx 30-projects.conf + Cloudflare Access runbook + .env.example Phase 5
- [x] 05-07-PLAN.md — register-mcp-tools.sh vérification + verify-phase5.sh (8 tests)

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
- [x] 06-02-PLAN.md — Landing page index.html (7 sections, TailwindCSS CDN)
- [x] 06-03-PLAN.md — Docs home + Architecture + Memory System pages
- [x] 06-04-PLAN.md — Teams + Chat + MCP Tools pages
- [x] 06-05-PLAN.md — Drive Sync + Chrome Extension + GitHub Auth pages
- [x] 06-06-PLAN.md — Agents + Graphiti + API Reference pages
- [x] 06-07-PLAN.md — Deployment + Configuration pages
- [x] 06-08-PLAN.md — Firebase deploy + checkpoint human verification

### Phase 8: Granola Per-User + Universal Extraction Pipeline + Platform Agents

**Goal**: Chaque utilisateur entre sa clé API Granola manuellement dans l'onboarding (Fernet chiffré, stocké dans `granola_user_connections`, révocable), toutes les sorties applicatives (LibreChat, Chrome extension, Granola meetings) alimentent automatiquement le CRM et les tâches, et les admins peuvent créer/éditer des agents de plateforme configurables (registry `agent_definitions`) accessibles depuis la plateforme.
**Depends on**: Phase 7
**Entry gate**: Phase 7 SHIPPED. Tables `contacts`, `tasks`, `granola_integrations` présentes.
**Requirements**: (phase post-v1 — nouvelles capacités hors scope 73 REQ-IDs v1)
**Success Criteria** (what must be TRUE):

  1. Un utilisateur entre sa clé API Granola dans l'étape optionnelle de l'onboarding → clé stockée chiffrée Fernet dans `granola_user_connections` (liée à `user_id`) via `POST /v1/me/granola-key` ; la connexion est visible dans son profil (GET retourne {connected: true}) et révocable (DELETE /v1/me/granola-key)
  2. Après saisie de la clé, granola-sync poll per-user et ingère les meetings → contacts + tasks (même pipeline Phase 7 mais déclenché par `user_id`, non team-API-key)
  3. Tout output LibreChat (message assistant) contenant des patterns contact (nom/email/entreprise) déclenche l'extraction contact → CRM (pipeline robuste, fail-soft, idempotent)
  4. Tout clip Chrome extension → extraction contact + task si action item détecté (via background worker)
  5. Table `agent_definitions` : admin peut CRUD via `POST/GET/PATCH/DELETE /v1/admin/agents` ; chaque agent a `name`, `description`, `system_prompt`, `model`, `tools_json`, `enabled`, `created_by` ; l'agent `meeting-recap` est seedé au déploiement
  6. L'agent `meeting-recap` seedé peut être invoqué via `POST /v1/agents/{id}/invoke` avec un transcript et retourne un recap structuré (format docs/meeting-recap.md)
  7. Dashboard GitHub dynamique : un user authentifié voit ses repos privés + ceux de son org GitHub liée — le dashboard appelle `GET /v1/github/repos` (memory-api) qui utilise le `github_access_token` stocké en MongoDB ; le scope OAuth GitHub inclut `repo` ; les repos privés sont visibles uniquement par le user concerné
  8. `bash infrastructure/scripts/verify-phase8.sh` retourne `PASS: 7 / 7`

**Plans**: 8 plans
Plans:

- [x] 08-01-PLAN.md — Migration Alembic 0012 (granola_user_connections + agent_definitions + seed meeting-recap) + alembic upgrade head
- [x] 08-02-PLAN.md — memory-api: routes agents.py (CRUD admin /v1/admin/agents + /v1/agents/{id}/invoke synchrone Anthropic)
- [x] 08-03-PLAN.md — memory-api: routes /v1/me/granola-key (POST/GET/DELETE) + Fernet encryption
- [x] 08-04-PLAN.md — granola-sync: deuxième boucle per-user dans granola_poller.py + auto-trigger meeting-recap (D5)
- [x] 08-05-PLAN.md — GitHub repos dynamiques: /api/xbrain/github-repos (LibreChat) + /v1/github/repos proxy (memory-api)
- [x] 08-06-PLAN.md — librechat-bridge: contact_extractor.py + hook mongo_watcher (extraction CRM depuis messages)
- [x] 08-07-PLAN.md — onboarding.js patch: étape 4 optionnelle Granola API key
- [x] 08-08-PLAN.md — verify-phase8.sh (7 tests) + .env.example section Phase 8

### Phase 9: Session Bridge — Pro/Max Routing via Chrome Extension

**Goal**: Un user xbrain qui possède un abonnement Claude Pro/Max peut consommer son propre quota d'inférence depuis LibreChat, au lieu de la clé API team. Quand son extension xbrain est active et qu'il est logué sur claude.ai dans son navigateur, les requêtes chat sont routées via WebSocket vers son browser, qui fait un fetch credentialed contre l'API interne claude.ai et streame la réponse de retour. ChatGPT Plus routing est explicitement déféré (Phase 10).
**Depends on**: Phase 8
**Entry gate**: Extension xbrain v1 actuelle stable (Phase 4 + Phase 8 team discovery), claude.ai accessible depuis browser des testeurs, sous-domaine `bridge.grooveos.app` disponible côté Cloudflare DNS, table `user_external_credentials` ou équivalent inexistante (à créer dans cette phase).
**Requirements**: (phase post-v1 — nouvelles capacités hors scope 73 REQ-IDs v1) — SESSION-01 à SESSION-06 (extension WebSocket bridge / claude.ai routing / per-user session tracking / graceful fallback / Sonnet via routed chat / SSE streaming translation)
**Success Criteria** (what must be TRUE):

  1. Un user xbrain logué sur claude.ai dans son browser, extension xbrain v2 installée, peut sélectionner "Claude (mon abonnement)" dans LibreChat et envoyer un message — la réponse arrive en streaming et le quota Pro/Max de l'user est décrémenté (visible sur claude.ai/settings/usage), pas la facture API team
  2. La même requête sans extension active OU sans onglet claude.ai actif retourne un message d'erreur explicite "Install xbrain extension and login to claude.ai" — aucun fallback silencieux vers la clé API team (l'user voit la friction)
  3. Le microservice `session-bridge` tourne en container docker-compose, expose `/v1/chat/completions` (OpenAI-compat sur HTTP) + `/ws/{user_sub}` (WebSocket persistante pour extension), accessible publiquement via nginx vhost `bridge.grooveos.app` (TLS via Cloudflare)
  4. Le popup de l'extension xbrain affiche le statut de la session ("Claude session: 🟢 Active / 🔴 None") avec l'email claude.ai loggé et permet refresh/disconnect ; la table `user_external_sessions` track les extensions connectées avec last_seen_at et metadata JSONB
  5. Le format de réponse claude.ai interne (SSE event-style) est correctement translaté en SSE OpenAI-compat dans `session-bridge` pour que LibreChat consomme la réponse sans patch
  6. `bash infrastructure/scripts/verify-phase9.sh` retourne `PASS: N / N` (compte de tests TBD au planning) — au minimum : (a) session-bridge healthcheck, (b) nginx vhost répond 200 sur `/v1/chat/completions` avec body d'auth attendu, (c) extension WebSocket connecte et echo bidirectionnel marche, (d) end-to-end LibreChat → session-bridge → extension → claude.ai mock retourne un texte

**Plans**: 6 plans
Plans:

- [x] 09-01-PLAN.md — session-bridge FastAPI skeleton (pool + auth + 401/503 chat stub + docker-compose entry)
- [x] 09-02-PLAN.md — claude.ai client + SSE translator (live DevTools capture + claude_ai_client.js + translate_sse.js + node tests)
- [x] 09-03-PLAN.md — Chrome extension v1.1.0 — WebSocket persistent + chrome.alarms watchdog + handleClaude dispatcher
- [x] 09-04-PLAN.md — Cloudflare DNS + nginx 50-bridge.conf + Alembic 0014 + memory-api /v1/me/external-sessions GET/DELETE
- [x] 09-05-PLAN.md — librechat.yaml "Claude (mon abonnement)" endpoint + extension popup Sessions section
- [x] 09-06-PLAN.md — verify-phase9.sh (8 tests) + .env.example + docs/sessions.html + 09-UAT.md

**UI hint**: yes (extension popup + LibreChat endpoint dropdown + settings page session status)

### Phase 10: GitHub-Primary Auth + Org-Driven Team Membership

**Goal**: GitHub devient l'identité principale de xbrain. Un user signe avec GitHub OAuth, ses team memberships sont auto-créées depuis les org GitHub matchant `teams.github_org`, et un admin peut bloquer un user spécifique même s'il est membre de l'org. Google reste un lien secondaire (Drive/Calendar/email lookup), pas l'auth principale. Les user rows orphelines (signé en Google puis GitHub, ou inverse) sont auto-mergées sur link/sign-in.
**Depends on**: Phase 5 (team platform), Phase 7 (SMTP notifications déjà wired dans `notifications.py`)
**Entry gate**: Auth Google-primary fonctionnelle (current state). Endpoint `POST /v1/me/link-github-with-code` shipped. SMTP fail-soft helper dispo dans `app/services/notifications.py`. Migration `0007_github_users` appliquée (colonnes `users.github_id`, `users.github_username`). Table `team_members` avec `joined_at`.
**Requirements**: (phase post-v1 — nouvelles capacités hors scope 73 REQ-IDs v1) — GHA-01 à GHA-08

- GHA-01: GitHub OAuth code exchange endpoint qui mint un `xbt_` directement (pas de Google requis en amont)
- GHA-02: Auto-grant team membership au 1er sign-in via GitHub org match (`teams.github_org` = un des orgs GitHub du user)
- GHA-03: Block/unblock d'un member existant (admin endpoint), persiste même si re-sign via org match
- GHA-04: Pré-block d'un GitHub login pas encore signé (table `team_org_blocks`)
- GHA-05: Email admin sur auto-grant (via `notifications.py` fail-soft)
- GHA-06: Auto-merge des user rows orphelines (Google ↔ GitHub) sur link/sign-in, migration `team_members` + soft-delete
- GHA-07: Sign-in GitHub primaire dans `chrome-extension/popup.html`
- GHA-08: Sign-in GitHub primaire dans `app-site/account/teams/index.html`

**Success Criteria** (what must be TRUE):

  1. Un user sans compte Google peut signer dans l'extension popup ET sur `grooveos.app/account/teams/` en cliquant "Sign in with GitHub" → un `xbt_` token est minté, stocké en `chrome.storage.local` ou `localStorage`, et il voit ses teams immédiatement via `/v1/teams/my-teams`
  2. Quand un user GitHub appartient à org `acme-corp` (vérifié via GitHub `/user/orgs`) et qu'une team xbrain existe avec `github_org='acme-corp'`, son 1er sign-in déclenche `INSERT team_members(role='member')` automatiquement — sauf si une ligne `team_org_blocks(team_id, github_login)` existe pour lui
  3. L'admin d'une team peut bloquer un member existant via `POST /v1/teams/{id}/members/{user_id}/block` (set `team_members.blocked_at`) ou pré-bloquer un GitHub login pas encore signé via `POST /v1/teams/{id}/org-blocks {github_login}` ; un user bloqué reçoit 403 sur toute route team-scoped même s'il est dans l'org
  4. Quand un user signe en GitHub et qu'une autre user row existe avec le même `github_id` (linkée précédemment depuis un compte Google), les `team_members` de la row orpheline sont migrés vers la row primary et la row orpheline est soft-deleted (`users.merged_into_user_id` set) — idempotent, sûr en cas de re-sign
  5. Sur auto-grant déclenché par GHA-02, un email est envoyé à tous les admins de la team via `send_member_autojoined_email()` (fail-soft si `SMTP_HOST` vide) avec le username GitHub + lien vers `grooveos.app/account/teams/` pour Block
  6. Le bouton "Sign in with GitHub" est le bouton primaire (visuellement dominant) dans `chrome-extension/popup.html` ET `app-site/account/teams/index.html` ; "Sign in with Google" reste accessible mais secondaire ("More options" ou lien plus petit)
  7. `bash infrastructure/scripts/verify-phase10.sh` retourne `PASS: N / N` (count TBD au planning) — au minimum : (a) `/v1/auth/github/exchange` mint un xbt_ pour un user sans compte Google préalable, (b) auto-grant respecte `team_org_blocks`, (c) `POST /block` refuse principal non-admin (403), (d) auto-merge migre `team_members` et soft-delete row orpheline, (e) email admin déclenché sur auto-grant (capté via SMTP mock)

**Plans**: 6 plans
Plans:

- [ ] 10-01-PLAN.md — Migrations 0016 (team_members.blocked_at + team_org_blocks + users.merged_into_user_id) + ORM + repo helpers + merge_user_rows
- [ ] 10-02-PLAN.md — POST /v1/auth/github/signin + auto-grant service + identity merge (GHA-01/02/05/06)
- [ ] 10-03-PLAN.md — Admin block/unblock + GitHub-login pre-block endpoints + 403 enforcement (GHA-03/04)
- [ ] 10-04-PLAN.md — app-site GitHub primary sign-in via Option B redirect + state-aware banners (GHA-08)
- [ ] 10-05-PLAN.md — Chrome extension popup GitHub-primary + background SIGNIN_GITHUB + options.html Block/Pre-block UI (GHA-07)
- [ ] 10-06-PLAN.md — verify-phase10.sh + KB article + docs/auth.html + UAT + SUMMARY template

**Wave order**: 1 (10-01) → 2a (10-02) → 2b (10-03) → 3 (10-04 + 10-05 parallel) → 4 (10-06)

(REVISION 1 / M-2: 10-02 and 10-03 both edit `deps.py:get_team_scope` and
`routes/teams.py`. They must run sequentially — 2a before 2b — to avoid the
guaranteed merge conflict that parallel execution produces. 10-04 and 10-05
touch independent file trees (app-site vs chrome-extension) and remain
parallel in wave 3.)

**UI hint**: yes (chrome-extension popup + chrome-extension options.html Settings, app-site /account/teams/)

### Phase 11: Brain Monitor — Universal Truth-Level Inspector + Soft Delete

**Goal**: Donner à chaque user/admin une **vue unifiée temps-réel de tout ce qui entre dans le brain** de sa team (memories, facts, conversations, transcripts Granola, tasks, contacts CRM, team messages), avec le `truth_level` affiché et éditable, et la possibilité de soft-delete tout item (rétention 30 jours puis purge réelle Postgres + Qdrant + Neo4j). Étend le tagging contract `truth_level` à TOUTES les entités (pas seulement memory layer) — c'est la concrétisation du différenciateur xbrain documenté dans CLAUDE.md.
**Depends on**: Phase 5 (team platform + `/account/teams/`), Phase 7 (entités tasks/contacts/team_messages), Phase 10 (auth GitHub-primary, surface `/account/teams/[slug]/` pour le brain page)
**Entry gate**: Phase 10 SHIPPED (auth + team membership stables). Toutes les entités cibles ont une colonne `team_id` ou `team_scope` permettant le filtrage par team. `memory_items` a déjà `truth_level` (Phase 2). `notifications.py` SMTP fail-soft dispo. Job runner ou cron container dispo pour purge quotidienne.
**Requirements**: BMO-01 à BMO-12 (phase post-v1)

- BMO-01: Migration ajout colonne `truth_level` (TEXT NOT NULL DEFAULT) + `deleted_at TIMESTAMPTZ NULL` + `deleted_by UUID NULL` sur `tasks`, `contacts`, `team_messages`, `conversations`, `granola_notes` (et toute table d'entité non-memory écrite dans le brain)
- BMO-02: Universal event view `v_brain_events` (UNION ALL des 7+ tables sources) avec colonnes normalisées : `entity_type`, `entity_id`, `team_id`, `created_at`, `created_by`, `truth_level`, `deleted_at`, `preview` (truncate 200 chars), `source`
- BMO-03: `GET /v1/brain/events` paginated (cursor `created_at + id`) + filtres `entity_type[]`, `truth_level[]`, `source[]`, `created_by`, `q` (text search sur preview), `include_deleted`, `since` — team-scoped via `X-Team-Scope`
- BMO-04: `PATCH /v1/brain/events/{entity_type}/{entity_id}` set `truth_level` — auteur peut éditer le sien, admin team peut tout éditer (vérif via `created_by == principal.user.id` OU `team_members.role='admin'`)
- BMO-05: `DELETE /v1/brain/events/{entity_type}/{entity_id}` set `deleted_at=now() + deleted_by=principal.user.id` (soft delete) — mêmes permissions que BMO-04 ; trigger purge Qdrant point delete async pour memory_items
- BMO-06: `POST /v1/brain/events/{entity_type}/{entity_id}/restore` clear `deleted_at` (auteur ou admin) — uniquement si `deleted_at > now() - INTERVAL '30 days'`
- BMO-07: Retrieval filter — toutes les routes existantes (memory search, tasks list, contacts list, etc.) DOIVENT exclure `deleted_at IS NOT NULL` par défaut. Régression-tests obligatoires.
- BMO-08: Service `brain-janitor` (cron container quotidien 03:00 UTC) — pour chaque entité avec `deleted_at < now() - 30 days` : (a) Qdrant point delete si vector, (b) Neo4j relation cleanup si node existant, (c) Postgres hard DELETE. Idempotent + audit log.
- BMO-09: app-site UI `/account/teams/[slug]/brain/` — table virtualisée (1000+ rows), filtres latéraux (entity_type, truth_level, source, date range, deleted), preview row, edit truth_level inline (dropdown 5 niveaux), bouton Delete (soft) + Restore (depuis filtre "Trash"), bulk select pour admin. Polling 30s pour live feed.
- BMO-10: Superadmin authorization helper `assert_is_superadmin(principal)` (wraps existing `_is_admin()` from `deps.py:266`) + new endpoint family `/v1/admin/brain/...` gated by it. Cross-team drill-down endpoint `GET /v1/admin/brain/events?team_slug=X&...` bypasses `X-Team-Scope` for superadmins and writes an `audit_log` entry per call (`action='superadmin_brain_access'`).
- BMO-11: Aggregate metrics endpoints (Pack M): `GET /v1/admin/brain/overview` (counts × truth_level × entity_type per team), `GET /v1/admin/brain/storage` (PG rows + Qdrant points + MinIO bytes per team), `GET /v1/admin/brain/activity?days=30` (events/day per team), `GET /v1/admin/brain/sources?days=30` (top sources breakdown per team). All on-the-fly queries, no pre-aggregation table in v1.
- BMO-12: app-site superadmin dashboard at `/account/admin/` (or `/account/admin/index.html`) — 4 sections (Brain Overview, Storage, Activity, Top Sources). Tables + inline SVG sparklines for Activity (no chart library dependency). Drill-down button per team row routes to `/account/teams/brain/?team=<slug>&as_superadmin=1` (shows a banner "Viewing as superadmin — this access is logged.").

**Success Criteria** (what must be TRUE):

  1. Un admin team peut ouvrir `https://grooveos.app/account/teams/{slug}/brain/`, voir les 50 derniers items entrés sur SA team (toutes entités confondues), filtrer par `truth_level=WORKING` et `entity_type=memory_item`, et la liste se rafraîchit toutes les 30 s sans recharger la page
  2. Le `truth_level` est visible et éditable inline pour CHAQUE row (memory, fact, task, contact, message, conversation, transcript Granola) ; un user non-admin voit le dropdown grisé sur les rows qu'il n'a pas créées
  3. Cliquer "Delete" sur une row set `deleted_at=now()` en DB ; la row disparaît de la vue par défaut mais réapparaît avec filtre "Show deleted" et un bouton "Restore" — restore réussit si `deleted_at > now() - 30 days`
  4. Toute route existante (`POST /v1/memory/search`, `GET /v1/tasks`, `GET /v1/crm/contacts`, etc.) ignore les items soft-deletés sans changement client — tests de régression dans chaque router confirment
  5. Le service `brain-janitor` tourne en cron quotidien, et un item avec `deleted_at = now() - 31 days` est purgé de Postgres + Qdrant + Neo4j à la prochaine exécution (audit log entrée écrite)
  6. Migration BMO-01 réussit sur DB existante (28+ tables, données réelles Phase 1-10) sans data loss — vérifiée par `verify-phase11.sh` avec snapshot avant/après row count + `truth_level` defaults appliqués correctement (memory_items conserve sa valeur ; tasks/contacts/messages défault à `WORKING` ; conversations défault à `EPHEMERAL`)
  7. `bash infrastructure/scripts/verify-phase11.sh` retourne `PASS: N / N` — au minimum : (a) GET /v1/brain/events filtre par team_scope + retourne ≥7 entity_types, (b) PATCH truth_level réussit pour auteur, échoue 403 pour non-auteur non-admin, (c) DELETE soft + restore round-trip, (d) janitor purge après 30j (mock clock), (e) tous les anciens endpoints exclude deleted_at, (f) UI app-site charge sous 2s pour 500 events
  8. Un principal listé dans `ADMIN_USER_SUBS` (= superadmin) peut ouvrir `https://grooveos.app/account/admin/` et voir une vue cross-team : Brain Overview (matrice counts × truth_level × entity_type pour ≥2 teams), Storage (PG rows + Qdrant points + MinIO bytes par team), Activity (sparkline events/day sur 30 jours par team), Top Sources (breakdown LibreChat/OWUI/Granola/agent/API par team) — un user non-superadmin reçoit 403 sur tous les `/v1/admin/brain/*` endpoints
  9. Le superadmin clique sur "Drill down" sur une team row → ouvre le brain monitor de cette team avec full content visibility et un banner "Viewing as superadmin — this access is logged." ; chaque appel à `/v1/admin/brain/events?team_slug=X` écrit dans `audit_log` (`action='superadmin_brain_access'`, `actor_user_id`, `target_team_slug`, `endpoint`, `query_params`)
  10. verify-phase11.sh ajoute des assertions superadmin : (g) GET /v1/admin/brain/overview retourne ≥2 teams pour un superadmin, 403 pour un user normal, (h) drill-down sur team_slug arbitraire réussit pour superadmin + écrit audit_log entry, (i) ADMIN_USER_SUBS env non set → tous les /v1/admin/brain/* retournent 403 (lockdown par défaut)

**Plans**: 11 plans
Plans:

- [ ] 11-01-PLAN.md — Migration 0017 truth_level + soft-delete columns + ORM updates
- [ ] 11-02-PLAN.md — Migration 0018 v_brain_events SQL view + composite indexes
- [ ] 11-03-PLAN.md — Qdrant payload deleted_at_ts + mark_deleted/mark_restored helpers
- [ ] 11-04-PLAN.md — GET /v1/brain/events paginated list + assert_can_edit_brain_event auth helper
- [ ] 11-05-PLAN.md — PATCH/DELETE/POST-restore on /v1/brain/events/{type}/{id} + audit log
- [ ] 11-06-PLAN.md — Retrieval regression filter (deleted_at IS NULL) on tasks/crm/conversations/messages/native_provider
- [ ] 11-07-PLAN.md — apps/brain-janitor/ cron container + docker-compose entry + Neo4j/Qdrant/PG purge
- [ ] 11-08-PLAN.md — app-site /account/teams/brain/ UI — vanilla JS feed, filters, inline edit, polling
- [ ] 11-09-PLAN.md — verify-phase11.sh + KB + docs/brain-monitor.html + UAT + SUMMARY template (covers BMO-01..12)
- [ ] 11-10-PLAN.md — Superadmin endpoints: assert_is_superadmin helper + /v1/admin/brain/{overview,storage,activity,sources,events} + audit_log on drill-down (BMO-10/11)
- [ ] 11-11-PLAN.md — app-site /account/admin/ UI: 4-section dashboard (Brain Overview / Storage / Activity / Top Sources) + inline SVG sparklines + drill-down to brain monitor with superadmin banner (BMO-12)

**Wave order**: 1 (11-01 — base columns) → 2 (11-02 — view requires 11-01 columns) → 3a (11-03 — Qdrant payload, isolated package) → 3b (11-04 — GET endpoint + auth helper, requires view from 11-02) → 3c (11-05 — PATCH/DELETE/restore, requires 11-03 + 11-04 same router file) → 4 (11-06 + 11-07 + 11-08 + 11-10 parallel — disjoint file trees: 11-10 creates new `app/routes/admin_brain.py` so no conflict with 11-06's edits to existing routes) → 5 (11-11 — superadmin UI, depends on 11-10 endpoints) → 6 (11-09 — verify + docs after everything ships, covers BMO-01..12 including superadmin)

**UI hint**: yes (app-site `/account/teams/brain/?team=<slug>` — flat path, slug via query param; `[slug]` in earlier notes was conceptual not literal — AND app-site `/account/admin/index.html` for the superadmin dashboard added 11-11)

### Phase 12: GitHub App Migration — Public-Deployment-Ready Auth

**Goal**: Migrate xbrain authentication from OAuth App to GitHub App so the platform is ready for public deployment. GitHub Apps support multiple callback URLs natively (eliminating the per-frontend OAuth App proliferation), use short-lived installation tokens (eliminating long-lived `GITHUB_API_PAT` and unbounded user tokens), enable org-level installation (canonical "Install xbrain on our org" UX instead of per-user authorization with global org-read scope), and unlock higher rate limits per installation. Clean break — no dual-auth maintained; the single existing user (mrboups) re-authorizes once via the new GitHub App.
**Depends on**: Phase 11 (Brain Monitor ships first per ordering decision 2026-05-17)
**Entry gate**: Phase 11 SHIPPED. OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) currently authorizes web sign-in only; Chrome extension flow is broken (single-callback constraint). Existing users: 1 (mrboups). `users.github_id` UNIQUE constraint already in place (Phase 10). `GITHUB_API_PAT` currently used for `/orgs/{org}/members/{username}` checks — must be replaced by installation token. No tests or production users depend on long-lived GitHub OAuth tokens (8h TTL acceptable with refresh token flow).
**Requirements**: GHAPP-01 to GHAPP-08 (phase post-v1 — public-deployment readiness)

- GHAPP-01: Create new GitHub App on `mrboups` account (or new dedicated GitHub org) with multi-callback URLs registered: `https://grooveos.app/account/teams/` (web) + `https://<ext-id>.chromiumapp.org/` (Chrome extension stable ID via manifest `key`). Permissions requested: `read:user`, `user:email`, `read:org` (or fine-grained equivalents). Generate private key (PEM), store securely server-side.
- GHAPP-02: Backend JWT signing infrastructure — load private key from secret, mint JWT signed with RS256 for GitHub App authentication, exchange JWT for installation tokens per installation_id. Cache installation tokens (1h TTL, refresh-on-401).
- GHAPP-03: New `installations` table (`installation_id INT PK`, `github_org_login TEXT`, `installed_at TIMESTAMPTZ`, `installed_by_github_id BIGINT`, `permissions JSONB`, `revoked_at TIMESTAMPTZ NULL`) + webhook handler `/v1/webhooks/github/installation` for `installation` and `installation_repositories` events. Sync source-of-truth from GitHub.
- GHAPP-04: Migrate `/orgs/{org}/members/{username}` org membership check from `GITHUB_API_PAT` to installation token (lookup installation by `github_org_login`, use cached installation token, fall back to "org not installed → user cannot join team" error). Remove `GITHUB_API_PAT` from `.env.example` and runtime config.
- GHAPP-05: User-to-server token expiration handling — implement refresh token flow per [GitHub docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens). Store `github_access_token`, `github_refresh_token`, `github_token_expires_at` on users row (migration 0018 or higher). Refresh transparently before any `/user/*` call when token is < 5 min from expiry.
- GHAPP-06: Install flow UI — when a user signs in but their primary org has not installed the GitHub App, redirect to GitHub's install URL (`https://github.com/apps/{app_slug}/installations/new`) with `state` for return URL. After install webhook arrives, user can complete team join. Banner messaging in `app-site/account/teams/index.html` and `chrome-extension/popup.html`.
- GHAPP-07: Update frontend client_id constants — `app-site/account/teams/teams.js:34` and `chrome-extension/background.js:63` to new GitHub App client_id. With GitHub App's multi-callback support, the same client_id serves both flows (no per-frontend dispatch in memory-api). Verify `chrome.runtime.id` stability via fixed `key` in `chrome-extension/manifest.json` (so the chromiumapp.org URL is deterministic).
- GHAPP-08: Remove OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) from active code path. Delete OAuth-App-specific dispatch logic in `apps/memory-api/app/routes/auth_github.py`. Document migration in `docs/auth.html`. (LibreChat-specific OAuth App `xbrain LibreChat` Client ID `Ov23li0XHV3NL8Git7Dk` remains untouched — separate concern.)

**Success Criteria** (what must be TRUE):

  1. A user can sign in via "Sign in with GitHub" on `https://grooveos.app/account/teams/` AND in the Chrome extension popup — both flows use the same GitHub App client_id with multi-callback URLs registered natively (no `Ov23liy7tZekl0uEztoj` OAuth App code path active)
  2. An org admin can click "Install xbrain on org" in app-site or popup → redirected to GitHub install page → after install, the installation webhook populates `installations` table; team members from that org auto-grant team membership at next sign-in (preserves Phase 10 semantics)
  3. User-to-server token (8h TTL) expires gracefully: subsequent API calls trigger transparent refresh via `github_refresh_token`; user is not signed out; no manual re-authorization needed within 6 months (refresh token lifetime)
  4. `/orgs/{org}/members/{username}` check works using installation token (verified by removing `GITHUB_API_PAT` env var and confirming the check still succeeds for installed orgs and returns "org not installed" for non-installed orgs)
  5. `team_org_blocks` + auto-grant team membership semantics unchanged from Phase 10 (regression tests pass): blocked GitHub login still cannot join even if org is installed; auto-grant still triggers on first sign-in for installed-org members
  6. mrboups (the single existing user) re-authorizes via the new GitHub App once and sees the same teams (`Dejavudev`), same `github_id` row preserved, all brain data intact
  7. `GITHUB_API_PAT` removed from `.env.example`, `docker-compose.yml`, and any runtime config. No long-lived GitHub PAT in the system after migration completes
  8. `bash infrastructure/scripts/verify-phase12.sh` returns `PASS: N / N` — at minimum: (a) JWT signing with private key produces a valid GitHub-App-authenticated request, (b) installation token cache returns a valid token (refresh-on-401 verified by clock-mocked expiry), (c) user sign-in via new GitHub App mints `xbt_` token, (d) refresh token rotation succeeds, (e) install flow webhook handler populates `installations` row, (f) auto-grant works for installed-org member, (g) `GITHUB_API_PAT` env unset and org membership check still succeeds, (h) Chrome extension callback URL stable (matches manifest `key`-derived `chrome.runtime.id`)

**Plans**: TBD (populated by `/gsd:plan-phase 12` — estimated 8-10 sub-plans covering JWT/installation tokens, refresh flow, install UI, multi-callback frontend updates, manifest `key` for stable extension ID, migration, verification)
**UI hint**: yes (app-site install banner + chrome-extension popup install/status section + install confirmation page)

### Phase 13: Chat → Brain Ingestion + Retrieval Enrichment — close the differentiator

**Goal**: Close the three unchecked core-differentiator requirements (MEM-04, CHAT-03, CHAT-07) by wiring every substantive chat message — across team chat, LibreChat, and Open WebUI — into the searchable brain (`memory_items` + Qdrant), gated by a Haiku relevance filter, and auto-enriching every chat turn with relevant `CANONICAL`/`VALIDATED` facts retrieved from team memory before the LLM call. After this phase, the contract "any chat content (human or agent, any frontend) lands in a shared team brain, scoped + truth-tagged, and is reused on the next conversation" actually holds end-to-end.
**Depends on**: Phase 12 (GitHub App migration LIVE — auth + frontends stable)
**Entry gate**: Phase 12 SHIPPED. `brain_ingest.py` already ships the team-chat ingest path (heuristic ≥15 chars, WORKING truth level). LibreChat path: `mongo_watcher.messages_watch_loop` already forwards LibreChat messages to `/v1/messages` (conversation log) but NOT to `memory_items` + Qdrant. Open WebUI path: `openwebui-pipeline/main.py` only handles `/ingest <url>` slash commands; no per-message auto-ingestion. LibreChat enrichment: `conv_enricher.py` injects a system message at the start of new conversations only — not per-turn. Open WebUI enrichment: none. VM disk < 80%. `ANTHROPIC_API_KEY` available for the Haiku relevance classifier. Memory provider already `MEMORY_BACKEND=native` with `OPENAI_API_KEY` for embeddings (live since 2026-05-23).
**Requirements**: MEM-04, CHAT-03, CHAT-07 (the three v1 requirements left unchecked since Phase 1/2 — closing the xbrain differentiator)
**Locked decisions** (confirmed before planning):

- **D1**: Relevance filter = Claude 4.5 Haiku (`claude-haiku-4-5-20251001`), prompt-cache friendly. The heuristic ≥15 chars stays as fail-soft fallback when Haiku errors/timeouts.
- **D2**: Filter is invoked async fire-and-forget post-send — never blocks the chat response path. Persisted via the same code path as today's `brain_ingest.ingest_team_message`.
- **D3**: Default ingestion `truth_level = WORKING`. Human promotion to `VALIDATED`/`CANONICAL` via Brain Monitor (Phase 11) stays the source of truth for retrieval gating.
- **D4**: All three active frontends in scope v1: team chat (refine — replace heuristic with Haiku gate), LibreChat (new ingest path via `mongo_watcher`), Open WebUI (new ingest path via `openwebui-pipeline`).
- **D5**: Retrieval injection point = **per-turn pre-LLM hook**, not just boot-of-conversation. Implemented in librechat-bridge (extension of `conv_enricher` → `message_enricher`) and openwebui-pipeline (new enricher). Team chat already runs per-turn via the @claude bundle.
- **D6**: Retrieval `top_k = 5`, filtered to `truth_level IN ('VALIDATED','CANONICAL')`. Configurable via env (`CHAT07_TOP_K`, `CHAT07_TRUTH_FILTER`) for future tuning.

**Success Criteria** (what must be TRUE):

  1. Sending a substantive human message in **team chat** ingests it into `memory_items` (truth=WORKING) + Qdrant exactly once; assistant messages are NOT ingested (LLM output, not new facts); messages starting with `@claude`/`@c`/`@cl` are NOT ingested
  2. Sending a substantive user message in **LibreChat** triggers, in parallel to the existing conversation-log write, an upsert into `memory_items` + Qdrant via the same shared `brain_ingest` service with `source='librechat:<model>'`; idempotent on retry
  3. Sending a substantive user message in **Open WebUI** triggers an equivalent ingest with `source='openwebui:<model>'`; existing `/ingest <url>` slash-command path remains untouched
  4. The **Haiku relevance classifier** runs as a pre-upsert filter (fail-soft fallback to the ≥15-char heuristic when Haiku errors/timeouts): `relevance.score ≥ threshold` → upsert; below threshold → no-op + log; observable via Langfuse trace; daily per-team Haiku-token spend capped (configurable, default 50K input tokens/day/team)
  5. Every chat turn — LibreChat, Open WebUI, team chat — fires a `memory_search` (top-K=5, truth filter `{VALIDATED, CANONICAL}`) and injects the returned facts into the system context **before** the LLM call. When no relevant facts surface, the call proceeds untouched (no empty "no facts" block)
  6. `conv_enricher.enrich_new_conversation` is generalized to `message_enricher.enrich_turn` (LibreChat) with an equivalent hook in Open WebUI's pipeline. Existing conv-boot enrichment behavior is preserved as the first-turn case (idempotency: same conv + same turn does not re-inject)
  7. Ingestion is fire-and-forget on all frontends — failure in the brain path NEVER fails the chat response; surfaced only via Langfuse error trace + structlog warning
  8. **Cross-frontend retrieval works**: a fact ingested via team chat (then promoted to VALIDATED in Brain Monitor) is retrievable in LibreChat and Open WebUI on the next turn — same `team_scope`
  9. `bash infrastructure/scripts/verify-phase13.sh` returns `PASS: N / N` — at minimum: (a) team-chat ingest + Qdrant point materialized; (b) LibreChat user-msg ingest + Qdrant point materialized; (c) Open WebUI user-msg ingest + Qdrant point materialized; (d) Haiku-low-score message does NOT land in `memory_items`; (e) Haiku error path falls back to heuristic and ingest proceeds; (f) chat turn injects retrieved CANONICAL facts into LibreChat system context (verified via mock LLM trace); (g) cross-frontend retrieval (ingest in team chat → retrieve in LibreChat); (h) chat send still succeeds when memory-api is unreachable (fail-soft)
 10. `REQUIREMENTS.md` ticks `MEM-04`, `CHAT-03`, `CHAT-07` to `[x]` and the traceability table marks them `Done in Phase 13`

**Plans**: 8 plans
Plans:
- [x] 13-01-PLAN.md — relevance_filter (Haiku 4.5 + cache + budget cap) + POST /v1/brain/ingest endpoint
- [x] 13-02-PLAN.md — team_chat ingest uses relevance_filter.classify (swap heuristic gate)
- [x] 13-03-PLAN.md — native_provider upsert race fix (INSERT … ON CONFLICT DO UPDATE)
- [x] 13-04-PLAN.md — LibreChat brain ingest hook in mongo_watcher.messages_watch_loop
- [x] 13-05-PLAN.md — LibreChat per-turn enricher (message_enricher.enrich_turn) + conv_enricher VALIDATED upgrade
- [x] 13-06-PLAN.md — Open WebUI ingest + enrichment in openwebui-pipeline.main.chat()
- [x] 13-07-PLAN.md — verify-phase13.sh + cross-frontend integration test + .env.example
- [x] 13-08-PLAN.md — Tick MEM-04 / CHAT-03 / CHAT-07; ROADMAP + STATE; marketing docs

**UI hint**: no (backend + bridges + observability only — Brain Monitor / @claude / LibreChat / Open WebUI surface the new flow without UI work)

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
- [x] 07-02-PLAN.md — require_paid_tier + _user_id_from_principal dans deps.py + router CRM /v1/crm/contacts (CRUD + audit)
- [x] 07-03-PLAN.md — Router /v1/tasks (CRUD + filtres + polling since)
- [x] 07-04-PLAN.md — Router granola_integration (admin Fernet + ingest atomic memory_item+contacts+tasks, dedup note_id, created_by=NULL)
- [x] 07-05-PLAN.md — Squelette apps/granola-sync/ (Dockerfile, pyproject, main, config, memory_client, extractor — pas de poller, pas de docker-compose)
- [x] 07-06-PLAN.md — Background tasks memory.py (extract contacts + auto-task via async_session_factory) + service notifications email
- [x] 07-07-PLAN.md — Dashboard tasks.html + Nginx routes + verify-phase7.sh + .env.example
- [x] 07-08-PLAN.md — Boucle polling granola_poller.py + service granola-sync dans docker-compose.yml (split de 07-05)
- [x] 07-09-PLAN.md — D5 Trigger 3 : librechat-bridge task_intent_detector.py + hook mongo_watcher (chat → contains_action → task auto via 07-06)

### Phase 14: Portability Foundation

**Goal**: The entire stack is config-driven, not hardcoded — every `grooveos.app` domain reference and `aibrussels` team identifier in **backend source, infra, and technical docs** is replaced by an environment-sourced value (or a neutral default), so an operator can point a fresh install at their own domain without touching source code. This is the prerequisite for every other v2.0 phase: edition selection by config (Phase 15), OSS packaging (Phase 16), and CI lockstep (Phase 17) all assume the codebase no longer bakes in xbrain's own production identity.

> **AMENDED 2026-07-12** — the original goal + SC#1/#2/#4 contradicted the locked decisions in `.planning/phases/14-portability-foundation/14-CONTEXT.md` (D-01c, D-01e, D-04). Those decisions are newer and deliberate; this section is corrected to match them (user-approved 2026-07-12). Three carve-outs now apply:
> - **`"default"` team_scope is KEPT** (D-04) — it is a neutral, brand-free fallback string, not a brand. Making it env-configurable adds zero portability value. NOT in scope for de-hardcoding.
> - **Browser/client bundles are DEFERRED to Phase 16** (D-01c) — `chrome-extension/**`, `app-site/account/**`. The frontend is being replaced there by the web group-chat; cleaning code we are about to delete is waste.
> - **Hosted-product marketing/docs KEEP `grooveos.app`** (D-01e) — `app-site/docs/**`, `marketing-site/docs/**`, `app-site/v0-v12/**`, README positioning, **`projects-dashboard/**` and `.github/workflows/deploy-dashboard.yml`** (EXTENDED 2026-07-12: that dashboard is deployed at `projects.<brand>` and the workflow is what ships it — same class as app-site/marketing-site). These are the live pages of the commercial hosted service, whose domain legitimately *is* grooveos.app. They are not OSS code an operator self-hosts.
>   **NOT exempt** (they ship to operators and MUST be brand-free): `Makefile`, `apps/librechat/Dockerfile`, and `.github/workflow-templates/**` — those templates are COPIED INTO OPERATORS' OWN REPOS, so a hardcoded `api.grooveos.app` would point every self-hoster's CI at xbrain's production API.

**Depends on**: Phase 13 (v1.0 complete) — first phase of milestone v2.0, no phase-internal dependency
**Entry gate**: Milestone v1.0 SHIPPED (13/13 phases). Real counts measured 2026-07-11 (see `14-RESEARCH.md`; the earlier "28x/15x/15x" figures were wrong — they conflated tests+docs): `grooveos.app` ~1009 occurrences / 203 files repo-wide but only **~123 occ / ~33 files in runtime+infra source**; `aibrussels` 105 occ / 20 files; `"default"` team_scope ~49 genuine occurrences (KEPT per D-04). `.env.example` at 115 vars (~90% already externalized). **NOTE: the production VM is currently TERMINATED** (cost pause during the Prime pivot) — see SC#2.
**Requirements**: PORT-01, PORT-02
**Success Criteria** (what must be TRUE):

  1. A repo-wide search for `grooveos` (unescaped — must also catch the escaped regex form `grooveos\.app`) and `aibrussels` returns zero matches in **backend runtime source + infra + technical docs**, except the documented keep-as-is exemptions: test fixtures & illustrative few-shot examples (neutral placeholder), the design doc "Locked Decisions" table, `14-CONTEXT.md` / `14-RESEARCH.md` / phase-14 plans, `ROADMAP.md` / `STATE.md`, the browser bundles deferred to Phase 16 (D-01c), the hosted-product marketing/docs surfaces (D-01e), and **the verifier's own pattern literals** (`infrastructure/scripts/verify-phase14.sh`, `infrastructure/scripts/preflight-env.sh` — they MUST contain the bare token, it IS their grep pattern; they carry their own no-brand-URL guard instead), plus build artifacts (`__pycache__/*.pyc`). The `"default"` team_scope literal is explicitly OUT of scope (D-04). Every remaining occurrence now reads from config/env at runtime.
  2. **Deploy-safe + deferred live regression** (amended — the prod VM is OFF, so the live suite cannot run now): (a) every new config var has a prod-value escape hatch, and a **pre-deploy guard** asserts the target `.env` defines the now-mandatory OAuth vars (`OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL`) **before** the compose fallback is removed — otherwise memory-api + mcp-brain crashloop on boot; (b) the `verify-phase1..13.sh` regression suite is **parameterized** (no hardcoded domain/team) and its execution is a **deferred gate recorded in the phase SUMMARY, to be run at the next real deploy** with the prod values (`grooveos.app` / `aibrussels`) — it must PASS then. Setting the vars back to the prod values must reproduce production behavior bit-for-bit.
  3. An operator can fill a single slim, documented OSS `.env.example` and get a working install pointed at their own domain, without opening any source file to find a hidden hardcoded reference.
  4. Setting the domain config vars to a different value (e.g. a fictitious `acme.example`) produces correctly-branded URLs, OAuth issuer/audience claims, webhook URLs, email bodies, **CORS allow-origin regex**, nginx `server_name`, and agent-KB copy — no residual xbrain-specific string leaks through the **backend/infra request-response path**. (Browser-bundle request paths are explicitly deferred to Phase 16 per D-01c and are NOT asserted by this phase's verifier.)

**Plans**: 7 plans

**Wave 1** *(no dependencies — disjoint file trees, run in parallel)*

- [x] 14-01-PLAN.md — memory-api + mcp-brain config neutralization + OAuth fail-fast validator + env-driven CORS + config-driven agent mention aliases (D-08) + pytest conftest repair
- [x] 14-02-PLAN.md — Functional domain leaks: xbrain_product_kb.md neutral rewrite (incl. the `@agent` alias, in lockstep with 14-01) + relevance_filter few-shots + onboarding.js build-configurable base
- [x] 14-03a-PLAN.md — nginx envsubst templates (all 7 vhosts) + real `nginx -t` render validation + stock default.conf handling

**Wave 2** *(blocked on Wave 1)*

- [x] 14-03b-PLAN.md — docker-compose fallback neutralization + librechat.yaml `${VAR}` + centrifugo origins env + Makefile env-check/deploy guard
- [x] 14-05-PLAN.md — Mechanical docs + `.planning` history scrub (autonomous, Sonnet executor) with a logged pragmatic bound

**Wave 3** *(blocked on Wave 2)*

- [x] 14-04-PLAN.md — Slim, documented OSS `.env.example` (PORT-02) + delete the vestigial infrastructure/.env.example

**Wave 4** *(blocked on Wave 3 — the acceptance gate)*

- [x] 14-06-PLAN.md — Regression safety: parameterize the verify/infra scripts + `preflight-env.sh` crashloop guard + `verify-phase14.sh` (PORT-01/PORT-02 gate) + the DEFERRED live-regression checkpoint

**Wave order**: 1 (14-01 + 14-02 + 14-03a ∥) → 2 (14-03b + 14-05 ∥) → 3 (14-04) → 4 (14-06)

**Cross-cutting constraints** (appear in 2+ plans):
- The `"default"` team_scope literal is KEPT everywhere (D-04) — it is neutral, not a brand.
- Five vars are MANDATORY at deploy or prod breaks silently: `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL` (empty → memory-api + mcp-brain crashloop), `CORS_ALLOWED_ORIGIN_REGEX` (missing → browser CORS-blocked), `XBRAIN_BASE_DOMAIN` (missing → every nginx vhost renders `*.localhost` = total ingress outage), `AGENT_MENTION_ALIASES` (missing → `@groove` silently stops working). Guarded by `preflight-env.sh` + Makefile `env-check`.
- **NOT autonomous**: 14-06 carries a blocking `checkpoint:human-verify` — the live regression suite is a DEFERRED gate (the prod VM is TERMINATED and the scripts curl a live deployment).
**UI hint**: no (backend/config refactor — no new user-facing surface)

### Phase 15: Edition Mechanics

**Goal**: One codebase serves every edition (OSS self-host / SaaS hosted) purely through deployment-time selection — Docker Compose `profiles:` pick which services run, and an `EDITION` flag picks which memory-api routes/behaviors are active. The core (brain, chat, retrieval, truth-levels, ChatGPT-web connector) is always mounted regardless of edition — a fix there ships to every edition automatically. **No product feature is paywalled** (locked decision Q6): the only closed surface is the hosted control plane (billing, multi-tenant provisioning, trial caps).
**Depends on**: Phase 14 (portability foundation must land first — profiles/flags read config values, not hardcoded ones)
**Entry gate**: Phase 14 SHIPPED — de-hardcoding complete, slim OSS `.env.example` exists.
**Requirements**: EDIT-01, EDIT-02
**Dropped**: ~~EDIT-03~~ (Ed25519 license) — removed by locked decision Q6 on 2026-07-11. No paid product tier exists; see REQUIREMENTS.md:23. Do not plan license or entitlement work.
**Success Criteria** (what must be TRUE):

  1. An operator can bring up the OSS-light service set (10 untagged services: memory-api, postgres, qdrant, centrifugo, nginx, **minio**, mcp-brain, mcp-gateway, mcp-scraper, brain-janitor) with `COMPOSE_PROFILES` unset, and separately opt into `integrations` (neo4j, graphiti, langfuse + deps, the mcp-* integrations, searxng, agent-runtime), `saas` (session-bridge, librechat + its mongo/meili/bridge, openwebui + its pipeline) and `ops` (xbrain-backup) — verified by real `docker compose` runs, not by grepping YAML. **There is no `pro` profile** (user decision, 2026-07-12; EDIT-03 dropped, and Q5 already makes Neo4j opt-in-but-not-paywalled). `ops` is retained — EDIT-01 names it. **MinIO is PROMOTED into the core**: it is currently named `langfuse-minio` yet memory-api points its media storage at it (`MINIO_ENDPOINT` default), so tagging it `integrations` would 503 `/v1/media/upload` in every OSS-light install.
  2. The identical `memory-api` Docker image, booted with `EDITION=oss`, exposes the always-on core routers (brain, chat, teams, memory, promotions/truth-levels, media, health, me, auth, ChatGPT-web connector) and does not expose the SaaS-only routers — no separate image build per edition. **The SaaS-only set is exactly `waitlist` + `external_sessions`** (resolved during planning, 2026-07-12): with Q6 meaning nothing is paywalled, the test is never "is this premium?" but "is this meaningless without the hosted control plane?". `crm`/`tasks` are CORE — their "paid tier only" docstrings are stale comments from the cancelled licence design. "multi-tenant admin" and "billing" have **no router at all** — there is nothing to gate.
  3. Setting `EDITION=saas` on that same unmodified image mounts the SaaS-only routers at boot with no rebuild and no code change — only an env var flip. Conversely an `EDITION=oss` boot must prove those routes are **absent (404)**, not merely that the core routes work: a router nobody classifies stays mounted by default, so the negative case is the one that catches a leaked SaaS surface. (~~`EDITION=pro`~~ removed 2026-07-12 per Q6 — no paid tier exists.)
  4. Neo4j is genuinely OPT-IN, not a hard boot dependency (locked decision Q5). Today `memory-api` declares `depends_on: neo4j: {condition: service_healthy}` (`infrastructure/docker-compose.yml`), so an OSS-light install cannot start without paying ~1 GB of RAM for a graph it may not want. After this phase, bringing the stack up with `COMPOSE_PROFILES` unset starts a healthy `memory-api` with **no Neo4j container running at all**, and the graph-backed routes degrade cleanly (documented behavior — not a crash, not a 500).
  5. Turning a profile on or off changes **only which containers run and which routers mount** — it never changes what a running service *believes about its data*. In particular, `QDRANT_COLLECTION` and the team-scope contract resolve identically in every edition, so an operator can enable or disable a profile without their brain silently pointing at a different collection or losing team isolation.

> **SC#4 and SC#5 were REWRITTEN on 2026-07-12.** They previously demanded an Ed25519-signed license unlocking a paid `pro` tier — which locked decision **Q6** explicitly DROPPED (`EDIT-03`; no product feature is paywalled, only the hosted control plane is closed). Planning license or entitlement work would build something that was cancelled. The two criteria above replace them with the gates the phase actually needs: making Neo4j opt-in (Q5, and a real contradiction found by the 2026-07-12 wiring audit) and guaranteeing that profile selection cannot silently change data identity (the class of bug that made brain-janitor's Qdrant purge a no-op — see commit 215882b).

**Plans**: 5 plans in 3 waves

- [x] 15-01-PLAN.md — Make the compose graph profile-safe: cut the 3 cross-profile `depends_on` edges (memory-api→neo4j, brain-janitor→neo4j, xbrain-backup→librechat-mongo) + promote/rename `langfuse-minio` → core `minio` (wave 1)
- [x] 15-02-PLAN.md — `EDITION` flag + explicit router gating in memory-api (33 core / 2 SaaS-only), fail-fast on unknown values, the `EDITION=oss` negative-case tests, and the `neo4j_outbox` guard keyed on the LIVE driver (`get_driver()`), not on static config — which is always truthy (wave 1, parallel)
- [x] 15-03-PLAN.md — Apply the profile table to all 32 services (10 untagged core / 14 `integrations` / 7 `saas` / 1 `ops`), asserted BY NAME, + wire `EDITION` through compose (wave 2)
- [x] 15-05-PLAN.md — Restore the boot-ordering guarantee that removing `depends_on: neo4j` destroyed: a bounded, non-blocking Neo4j reconnect so a cold `--profile integrations up` does not silently kill graph sync (wave 2, parallel)
- [x] 15-04-PLAN.md — `verify-phase15.sh`: the acceptance gate, asserted against real `docker compose` output and real running containers; + `preflight-env.sh` rejects `COMPOSE_PROFILES=saas` with `EDITION=oss` (wave 3)

**UI hint**: no (infra/backend gating — no new user-facing surface; existing frontends unaffected)

### Phase 18: Local Auth (OSS default)

> **Execution order: 14 → 15 → 18 → 16 → 17.** Numbered 18 to avoid renumbering Phases 16/17 (which are cross-referenced by REQUIREMENTS.md), but it runs BEFORE Phase 16 — its position in this file reflects execution order, not numeric order.

**Goal**: A self-hoster can create an account and sign in with an email and a password, with **zero external OAuth setup** — no Google Cloud project, no GitHub App, no callback URLs. Google OAuth and the GitHub App become opt-in paths for org-driven membership rather than the only way in. Locked decision **Q2** of the open-core design; it had no roadmap entry at all until 2026-07-12.
**Depends on**: Phase 14 (portability — auth config must be env-driven, and the OAuth identity vars now fail-fast at boot, so a no-OAuth install needs a path that does not set them)
**Entry gate**: Phase 14 SHIPPED.
**Requirements**: LAUTH-01, LAUTH-02
**Why it blocks Phase 16**: Phase 16 SC#1 promises an operator reaches a running stack "following only the published install docs (no source reading, no tribal knowledge)", and SC#4 promises a user opens the standalone web app and chats. Today the ONLY ways in are Google OAuth and the GitHub App (`apps/memory-api/app/deps.py:46-333` resolves five principal kinds — none password-based). Requiring a self-hoster to register an OAuth application with a third party before they can log in to their own install contradicts both criteria. It is equally the blocker for hosted signup: nobody signs up to a SaaS through a GitHub App.
**Success Criteria** (what must be TRUE):

  1. On a fresh install with no `GOOGLE_CLIENT_ID`, no `GITHUB_APP_*` and no OAuth identity vars set, a new user registers with email + password and signs in — the stack boots and the flow completes end to end.
  2. Passwords are stored only as a salted hash from a memory-hard KDF (argon2id or bcrypt) — never plaintext, never a bare SHA. Verified by inspecting the persisted row.
  3. The resulting principal is indistinguishable downstream from the existing ones: `get_current_principal` returns the same shape, and every existing team-scoped route (chat, brain, memory, promotions) authorizes it identically — no route learns a sixth special case.
  4. Google OAuth and the GitHub App still work unchanged when configured, and an install may enable any combination of the three — the local path is a default, not a replacement.
  5. The account surface is complete enough to be usable and safe: registration, sign-in, sign-out, and password change. (Email-based password RESET is explicitly OUT of scope — it needs outbound SMTP, which an OSS-light install has no reason to require. Document the recovery story instead.)
  6. Basic abuse resistance on the credential endpoints — rate limiting / lockout on repeated failures — so a default install is not trivially brute-forceable.

**Plans**: 6 plans
Plans:
- [x] 18-01-PLAN.md — Data layer: migration 0024 (local_credentials) + LOCAL_AUTH_* config + repo
- [x] 18-02-PLAN.md — Services: argon2-cffi + limits deps, password_hash (decoy) + rate_limit + shared xbt_ mint
- [x] 18-03-PLAN.md — Register + Login routes (single-commit, decoy-timed 401, DB lockout) + wire CORE router
- [x] 18-04-PLAN.md — Authenticated set-password (convergence, D-18-05) + operator recovery runbook
- [x] 18-05-PLAN.md — Auth UI: register / login / set-password screens (vanilla, English) + human verify
- [x] 18-06-PLAN.md — verify-phase18.sh acceptance gate (real Postgres, SKIP-as-FAIL) + docs/auth.html

**Wave order**: 1 (18-01 + 18-02 parallel — disjoint files) -> 2 (18-03) -> 3 (18-04) -> 4 (18-05) -> 5 (18-06)
**UI hint**: yes (registration + sign-in + password-change surface — new user-facing screens)

### Phase 19: Local Embeddings (OSS default)

> **Execution order: 14 → 15 → 18 → 19 → 16 → 20 → 17.** Runs BEFORE Phase 16. Numbered 19 to avoid renumbering 16/17.

**Goal**: A fresh OSS-light install performs semantic ingest + retrieval with **no OpenAI key** — embeddings run in-container, keyless. OpenAI embeddings remain selectable via config. Locked decision **Q3**.
**Depends on**: Phase 15 (the OSS-light service set is defined; the embedder ships inside memory-api or as an untagged-core sidecar).
**Entry gate**: Phase 15 SHIPPED.
**Requirements**: EMBED-01
**Why it blocks Phase 16**: Phase 16 SC#3 promises a zero-key install can "ingest and **retrieve**". Today `apps/memory-api/app/embedders.py:13` hard-raises `RuntimeError("OPENAI_API_KEY not configured for embeddings")`, so semantic `memory_search` is impossible without an OpenAI key — and the "one key: Anthropic OR OpenAI OR Grok" promise is broken because embeddings force OpenAI specifically. Phase 16 cannot honestly claim SC#3 until this lands.
**Success Criteria** (what must be TRUE):

  1. On an install with NO `OPENAI_API_KEY` (and no other embeddings key), a document/message is ingested, embedded locally, written to Qdrant, and retrieved by semantic `memory_search` — end to end, proven live.
  2. The local embedder runs in-container with no external network call and no API key — a self-hoster adds zero credentials for retrieval to work.
  3. The embedder is pluggable: setting `OPENAI_API_KEY` (or the configured provider) switches to that provider without code changes; the provider abstraction in `packages/memory-models` / `embedders.py` is respected.
  4. The chosen local model has BOTH arm64 and amd64 wheels/artifacts (dev host is arm64, prod amd64) and fits the OSS-light RAM budget (must not OOM an e2-medium) — stated with the measured footprint.
  5. Existing OpenAI-embedded vectors and the OpenAI path do not regress when a key IS configured; the Qdrant collection's vector dimensions are handled correctly if the local model's dimension differs from OpenAI's 1536 (migration/re-embed story documented, not silently broken).

**Plans**: 3 plans
- [x] 19-01-PLAN.md — Local embedder engine: provider selector (local default), fastembed/bge-small-en-v1.5, provider-derived Qdrant dimension across all 3 sites + fail-loud mismatch (Wave 1)
- [x] 19-02-PLAN.md — Offline model bake (HF_HUB_OFFLINE, --network none proof), configurable uvicorn workers, both-arch build, compose + .env.example wiring (Wave 2)
- [x] 19-03-PLAN.md — Gate-lesson proof: real Postgres + real Qdrant keyless semantic-retrieval test, OpenAI regression, re-embed migration doc (Wave 2)
**UI hint**: no (backend embedding engine — no user-facing surface)

### Phase 16: OSS Light Packaging

**Goal**: A team with no prior knowledge of the xbrain source can stand up the OSS-light edition on a fresh VM from the install docs alone, and the brain works end-to-end (chat via the existing surfaces + ChatGPT-web connector + doc analysis + ingest + **retrieval, now keyless thanks to Phase 19** + truth-levels + clip) with **zero external keys**. **This phase is real packaging — the standalone web chat frontend is Phase 20, not here.**
**Depends on**: Phase 14 (portability), Phase 15 (edition mechanics — profiles + EDITION flag), Phase 18 (local auth — so "install docs alone" needs no third-party OAuth app), **Phase 19 (local embeddings — so SC#3's keyless retrieval is actually deliverable)**.
**Entry gate**: Phase 14 + 15 + 18 + 19 SHIPPED. OSS-light service set defined and boot-tested individually in Phase 15.
**Requirements**: PKG-01
**Success Criteria** (what must be TRUE):

  1. Following only the published install docs (no source reading, no tribal knowledge), an operator provisions a fresh VM and reaches a running OSS-light stack — a clean-install test passes end-to-end.
  2. The OSS-light compose profile (`COMPOSE_PROFILES` unset) boots ~10 services with all healthchecks green, matching the Phase 15 profile table.
  3. On the fresh install, with **no external keys set** (no OpenAI, no Google, no GitHub App), a user registers via local auth (Phase 18), uploads/analyzes a document, has it **ingested and semantically retrieved** (Phase 19, keyless) with truth-levels visible, connects via the ChatGPT-web connector, and clips a web page into memory — all with `COMPOSE_PROFILES` unset (no `integrations`/`saas`; there is no `pro` profile). Chat is exercised via the existing surfaces (the Chrome extension against the install, and/or the ChatGPT connector) — the standalone web app is Phase 20.
  4. The published OSS release artifact shape exists and is reproducible: tagged multi-arch images (or a documented build-on-VM path), the light compose file, and the install docs — the same bundle Phase 17 will later automate.

**Plans**: 4 plans
Plans:

- [x] 16-01-PLAN.md — OAuth-AS local-auth branch (D-16-02): zero-key ChatGPT-web connector sign-in via Phase-18 local auth + threat model
- [x] 16-02-PLAN.md — .env.example restructure (D-16-05: MinIO required/≥8-char + saas de-conflation) + `make oss-init` zero-key secret generator
- [x] 16-03-PLAN.md — Install docs (docs/INSTALL.md, docs-alone) + README Quickstart rewrite + SC#4 build-on-VM release-artifact shape (D-16-06)
- [x] 16-04-PLAN.md — Clean-install gate verify-phase16.sh: REAL core boot + SC#3 HTTP walk (register → keyless doc ingest/retrieval → connector local-auth → clip)

**Wave order**: 1 (16-01 + 16-02 parallel — disjoint files) → 2 (16-03 depends on 16-02; 16-04 depends on 16-01 + 16-02; 16-03 + 16-04 parallel — disjoint files, Makefile edited sequentially after wave 1)
**UI hint**: partial (install docs; no new app UI — the web app is Phase 20)

### Phase 20: Standalone Web Chat (the product per Q4)

> **Execution order: after Phase 16, before Phase 17.**

**Goal**: A user opens a standalone hosted web app — not the Chrome extension popup — and chats with their team brain with functionality equivalent to the extension's chat surface. The chat UI is extracted from the extension (`chrome-extension/popup.js`, ~1125 lines: Centrifugo realtime, message list, composer, `@`-mention agent, streaming replies) into a shared, browser-extension-independent frontend, wiring in Phase 18 auth and clip-to-memory. This is THE product per locked decision Q4.
**Depends on**: Phase 16 (needs the packaged, installable OSS-light stack to run against + serve the app), Phase 18 (auth screens the web app signs in with).
**Entry gate**: Phase 16 SHIPPED.
**Requirements**: PKG-02
**Success Criteria** (what must be TRUE):

  1. A user opens the standalone web app (no browser extension installed) and completes register/sign-in via Phase 18 local auth, then sees their team chat.
  2. Realtime chat works: the message list loads history, the composer posts, Centrifugo delivers incoming messages live, and `@agent`/`@chad` mentions stream the agent's reply back into the chat — functional parity with the extension's chat surface (the same `team_chat.py` REST/WS contract; no backend changes needed, it is already multi-frontend).
  3. Clip-to-memory (a headline OSS feature) is reachable from the standalone web app, not only from the browser extension.
  4. The web app is served by the OSS-light stack (Phase 16) and needs no third-party keys to run; app-site is debranded here or references `XBRAIN_BASE_DOMAIN` (closing the D-01c app-site portability deferral).
  5. A human UAT confirms the register → chat → mention → clip loop in a real browser (this is the browser-UAT deferred from Phase 18's UI checkpoint — it lands here where a running stack + real frontend exist).

**Plans**: TBD (populated by `/gsd:plan-phase 20`)
**UI hint**: yes (the standalone web chat app is the major new user-facing surface of the whole milestone)

### Phase 17: CI Lockstep

**Goal**: One CI pipeline per commit builds images once, tests both the OSS subset and the full profile, then — from that same commit — publishes the OSS release and deploys the SaaS full profile. Editions can never drift apart because they are built, tested, and shipped together by construction; self-host installs upgrade via forward-only, edition-agnostic migrations.
**Depends on**: Phase 15 (edition mechanics — profiles/flags must exist for CI to test "both profiles"), Phase 16 (OSS packaging — install docs + light compose must exist for CI to publish an OSS release), Phase 20 (standalone web chat — the shipped frontend CI builds/publishes as part of the OSS release)
**Entry gate**: Phase 15 + Phase 16 + Phase 20 SHIPPED. OSS release artifact shape (tagged images + light compose + install docs + web app) already exists (produced manually in Phases 16/20); CI now automates producing and publishing it.
**Requirements**: REL-01, REL-02, REL-03
**Success Criteria** (what must be TRUE):

  1. A single CI run, triggered by one commit to `main`, builds images exactly once and runs the test suite against both the OSS subset and the full profile before any publish or deploy step executes.
  2. That same commit's CI run produces the published OSS release (tagged images + light compose + install docs) AND deploys the SaaS full profile to production — one commit SHA, both editions shipped, never a manual second push.
  3. If either the OSS-subset tests or the full-profile tests fail, neither the OSS release nor the SaaS deploy proceeds — lockstep is enforced by the pipeline, not by developer discipline.
  4. An operator running a self-hosted install applies a released migration and upgrades cleanly with a single command — the migration path is forward-only (no down-migrations required) and edition-agnostic (the same migration applies whether the install runs oss, saas, or pro).
  5. Migrations are validated in CI against both profiles before release — a migration that would break one edition never reaches release.

**Plans**: TBD (populated by `/gsd:plan-phase 17`)
**UI hint**: no (CI/release infrastructure — no user-facing surface)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 3.5 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Socle Infra + Frontends + memory-api | 6/6 | ✅ Complete | 2026-05-03 |
| 2. Mémoire Intelligente + Agents | 9/9 | ✅ Complete | 2026-05-04 |
| 3. Graphe + Extraction + Intégrations | 12/12 | ✅ Complete | 2026-05-04 |
| 3.5. MCP Gateway Fix + Corrections Phase 3 (INSERTED) | 2/2 | ✅ Complete | 2026-05-05 |
| 4. Consolidation MCP Frontends + Intégrations Avancées | 8/8 | ✅ Complete | 2026-05-05 |
| 5. Plateforme Projets Équipe | 7/7 | ✅ Complete | 2026-05-06 |
| 6. Marketing Site + Documentation | 8/8 | ✅ Complete | 2026-05-07 |
| 7. CRM + Granola + Task Intelligence | 9/9 | ✅ Complete | 2026-05-07 |
| 8. Granola Per-User + Universal Extraction + Platform Agents | 8/8 | ✅ Complete | 2026-05-09 (verify 7/7 PASS reconfirmed 2026-05-17) |
| 9. Session Bridge — Pro/Max Routing via Chrome Extension | 6/6 | ✅ LIVE | 2026-05-12 (verify 6/6 PASS + 2 SKIP reconfirmed 2026-05-17) |
| 10. GitHub-Primary Auth + Org-Driven Team Membership | 6/6 | ✅ LIVE | 2026-05-14 (deployed + verify PASS) — OAuth client_id+callback URL bug fixed 2026-05-17 |
| 11. Brain Monitor — Universal Truth-Level Inspector + Soft Delete + Superadmin Dashboard | 11/11 | ✅ LIVE | 2026-05-17 (verify-phase11.sh PASS 5/5 + 11 SKIP fixture, alembic 0018 head on VM, brain-janitor running, UI live grooveos.app/account/teams/brain/ + /account/admin/) |
| 12. GitHub App Migration — Public-Deployment-Ready Auth | 11/11 | ✅ LIVE | 2026-05-17 (alembic 0019 head, memory-api rebuilt, verify-phase12.sh PASS 13/13 + 5 SKIP fixture, Firebase teams.js with new client_id Iv23liVnZvIN0Lo6isof live) |
| 13. Chat → Brain Ingestion + Retrieval Enrichment | 8/8 | Complete   | 2026-05-27 |
| 14. Portability Foundation | 8/8 | Complete    | 2026-07-12 |
| 15. Edition Mechanics | 6/6 | Complete    | 2026-07-13 |
| 16. OSS Light Packaging | 4/4 | Complete   | 2026-07-18 |
| 17. CI Lockstep | 0/TBD | Not started | - |

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


---

## Coverage Map v2.0 (10 requirements -> 4 phases)

| Category | Phase 14 | Phase 15 | Phase 16 | Phase 17 |
|----------|----------|----------|----------|----------|
| PORT (2) | PORT-01, PORT-02 | -- | -- | -- |
| EDIT (3) | -- | EDIT-01, EDIT-02, EDIT-03 | -- | -- |
| PKG (2) | -- | -- | PKG-01, PKG-02 | -- |
| REL (3) | -- | -- | -- | REL-01, REL-02, REL-03 |
| **Total** | **2** | **3** | **2** | **3** |

**Coverage: 10/10 v2.0 requirements mapped. No orphans.**

**Out of scope for v2.0 (separate tracks, not folded into any phase):** Email feature (send + Gmail read/search/ingest); Grok API-key fallback + per-message trial cap (SaaS trial).

**Phase dependency chain:** 14 -> 15 -> 16 -> 17, with Phase 16 depending on both 14 and 15, and Phase 17 depending on both 15 and 16 (per `.planning/features/open-core-edition-design.md` execution sequence A->B->C->D).

---
*Roadmap for milestone v2.0 created: 2026-07-11*
*Granularity: coarse (4 phases -- matches the design blueprint's fixed A->B->C->D sequence, no extra phases invented)*
*Design source: `.planning/features/open-core-edition-design.md`*
