# Phase 7: CRM + Granola + Task Intelligence - Context

**Gathered:** 2026-05-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 7 transforme le brain en système actif sur trois axes :

1. **CRM interne** — contacts extraits automatiquement depuis tout ce qui passe par le brain (chats, clips, notes Granola, outputs agents). Deux types de contacts : directs (interactions réelles) et mass contacts (mailing lists, LinkedIn campaigns). Organisé par activité réelle de la compagnie telle que reflétée dans le brain. **Fonctionnalité paid tier uniquement.**

2. **Intégration Granola** — notes de réunion ingérées via webhook (priorité) ou polling API (fallback). Tout est extrait : résumé, participants, actions items, décisions, contexte projet. Les meetings sont traités comme une source de brain content de premier ordre — aussi riche qu'un document Drive.

3. **Task tracking + notifications** — tâches générées automatiquement depuis trois sources (action items meetings, outputs agents avec décisions/livrables, mentions explicites dans LibreChat). Tâches assignables aux contacts CRM (internes et externes). Notifications email + in-app (LibreChat + dashboard dédié). **Fonctionnalité paid tier uniquement.**

**Hors périmètre Phase 7 :**
- Sync vers outils externes (Linear, Notion, Jira) — pas en Phase 7
- Éditeur de campagne mailing intégré — outil de composition de mailing hors scope
- RBAC granulaire sur les contacts — accès par team_scope suffit en v1
- Mobile push notifications

</domain>

<decisions>
## Implementation Decisions

### D1 — CRM : architecture interne

**Décision :** CRM = tables PostgreSQL dans memory-api. Pas de service externe.

Deux types de contacts dans le schéma :
- **`direct`** — personne avec qui la team a une interaction réelle (meeting Granola, échange, assignation de tâche). Confiance : au minimum `WORKING`.
- **`mass`** — personne dans une liste de campagne (mailing, LinkedIn outreach). Confiance : `EPHEMERAL` jusqu'à interaction réelle.

Enrichissement automatique : chaque mention nominative détectée dans un memory_item (par Claude NER) crée ou enrichit un contact. Le truth_level du contact monte au fur et à mesure des interactions.

### D2 — CRM : paid tier enforcement

CRM et Task tracking sont réservés au tier **Team** et **Enterprise**. Un user Starter ne voit pas ces features. Enforcement via `team_plan` check sur les endpoints `/v1/crm/*` et `/v1/tasks/*`.

### D3 — Granola : ingestion

**Priorité :** webhook Granola → memory-api endpoint dédié `/v1/integrations/granola/webhook`.
**Fallback :** polling API Granola si webhook indisponible (le researcher vérifie la doc API Granola).

Ce qui est extrait de chaque note de réunion :
- Résumé de la réunion → indexé comme memory_item (source=`granola`, truth_level=`WORKING`)
- Participants détectés → création/enrichissement automatique de contacts CRM (type=`direct`)
- Action items → création automatique de tâches dans task tracking
- Décisions prises → indexées séparément avec tag `decision: true` pour promotion truth-level facilitée
- Contexte projet → linkage au project_scope si détecté

### D4 — Task tracking : modèle de données

Tâches stockées dans PostgreSQL. Lien fort avec le CRM : `assigned_to` référence un `contact_id` (pas seulement un `user_id`). Les contacts externes peuvent recevoir des tâches → notification par email uniquement (pas in-app).

Champs minimaux : `title`, `description`, `status` (todo/in_progress/done), `priority`, `assigned_to` (contact_id), `created_by` (user_id), `source` (granola/agent/chat), `source_ref` (memory_item_id à l'origine), `due_date`, `team_scope`, `project_scope`.

### D5 — Auto-génération de tâches : trois déclencheurs

1. **Action items Granola** — détectés lors de l'ingestion de notes de réunion (Claude extrait les phrases d'action)
2. **Outputs agents** — tout memory_item écrit par un agent avec `contains_action: true` ou détection pattern ("TODO", "à faire", "action requise", verbe + assigné)
3. **Mentions dans LibreChat** — Claude détecte les intentions de tâche dans les chats ("fais ça", "quelqu'un doit", "@nom fait X") → tâche créée avec confirmation optionnelle

### D6 — Notifications

- **Email** : tous les contacts assignés (internes + externes)
- **In-app LibreChat** : membres internes uniquement — via le système de notifications LibreChat existant ou un endpoint SSE dédié
- **Dashboard tasks** : nouvelle page `/tasks` dans le dashboard projets existant (`projects.dejavu.cat` ou équivalent) — liste des tâches par team, filtrables par assigné/statut/projet

### Folded Todos

Ces 3 todos ouverts ont été intégrés directement dans le périmètre Phase 7 :
- **#36 CRM auto-populate depuis le brain** → D1 + D2
- **#37 Intégration Granola → brain** → D3
- **#38 Task tracking auto + notifications team** → D4 + D5 + D6

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Architecture existante
- `.planning/PROJECT.md` — contrat de tagging obligatoire, contraintes OSS, multi-frontend invariant
- `.planning/REQUIREMENTS.md` — requirements AUTH, TEAM, MEM, TRUTH référencés par Phase 7
- `.planning/STATE.md` — état courant de l'infra, décisions accumulées Phases 1-6

### Phases précédentes pertinentes
- `.planning/phases/05-plateforme-projets-equipe/05-CONTEXT.md` — Graphiti (extraction entités), auth GitHub+Google, dashboard projets
- `.planning/phases/03-graphe-extraction-integrations/03-CONTEXT.md` — mcp-gateway, drive-sync, Neo4j patterns
- `.planning/phases/04-consolidation-mcp-frontends-et-integrations/04-CONTEXT.md` — memory-api upsert, conversation logging patterns

### Codebase
- `apps/memory-api/` — FastAPI central, modèles Alembic, patterns endpoints existants
- `apps/memory-api/alembic/versions/` — dernière migration pour numéro de la prochaine
- `infrastructure/docker-compose.yml` — services existants, réseau, volumes
- `infrastructure/nginx/conf.d/` — routing existant à étendre pour `/v1/crm/*` et `/v1/tasks/*`

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `apps/memory-api/routers/` — pattern FastAPI router à dupliquer pour `/v1/crm/` et `/v1/tasks/`
- `apps/memory-api/alembic/` — pipeline migration Alembic pour nouvelles tables contacts + tasks
- `apps/drive-sync/` — pattern ingestion document (fetch → extract → POST memory_items) réutilisable pour Granola
- `apps/graphiti-service/` — NER + extraction entités Claude déjà en place, à appeler pour détection contacts et action items

### Established Patterns
- Tout endpoint écrit en FastAPI async avec `asyncpg` pour PostgreSQL
- Chaque memory_item porte le contrat de tagging 7 champs — les contacts et tasks doivent aussi le porter (`team_scope`, `truth_level`, `source`, etc.)
- Migrations Alembic numérotées séquentiellement (dernière connue : 0008 Phase 5)
- Services Docker isolés avec healthcheck + dépendances déclarées dans compose

### Integration Points
- **Granola webhook** → nouvel endpoint `POST /v1/integrations/granola/webhook` dans memory-api
- **Contact creation** → déclenché depuis memory-api sur chaque write qui contient une entité personne (via graphiti-service NER existant)
- **Task creation** → déclenché depuis 3 sources : ingestion Granola, agent outputs, LibreChat chat hook
- **Notifications in-app** → LibreChat a un système de notifications — vérifier si exposé en API ou si SSE endpoint custom nécessaire
- **Dashboard tasks** → extension du `projects-dashboard` existant (`apps/projects-dashboard/`) ou nouvelle page dans marketing-site

</code_context>

<specifics>
## Specific Ideas

- Les contacts CRM sont organisés par **activité réelle de la compagnie telle que vue dans le brain** — pas juste par import statique. Un contact qui revient souvent dans les meetings, les chats et les décisions doit être mis en avant automatiquement.
- Les mass contacts (listes mailing / LinkedIn campaigns) sont un premier citoyen du CRM — pas une feature secondaire. Le modèle de données doit les accueillir dès le départ avec les champs de campagne (liste source, date d'ajout, statut opt-in).
- Un meeting Granola = source de brain aussi riche qu'un doc Drive. L'ingestion doit être aussi complète que drive-sync (résumé + entités + actions + décisions + contexte).
- Les tâches assignées à des contacts **externes** (non membres xbrain) sont notifiées par email uniquement — l'externe n'a pas accès à l'in-app.

</specifics>

<deferred>
## Deferred Ideas

- **Sync tâches vers Linear/Notion/Jira** — utile mais hors Phase 7. Phase 8 ou feature request.
- **Éditeur de campagne mailing intégré** — composition et envoi d'emails depuis xbrain hors scope v1.
- **Mobile push notifications** — pas de mobile app en v1.
- **RBAC granulaire sur les contacts** — accès par team_scope suffit pour l'instant.
- **Scoring automatique des contacts** (lead scoring) — intéressant mais complexité Phase 8+.

</deferred>

---

*Phase: 7-crm-granola-tasks*
*Context gathered: 2026-05-07*
