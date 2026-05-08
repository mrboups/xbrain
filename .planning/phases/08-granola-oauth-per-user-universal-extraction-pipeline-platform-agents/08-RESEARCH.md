# Phase 8: Granola Per-User + Universal Extraction + Platform Agents - Research

**Researched:** 2026-05-08
**Domain:** FastAPI extension patterns, Alembic migrations, asyncio fire-and-forget, LibreChat onboarding JS injection
**Confidence:** HIGH (all findings verified directly from codebase — no external lookups needed)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D1** — Granola per-user : champ "Granola API key" dans le profil utilisateur, stockée chiffrée Fernet dans `granola_user_connections` (`user_id` FK → `users.id`, `api_key_enc`, `last_polled_at`, `team_scope`, `enabled`). Même schéma Fernet que `granola_integrations`.
- **D2** — granola-sync étendu avec deux boucles cohabitant dans le même service : boucle existante (`granola_integrations`) + nouvelle boucle (`granola_user_connections`).
- **D3** — Extraction split par source : LibreChat via librechat-bridge (`mongo_watcher.py`), Chrome ext via `memory-api` handler `/v1/memory/upsert` (fire-and-forget asyncio), Granola via granola-sync pipeline existant.
- **D4** — Invocation agents : Claude direct depuis memory-api (Anthropic API), synchrone.
- **D5** — `auto_trigger: true` par défaut sur `agent_definitions`. Agent `meeting-recap` seedé, déclenché auto par granola-sync après chaque ingestion.
- **D6** — Prochaine migration Alembic : **0012**. Tables : `granola_user_connections` + `agent_definitions`.

### Claude's Discretion
- Schéma exact de `agent_definitions` (au-delà des champs listés en D4)
- Format exact du système prompt de l'agent `meeting-recap`
- Structure du verify-phase8.sh (6 tests couvrant les 6 success criteria ROADMAP)
- Ordre de séquencement des plans (wave structure)

### Deferred Ideas (OUT OF SCOPE)
- OAuth PKCE Granola (popup self-service sans clé manuelle)
- Tool calls MCP réels depuis agents (`tools_json` stocké, non exécuté en Phase 8)
- Invocation LangGraph / agent-runtime pour les agents (Phase 9+)
- Lead scoring automatique contacts
- Sync tâches vers Linear/Notion/Jira
</user_constraints>

---

## Summary

Phase 8 est une **phase d'extension pure** — aucun nouveau container Docker, aucune nouvelle infrastructure. Tout s'implémente dans des services existants : `memory-api`, `granola-sync`, `librechat-bridge`. Les patterns à suivre sont déjà établis et testés dans le codebase (Phases 7 et précédentes).

Les trois axes (Granola per-user, extraction universelle, agents) sont **indépendants au niveau base de données** mais partagent les mêmes helpers Fernet, le même client Anthropic, et le même pattern fire-and-forget asyncio. La migration 0012 crée deux tables sans dépendance entre elles — elle peut être écrite en un seul fichier.

Le principal risque d'exécution est **l'injection du champ Granola API key dans l'onboarding LibreChat** : `apps/librechat/patches/onboarding.js` est un gros fichier JS injecté dans LibreChat, et l'extension correcte requiert de comprendre la structure de l'overlay multi-étapes existant. Toute la logique serveur (memory-api routes) est en revanche straightforward.

**Primary recommendation:** Séquencer en 3 waves parallèles — (1) migration 0012, (2) granola-sync per-user loop + admin/agents CRUD + onboarding field, (3) agent invoke + meeting-recap seeding + extraction bridge/upsert.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Stockage clé Granola per-user (chiffré) | API / Backend (memory-api) | — | Fernet encryption → DB PostgreSQL. Même pattern que `granola_integrations`. |
| Polling meetings per-user | Background Worker (granola-sync) | API / Backend (memory-api ingest) | Deuxième boucle asyncio dans granola_poller.py, appelle `/v1/integrations/granola/ingest` |
| Saisie clé Granola par l'utilisateur | Frontend (LibreChat onboarding.js patch) | API / Backend (POST /v1/me/granola-key) | Champ input dans l'overlay existant, persist via memory-api |
| Extraction contacts depuis LibreChat | Background Worker (librechat-bridge) | API / Backend (contacts upsert) | Extension de mongo_watcher.py — pattern task_intent_detector.py |
| Extraction contacts depuis Chrome ext | API / Backend (memory-api upsert handler) | — | Fire-and-forget déjà en place pour graphiti + crm. Extension inline. |
| Registry agent_definitions CRUD | API / Backend (memory-api admin routes) | — | Pattern admin_projects.py / admin_drive.py |
| Invocation synchrone agent | API / Backend (memory-api) | Anthropic API (externe) | Direct Anthropic SDK depuis memory-api — pattern _get_anthropic() déjà en place |
| Seeding agent meeting-recap | Background Worker (granola-sync) | API / Backend (POST /v1/agents/{id}/invoke) | Déclenché après chaque ingest par la boucle poller |

---

## Standard Stack

### Core (déjà installé — pas de nouvelles dépendances)

| Library | Version | Purpose | Status |
|---------|---------|---------|--------|
| `anthropic` | (pinned in Dockerfiles) | Invocation Claude depuis memory-api et granola-sync | [VERIFIED: apps/memory-api/app/routes/memory.py, apps/granola-sync/app/extractor.py] |
| `cryptography` (Fernet) | (pinned) | Chiffrement at rest des API keys | [VERIFIED: apps/memory-api/app/routes/granola_integration.py] |
| `asyncpg` | (pinned) | Driver PostgreSQL async | [VERIFIED: apps/granola-sync/app/granola_poller.py] |
| `fastapi` | (pinned) | Router memory-api | [VERIFIED: apps/memory-api/app/main.py] |
| `sqlalchemy` (async) | (pinned) | ORM async queries | [VERIFIED: apps/memory-api/app/routes/*.py] |
| `alembic` | (pinned) | Migrations versionées | [VERIFIED: 0001..0011 présents] |
| `authlib` | (pinned) | Bridge JWT HS256 generation | [VERIFIED: apps/granola-sync/app/memory_client.py] |
| `httpx` | (pinned) | Appels HTTP async (Granola API + agents invoke) | [VERIFIED: apps/granola-sync/app/granola_poller.py] |
| `structlog` | (pinned) | Logging structuré JSON | [VERIFIED: tous les services] |
| `pydantic-settings` | (pinned) | Config depuis env vars | [VERIFIED: apps/*/app/config.py] |

**Aucune nouvelle dépendance Python requise pour Phase 8.** [VERIFIED: codebase scan]

---

## Architecture Patterns

### Fernet Encryption Pattern (réutilisé tel quel)

```python
# Source: apps/memory-api/app/routes/granola_integration.py
def _require_granola_fernet() -> Fernet:
    key = settings.FERNET_KEY or settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY
    if not key:
        raise HTTPException(500, "FERNET_KEY not configured")
    return Fernet(key.encode())

# Encrypt at write:
encrypted = fernet.encrypt(api_key.encode()).decode()

# Decrypt at read (granola_poller.py pattern):
f = Fernet(settings.FERNET_KEY.encode())
plain = f.decrypt(enc.encode()).decode()
```

[VERIFIED: apps/memory-api/app/routes/granola_integration.py + apps/granola-sync/app/granola_poller.py]

### Fire-and-Forget Asyncio Pattern (réutilisé tel quel)

```python
# Source: apps/memory-api/app/routes/memory.py (upsert handler)
asyncio.create_task(_extract_crm_contacts(content, team_scope, source))
asyncio.create_task(_maybe_create_task_from_action(item, team_scope))
asyncio.create_task(_enrich_with_graphiti(content, team_scope))
```

Phase 8 : ajouter `asyncio.create_task(_extract_crm_contacts_from_clip(...))` dans le handler `/v1/memory/upsert` — déjà présent, rien à changer pour la Chrome ext (l'extraction contacts fire-and-forget existe déjà sur ce path). [VERIFIED: apps/memory-api/app/routes/memory.py lines 344-352]

### Admin Router Pattern (à dupliquer pour /v1/admin/agents)

```python
# Source: apps/memory-api/app/routes/admin_projects.py (pattern compact)
router = APIRouter()

@router.post("/admin/agents", response_model=AgentOut, status_code=201)
async def create_agent(
    body: AgentCreateBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    # INSERT INTO agent_definitions ...
    await session.commit()
    return AgentOut(...)
```

`_is_admin` est dans `deps.py` — importable directement. [VERIFIED: apps/memory-api/app/deps.py line 180]

### Anthropic Client Pattern (réutilisé pour agent invoke)

```python
# Source: apps/memory-api/app/routes/memory.py lines 60-73
_anthropic_client: AsyncAnthropic | None = None

def _get_anthropic() -> AsyncAnthropic | None:
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        return None
    _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client

# Usage dans invoke:
msg = await client.messages.create(
    model=agent_def["model"],        # depuis agent_definitions
    max_tokens=4096,
    system=agent_def["system_prompt"],
    messages=[{"role": "user", "content": user_content}],
)
```

[VERIFIED: apps/memory-api/app/routes/memory.py + apps/granola-sync/app/extractor.py]

### Bridge JWT Pattern pour granola-sync → memory-api

```python
# Source: apps/granola-sync/app/memory_client.py
def _make_bridge_jwt() -> str:
    payload = {
        "sub": "granola-sync",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode({"alg": settings.JWT_ALGORITHM}, payload, settings.BRIDGE_SHARED_SECRET).decode()
```

Pour appeler `/v1/agents/{id}/invoke` depuis granola-sync, utiliser le même bridge JWT avec `team_scope` dans le header `X-Team-Scope`. [VERIFIED: apps/granola-sync/app/memory_client.py]

### Deuxième Boucle Poller Pattern (à ajouter dans granola_poller.py)

```python
# Inspired by: apps/granola-sync/app/granola_poller.py run_poll_loop()
async def run_poll_loop(database_url: str) -> None:
    pool = await asyncpg.create_pool(pg_url, min_size=1, max_size=2)
    while True:
        try:
            async with pool.acquire() as conn:
                # Boucle 1 — team integrations (existante, inchangée)
                rows = await conn.fetch("SELECT ... FROM granola_integrations ...")
                for row in rows:
                    await _process_team_integration(conn, row)
                # Boucle 2 — per-user connections (NOUVELLE Phase 8)
                user_rows = await conn.fetch(
                    "SELECT guc.id, guc.user_id, guc.api_key_enc, guc.last_polled_at, guc.team_scope "
                    "FROM granola_user_connections guc WHERE guc.enabled = true "
                    "ORDER BY guc.last_polled_at NULLS FIRST"
                )
                for row in user_rows:
                    await _process_user_connection(conn, row)
            SENTINEL_PATH.touch()
        except Exception as exc:
            log.error("poll_loop.error", error=str(exc))
        await asyncio.sleep(settings.GRANOLA_POLL_INTERVAL_SECONDS)
```

[ASSUMED: structure exacte de `_process_user_connection` — à implémenter en miroir de `_process_team_integration`]

### Onboarding JS Injection Pattern

Le fichier `apps/librechat/patches/onboarding.js` est un overlay modal multi-étapes injecté dans LibreChat. Il utilise des fonctions helpers `el()`, `setHtml()`, et gère un token xbrain via `/api/xbrain/token`.

Pour ajouter le champ Granola API key, deux approches :

**Option A (recommandée) :** Ajouter une étape 4 dans le flow onboarding existant si l'utilisateur n'a pas de clé Granola. Vue rendue conditionnellement après `renderWelcome()`.

**Option B :** Ajouter un champ dans le modal de settings/profil si LibreChat expose un hook — mais l'onboarding existant est la seule surface d'injection disponible via patch. [VERIFIED: apps/librechat/patches/onboarding.js]

Le token xbrain obtenu via `getToken()` permet d'appeler `POST /v1/me/granola-key` (nouvel endpoint à créer dans `me.py`) avec `{"api_key": "..."}`.

---

## New Files / Routes à Créer

### Alembic 0012 — deux tables en un seul fichier

```python
# apps/memory-api/alembic/versions/0012_granola_user_agents.py
revision = "0012"
down_revision = "0011"

def upgrade():
    # Table: granola_user_connections
    op.create_table(
        "granola_user_connections",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("team_scope", sa.String(64), nullable=False),
        sa.Column("api_key_enc", sa.Text, nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", ...), sa.Column("updated_at", ...),
    )
    op.create_index("idx_guc_user", "granola_user_connections", ["user_id"])
    op.create_unique_constraint("guc_user_uniq", "granola_user_connections", ["user_id"])

    # Table: agent_definitions
    op.create_table(
        "agent_definitions",
        sa.Column("id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=False),
        sa.Column("model", sa.String(128), nullable=False, server_default="claude-3-5-haiku-20241022"),
        sa.Column("tools_json", JSONB, nullable=True),    # réservé Phase 9+
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("auto_trigger", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_by", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", ...), sa.Column("updated_at", ...),
    )
```

[ASSUMED: schéma exact des colonnes — basé sur D4 CONTEXT.md et patterns existants]

### Nouveaux Endpoints memory-api

| Endpoint | Pattern Source | Auth |
|----------|---------------|------|
| `POST /v1/me/granola-key` | `me.py` + pattern Fernet `granola_integration.py` | User token (kind=user) |
| `GET /v1/me/granola-key` | `me.py` | User token — retourne `{"connected": bool, "last_polled_at": ...}` sans clé |
| `DELETE /v1/me/granola-key` | `me.py` | User token |
| `POST /v1/admin/agents` | `admin_projects.py` pattern | Admin only |
| `GET /v1/admin/agents` | idem | Admin only |
| `PATCH /v1/admin/agents/{id}` | idem | Admin only |
| `DELETE /v1/admin/agents/{id}` | idem | Admin only |
| `POST /v1/agents/{id}/invoke` | nouveau | Bridge JWT ou User token |

Le nouveau fichier de routes `apps/memory-api/app/routes/agents.py` couvre les 5 derniers endpoints. À inclure dans `main.py`. [VERIFIED: apps/memory-api/app/main.py — pattern d'inclusion des routers]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---------|-------------|-------------|
| Fernet encrypt/decrypt | Custom crypto | `cryptography.fernet.Fernet` (déjà utilisé) |
| Bridge JWT génération | Signature custom | `authlib.jose.jwt` (déjà utilisé dans granola-sync) |
| Async DB pool granola-sync | Connection management custom | `asyncpg.create_pool()` (déjà utilisé) |
| Anthropic client lifecycle | Instanciation à chaque call | Singleton global `_anthropic_client` (pattern établi) |
| Contact extraction LLM | NER custom | Pattern `_extract_crm_contacts()` déjà en place dans `memory.py` |

---

## Common Pitfalls

### Pitfall 1 : Bridge JWT missing `team_scope` claim pour `/v1/agents/{id}/invoke`

**What goes wrong:** granola-sync génère un bridge JWT via `_make_bridge_jwt()` qui ne contient pas `team_scope` dans le payload. Si `/v1/agents/{id}/invoke` utilise `get_team_scope` dependency, le `X-Team-Scope` header doit correspondre au `team_scope` du JWT.

**Why it happens:** Le bridge JWT de granola-sync est généré avec `sub=granola-sync, scope=bridge` sans `team_scope` fixe — le team_scope varie par meeting ingéré.

**How to avoid:** Deux options :
- A (recommandée) : `/v1/agents/{id}/invoke` utilise `get_current_principal` sans `get_team_scope` — le team_scope est passé dans le body JSON. Le handler vérifie `_is_bridge(principal)`.
- B : Régénérer le bridge JWT avec `team_scope` injecté par boucle (comme le fait `post_ingest` via header `X-Team-Scope`).

[VERIFIED: apps/granola-sync/app/memory_client.py — post_ingest passe X-Team-Scope en header]

### Pitfall 2 : `source` CHECK constraint sur `tasks` ne couvre pas `'agent'`

**What goes wrong:** `tasks_source_check` autorise `('granola','agent','chat','manual')` — [VERIFIED: 0010_tasks.py line 78]. Le recap stocké par meeting-recap comme `memory_item` a `source='agent'` — OK. Mais si Phase 8 crée des tasks depuis l'invocation d'un agent, `source='agent'` est valide.

**How to avoid:** Utiliser `source='agent'` pour les tasks créées par invocation d'agent. Pas besoin de migration.

### Pitfall 3 : Dedup `granola_user_connections` — un seul row par user

**What goes wrong:** Si l'utilisateur soumet sa clé deux fois, INSERT double.

**How to avoid:** UNIQUE constraint sur `user_id` dans la table (voir schema migration). Le UPSERT `ON CONFLICT (user_id) DO UPDATE SET api_key_enc = ...` gère la mise à jour de clé. [ASSUMED: à confirmer via contrainte dans migration]

### Pitfall 4 : `agent_definitions` seeding au démarrage vs migration

**What goes wrong:** Le seeding de l'agent `meeting-recap` doit être idempotent — un redémarrage memory-api ne doit pas insérer un doublon.

**How to avoid:** Utiliser `INSERT INTO agent_definitions ... ON CONFLICT (name) DO NOTHING`. Le seed peut être dans le Alembic `upgrade()` ou dans le `lifespan()` startup de memory-api. **Recommandé : dans la migration 0012 comme data migration** — c'est la façon la plus propre pour un seed déterministe. [VERIFIED: pattern de migration data existant absent dans 0009-0011 → à établir en Phase 8]

### Pitfall 5 : `last_polled_at` BEFORE `_fetch_notes` dans per-user loop

**What goes wrong:** Si l'appel Granola API échoue après UPDATE `last_polled_at`, les meetings de l'intervalle sont perdus.

**Why it happens:** Pattern décidé en Phase 7 : "UPDATE before fetch = at-most-once delivery" combiné avec dedup note_id = exactly-once-effective. [VERIFIED: apps/granola-sync/app/granola_poller.py lines 111-120 + STATE.md decision 07-08]

**How to avoid:** Conserver le même pattern pour la boucle per-user — UPDATE `last_polled_at` AVANT `_fetch_notes`.

### Pitfall 6 : `tools_json` dans agent invoke — ne pas l'exécuter en Phase 8

**What goes wrong:** `tools_json` est stocké dans `agent_definitions`. Si le handler `/v1/agents/{id}/invoke` tente de passer `tools_json` à l'API Anthropic, les tool calls retournés ne seront pas exécutés et Claude entrera dans une boucle incomplète.

**How to avoid:** Ne pas passer `tools` à `client.messages.create()` en Phase 8. `tools_json` est stocké uniquement, ignoré à l'invocation. [VERIFIED: D4 CONTEXT.md]

### Pitfall 7 : Onboarding JS injection — token lifecycle

**What goes wrong:** `_libreToken` capturé via XHR interceptor peut expirer. Le flow Granola key submission doit appeler `getToken()` (bridge token) qui prend ce LibreChat token pour demander un bridge JWT.

**How to avoid:** Appeler `getToken()` juste avant le submit de la clé Granola (pas en cache), pattern identique au flow existant `apiCall()`. [VERIFIED: apps/librechat/patches/onboarding.js lines 39-51]

---

## Code Examples

### Invocation synchrone d'un agent (pattern Phase 8)

```python
# apps/memory-api/app/routes/agents.py — POST /v1/agents/{id}/invoke
@router.post("/agents/{agent_id}/invoke")
async def invoke_agent(
    agent_id: UUID,
    body: AgentInvokeBody,       # {"content": str, "team_scope": str, "source_ref": str | None}
    principal: dict = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    # 1. Fetch agent definition
    row = (await session.execute(
        sa.text("SELECT * FROM agent_definitions WHERE id = :id AND enabled = true"),
        {"id": str(agent_id)},
    )).mappings().fetchone()
    if row is None:
        raise HTTPException(404, "Agent not found or disabled")

    # 2. Invoke Claude synchronously
    client = _get_anthropic()
    if client is None:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    msg = await client.messages.create(
        model=row["model"],
        max_tokens=4096,
        system=row["system_prompt"],
        # tools_json NOT passed — Phase 8 stores but does not execute tools
        messages=[{"role": "user", "content": body.content[:50000]}],
    )
    recap_text = msg.content[0].text if msg.content else ""

    # 3. Store recap as memory_item (source='agent', truth_level='WORKING')
    memory_item_id = uuid4()
    await session.execute(sa.text("""
        INSERT INTO memory_items (
            id, team_scope, content, source, source_ref,
            truth_level, confidence, visibility, validation_status, metadata
        ) VALUES (
            :id, :ts, :content, 'agent', :sref,
            'WORKING', 0.85, 'team', 'pending', :meta::jsonb
        )
    """), {
        "id": str(memory_item_id),
        "ts": body.team_scope,
        "content": recap_text,
        "sref": body.source_ref,
        "meta": json.dumps({"agent_id": str(agent_id), "agent_name": row["name"]}),
    })
    await session.commit()

    return {"memory_item_id": str(memory_item_id), "recap": recap_text}
```

[VERIFIED: pattern inspiré de apps/memory-api/app/routes/granola_integration.py + memory.py]

### Per-user granola key endpoint

```python
# apps/memory-api/app/routes/me.py — POST /v1/me/granola-key
@router.post("/me/granola-key", status_code=201)
async def set_granola_key(
    body: GranolaKeyBody,    # {"api_key": str}
    principal: dict = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
):
    if principal.get("kind") != "user":
        raise HTTPException(403, "User authentication required")
    user = principal["user"]
    fernet = _require_granola_fernet()   # import from granola_integration.py
    encrypted = fernet.encrypt(body.api_key.encode()).decode()
    # Upsert — one row per user
    await session.execute(sa.text("""
        INSERT INTO granola_user_connections (user_id, team_scope, api_key_enc, enabled)
        VALUES (:uid, :ts, :key, true)
        ON CONFLICT (user_id) DO UPDATE
        SET api_key_enc = EXCLUDED.api_key_enc, enabled = true, updated_at = now()
    """), {"uid": str(user.id), "ts": body.team_scope, "key": encrypted})
    await session.commit()
    return {"connected": True}
```

[ASSUMED: team_scope provient du body — l'utilisateur a un team_scope connu au moment du submit onboarding]

---

## verify-phase8.sh — Structure des 6 tests

Les 6 success criteria du ROADMAP Phase 8 mappent directement aux tests :

| Test | Success Criterion | Méthode de vérification |
|------|-------------------|------------------------|
| [1/6] Migration 0012 appliquée | Tables `granola_user_connections` + `agent_definitions` présentes | `psql SELECT to_regclass(...)` |
| [2/6] `granola_user_connections` contient bien FK vers `users` | Structure FK | `pg_constraint conrelid + confrelid` |
| [3/6] `agent_definitions` seedée avec `meeting-recap` | Row présent avec `name='meeting-recap'` | `psql SELECT count(*)` |
| [4/6] `POST /v1/admin/agents` répond (401/403 sans auth) | Endpoint enregistré | `curl -s -o /dev/null -w "%{http_code}"` |
| [5/6] `POST /v1/agents/{id}/invoke` répond (401/403 sans auth) | Endpoint enregistré | `curl -s -o /dev/null -w "%{http_code}"` |
| [6/6] `POST /v1/me/granola-key` répond (401/403 sans auth) | Endpoint enregistré | `curl -s -o /dev/null -w "%{http_code}"` |

Pattern `set -uo pipefail` (pas `-e`), identique à verify-phase7.sh. [VERIFIED: infrastructure/scripts/verify-phase7.sh]

---

## Wave Structure (séquencement plans)

```
Wave 0 (prerequisite)
└── Plan 08-01 — Migration Alembic 0012
    Tables: granola_user_connections + agent_definitions + seeding meeting-recap

Wave 1 (parallel — tous dépendent uniquement de 08-01)
├── Plan 08-02 — memory-api: routes agents (CRUD admin + invoke endpoint)
├── Plan 08-03 — memory-api: route /v1/me/granola-key (GET/POST/DELETE)
└── Plan 08-04 — granola-sync: deuxième boucle per-user (granola_poller.py extension)

Wave 2 (parallel — peuvent démarrer après 08-01)
├── Plan 08-05 — librechat-bridge: contact extraction depuis messages (mongo_watcher extension)
└── Plan 08-06 — onboarding.js patch: champ Granola API key

Wave 3 (final)
└── Plan 08-07 — verify-phase8.sh (6 tests) + .env.example Phase 8 additions
```

**Total : 7 plans** (vs 9 en Phase 7 — Phase 8 est plus ciblée). Plans 08-02, 08-03, 08-04 peuvent être générés et exécutés en parallèle une fois 08-01 terminé.

**Note sur 08-05 (bridge extraction) :** Le path `/v1/memory/upsert` appelle déjà `_extract_crm_contacts` en fire-and-forget (Phase 7 D1). Pour la Chrome ext (D3), l'extraction contacts est **déjà fonctionnelle** — aucun changement requis dans le handler. Phase 8 D3 Chrome ext = "rien à implémenter côté memory-api", c'est acquis.

Pour **librechat-bridge** (D3 extraction contacts LibreChat), `mongo_watcher.py` doit appeler `_extract_contacts_from_message()` de la même façon que `detect_task_intent()` — nouveau fichier `contact_extractor.py` dans librechat-bridge.

---

## Runtime State Inventory

> Phase 8 : pas de rename/refactor/migration de string. Inventaire minimal.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | Aucun record existant pour `granola_user_connections` (nouvelle table) | Migration create table — aucune migration de données |
| Live service config | granola-sync container live (xbrain-granola-sync) — nécessite rebuild image après modification `granola_poller.py` | `docker compose up -d --build granola-sync` |
| OS-registered state | None — aucun scheduler OS pour ce service (Docker restart policy) | — |
| Secrets/env vars | `FERNET_KEY` déjà configuré (Phase 7). `ANTHROPIC_API_KEY` déjà configuré. Aucun nouveau secret requis. | None |
| Build artifacts | `xbrain/granola-sync:phase7` image tag devra être mis à jour vers `phase8` ou `latest` | Update image tag dans docker-compose.yml |

**Nothing found in additional categories** — verified by codebase scan.

---

## Environment Availability

> Step 2.6: Audit partiel — Phase 8 n'ajoute aucun nouveau service externe.

| Dependency | Required By | Available | Notes |
|------------|------------|-----------|-------|
| Anthropic API | agent invoke + granola extractor | Confirmed | `ANTHROPIC_API_KEY` configuré Phase 7 (STATE.md + .env.example line 20) |
| Fernet key | granola_user_connections encryption | Confirmed | `FERNET_KEY` ou `OAUTH_CREDENTIALS_ENCRYPTION_KEY` configuré Phase 7 |
| PostgreSQL | Migration 0012 | Confirmed | xbrain-postgres running since Phase 1 |
| Granola API `https://api.granola.ai` | Per-user polling | External | [ASSUMED: même endpoint que team polling — `GRANOLA_API_BASE` déjà configuré] |

**Missing dependencies with no fallback:** None.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Structure `_process_user_connection` est un miroir de `_process_team_integration` avec `user_id` comme paramètre d'identification | Wave Structure / Code Examples | Mineur — refactor facile |
| A2 | `POST /v1/me/granola-key` reçoit `team_scope` dans le body (l'utilisateur a déjà un team au moment du submit) | Code Examples | Si l'utilisateur n'a pas encore de team, flow doit être revu — mais onboarding force la sélection de team avant Granola |
| A3 | UNIQUE constraint sur `granola_user_connections.user_id` (1 clé par user) | Migration schema | Peut être 1 clé par (user_id, team_scope) si l'utilisateur est dans plusieurs teams — à confirmer avec le comportement attendu |
| A4 | `agent_definitions.name` unique (pour le seeding idempotent de `meeting-recap`) | Code Examples | Si name non-unique, le seed via `ON CONFLICT (name)` échoue — remplacer par un slug ou un id fixe |
| A5 | Granola API per-user utilise le même endpoint `/v1/notes?created_after=...` avec le même format de réponse que l'API team | granola_poller extension | Si l'API per-user a une structure différente, la pagination et les champs seraient différents |

---

## Open Questions

1. **`granola_user_connections` : 1 row par user ou 1 row par (user, team) ?**
   - Ce qu'on sait : la table a `user_id` FK + `team_scope`. Un utilisateur membre de 2 teams pourrait avoir 2 entrées.
   - Ce qui est flou : UNIQUE constraint sur `user_id` seul (1 clé Granola par user quel que soit le team) ou UNIQUE sur `(user_id, team_scope)` ?
   - Recommandation : UNIQUE sur `user_id` — une clé Granola personnelle est per-person, pas per-team. Si l'utilisateur est dans plusieurs teams, les meetings sont attribués au team_scope du moment du poll.

2. **Contact extraction librechat-bridge : assistant messages ou user messages seulement ?**
   - Ce qu'on sait : `task_intent_detector` ne traite que les messages `role=user` (voir mongo_watcher.py line 105).
   - Ce qui est flou : pour l'extraction contacts, faut-il aussi traiter les messages assistant ?
   - Recommandation : Traiter les deux — les réponses assistant mentionnent aussi des personnes (e.g. Claude résume "Alice a validé...").

3. **Agent invoke : accès non-admin pour les users ?**
   - Ce qu'on sait : D4 dit "synchrone, retourne réponse dans la même requête HTTP". Pas mentionné si l'invocation est réservée aux admins.
   - Recommandation : Permettre aux users authentifiés (kind=user) d'invoquer un agent enabled — les admins peuvent désactiver un agent via `enabled=false`. L'endpoint bridge (granola-sync) passe par le même path.

---

## Sources

### Primary (HIGH confidence — verified from codebase)
- `apps/granola-sync/app/granola_poller.py` — polling loop, Fernet decrypt, asyncpg pattern
- `apps/granola-sync/app/extractor.py` — Anthropic client singleton pattern
- `apps/granola-sync/app/memory_client.py` — bridge JWT generation, post_ingest
- `apps/memory-api/app/routes/granola_integration.py` — Fernet helper, bridge auth, ingest atomic pattern
- `apps/memory-api/app/routes/memory.py` — fire-and-forget asyncio tasks, `_extract_crm_contacts`, `_get_anthropic()`
- `apps/memory-api/app/routes/admin_projects.py` — admin router pattern (CRUD)
- `apps/memory-api/app/routes/admin_drive.py` — Fernet encrypt pattern, OAuth callback pattern
- `apps/memory-api/app/deps.py` — `_is_admin`, `_is_bridge`, `require_paid_tier`, `get_current_principal`
- `apps/memory-api/app/main.py` — router registration pattern
- `apps/memory-api/app/config.py` — Settings fields existants
- `apps/memory-api/alembic/versions/0009_crm_contacts.py` — contacts + granola_integrations schema reference
- `apps/memory-api/alembic/versions/0010_tasks.py` — tasks schema reference
- `apps/memory-api/alembic/versions/0011_team_onboarding.py` — dernière migration (0012 suit)
- `apps/librechat-bridge/app/mongo_watcher.py` — change stream watcher, task_intent hook pattern
- `apps/librechat-bridge/app/task_intent_detector.py` — lazy Claude client, fail-soft JSON parse pattern
- `apps/librechat/patches/onboarding.js` — overlay modal structure, `el()` helper, `getToken()`, `apiCall()`
- `infrastructure/scripts/verify-phase7.sh` — verify script pattern (8 tests, psql + curl)
- `infrastructure/docker-compose.yml` — granola-sync service definition
- `.env.example` — Phase 7 env vars (FERNET_KEY, ANTHROPIC_API_KEY, GRANOLA_API_BASE)

### Tertiary (LOW confidence — needs validation)
- A5: Granola API per-user endpoint structure identique à team API [ASSUMED — non vérifié depuis la VM]

---

## Metadata

**Confidence breakdown:**
- Standard Stack: HIGH — aucune nouvelle dépendance, tout est déjà installé et utilisé
- Architecture: HIGH — tous les patterns sont vérifiés dans le codebase existant
- Pitfalls: HIGH (P1-P5) / MEDIUM (P6-P7) — basés sur décisions STATE.md + code review direct
- Wave structure: MEDIUM — séquencement raisonnable mais le planner peut réorganiser

**Research date:** 2026-05-08
**Valid until:** 2026-06-08 (codebase stable — pas de dépendances externes changeantes)
