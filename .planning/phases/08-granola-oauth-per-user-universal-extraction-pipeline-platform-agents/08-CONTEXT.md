# Phase 8: Granola OAuth Per-User + Universal Extraction Pipeline + Platform Agents - Context

**Gathered:** 2026-05-08
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 8 rend xbrain fully self-service sur trois axes :

1. **Granola per-user** — chaque utilisateur entre sa propre clé API Granola dans son profil (champ settings), stockée chiffrée Fernet dans `granola_user_connections` (liée à `user_id`). granola-sync poll per-user en plus des team API keys existantes. Pas d'OAuth PKCE en Phase 8 — clé API manuelle, plus simple.

2. **Pipeline d'extraction universel** — toutes les sources (LibreChat, Chrome extension, Granola) alimentent automatiquement le CRM et les tâches. Extraction contacts + tasks : LibreChat via librechat-bridge (extension de `mongo_watcher`), Chrome ext via `memory-api` handler `/v1/memory/upsert` (fire-and-forget asyncio). Graphiti appelé dans les deux cas pour les mises à jour du graphe Neo4j.

3. **Registry d'agents de plateforme** — table `agent_definitions`, CRUD admin via `POST/GET/PATCH/DELETE /v1/admin/agents`. Invocation via `POST /v1/agents/{id}/invoke` : Claude direct depuis memory-api (Anthropic API), synchrone. L'agent `meeting-recap` est seedé au déploiement avec `auto_trigger: true` — déclenché automatiquement par granola-sync après chaque ingestion de meeting.

**Hors périmètre Phase 8 :**
- OAuth PKCE Granola (clé API manuelle suffit pour l'instant)
- Tool calls MCP réels depuis les agents (`tools_json` stocké, pas exécuté en Phase 8)
- Invocation LangGraph / agent-runtime pour les agents (Phase 9+)

</domain>

<decisions>
## Implementation Decisions

### D1 — Granola per-user : stockage credentials

**Décision :** Champ "Granola API key" dans le profil utilisateur (LibreChat settings ou onboarding). Clé stockée chiffrée Fernet dans une nouvelle table `granola_user_connections` (`user_id`, `api_key_enc`, `last_polled_at`, `team_scope`, `enabled`). Même schéma de chiffrement que `granola_integrations` (FERNET_KEY).

Pas d'OAuth PKCE en Phase 8 — l'utilisateur entre sa clé manuellement. OAuth PKCE est différé à une phase future.

### D2 — granola-sync : extension per-user

**Décision :** granola-sync étendu avec **deux boucles cohabitant** dans le même service :
- Boucle existante : `granola_integrations` (team API keys)
- Nouvelle boucle : `granola_user_connections` (user API keys)

Un seul container, polling configurable par `GRANOLA_POLL_INTERVAL_SECONDS`. La nouvelle boucle ingère les meetings de l'utilisateur et déclenche le même pipeline d'extraction que la boucle team (contacts, tasks, meeting-recap).

### D3 — Pipeline extraction universel : points de déclenchement

**Décision : split par source**

- **LibreChat** → librechat-bridge (`mongo_watcher.py`) : extension naturelle du pattern Phase 7 (`task_intent_detector.py`). Tout traitement post-message LibreChat reste dans le bridge.
- **Chrome extension** → `memory-api` handler `POST /v1/memory/upsert` : fire-and-forget asyncio task. La clip arrive déjà là, extraction inline évite un aller-retour réseau.
- **Granola** → granola-sync pipeline existant (déjà extrait contacts + tasks Phase 7, Phase 8 ajoute le déclenchement meeting-recap).

Dans les deux cas, Graphiti est appelé en fire-and-forget pour les mises à jour du graphe Neo4j.

### D4 — Platform agents : invocation

**Décision :** Claude direct depuis memory-api (Anthropic API). Synchrone — la réponse est retournée dans la même requête HTTP.

Schéma `agent_definitions` : `id`, `name`, `description`, `system_prompt`, `model`, `tools_json` (réservé, non exécuté en Phase 8), `enabled`, `auto_trigger` (bool), `created_by`.

`tools_json` est stocké pour les phases futures — Phase 8 ne les exécute pas. Si une phase future nécessite des tool calls MCP réels, la délégation à agent-runtime LangGraph sera ajoutée alors.

### D5 — Auto-trigger meeting-recap

**Décision :** `auto_trigger: true` par défaut sur `agent_definitions`. L'agent `meeting-recap` est seedé avec `auto_trigger: true` — granola-sync l'invoque automatiquement via `POST /v1/agents/{id}/invoke` après chaque ingestion de meeting (team ou per-user). La config est éditable par les admins.

Le recap généré est stocké comme `memory_item` (`source=agent`, `truth_level=WORKING`, `team_scope` hérité du meeting).

### D6 — Migration Alembic

Prochaine migration : **`0012`**. Tables à créer : `granola_user_connections`, `agent_definitions`.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Architecture & contrat de tagging
- `.planning/PROJECT.md` — contrat de tagging 7 champs obligatoire, contraintes OSS, multi-frontend invariant
- `.planning/REQUIREMENTS.md` — requirements référencés Phases 1-7
- `.planning/STATE.md` — état courant de l'infra, décisions accumulées

### Phases précédentes pertinentes
- `.planning/phases/07-crm-granola-tasks/07-CONTEXT.md` — CRM, tasks, Granola ingestion (D1-D6 Phase 7), paid tier enforcement, schémas contacts + tasks
- `.planning/phases/05-plateforme-projets-equipe/05-CONTEXT.md` — Graphiti (extraction entités), auth patterns, agent-runtime patterns
- `.planning/phases/03-graphe-extraction-integrations/03-CONTEXT.md` — Fernet encryption pattern Drive OAuth, Neo4j, MCP gateway

### Codebase — services à étendre
- `apps/granola-sync/app/granola_poller.py` — boucle team API key existante à étendre pour per-user
- `apps/granola-sync/app/extractor.py` — extracteur Claude existant (contacts, tasks, résumé)
- `apps/librechat-bridge/app/mongo_watcher.py` — point d'extension pour extraction contacts LibreChat
- `apps/librechat-bridge/app/task_intent_detector.py` — pattern de détection Phase 7 à suivre
- `apps/memory-api/app/routes/granola_integration.py` — pattern admin granola integration (Fernet, bridge auth)
- `apps/memory-api/app/routes/crm.py` — endpoints CRM existants (contacts upsert)
- `apps/memory-api/app/routes/tasks.py` — endpoints tasks existants
- `apps/memory-api/app/routes/admin_drive.py` — pattern OAuth callback + Fernet encryption à réutiliser
- `chrome-extension/background.js` — endpoint cible `/v1/memory/upsert` (point d'injection extraction)

### Infra
- `apps/memory-api/alembic/versions/0011_team_onboarding.py` — dernière migration (prochaine : 0012)
- `infrastructure/docker-compose.yml` — services existants, pas de nouveau container en Phase 8
- `infrastructure/nginx/conf.d/` — routing existant (pas de nouveaux paths nginx nécessaires a priori)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `apps/granola-sync/app/granola_poller.py` — logique de polling, pagination, retry, sentinel file — à dupliquer/étendre pour per-user loop
- `apps/granola-sync/app/extractor.py` — extraction Claude (résumé, participants, action items) — réutilisé tel quel pour per-user meetings
- `apps/memory-api/app/routes/granola_integration.py` — `_require_granola_fernet()`, `GranolaParticipantIn`, `GranolaNoteIn` — réutilisables pour `granola_user_connections`
- `apps/librechat-bridge/app/task_intent_detector.py` — pattern fire-and-forget asyncio + fail-soft à reproduire pour contact extraction
- `apps/memory-api/app/routes/admin_drive.py` — Fernet encryption pattern pour stocker les user API keys

### Established Patterns
- Tout endpoint FastAPI async avec `asyncpg` (SQLAlchemy Core async)
- Chaque `memory_item` porte les 7 champs de tagging — les recaps agents aussi
- Migrations Alembic numérotées séquentiellement, dernière : `0011`
- Services Docker sans nouveau container en Phase 8 — tout dans les services existants
- Graphiti appelé fire-and-forget depuis memory-api (pattern établi Phase 5)
- FERNET_KEY pour chiffrement credentials at rest (Granola API keys, Drive OAuth tokens)
- `_is_bridge()` check pour les appels service-to-service (granola-sync → memory-api)

### Integration Points
- **granola-sync → `granola_user_connections`** : nouvelle table queryée par la deuxième boucle du poller
- **librechat-bridge → contact extraction** : `mongo_watcher.py` déclenche extraction après `POST /v1/messages`
- **`/v1/memory/upsert` → contact extraction** : asyncio task fire-and-forget dans le handler
- **granola-sync → `POST /v1/agents/{id}/invoke`** : appel interne après ingestion si `auto_trigger: true`
- **`/v1/admin/agents` CRUD** : nouveau router dans `apps/memory-api/app/routes/` (pattern admin_projects existant)

</code_context>

<specifics>
## Specific Ideas

- Clé API Granola per-user : champ dans le profil LibreChat (onboarding existant) — pas une page dédiée
- `auto_trigger: true` par défaut sur `agent_definitions` — l'admin peut le désactiver par agent
- Le recap meeting-recap est stocké comme `memory_item` avec `source=agent`, `truth_level=WORKING` et lien vers le meeting source (`source_ref=granola_note_id`)
- `tools_json` dans `agent_definitions` est un champ JSON réservé — stocké mais non exécuté en Phase 8 (prévu pour Phase 9+ avec tool calls MCP)
- Contact extraction : fail-soft obligatoire — si Graphiti est down, le clip/message est quand même stocké, l'extraction est juste skippée silencieusement (pattern établi)

</specifics>

<deferred>
## Deferred Ideas

- **OAuth PKCE Granola** — connexion self-service popup sans clé manuelle. Différé : clé API manuelle suffit pour Phase 8.
- **Tool calls MCP réels depuis agents** — `tools_json` exécuté via mcp-gateway. Différé Phase 9+ avec délégation agent-runtime LangGraph.
- **Scoring automatique contacts** (lead scoring) — noté Phase 7, toujours différé.
- **Sync tâches vers Linear/Notion/Jira** — noté Phase 7, toujours différé.

</deferred>

---

*Phase: 8-granola-oauth-per-user-universal-extraction-pipeline-platform-agents*
*Context gathered: 2026-05-08*
