# Phase 7: CRM + Granola + Task Intelligence - Research

**Researched:** 2026-05-07
**Domain:** CRM PostgreSQL, Granola REST API polling, NER-triggered contact extraction, task tracking, email/in-app notifications
**Confidence:** HIGH (codebase), MEDIUM (Granola API), HIGH (patterns)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D1 — CRM : architecture interne**
CRM = tables PostgreSQL dans memory-api. Pas de service externe.
Deux types de contacts : `direct` (interaction réelle, truth_level min `WORKING`) et `mass` (liste de campagne, `EPHEMERAL` jusqu'à interaction réelle).
Enrichissement automatique : chaque mention nominative détectée dans un memory_item (par Claude NER) crée ou enrichit un contact. Le truth_level du contact monte au fur et à mesure des interactions.

**D2 — CRM : paid tier enforcement**
CRM et Task tracking sont réservés au tier **Team** et **Enterprise**. Un user Starter ne voit pas ces features. Enforcement via `team_plan` check sur les endpoints `/v1/crm/*` et `/v1/tasks/*`.

**D3 — Granola : ingestion**
Priorité : webhook Granola → memory-api endpoint dédié `/v1/integrations/granola/webhook`.
Fallback : polling API Granola si webhook indisponible.
Extraction : résumé, participants, action items, décisions, contexte projet.

**D4 — Task tracking : modèle de données**
Tâches stockées dans PostgreSQL. `assigned_to` référence `contact_id`. Champs minimaux : `title`, `description`, `status` (todo/in_progress/done), `priority`, `assigned_to` (contact_id), `created_by` (user_id), `source` (granola/agent/chat), `source_ref` (memory_item_id), `due_date`, `team_scope`, `project_scope`.

**D5 — Auto-génération de tâches : trois déclencheurs**
1. Action items Granola (extraction lors de l'ingestion)
2. Outputs agents avec `contains_action: true` ou pattern ("TODO", "à faire", "action requise")
3. Mentions dans LibreChat (Claude détecte les intentions de tâche)

**D6 — Notifications**
Email pour tous les contacts assignés (internes + externes). In-app LibreChat pour membres internes uniquement. Dashboard tasks dans le dashboard projets existant.

### Deferred Ideas (OUT OF SCOPE)
- Sync tâches vers Linear/Notion/Jira
- Éditeur de campagne mailing intégré
- Mobile push notifications
- RBAC granulaire sur les contacts
- Lead scoring automatique
</user_constraints>

---

## Summary

Phase 7 greffe trois fonctionnalités sur la memory-api existante — CRM, ingestion Granola, et task tracking — toutes en tables PostgreSQL et en routers FastAPI supplémentaires. La plus grande surprise de la recherche est que **Granola n'a pas de webhook** : la documentation officielle confirme que les webhooks sont "on the roadmap" et que la seule option aujourd'hui est le polling REST (`GET /v1/notes`). Le plan doit donc partir sur un modèle polling-first plutôt que webhook.

La deuxième découverte clé est l'absence de colonne `team_plan` dans la table `teams` existante (vérifiée sur toutes les migrations 0001–0007) : il faudra une migration `0008_team_plan` pour ajouter ce champ avant d'implémenter le paid-tier enforcement des endpoints CRM et tasks.

Le reste des patterns (router FastAPI, migration Alembic, ingestion agent, NER graphiti-service) est bien établi dans le codebase et directement réutilisable.

**Primary recommendation:** Commencer par la migration schéma (0008 + 0009), puis les routers CRM/tasks, puis le polling Granola, puis les notifications. Dépendances strictes dans cet ordre.

---

## Granola Integration

### API Availability — VERDICT: Polling uniquement (pas de webhook en 2026)

**Source vérifiée** : docs.granola.ai/help-center/sharing/integrations/personal-api [VERIFIED: WebFetch docs.granola.ai]

- **Webhooks** : NON disponibles. Citation exacte : *"Not yet — you need to poll the API for new notes. Webhooks are on our roadmap."*
- **Plan requis** : Personal API key = plan Business ($14/user/month) ou Enterprise. Les plans Free et Pro n'ont PAS d'accès API.
- **Rate limits** : 25 requêtes burst, 5 req/s sustained par workspace.
- **Authentification** : Bearer token, format `grn_YOUR_API_KEY` dans l'en-tête Authorization.

### Endpoints REST disponibles [VERIFIED: WebFetch docs.granola.ai/api-reference/openapi.json]

| Endpoint | Description |
|----------|-------------|
| `GET /v1/notes` | Liste les notes avec pagination (cursor), filtre par `created_after` / `updated_after` |
| `GET /v1/notes/{note_id}` | Note complète avec transcript optionnel (`?include_transcript=true`) |
| `GET /v1/folders` | Liste des dossiers avec pagination |

### Schéma Note — champs retournés [VERIFIED: openapi.json]

```json
{
  "id": "not_xxxxxxxxxxxxxx",
  "object": "note",
  "title": "Meeting title",
  "owner": { "name": "Alice", "email": "alice@co.com" },
  "created_at": "2026-05-07T10:00:00Z",
  "updated_at": "2026-05-07T11:00:00Z",
  "web_url": "https://app.granola.ai/notes/...",
  "calendar_event": {
    "event_title": "Product sync",
    "scheduled_start_time": "...",
    "scheduled_end_time": "...",
    "organiser": { "name": "...", "email": "..." },
    "invitees": [{ "email": "bob@co.com" }]
  },
  "attendees": [{ "name": "Alice", "email": "alice@co.com" }],
  "folder_membership": [{ "id": "...", "name": "Team folder" }],
  "summary_text": "Plain text AI summary...",
  "summary_markdown": "## Markdown summary...",
  "transcript": [
    { "speaker": { "source": "microphone" }, "text": "...", "start_time": 0.0, "end_time": 5.2 }
  ]
}
```

**Points importants :**
- `attendees` = participants détectés (provient du calendar event + microphone detection)
- `calendar_event.invitees` = liste des invités de l'event Google Calendar
- **Action items et decisions** : PAS de champ structuré dédié dans l'API REST. Ils sont dans le `summary_text` / `summary_markdown` générés par l'IA de Granola. L'extraction doit se faire par LLM (Claude) sur le texte du résumé, exactement comme graphiti-service le fait déjà pour les memory_items.

### Stratégie d'ingestion : polling-first [ASSUMED: architecture préférable]

Deux options pour déclencher l'ingestion :

**Option A (recommandée) : granola-sync service dédié** — calqué sur `drive-sync/`
- Conteneur FastAPI + boucle asyncio, poll toutes les N minutes
- Stocke `last_polled_at` par `(team_scope, granola_api_key)` dans PostgreSQL
- Requête `GET /v1/notes?created_after=<last_polled_at>`
- Envoie le contenu à memory-api `/v1/integrations/granola/ingest` (interne)
- Avantage : isolé, failsoft, pattern existant à copier

**Option B : endpoint webhook façade dans memory-api** — garder le design D3 mais sans webhook réel
- `POST /v1/integrations/granola/webhook` reçoit quand même un payload (depuis granola-sync ou future intégration)
- granola-sync poste vers cet endpoint — memory-api ne voit que la même interface
- Avantage : quand Granola livrera les vrais webhooks, il suffira de pointer leur webhook vers cette URL sans changer memory-api

**Recommandation** : combiner les deux. Un service `granola-sync` qui poll et poste vers `POST /v1/integrations/granola/ingest` dans memory-api. L'endpoint interne `/v1/integrations/granola/webhook` reste prévu pour les vrais webhooks futurs.

### Extraction depuis le résumé Granola (LLM)

Pas d'action items structurés dans l'API. Pipeline d'extraction :
1. Récupérer `summary_text` + `summary_markdown`
2. Appeler Claude (via agent-runtime ou directement dans granola-sync) avec un prompt structuré :
   - Extraire : `{participants, action_items, decisions, project_context}`
   - Format JSON structured output
3. Participants → contacts CRM (type=`direct`)
4. Action items → tasks (source=`granola`)
5. Decisions → memory_items avec `metadata.decision=true`
6. Résumé global → memory_item (source=`granola`, truth_level=`WORKING`)

Ce pipeline est identique à ce que fait `agent-runtime/agents/ingestion_agent.py` pour Drive — réutiliser le même LangGraph graph d'ingestion ou le dupliquer.

---

## CRM Architecture

### Migration 0008 : table `team_plan` sur `teams` [VERIFIED: grep toutes migrations 0001-0007]

La table `teams` actuelle n'a **pas** de colonne `plan` ou `team_plan`. Les migrations 0001–0007 ont été vérifiées : aucune référence à ce champ. Il faut une migration `0008_team_plan` :

```python
# Migration 0008 — ajouter team_plan aux teams
op.add_column(
    "teams",
    sa.Column(
        "plan",
        sa.String(16),
        nullable=False,
        server_default="starter",
    )
)
op.create_check_constraint(
    "teams_plan_check",
    "teams",
    "plan IN ('starter', 'team', 'enterprise')"
)
```

La prochaine migration libre est **0008** (0007 = github_users, la plus récente).

### Migration 0009 : tables `contacts` et `crm_list_memberships` [ASSUMED: schéma proposé]

```sql
-- contacts
CREATE TABLE contacts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    team_scope VARCHAR(64) NOT NULL REFERENCES teams(slug),
    contact_type VARCHAR(8) NOT NULL CHECK (contact_type IN ('direct','mass')),
    -- Identité
    full_name VARCHAR(256),
    email VARCHAR(256),
    company VARCHAR(256),
    role VARCHAR(256),
    -- Tagging contract (appliqué aux contacts comme aux memory_items)
    truth_level VARCHAR(16) NOT NULL DEFAULT 'EPHEMERAL'
        CHECK (truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')),
    confidence FLOAT NOT NULL DEFAULT 1.0,
    source VARCHAR(128) NOT NULL,  -- 'granola', 'chat', 'agent', 'import'
    project_scope VARCHAR(64),
    -- Mass contact specific
    list_source VARCHAR(256),       -- e.g. 'linkedin-campaign-2026-05'
    opt_in_status VARCHAR(16) DEFAULT 'unknown'
        CHECK (opt_in_status IN ('unknown','opted_in','opted_out')),
    list_added_at TIMESTAMPTZ,
    -- Interaction tracking
    interaction_count INT NOT NULL DEFAULT 0,
    last_interaction_at TIMESTAMPTZ,
    -- Standard
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index clés
CREATE UNIQUE INDEX idx_contacts_team_email ON contacts(team_scope, email)
    WHERE email IS NOT NULL;
CREATE INDEX idx_contacts_team ON contacts(team_scope);
CREATE INDEX idx_contacts_type ON contacts(contact_type);
```

Note : pas de FK `source_memory_item_id` dans le schéma contacts — la traçabilité se fait via `audit_log` (action=`crm.contact.created`, payload contient le memory_item_id source).

### Migration 0010 : table `tasks`

```sql
CREATE TABLE tasks (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    -- Tagging contract
    team_scope VARCHAR(64) NOT NULL,
    project_scope VARCHAR(64),
    -- Contenu
    title VARCHAR(512) NOT NULL,
    description TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'todo'
        CHECK (status IN ('todo','in_progress','done','cancelled')),
    priority VARCHAR(8) NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low','normal','high','urgent')),
    due_date DATE,
    -- Assignments
    assigned_to UUID REFERENCES contacts(id) ON DELETE SET NULL,
    created_by UUID NOT NULL REFERENCES users(id),
    -- Provenance (D5 — three sources)
    source VARCHAR(16) NOT NULL CHECK (source IN ('granola','agent','chat','manual')),
    source_ref UUID,  -- memory_item_id qui a généré la tâche
    -- Standard
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tasks_team ON tasks(team_scope);
CREATE INDEX idx_tasks_assigned ON tasks(assigned_to);
CREATE INDEX idx_tasks_status ON tasks(team_scope, status);
```

**Récapitulatif des migrations Phase 7 :**

| Migration | Contenu |
|-----------|---------|
| 0008 | `plan` column sur `teams` (starter/team/enterprise) |
| 0009 | Table `contacts` |
| 0010 | Table `tasks` |

### Contact extraction trigger depuis NER

Le pipeline actuel (Phase 5) :

```
POST /v1/memory/upsert
  → write memory_item
  → asyncio.create_task(_enrich_with_graphiti(content, team_scope))
      → POST graphiti-service:8300/v1/ingest
          → graphiti_core.add_episode() → NER Claude → entités Neo4j
```

graphiti-service fait le NER mais **n'expose pas les entités détectées via son API** — il les écrit directement dans Neo4j via graphiti-core. Il n'y a pas d'endpoint `GET /v1/entities` ou de callback vers memory-api.

**Pour l'extraction CRM, deux options :**

**Option A (recommandée) : extraction Claude inline dans memory-api**
- Après chaque `POST /v1/memory/upsert`, lancer un background task `asyncio.create_task(_extract_contacts(content, team_scope))`
- `_extract_contacts` appelle Claude directement (via `anthropic` SDK, comme agent-runtime le fait)
- Prompt : "Extrait les noms de personnes avec email si disponible. Retourne JSON array."
- Upsert dans `contacts` table : `ON CONFLICT (team_scope, email) DO UPDATE SET interaction_count = interaction_count + 1`
- Fail-soft (comme `_enrich_with_graphiti`) — la mémoire est écrite même si le NER plante

**Option B : appeler graphiti-service avec callback**
- Modifier graphiti-service pour qu'il retourne les entités Person détectées
- Plus complexe, nécessite changement dans graphiti-service
- Non recommandé — trop de couplage

**Pattern à utiliser** (déjà dans memory.py) :

```python
# Dans upsert_item, après le commit :
if body.item.content:
    asyncio.create_task(_enrich_with_graphiti(body.item.content, team_scope))
    # Phase 7 — CRM contact extraction (paid tier only, fail-soft)
    asyncio.create_task(_extract_crm_contacts(body.item.content, team_scope, session))
```

### Paid tier enforcement pattern [VERIFIED: codebase grep]

La table `teams` n'a PAS de colonne `plan` (confirmé). Le pattern d'enforcement doit donc :
1. Migration 0008 : ajouter `plan VARCHAR(16) DEFAULT 'starter'` sur `teams`
2. Helper dans `deps.py` ou `auth.py` :

```python
async def require_paid_tier(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(get_team_scope),
) -> str:
    """Raises 403 if the team's plan is 'starter'. Used for CRM + tasks endpoints."""
    row = await session.execute(
        sa.text("SELECT plan FROM teams WHERE slug = :slug"),
        {"slug": team_scope}
    )
    result = row.fetchone()
    if result is None or result.plan == "starter":
        raise HTTPException(403, "CRM and task tracking require a Team or Enterprise plan")
    return team_scope
```

3. Utiliser comme dependency sur tous les endpoints `/v1/crm/*` et `/v1/tasks/*` :

```python
@router.get("/crm/contacts")
async def list_contacts(
    team_scope: str = Depends(require_paid_tier),  # enforces paid tier
    ...
):
```

**Attention** : le `require_paid_tier` appelle lui-même `get_team_scope` (qui vérifie la membership). Il ne doit pas être chaîné naïvement si `get_team_scope` est aussi injecté séparément — utiliser une seule dépendance combinée pour éviter deux requêtes DB.

---

## Task Tracking

### Auto-génération : implémentation des trois déclencheurs

**Déclencheur 1 : Action items Granola**
- granola-sync extrait les action items du `summary_text` via Claude
- POST vers `POST /v1/tasks` dans memory-api avec `source=granola`, `source_ref=<memory_item_id>`
- `assigned_to` = contact_id résolu depuis l'email extrait (ou null si non résolu)

**Déclencheur 2 : Outputs agents**
- memory-api inspecte le metadata des memory_items écrits par des agents
- Pattern dans `upsert_item` :
```python
if body.item.metadata.get("contains_action") is True:
    asyncio.create_task(_create_task_from_memory_item(body.item, team_scope))
```
- `_create_task_from_memory_item` : Claude extrait le titre + assigné depuis le contenu, insère dans `tasks`
- Alternative légère : pattern simple regex/keyword avant LLM ("TODO:", "action required:", "à faire:")

**Déclencheur 3 : Mentions LibreChat**
- librechat-bridge surveille déjà les messages via MongoDB change streams
- Extension : après chaque message inséré en mémoire, background task de détection d'intention de tâche
- Si détecté : créer une task avec `status=todo`, optionnellement poster une notification de confirmation dans LibreChat

### Assignation à des contacts externes

- `assigned_to` → FK vers `contacts(id)`, pas `users(id)` (D4)
- Un contact `mass` ou `direct` avec email peut recevoir une tâche même s'il n'est pas membre xbrain
- La notification se fait par email uniquement (aucun accès in-app pour les externes)
- Logique dans le handler de création de tâche :
  1. `contact.email IS NOT NULL` → envoyer email de notification
  2. Contact a un `user_id` correspondant dans `users` → envoyer aussi in-app

---

## Notifications

### Email : infrastructure [ASSUMED: SMTP via LibreChat config ou aiosmtplib standalone]

**État actuel** : aucun service SMTP dans le `docker-compose.yml` [VERIFIED: grep docker-compose.yml]. LibreChat a une config email (variables `EMAIL_SERVICE`, `EMAIL_HOST`, `EMAIL_PORT`, etc.) mais c'est configuré dans LibreChat, pas exposé comme service partagé.

**Deux options :**

**Option A (recommandée) : aiosmtplib dans memory-api**
- `pip install aiosmtplib` + `email` stdlib
- Config via env vars : `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`
- Background task async (fail-soft) après création de tâche avec assigné
- Gabarit Jinja2 simple : "Vous avez une nouvelle tâche : {title}"
- Pas de service supplémentaire — memory-api gère l'envoi directement

**Option B : fastapi-mail** [CITED: pypi.org/project/fastapi-mail]
- Plus complet (templates Jinja2, bulk, HTML), mais overhead de dépendance
- Overkill pour v1

**Configuration env vars à ajouter** (dans docker-compose.yml memory-api) :
```yaml
SMTP_HOST: ${SMTP_HOST:-}
SMTP_PORT: ${SMTP_PORT:-587}
SMTP_USER: ${SMTP_USER:-}
SMTP_PASSWORD: ${SMTP_PASSWORD:-}
SMTP_FROM: ${SMTP_FROM:-noreply@dejavu.cat}
SMTP_TLS: ${SMTP_TLS:-true}
```

Si `SMTP_HOST` n'est pas défini → notifications email désactivées (fail-soft, log warning).

### In-app LibreChat : pas d'API notification native [VERIFIED: WebSearch + LibreChat docs]

La recherche confirme : **LibreChat n'a pas d'endpoint dédié aux notifications in-app externes**. Collections MongoDB documentées (actions, agents, conversations, messages, users...) — `notifications` n'existe pas [CITED: gist.github.com/ChakshuGautam/fca45e48a362b6057b5e67145b82a994].

Une feature request GitHub (#12105) demande exactement ce comportement ("Make Remote Agent API Runs Visible in LibreChat UI") — non encore implémentée.

**Solution recommandée pour Phase 7 : SSE endpoint custom dans memory-api**

```
GET /v1/notifications/stream  (SSE, authenticated)
```

- Le dashboard tasks se connecte à cet endpoint en SSE pour recevoir les notifications en temps réel
- memory-api poste les events dans une queue asyncio (ou Redis pub/sub si besoin de scale) quand une tâche est créée/assignée
- LibreChat ne reçoit PAS de notifications in-app dans Phase 7 — c'est le dashboard tasks qui joue ce rôle

**Approche simplifiée v1 (recommandée)** : polling depuis le dashboard (GET /v1/tasks?assigned_to=me&status=todo&since=<last_check>) toutes les 30s. Plus simple qu'un SSE endpoint, suffisant pour v1.

### Dashboard tasks : extension du projects-dashboard existant

`apps/projects-dashboard/` n'existe pas comme répertoire dans le repo [VERIFIED: glob]. Le dashboard est probablement déployé via Firebase (Phase 5 pattern ou Phase 6 marketing-site). La phase 5 a créé un dashboard GitHub-auth + projets.

**À investiguer par le planner** : vérifier où le dashboard projets est actuellement déployé (Firebase site `xbrain-marketing`? ou sur `projects.dejavu.cat`?). Pour Phase 7, la nouvelle page `/tasks` peut être :
1. Une page HTML statique supplémentaire dans le même Firebase site
2. Un nouvel onglet dans le dashboard React/Vue si existant

Vu le pattern Phase 6 (HTML statique Firebase), une page `/tasks/index.html` statique qui consomme l'API `/v1/tasks` via fetch est la voie la plus simple et cohérente.

---

## Existing Patterns

### Drive-sync pattern adapté pour Granola

| Drive-sync | Granola-sync (à créer) |
|------------|------------------------|
| OAuth Fernet credentials en DB | API key Granola stockée en DB (encrypted Fernet, même pattern) |
| Poll `changes.list` → `pageToken` | Poll `GET /v1/notes?created_after=<cursor>` → cursor = `updated_at` ISO |
| `team_drive_mappings` table | Table `granola_integrations(id, team_scope, api_key_enc, last_polled_at)` |
| Export file text → ingestion agent | Parse note → LLM extraction → POST /v1/integrations/granola/ingest |
| Sentinel file healthcheck | Même pattern `/tmp/granola-sync-alive` |
| `POLL_INTERVAL_SECONDS` env var | `GRANOLA_POLL_INTERVAL_SECONDS` env var |
| Port 8200 webhook server | Pas de webhook server (Granola n'a pas de webhooks) |

### Router patterns memory-api [VERIFIED: codebase]

Pattern type pour un nouveau router (ex : `apps/memory-api/app/routes/crm.py`) :

```python
# Pattern extrait de memory.py / teams.py
router = APIRouter()

class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    team_scope: str
    contact_type: str
    full_name: str | None
    email: str | None
    truth_level: str
    source: str

@router.get("/crm/contacts", response_model=list[ContactOut])
async def list_contacts(
    session: AsyncSession = Depends(get_session),
    team_scope: str = Depends(require_paid_tier),  # paid tier check + membership
    limit: int = Query(default=50, ge=1, le=200),
):
    ...
```

Enregistrement dans `main.py` :
```python
from app.routes import crm, tasks, granola_integration
app.include_router(crm.router, prefix="/v1", tags=["crm"])
app.include_router(tasks.router, prefix="/v1", tags=["tasks"])
app.include_router(granola_integration.router, prefix="/v1", tags=["integrations"])
```

### Dernière migration Alembic [VERIFIED: glob alembic/versions/]

Migration la plus récente : **`0007_github_users.py`** (revision=`0007`, down_revision=`0006`).
Prochaine migration Phase 7 : **`0008_team_plan.py`**

Séquence Phase 7 :
- `0008_team_plan` — colonne `plan` sur `teams`
- `0009_crm_contacts` — table `contacts`
- `0010_tasks` — table `tasks`

### Config memory-api [VERIFIED: docker-compose.yml]

memory-api tourne sur port 8000, mem_limit 384m, 2 workers uvicorn. La RAM totale Phase 7 est approximativement :
- granola-sync (nouveau conteneur) : ~128m (léger, asyncio + httpx + anthropic SDK)
- Overhead contacts/tasks endpoints dans memory-api : négligeable (même process)
- Total delta Phase 7 : ~128m RAM supplémentaire

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| CRM contacts storage + CRUD | memory-api (API) | PostgreSQL (DB) | Invariant : toute donnée via memory-api |
| Contact extraction from NER | memory-api (background task) | graphiti-service (NER) | NER déjà dans graphiti, mais contacts écrits en SQL via memory-api |
| Granola polling | granola-sync (new service) | memory-api (ingest endpoint) | Pattern drive-sync isolé, fail-soft |
| Granola LLM extraction | granola-sync ou agent-runtime | Claude API | Extraction structurée nécessite LLM |
| Task auto-generation | memory-api (background task) | agent-runtime (complex cases) | Déclencheurs inline dans upsert pour Granola/agent ; librechat-bridge pour chat |
| Paid tier enforcement | memory-api (deps layer) | PostgreSQL (teams.plan) | RBAC au niveau API, pas frontends |
| Email notifications | memory-api (async task) | SMTP external | Fail-soft, env-var driven |
| In-app dashboard tasks | Static HTML (Firebase) | memory-api API | Polling simple, cohérent avec Phase 6 pattern |

---

## Technical Risks

### Risque 1 : Granola API — plan Business requis (bloquant)

**Ce qui va mal** : si l'équipe est sur un plan Free ou Pro Granola, l'intégration est bloquée à la source.
**Root cause** : Personal API keys nécessitent Business ($14/user/month) ou Enterprise.
**Mitigation** : le planner doit inclure une tâche de vérification du plan Granola actuel avant d'investir dans l'intégration. Si plan insuffisant, l'option community-webhook (monitoring du fichier `cache-v3.json` local) est une alternative pour macOS — mais non applicable sur serveur Linux.
**Confidence** : MEDIUM — le plan exact de l'équipe est inconnu.

### Risque 2 : Granola action items — pas de champ structuré

**Ce qui va mal** : `summary_text` contient les action items en prose, pas en JSON. L'extraction LLM peut rater des items ou en halluciner.
**Root cause** : l'API Granola ne fournit pas de champ `action_items[]` structuré dans son schéma.
**Mitigation** : prompt Claude structuré avec few-shot examples pour l'extraction. Garder le `source_ref` qui pointe vers le memory_item du résumé — l'humain peut toujours corriger via le dashboard.
**Confidence** : HIGH (risque connu, mitigation claire).

### Risque 3 : teams.plan — migration requise avant tout endpoint CRM/tasks

**Ce qui va mal** : si 0008 n'est pas appliquée avant le code qui lit `teams.plan`, les endpoints CRM retournent des erreurs SQL.
**Root cause** : colonne inexistante sur `teams` en production.
**Mitigation** : les migrations sont exécutées en premier dans le CMD docker-compose (`python -m alembic upgrade head && uvicorn...`). Ordre strict dans les plans : migration d'abord, code ensuite.

### Risque 4 : Contact deduplication — email NULL

**Ce qui va mal** : un contact sans email détecté dans un chat crée des doublons.
**Root cause** : l'index unique `contacts(team_scope, email)` est conditionnel (`WHERE email IS NOT NULL`). Deux contacts "Alice Dupont" sans email = deux rows séparées.
**Mitigation** : enrichissement par `full_name` en fallback. Ou déduplication manuelle via dashboard. Pour Phase 7 v1, tolérer les doublons est acceptable (D1 ne mentionne pas de dedup automatique).

### Risque 5 : LibreChat in-app notifications — pas d'API native

**Ce qui va mal** : les membres xbrain sur LibreChat ne voient pas les nouvelles tâches qui leur sont assignées.
**Root cause** : LibreChat n'expose pas d'API de notification externe — confirmé dans la recherche.
**Mitigation** : pour Phase 7, les notifications in-app se font via le dashboard tasks (polling ou SSE). Les membres utilisent le dashboard plutôt que LibreChat pour voir leurs tâches. C'est acceptable vu que D6 mentionne "dashboard tasks" et "in-app LibreChat" comme deux canaux séparés — ne pas bloquer Phase 7 sur LibreChat in-app.

### Risque 6 : granola-sync — RAM VM

La VM est à 99% disque (STATE.md) et la RAM est serrée. Un nouveau conteneur granola-sync (~128m) est manageable mais le disque doit être agrandi AVANT Phase 7.
**Mitigation** : tâche de nettoyage disque / resize explicite dans Wave 0 du plan.

---

## Recommended Implementation Order

### Wave 0 : Fondations schéma (bloquantes pour tout le reste)
1. Migration `0008_team_plan` — ajouter `plan` sur `teams`
2. Migration `0009_crm_contacts` — table `contacts`
3. Migration `0010_tasks` — table `tasks`
4. Nettoyage disque VM (docker system prune, ou resize disque)

### Wave 1 : CRM CRUD + paid tier enforcement
5. Helper `require_paid_tier` dans `deps.py`
6. Router `apps/memory-api/app/routes/crm.py` — CRUD contacts
7. Router `apps/memory-api/app/routes/tasks.py` — CRUD tasks
8. Enregistrement dans `main.py`

### Wave 2 : Granola ingestion
9. Service `apps/granola-sync/` — boucle polling + extraction LLM
10. Table `granola_integrations` (migration 0011 ou incluse dans 0009)
11. Endpoint `POST /v1/integrations/granola/ingest` dans memory-api
12. Admin endpoint `POST /v1/admin/granola-integration` (enregistrer une API key)
13. Contact extraction depuis participants Granola
14. Task creation depuis action items extraits

### Wave 3 : Auto-génération tâches depuis memory_items
15. Background task `_extract_crm_contacts` dans `memory.py` (upsert hook)
16. Background task `_maybe_create_task` dans `memory.py` pour agents avec `contains_action=true`
17. Extension librechat-bridge pour détection intentions tâche dans chats

### Wave 4 : Notifications + Dashboard
18. Notification email (`aiosmtplib`) — tâche assignée
19. Dashboard tasks (page HTML statique Firebase `/tasks/index.html`)
20. Nginx route si nécessaire pour le dashboard

---

## Open Questions (RESOLVED)

1. **Plan Granola de l'équipe** — **RESOLVED:** Si plan insuffisant → granola-sync démarre mais ne polle rien (fail-soft). CRM + tasks fonctionnent indépendamment. Plan Business/Enterprise requis pour activer le polling. À vérifier en entrée de phase avant d'exécuter 07-05/07-08.

2. **Dashboard tasks location** — **RESOLVED:** HTML statique dans `apps/projects-dashboard/public/` — même pattern que Phase 5. Plan 07-07 implémente `tasks.html` dans ce répertoire.

3. **librechat-bridge extension** — **RESOLVED:** Implémenté en 07-09 — extension `librechat-bridge` pour chat hook. Détection Claude des intentions de tâche dans les messages ("fais ça", "quelqu'un doit", "@nom fait X") avec création auto-task.

4. **Granola API key management** — **RESOLVED:** Clé par équipe, stockage Fernet (cf. table `granola_integrations` migration 0009), admin endpoint pattern `admin_drive.py` (`_is_admin` + `_require_fernet`).

---

## Sources

### Primary (HIGH confidence)
- Granola OpenAPI spec : [docs.granola.ai/api-reference/openapi.json](https://docs.granola.ai/api-reference/openapi.json) — schéma Note complet, tous les endpoints
- Granola Personal API : [docs.granola.ai/help-center/sharing/integrations/personal-api](https://docs.granola.ai/help-center/sharing/integrations/personal-api) — no webhooks confirmed
- Codebase xbrain : migrations 0001-0007, memory-api routes, drive-sync pattern — vérifiés directement

### Secondary (MEDIUM confidence)
- LibreChat collections listing (architecture doc) : [gist.github.com/ChakshuGautam/fca45e48a362b6057b5e67145b82a994](https://gist.github.com/ChakshuGautam/fca45e48a362b6057b5e67145b82a994) — pas de collection `notifications`
- LibreChat issue #12105 : notification in-app depuis API externe = feature request non implémentée
- Granola Personal API plan requirements : confirmé Business/Enterprise via docs officielle

### Tertiary (LOW confidence)
- owengretzinger/granola-webhook : community solution via cache-v3.json local — non applicable en production server

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | granola-sync doit être un service séparé (pattern drive-sync) | Granola Integration | Pourrait être inline dans memory-api — mais isolation préférable |
| A2 | aiosmtplib est la meilleure option email (pas fastapi-mail) | Notifications | fastapi-mail plus complet pour templates — mais overhead |
| A3 | Dashboard tasks = page HTML statique Firebase (cohérent Phase 6) | Notifications | Si le dashboard Phase 5 est React/Vue, build step différent |
| A4 | Extraction LLM inline dans memory-api (pas via graphiti-service) | CRM Architecture | Pourrait utiliser graphiti NER existant — mais graphiti ne retourne pas les entités via API |
| A5 | Granola API key par équipe (modèle Enterprise) | Open Questions | Si Personal API keys seulement, schéma granola_integrations + UI différents |

---

**Confidence breakdown:**
- Standard Stack : HIGH — entièrement défini par codebase existant
- Granola integration : MEDIUM — API documentée, mais plan Granola équipe inconnu
- Architecture patterns : HIGH — patterns bien établis dans phases précédentes
- Paid tier enforcement : HIGH — colonne manquante identifiée, migration claire
- Notifications : MEDIUM — email straightforward, in-app LibreChat non supporté nativement

**Research date:** 2026-05-07
**Valid until:** 2026-06-07 (Granola pourrait livrer webhooks entre-temps)
