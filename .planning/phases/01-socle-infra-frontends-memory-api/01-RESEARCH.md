---
phase: 1
phase_name: Socle Infra + Frontends + memory-api
created: 2026-05-03
author: orchestrator (rédigé directement, pas de subagent — researcher avait stallé en background)
sources:
  - D:/VSC/xbrain/CLAUDE.md
  - D:/VSC/xbrain/.planning/REQUIREMENTS.md
  - D:/VSC/xbrain/.planning/research/PITFALLS.md
  - C:/Users/userx/.claude/projects/D--VSC-xbrain/memory/project_xbrain_*.md
---

# Phase 1 — RESEARCH

Recherche resserrée sur 5 questions critiques qui débloquent le planning. Tout le reste (mem0, LangGraph, Neo4j, MinIO, Langfuse, Memori) est **hors-scope Phase 1** — voir `project_xbrain_memory_layer_decision.md`.

---

## Q1 — Scaffold memory-api Python (LE point central)

### Stack

- **Framework** : FastAPI (>= 0.115). Async natif, OpenAPI built-in, Pydantic v2 integration.
- **Validation** : Pydantic v2 (`extra="forbid"` partout pour rejeter les champs en plus → cohérent avec "tagging contract obligatoire").
- **Settings** : `pydantic-settings` (lecture `.env`).
- **DB ORM** : SQLAlchemy 2.0 async + driver `asyncpg`. Migrations via Alembic.
- **Vector** : `qdrant-client` (synchronous suffit en Phase 1 — pas de hot path), créer la collection `messages` au démarrage de façon idempotente.
- **Auth** : `authlib` (intégration OIDC native, plus moderne que `python-jose`). Validation des ID tokens Google contre les JWKs publics : `https://www.googleapis.com/oauth2/v3/certs` (cache 1h).
- **HTTP client** : `httpx` (async).
- **Tests** : `pytest` + `pytest-asyncio` + `httpx.AsyncClient`. Postgres + Qdrant via `testcontainers-python` pour les tests d'intégration (sinon SQLite en mémoire pour les tests unitaires de la couche Pydantic).

### Endpoints minimum Phase 1

| Méthode | Path | Couvre | Notes |
|---|---|---|---|
| `GET` | `/v1/healthz` | OBS-01 | liveness, ne touche pas la DB |
| `GET` | `/v1/readyz` | OBS-01 | readiness, ping Postgres + Qdrant |
| `POST` | `/v1/messages` | MEM-01, CHAT-08 | **422 si l'un des 7 champs manque** (success criterion 2) |
| `GET` | `/v1/messages` | MEM-04, SRCH-01, SRCH-02 | filtre `team_scope` obligatoire ; full-text via `tsvector` Postgres en P1 (vector search → P2) |
| `POST` | `/v1/conversations` | CHAT-01 | créer une conversation (parent des messages) |
| `GET` | `/v1/conversations` | CHAT-04 | lister les conversations de l'utilisateur scoped team |
| `POST` | `/v1/teams` | ADMIN-01, TEAM-01 | admin only, crée une team |
| `POST` | `/v1/teams/{team_id}/members` | ADMIN-02, TEAM-02 | invite un membre |
| `GET` | `/v1/teams/{team_id}/members` | ADMIN-03, TEAM-03 | liste membres |
| `DELETE` | `/v1/teams/{team_id}/members/{user_id}` | ADMIN-04, TEAM-04 | revoke |
| `GET` | `/v1/me` | AUTH-04 | user actuel, basé sur le JWT |
| `GET` | `/v1/audit` | OBS-04, ADMIN-05 | log des opérations write (admin only) |

### Tables Postgres (Alembic migration #1)

```sql
-- users (mappe le sub OIDC sur un user interne)
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_user_id TEXT NOT NULL UNIQUE,  -- Google OIDC sub
  email TEXT NOT NULL,
  display_name TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- teams + membership
CREATE TABLE teams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug TEXT NOT NULL UNIQUE,             -- "team_scope" externe
  display_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE team_members (
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('admin','member')),
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (team_id, user_id)
);

-- conversations
CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_scope TEXT NOT NULL REFERENCES teams(slug),
  project_scope TEXT,
  owner_user_id UUID NOT NULL REFERENCES users(id),
  title TEXT,
  source TEXT NOT NULL,          -- "librechat:claude-3.5", "openwebui:gpt-4o", etc.
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conv_team ON conversations(team_scope);

-- messages (le contrat de tagging vit ici)
CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  -- 7 champs du contrat (NOT NULL = enforcement DB)
  team_scope TEXT NOT NULL,
  project_scope TEXT,                   -- nullable
  visibility TEXT NOT NULL CHECK (visibility IN ('private','team','org','public')),
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  truth_level TEXT NOT NULL CHECK (truth_level IN ('EPHEMERAL','WORKING','VALIDATED','CANONICAL','PUBLIC')),
  source TEXT NOT NULL,                 -- format `^[a-z][a-z0-9_-]*:[a-z0-9._-]+$`
  validation_status TEXT NOT NULL CHECK (validation_status IN ('pending','validated','rejected','n/a')),
  -- payload
  role TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  content TEXT NOT NULL,
  content_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_msg_team ON messages(team_scope);
CREATE INDEX idx_msg_conv ON messages(conversation_id);
CREATE INDEX idx_msg_tsv ON messages USING GIN(content_tsv);  -- full-text search
CREATE INDEX idx_msg_truth ON messages(truth_level);

-- audit log (immuable, append-only)
CREATE TABLE audit_log (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_user_id UUID REFERENCES users(id),
  team_scope TEXT,
  action TEXT NOT NULL,                 -- "messages.create", "teams.create", "members.invite"
  target_id TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX idx_audit_ts ON audit_log(ts DESC);
CREATE INDEX idx_audit_team ON audit_log(team_scope);
```

### Modèle Pydantic du contrat de tagging

```python
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field

SOURCE_PATTERN = r"^[a-z][a-z0-9_-]*:[a-z0-9._-]+$"

class Visibility(str, Enum):
    PRIVATE = "private"
    TEAM    = "team"
    ORG     = "org"
    PUBLIC  = "public"

class TruthLevel(str, Enum):
    EPHEMERAL = "EPHEMERAL"
    WORKING   = "WORKING"
    VALIDATED = "VALIDATED"
    CANONICAL = "CANONICAL"
    PUBLIC    = "PUBLIC"

class ValidationStatus(str, Enum):
    PENDING   = "pending"
    VALIDATED = "validated"
    REJECTED  = "rejected"
    NA        = "n/a"

class TaggingContract(BaseModel):
    """7 champs obligatoires sur chaque message Phase 1."""
    model_config = ConfigDict(extra="forbid", frozen=False)
    team_scope:        str             = Field(..., min_length=1, max_length=64)
    project_scope:     str | None      = Field(default=None, max_length=64)
    visibility:        Visibility
    confidence:        float           = Field(..., ge=0.0, le=1.0)
    truth_level:       TruthLevel      = TruthLevel.EPHEMERAL
    source:            str             = Field(..., pattern=SOURCE_PATTERN, max_length=128)
    validation_status: ValidationStatus = ValidationStatus.PENDING
```

`extra="forbid"` rejette les champs inconnus → si LibreChat envoie un truc bizarre, 422. C'est le **garde-fou** qui rend le contrat utile.

### Team isolation

**Décision Phase 1 : enforcement côté code Python** (FastAPI `Depends`), Postgres RLS différé en Phase 2 (defense-in-depth).

Pattern :

```python
async def get_current_team_membership(
    user: User = Depends(get_current_user),
    team_scope: str = Header(..., alias="X-Team-Scope"),  # frontend doit envoyer ce header
    session: AsyncSession = Depends(get_session),
) -> TeamMembership:
    membership = await teams_repo.get_membership(session, user.id, team_scope)
    if membership is None:
        raise HTTPException(403, f"Not a member of team {team_scope}")
    return membership
```

Toute query messages/conversations filtre obligatoirement `WHERE team_scope = :ts`. Repository pattern garantit qu'on ne peut pas l'oublier (pas de méthode `list_all_messages()` sans param `team_scope`).

**Pourquoi pas RLS Phase 1** : RLS demande de configurer un user Postgres par team OU une session var par requête. Complexe à mettre en place et à débugger. App-level filter + tests d'intégration solides (success criterion 3) suffit pour Phase 1.

### Layout `/apps/memory-api/`

```
apps/memory-api/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI(), lifespan startup/shutdown, route mounting
│   ├── config.py            # Settings (DATABASE_URL, QDRANT_URL, GOOGLE_CLIENT_ID, etc.)
│   ├── deps.py              # get_session, get_current_user, get_current_team_membership
│   ├── auth.py              # JWT validation via authlib + Google JWKs cache
│   ├── audit.py             # write_audit() helper, used by every mutation
│   ├── qdrant_setup.py      # ensure_collection("messages") au boot
│   ├── models/
│   │   ├── __init__.py
│   │   ├── tagging.py       # TaggingContract, enums (le code ci-dessus)
│   │   ├── message.py       # MessageCreate, MessageOut, Message ORM
│   │   ├── conversation.py
│   │   ├── team.py
│   │   └── user.py
│   ├── repos/
│   │   ├── messages.py      # CRUD avec team_scope obligatoire
│   │   ├── conversations.py
│   │   ├── teams.py
│   │   └── users.py
│   ├── routes/
│   │   ├── health.py        # /v1/healthz, /v1/readyz
│   │   ├── messages.py      # /v1/messages
│   │   ├── conversations.py
│   │   ├── teams.py         # CRUD team + members
│   │   ├── me.py
│   │   └── audit.py
│   └── db/
│       ├── session.py       # async_engine, async_sessionmaker
│       └── base.py          # DeclarativeBase
├── alembic/
│   ├── versions/
│   │   └── 0001_initial.py
│   ├── env.py
│   └── script.py.mako
├── alembic.ini
├── tests/
│   ├── conftest.py
│   ├── test_tagging_contract.py    # 422 si champ manquant
│   ├── test_team_isolation.py      # Team A query → 0 Team B rows
│   ├── test_auth.py                # JWT invalide → 401, expiré → 401
│   └── test_health.py
├── Dockerfile
├── pyproject.toml
├── .env.example
└── README.md
```

### `pyproject.toml` deps minimum

```toml
[project]
name = "memory-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "pydantic>=2.10",
  "pydantic-settings>=2.6",
  "sqlalchemy[asyncio]>=2.0.36",
  "asyncpg>=0.30",
  "alembic>=1.14",
  "qdrant-client>=1.17",
  "authlib>=1.3",
  "httpx>=0.28",
  "python-multipart>=0.0.18",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3", "pytest-asyncio>=0.25",
  "ruff>=0.8", "mypy>=1.13",
  "testcontainers[postgres]>=4.8",
]
```

---

## Q2 — LibreChat → memory-api : pas de webhook natif, sidecar requis

### Constat

LibreChat v0.8.5+ **n'a pas** de mécanisme webhook outbound pour "envoyer chaque message à un service externe". Architecture interne :
- Chats stockés dans MongoDB (collections `messages`, `conversations`)
- Recherche in-app via MeiliSearch (indexation automatique)
- pgvector pour la "RAG API" interne (séparée de notre Qdrant)
- Pas de hook `onMessageCreated`, pas de plugin server-side stable

### Recommandation : sidecar `librechat-bridge` qui watch MongoDB Change Stream

```
┌────────────┐     ┌────────────┐    ┌──────────────────┐
│ LibreChat  │────▶│  MongoDB   │◀───│ librechat-bridge │
│ (Node app) │     │ (oplog)    │    │  (Python)        │
└────────────┘     └────────────┘    └──────┬───────────┘
                                            │ POST + JWT
                                            ▼
                                   ┌──────────────────┐
                                   │   memory-api     │
                                   └──────────────────┘
```

**Mécanisme** :
- MongoDB `>=4.0` expose des **change streams** (require replica set, même si single node — config `replSet=rs0`).
- `librechat-bridge` (Python, `motor` driver async) ouvre `db.messages.watch()` au démarrage, traite chaque insert.
- Pour chaque message inséré :
  1. Lookup conversation parent → récupère le user_id + le model
  2. Lookup user → récupère `source_user_id` (le `sub` Google) + team_scope (depuis user metadata custom — voir Q4)
  3. Build le payload contrat (7 champs) :
     - `team_scope` = team de l'utilisateur (résolu via memory-api `/v1/me`)
     - `project_scope` = `null` (ou récupéré depuis le titre/tag conversation si conventionné)
     - `visibility` = `"team"` (défaut)
     - `confidence` = `1.0`
     - `truth_level` = `"EPHEMERAL"` (chats bruts, pas encore validés)
     - `source` = `f"librechat:{model_name}"` (ex: `librechat:claude-3.5-sonnet`)
     - `validation_status` = `"pending"`
  4. POST `/v1/messages` avec un **service JWT** (pas le user JWT — bridge n'est pas un user). JWT signé par memory-api avec un secret partagé.
- Idempotence : utiliser le `_id` Mongo comme `external_id` côté memory-api (UNIQUE constraint), ignorer si déjà inséré (évite les doublons sur restart du bridge).
- Resume token Mongo persisté dans une petite collection `xbrain_bridge_state` pour reprendre après crash.

**Pourquoi pas patcher LibreChat directement** : casserait à chaque bump de version, fork à maintenir. Le sidecar est isolé et résilient.

**Sources** :
- LibreChat config docs : https://www.librechat.ai/docs/configuration
- LibreChat MongoDB collections : https://github.com/danny-avila/LibreChat/tree/main/api/models
- MongoDB change streams : https://www.mongodb.com/docs/manual/changeStreams/
- `motor` async driver : https://motor.readthedocs.io/

[OPEN: pré-Phase 2, est-ce qu'on extrait aussi les conversations historiques au premier boot du bridge, ou seulement les messages NEW depuis l'install ? Recommandation : option `BACKFILL_FROM=startup` qui scan une fois puis switch en change stream.]

---

## Q3 — Open WebUI → memory-api : utiliser **Pipelines**

### Constat

Open WebUI v0.9.0 a un framework officiel pour ce cas d'usage : **Pipelines** (https://github.com/open-webui/pipelines). Un Pipeline est un service FastAPI séparé qui reçoit chaque chat completion (avant/après) et peut transformer/logger/proxifier.

### Architecture

```
┌──────────────┐  ┌──────────────────────┐  ┌──────────────┐
│ Open WebUI   │─▶│ openwebui-pipeline   │─▶│ LLM provider │
│ (frontend)   │  │ (FastAPI, Pipelines) │  │ (OpenAI/etc) │
└──────────────┘  └──────────┬───────────┘  └──────────────┘
                             │ POST + JWT
                             ▼
                      ┌──────────────┐
                      │  memory-api  │
                      └──────────────┘
```

### Implémentation

`openwebui-pipeline` est un container Python qui :
- Implémente la classe `Pipeline` du SDK Pipelines (méthode `inlet(body, user)` côté request, `outlet(body, user)` côté response)
- Dans `outlet()` : récupère la conversation finale (user message + assistant response), construit 2 payloads contrat, POST sur `/v1/messages`
- Le user payload Pipelines contient : `id` (= `sub` Google), `email`, `name`, et les `messages` complets

**Config Open WebUI pour le brancher** :
- `OPENAI_API_BASE_URL=http://openwebui-pipeline:9099`
- `OPENAI_API_KEY=<pipeline-shared-secret>`

Le pipeline forward ensuite vers le vrai LLM (OpenAI/Anthropic via leurs APIs) — il agit comme un MITM contrôlé.

### Mapping team_scope

Open WebUI a un système de **groups** depuis v0.5+. Chaque user appartient à un ou plusieurs groups. Le pipeline peut :
- Lire `user.groups` du payload (si Open WebUI l'inclut — à vérifier dans la doc actuelle)
- Sinon, faire un appel à memory-api `/v1/me` avec le `sub` pour récupérer le team_scope

**Sources** :
- Pipelines docs : https://docs.openwebui.com/pipelines/
- Pipelines examples : https://github.com/open-webui/pipelines/tree/main/examples
- Open WebUI groups : https://docs.openwebui.com/features/admin/groups/

[OPEN: Pipelines vs Functions — clarifier au moment du dev. Functions sont user-facing (tools dans le chat), Pipelines sont infra (logging/transformation). Le bon choix est Pipelines.]

---

## Q4 — Google SSO partagé entre LibreChat + Open WebUI

### Recommandation : 1 Google OAuth Client, 2 frontends config natifs (pas oauth2-proxy)

`oauth2-proxy` ajoute un container, du reverse proxy header injection, et de la complexité. Les deux frontends ont l'OIDC natif → autant l'utiliser.

### Setup Google Cloud Console

1. Console → APIs & Services → OAuth consent screen → "External" (ou Internal si Google Workspace).
2. Credentials → Create Credentials → OAuth client ID → Web application
3. **Authorized redirect URIs** (les 2) :
   - `http://__VM_HOST__/oauth/google/callback` (LibreChat)
   - `http://__VM_HOST__/openwebui/oauth/google/callback` (Open WebUI, si reverse-proxied sous `/openwebui`)
4. Récupérer `client_id` + `client_secret` → `.env`

### LibreChat OIDC (`librechat.yaml`)

```yaml
version: 1.2.4
cache: true

registration:
  socialLogins: ["google"]
  allowedDomains:
    - "*"   # à restreindre si tu veux limiter aux mails @tondomaine

endpoints:
  custom: []

# Google OAuth utilise les env vars natives :
# GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_CALLBACK_URL
```

`.env` :
```
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_CALLBACK_URL=http://__VM_HOST__/oauth/google/callback
DOMAIN_CLIENT=http://__VM_HOST__
DOMAIN_SERVER=http://__VM_HOST__
ALLOW_SOCIAL_LOGIN=true
ALLOW_SOCIAL_REGISTRATION=true
```

### Open WebUI OIDC (`.env`)

```
ENABLE_OAUTH_SIGNUP=true
OAUTH_PROVIDER_NAME=Google
OAUTH_GOOGLE_CLIENT_ID=...
OAUTH_GOOGLE_CLIENT_SECRET=...
GOOGLE_REDIRECT_URI=http://__VM_HOST__/openwebui/oauth/google/callback
WEBUI_AUTH=true
```

### Comment memory-api reçoit le `sub` partagé

Chaque frontend stocke en session le `sub` Google (claim standard OIDC). Quand un frontend (ou son sidecar/pipeline) appelle memory-api, il envoie un JWT en `Authorization: Bearer ...`.

**Deux modes selon l'appelant** :

| Appelant | JWT envoyé | Validation memory-api |
|---|---|---|
| User direct (rare en P1) | Google ID token brut | authlib + Google JWKs |
| `librechat-bridge` (au nom du user) | JWT interne signé HS256 avec secret partagé, payload `{sub, team_scope, scope:"bridge"}` | HS256 + secret env `BRIDGE_SHARED_SECRET` |
| `openwebui-pipeline` | JWT interne signé HS256, mêmes claims | idem |

**Pourquoi un JWT interne pour les sidecars** : ils ne peuvent pas générer un Google ID token au nom du user (c'est Google qui les signe). On utilise un service token court-vif (TTL 5 min) signé avec un secret partagé entre memory-api et les sidecars.

### memory-api auth code (squelette)

```python
# app/auth.py
from authlib.jose import jwt, JsonWebKey
import httpx, time

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_jwks_cache = {"keys": None, "ts": 0}

async def google_jwks():
    if time.time() - _jwks_cache["ts"] > 3600:
        async with httpx.AsyncClient() as c:
            r = await c.get(GOOGLE_JWKS_URL)
            _jwks_cache["keys"] = JsonWebKey.import_key_set(r.json())
            _jwks_cache["ts"] = time.time()
    return _jwks_cache["keys"]

async def verify_google_id_token(token: str, client_id: str) -> dict:
    keys = await google_jwks()
    claims = jwt.decode(token, keys, claims_options={
        "iss": {"essential": True, "values": ["https://accounts.google.com", "accounts.google.com"]},
        "aud": {"essential": True, "value": client_id},
    })
    claims.validate()
    return claims  # contient .sub, .email, .name

def verify_bridge_jwt(token: str, secret: str) -> dict:
    claims = jwt.decode(token, secret)
    claims.validate()
    return claims
```

[OPEN: en prod (post-Phase 1), Google OAuth refuse les redirect URIs en IP brute → besoin d'un domaine. Pour Phase 1 dev/test, IP marche. Cf. Phase 1.5 ou Phase 2 : attacher domaine + Let's Encrypt.]

---

## Q5 — Docker Compose layout (4 GB VM)

### Vue d'ensemble

| Service | Image | mem_limit | CPU | Volumes | Healthcheck |
|---|---|---|---|---|---|
| `nginx` | `nginx:1.27-alpine` | 64M | 0.2 | `./nginx/conf.d:/etc/nginx/conf.d:ro` | wget /nginx-health |
| `memory-api` | `xbrain/memory-api:phase1` (build local) | 256M | 0.5 | — | curl /v1/healthz |
| `postgres` | `postgres:17` | 384M | 0.5 | `xbrain_pg:/var/lib/postgresql/data` | pg_isready |
| `qdrant` | `qdrant/qdrant:v1.17.1` | 384M | 0.4 | `xbrain_qdrant:/qdrant/storage` | curl /healthz |
| `librechat` | `ghcr.io/danny-avila/librechat:v0.8.5` | 384M | 0.5 | `librechat_uploads:/app/client/public/uploads`, `./librechat.yaml:/app/librechat.yaml:ro` | wget /api/health |
| `librechat-mongo` | `mongo:7` (avec `--replSet rs0`) | 256M | 0.3 | `librechat_mongo:/data/db` | mongosh ping |
| `librechat-meili` | `getmeili/meilisearch:v1.10` | 192M | 0.2 | `librechat_meili:/meili_data` | curl /health |
| `librechat-bridge` | `xbrain/librechat-bridge:phase1` (build local) | 128M | 0.2 | — | curl /healthz |
| `openwebui` | `ghcr.io/open-webui/open-webui:v0.9.0` | 512M | 0.5 | `openwebui_data:/app/backend/data` | curl /health |
| `openwebui-pipeline` | `xbrain/openwebui-pipeline:phase1` (build local) | 192M | 0.2 | `openwebui_pipelines:/app/pipelines` | curl /health |

**Total mem_limit** : 2752 MB ≈ 2.7 GB. **Headroom OS + buffers** : ~1.2 GB sur 4 GB. C'est serré mais tient si on ne lance pas de gros build sur la VM. Surveillance `docker stats` obligatoire.

### Topologie réseau

- **1 seul bridge network** : `xbrain_net` (simplicité). nginx est le seul à exposer des ports vers l'host (80, 443).
- Tous les autres services écoutent uniquement sur `xbrain_net` — **pas de `ports:` mapping vers l'host**.
- Service discovery par nom de container : `memory-api:8000`, `postgres:5432`, etc.

### nginx ingress (Phase 1 sans TLS)

```nginx
# nginx/conf.d/xbrain.conf
upstream librechat   { server librechat:3080; }
upstream openwebui   { server openwebui:8080; }
upstream memory_api  { server memory-api:8000; }

server {
  listen 80 default_server;
  server_name _;

  # LibreChat à la racine
  location / {
    proxy_pass http://librechat;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;          # SSE/streaming responses
  }

  # Open WebUI sous /openwebui (réécriture path)
  location /openwebui/ {
    proxy_pass http://openwebui/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_buffering off;
  }

  # API admin (memory-api) sous /api
  location /api/ {
    proxy_pass http://memory_api/;
    proxy_set_header Authorization $http_authorization;
  }

  # Health endpoint nginx interne (pour healthcheck du container)
  location /nginx-health { return 200 "ok"; access_log off; }
}
```

[OPEN: Open WebUI sous un sub-path peut casser certains assets relatifs. Si ça pose problème, basculer sur subdomains nginx (mais nécessite domaine — pas Phase 1). Workaround Phase 1 si bug : exposer Open WebUI sur un autre port (8080 host).]

### Volumes nommés

- `xbrain_pg` — Postgres data
- `xbrain_qdrant` — Qdrant collections + snapshots
- `librechat_mongo` — LibreChat MongoDB (chats users)
- `librechat_meili` — index search LibreChat
- `librechat_uploads` — fichiers uploadés via LibreChat
- `openwebui_data` — internal SQLite + uploads Open WebUI
- `openwebui_pipelines` — registered pipeline modules

Tous persistés sous `/var/lib/docker/volumes/` sur la VM. **Backup** = `tar` de chacun + `pg_dump` + Qdrant snapshot API (cf. ci-dessous).

### Backup + restore (success criterion 5)

**Stratégie** : container `xbrain-backup` (alpine + cron + gcloud SDK + postgres-client + mongodump-tools).

Script quotidien :
1. `pg_dump` → `xbrain-backups/pg/YYYY-MM-DD.sql.gz`
2. `mongodump` → `xbrain-backups/mongo/YYYY-MM-DD.archive`
3. Qdrant snapshot via `POST /collections/{name}/snapshots` → download → `xbrain-backups/qdrant/YYYY-MM-DD.tar`
4. `tar czf openwebui-data.tar.gz openwebui_data/` (volume mount read-only)
5. `gsutil cp -r xbrain-backups/* gs://xbrain-backups-prod/$(date +%F)/`
6. Rétention : 7 daily + 4 weekly (script de purge).

**Restore test (à scripter)** :
- Spinup d'une VM e2-medium clone (ou container compose à part)
- Pull du backup le plus récent depuis GCS
- `pg_restore`, `mongorestore`, Qdrant snapshot recovery, untar volume
- `docker compose up -d` → vérif healthchecks pass + 1 query memory-api retrouve un message connu

[OPEN: GCS bucket name — `xbrain-backups-prod` ? Region : `europe-west1` (même que VM). Service account avec `roles/storage.objectAdmin` sur ce bucket uniquement. À setup avant le premier backup.]

---

## Items ouverts pour le planner

Le planner devra trancher (ou poser au user) :

1. **`[OPEN-1]`** — Domaine en Phase 1 ? Recommandation : **non**, accès via IP. Le user a déjà tranché ("domain et ip later").
2. **`[OPEN-2]`** — GCS bucket pour backups : créer maintenant ou backup local-only Phase 1 ? Recommandation : **GCS dès le départ** pour que le success criterion 5 (restore depuis backup) soit défensif. Coût : ~quelques cents/mois.
3. **`[OPEN-3]`** — Backfill historique des chats LibreChat existants au premier run du bridge ? Recommandation : **option de config** `BACKFILL_FROM=startup`, off par défaut.
4. **`[OPEN-4]`** — Open WebUI sous `/openwebui` sub-path peut casser des assets. Si bug à l'install : exposer sur port 8080 host (workaround) ou attendre domaine + subdomains.
5. **`[OPEN-5]`** — `BRIDGE_SHARED_SECRET` partagé entre memory-api et les 2 sidecars : généré aléatoirement au premier `docker compose up`, persisté dans `.env`. Pas dans le repo.
6. **`[OPEN-6]`** — Tests E2E : Playwright contre LibreChat + Open WebUI ? Trop lourd pour Phase 1 — se contenter de tests d'intégration memory-api (pytest + testcontainers) pour les success criteria 2 et 3.
7. **`[OPEN-7]`** — `librechat.yaml` allowedDomains : `*` (open) ou liste (`@gmail.com`) ? Recommandation : `*` Phase 1 (un seul user de test toi), restreindre Phase 2.

---

## Pitfalls majeurs (référencés depuis PITFALLS.md)

- **MongoDB single-node + change streams** : oblige à activer le replica set même sans réplication réelle. Config : `command: ["--replSet", "rs0"]` puis `mongosh --eval "rs.initiate()"` au premier boot.
- **Open WebUI v0.6.6+ licence** : OK pour usage interne ≤50 users, branding préservé. Pas de stripping de footer/logo.
- **MinIO Docker Hub discontinué** : pas concerné en Phase 1 (pas de MinIO encore), mais à noter pour Phase 3.
- **Qdrant 1.17 + grpc** : les health checks via curl HTTP marchent (port 6333), pas besoin de grpc. Healthcheck `curl -f localhost:6333/healthz`.
- **e2-medium 4 GB** : tout build Docker sur la VM est risqué (OOM). Recommandation : **build images en local + push GHCR**, VM ne fait que `docker compose pull && up`. Sinon `docker compose build` peut tuer Postgres/Qdrant en cours de route.

---

## RESEARCH COMPLETE

Recommandations clés :
- memory-api = FastAPI + Pydantic v2 + SQLAlchemy async + asyncpg + qdrant-client + authlib. Tagging contract Pydantic avec `extra="forbid"`. Team isolation app-level Phase 1 (RLS Phase 2).
- LibreChat → memory-api : sidecar `librechat-bridge` (Python + motor) qui watch MongoDB change streams, POST avec service JWT.
- Open WebUI → memory-api : Pipeline FastAPI dédié, intercepte chat completions via le SDK Pipelines officiel.
- Google SSO : 1 client OAuth, 2 frontends config natifs (pas oauth2-proxy). memory-api valide Google ID tokens via authlib + JWKs cache.
- Docker Compose : 10 services, total ~2.7 GB mem_limit, nginx ingress unique, builds en local pas sur la VM (OOM risk).

[OPEN] items à trancher pendant le planning : domaine, GCS bucket backups, sous-path Open WebUI, secrets management, scope tests E2E.
