# Phase 5 : Plateforme Projets Equipe — Pattern Map

**Date :** 2026-05-06
**Fichiers analysés :** 10 nouveaux fichiers / 5 modifications
**Analogues trouvés :** 14 / 15

---

## Classification des fichiers

| Fichier nouveau / modifié | Role | Data Flow | Analogue le plus proche | Qualite |
|---|---|---|---|---|
| `apps/graphiti-service/app/main.py` | service (FastAPI wrapper) | request-response | `apps/mcp-scraper/app/main.py` | role-match |
| `apps/graphiti-service/Dockerfile` | config | — | `apps/mcp-deck/Dockerfile` | exact |
| `apps/graphiti-service/pyproject.toml` | config | — | `apps/mcp-scraper/pyproject.toml` | exact |
| `infrastructure/docker-compose.yml` (ajout graphiti-service) | config | — | bloc `mcp-deck` lines 622-651 | exact |
| `apps/memory-api/app/auth.py` (ajout verify_github_token) | middleware | request-response | `apps/memory-api/app/auth.py` lignes 1-57 | exact |
| `apps/memory-api/app/deps.py` (ajout cache GitHub membership) | middleware | request-response | `apps/memory-api/app/deps.py` lignes 85-100 | exact |
| `apps/memory-api/app/config.py` (ajout GITHUB_* vars) | config | — | `apps/memory-api/app/config.py` | exact |
| `apps/memory-api/alembic/versions/0007_github_users.py` | migration | CRUD | `apps/memory-api/alembic/versions/0006_drive_watch_channels.py` | exact |
| `infrastructure/librechat/librechat.yaml` (ajout github OAuth) | config | — | librechat.yaml lignes 12-16 (registration.socialLogins) | exact |
| `infrastructure/docker-compose.yml` (env GitHub librechat) | config | — | bloc `librechat` lines 246-288 | exact |
| `chrome-extension/manifest.json` | config | — | aucun analogue interne | none |
| `chrome-extension/popup.html + popup.js` | component | request-response | `apps/memory-api/app/routes/me.py` (pattern appel API) | partial |
| `projects-dashboard/index.html` | component | request-response | aucun analogue interne (site statique) | none |
| `infrastructure/nginx/conf.d/30-projects.conf` | config | request-response | `infrastructure/nginx/conf.d/20-api.conf` | exact |
| `.github/workflows/deploy-brain.yml` (template) | config | — | aucun analogue interne (GH Actions) | none |

---

## Assignations de patterns par composant

---

### 1. `apps/graphiti-service/app/main.py` (service, request-response)

**Analogue :** `apps/mcp-scraper/app/main.py` — le sidecar FastMCP le plus simple du projet.

**Attention :** graphiti-service n'est PAS un sidecar FastMCP. C'est un wrapper FastAPI pur (~50 lignes) qui appelle la librairie `graphiti-core` en process. On réutilise la structure du fichier mcp-scraper (structlog, config via env, endpoint unique) mais on remplace FastMCP par FastAPI.

**Pattern imports** (mcp-scraper lines 1-16) :
```python
from __future__ import annotations
import structlog
from mcp.server.fastmcp import FastMCP  # → remplacer par FastAPI

log = structlog.get_logger(__name__)
```

**Adaptation pour graphiti-service :**
```python
from __future__ import annotations
import os
import structlog
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = structlog.get_logger(__name__)

NEO4J_URI     = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER    = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL     = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

app = FastAPI(title="xbrain graphiti-service", version="0.1.0")
```

**Pattern endpoint + structlog** (mcp-scraper lines 35-60) :
```python
@mcp.tool()
async def scrape(url: str) -> str:
    log.info("scraper.fetch", url=url[:100])
    try:
        text = await _load_url(url)
        log.info("scraper.done", url=url[:100], bytes=len(text))
        return text
    except httpx.HTTPStatusError as exc:
        log.warning("scraper.http_error", url=url[:100], status=exc.response.status_code)
        raise
    except Exception as exc:
        log.error("scraper.error", url=url[:100], error=str(exc))
        raise
```

Pour graphiti-service, le pattern devient :
```python
@app.post("/v1/extract")
async def extract_facts(body: ExtractBody):
    log.info("graphiti.extract", content_len=len(body.content), group_id=body.group_id)
    try:
        result = await graphiti_client.add_episode(...)
        log.info("graphiti.extract_done", episodes=len(result))
        return {"status": "ok", "episodes": result}
    except Exception as exc:
        log.error("graphiti.extract_error", error=str(exc))
        raise HTTPException(500, str(exc))
```

**Pattern error handling — dégradation gracieuse** (mcp-gateway lifespan pattern) :
Graphiti doit démarrer même si Neo4j n'est pas prêt. Copier le pattern `init_driver()` de `apps/memory-api/app/main.py` lignes 48-49 :
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_graphiti_client()
    except Exception as e:
        log.warning("graphiti.init_skipped", err=str(e))
    yield
    await close_graphiti_client()
```

**Healthcheck endpoint** — copier exactement depuis `apps/memory-api/app/routes/health.py` (pattern `GET /healthz` retourne `{"status": "ok"}`).

---

### 2. `apps/graphiti-service/Dockerfile`

**Analogue exact :** `apps/mcp-deck/Dockerfile` (Python 3.12-slim, pip install -e ., COPY app/).

```dockerfile
FROM python:3.12-slim
WORKDIR /app

# graphiti-core nécessite lxml (comme mcp-deck pour python-pptx)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app/ ./app/

EXPOSE 8300
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
```

Différence avec mcp-scraper/Dockerfile : graphiti-service utilise uvicorn directement (FastAPI, pas FastMCP), donc pas de `python app/main.py`.

---

### 3. `apps/graphiti-service/pyproject.toml`

**Analogue exact :** `apps/mcp-deck/pyproject.toml` (structure [project], requires-python, dependencies).

```toml
[project]
name = "graphiti-service"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "graphiti-core>=0.29.0",
    "neo4j>=6.1",
    "fastapi>=0.111",
    "uvicorn>=0.29",
    "httpx>=0.27",
    "structlog>=24.0",
    "pydantic>=2.0",
]
```

---

### 4. `infrastructure/docker-compose.yml` — ajout service `graphiti-service`

**Analogue exact :** bloc `mcp-deck` lines 622-651, lui-même inspiré de `mcp-scraper` lines 551-570.

Copier le pattern mcp-deck et adapter :
- port : `8300` (graphiti-service)
- image : `xbrain/graphiti-service:phase5`
- `mem_limit: 512m` (graphiti-core + Neo4j driver + sentence-transformers sont plus lourds que python-pptx)
- `depends_on: neo4j: { condition: service_healthy }` (en plus de memory-api)
- Pas de MinIO, pas de Bridge secret (graphiti-service appelle Neo4j directement)

```yaml
graphiti-service:
  build:
    context: ../apps/graphiti-service
    dockerfile: Dockerfile
  image: xbrain/graphiti-service:phase5
  container_name: xbrain-graphiti-service
  restart: unless-stopped
  environment:
    NEO4J_URI: bolt://neo4j:7687
    NEO4J_USER: neo4j
    NEO4J_PASSWORD: ${NEO4J_PASSWORD}
    OPENAI_API_KEY: ${OPENAI_API_KEY:-}
    ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
    LLM_MODEL: ${GRAPHITI_LLM_MODEL:-claude-haiku-4-5-20251001}
    LOG_LEVEL: ${LOG_LEVEL:-INFO}
  networks: [xbrain_net]
  mem_limit: 512m
  depends_on:
    neo4j: { condition: service_healthy }
    memory-api: { condition: service_healthy }
  healthcheck:
    test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8300/v1/healthz 2>/dev/null | grep -q ok"]
    interval: 30s
    timeout: 10s
    retries: 5
    start_period: 60s
```

---

### 5. `apps/memory-api/app/auth.py` — ajout `verify_github_token`

**Analogue direct :** `apps/memory-api/app/auth.py` lignes 1-42 (pattern `verify_google_id_token` + cache JWKs).

La vérification GitHub OAuth est différente de Google OIDC (pas de JWT à décoder, on appelle l'API GitHub avec le token) — copier le **pattern httpx + cache** :

```python
# Pattern cache à copier depuis lines 13-25 de auth.py
_jwks_cache: dict = {"keys": None, "ts": 0.0}

async def _fetch_google_jwks() -> JsonWebKey:
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["ts"] > 3600:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(GOOGLE_JWKS_URL)
            r.raise_for_status()
            _jwks_cache["keys"] = JsonWebKey.import_key_set(r.json())
            _jwks_cache["ts"] = now
    return _jwks_cache["keys"]
```

Adapter en cache membership GitHub (5min TTL) :
```python
_github_membership_cache: dict[str, tuple[float, dict]] = {}
# clé = github_token ou github_username, valeur = (timestamp, {login, orgs, repos})
GITHUB_CACHE_TTL = 300  # 5 minutes

async def verify_github_token(token: str, org: str) -> dict:
    """Appelle GET https://api.github.com/user avec le token OAuth GitHub.
    Retourne {login, email, name, is_org_member: bool}."""
    now = time.time()
    if token in _github_membership_cache:
        ts, result = _github_membership_cache[token]
        if now - ts < GITHUB_CACHE_TTL:
            return result
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        user_data = r.json()
        # Vérifier membership Org
        org_r = await client.get(
            f"https://api.github.com/orgs/{org}/members/{user_data['login']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        result = {**user_data, "is_org_member": org_r.status_code == 204}
    _github_membership_cache[token] = (now, result)
    return result
```

**Pattern make_bridge_jwt** — déjà utilisé dans `apps/agent-runtime/app/tools/mcp_gateway_client.py` lignes 37-40. La version canonique est dans `apps/memory-api/app/auth.py` (ou `agent-runtime/app/auth.py`). Réutiliser tel quel sans modification.

---

### 6. `apps/memory-api/app/deps.py` — middleware GitHub membership

**Analogue direct :** `apps/memory-api/app/deps.py` lignes 85-100 (`get_team_scope` — vérifie membership en DB).

La logique actuelle `get_team_scope` vérifie en PostgreSQL. Pour GitHub, on ajoute une branche :

```python
# Pattern existant à étendre (deps.py lines 85-100)
async def get_team_scope(
    principal: dict[str, Any] = Depends(get_current_principal),
    x_team_scope: str = Header(..., alias="X-Team-Scope"),
    session: AsyncSession = Depends(get_session),
) -> str:
    if principal["kind"] == "bridge":
        if principal["team_scope"] != x_team_scope:
            raise HTTPException(403, "Bridge JWT team_scope mismatch with header")
        return x_team_scope
    user = principal["user"]
    membership = await get_membership(session, user_id=user.id, team_slug=x_team_scope)
    if membership is None:
        raise HTTPException(403, f"Not a member of team {x_team_scope}")
    return x_team_scope
```

Pour Phase 5, ajouter une dépendance `get_current_principal` qui accepte GitHub OAuth token en plus de Google OIDC. Le pattern de fallback en cascade est déjà en place (deps.py lignes 33-81 : try Google → try bridge). Ajouter une 3e branche `try GitHub` avant le `except` final.

---

### 7. `apps/memory-api/app/config.py` — variables GitHub

**Analogue exact :** `apps/memory-api/app/config.py` lignes 30-36 (ajout de variables Drive OAuth en Phase 3).

Copier le pattern d'ajout de variables optionnelles avec valeur par défaut vide :

```python
# Pattern etabli (config.py lines 30-36)
GOOGLE_CLIENT_SECRET: str = ""
OAUTH_CREDENTIALS_ENCRYPTION_KEY: str = ""
MEMORY_API_EXTERNAL_URL: str = "https://x.dejavu.cat"
```

Ajouter en Phase 5 :
```python
# Phase 5 — GitHub OAuth
GITHUB_CLIENT_ID: str = ""
GITHUB_CLIENT_SECRET: str = ""
GITHUB_ORG: str = "your-github-org"  # GitHub Org pour membership verification
```

---

### 8. `apps/memory-api/alembic/versions/0007_github_users.py`

**Analogue exact :** `apps/memory-api/alembic/versions/0006_drive_watch_channels.py` — même structure boilerplate.

```python
# Structure à copier exactement (0006 lines 1-20)
"""github_users — ajout github_username + github_id sur la table users

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-06
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("users", sa.Column("github_username", sa.String(256), nullable=True))
    op.add_column("users", sa.Column("github_id", sa.BigInteger, nullable=True))
    op.create_index("idx_users_github_username", "users", ["github_username"])
    op.create_index("idx_users_github_id",       "users", ["github_id"], unique=True)

def downgrade() -> None:
    op.drop_index("idx_users_github_id",       table_name="users")
    op.drop_index("idx_users_github_username", table_name="users")
    op.drop_column("users", "github_id")
    op.drop_column("users", "github_username")
```

Pour le pattern ADD COLUMN + CREATE INDEX, copier depuis `0005_multi_folder_drive.py` lignes 26-42 (pattern `op.add_column` + `op.create_index`).

---

### 9. `infrastructure/librechat/librechat.yaml` — GitHub OAuth

**Analogue exact :** `infrastructure/librechat/librechat.yaml` lignes 12-16 (Google OAuth existant).

```yaml
# Pattern existant (lignes 12-16)
registration:
  socialLogins: ["google"]
  allowedDomains:
    - "acme.example.com"
    - "gmail.com"
```

Modifier pour Phase 5 :
```yaml
registration:
  socialLogins: ["google", "github"]   # ajout github
  allowedDomains:
    - "acme.example.com"
    - "gmail.com"
    # pas de restriction par domaine pour GitHub — la Org membership est le filtre
```

---

### 10. `infrastructure/docker-compose.yml` — env GitHub pour LibreChat

**Analogue exact :** bloc `librechat` du docker-compose.yml lignes 264-268 (pattern Google OAuth existant).

```yaml
# Pattern existant à copier (librechat env lines 264-268)
GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID}
GOOGLE_CLIENT_SECRET: ${GOOGLE_CLIENT_SECRET}
GOOGLE_CALLBACK_URL: ${GOOGLE_CALLBACK_URL}
ALLOW_SOCIAL_LOGIN: ${ALLOW_SOCIAL_LOGIN:-true}
ALLOW_SOCIAL_REGISTRATION: ${ALLOW_SOCIAL_REGISTRATION:-true}
```

Ajouter dans le même bloc :
```yaml
GITHUB_CLIENT_ID: ${GITHUB_CLIENT_ID:-}
GITHUB_CLIENT_SECRET: ${GITHUB_CLIENT_SECRET:-}
GITHUB_CALLBACK_URL: ${GITHUB_CALLBACK_URL:-https://x.dejavu.cat/oauth/github/callback}
```

---

### 11. `infrastructure/nginx/conf.d/30-projects.conf`

**Analogue exact :** `infrastructure/nginx/conf.d/20-api.conf` (même structure : server block, resolver Docker, proxy_pass avec variable, headers Cloudflare).

Copier `20-api.conf` intégralement et adapter :

```nginx
# Copier depuis 20-api.conf lignes 1-39, adapter server_name + upstream

# Résolver Docker lazy (copier depuis 10-xbrain.conf ligne 2)
resolver 127.0.0.11 valid=30s ipv6=off;

server {
  listen 80;
  server_name projects.dejavu.cat;

  client_max_body_size 1m;   # site statique, pas d'upload

  # Site statique généré — servi par nginx directement depuis volume
  # OU proxy vers Firebase Hosting (si hébergé là)
  location / {
    # Option A : fichiers statiques dans /var/www/projects
    root /var/www/projects;
    try_files $uri $uri/ /index.html;

    # Option B : proxy Firebase Hosting (si le dashboard est déployé là)
    # set $firebase_upstream https://xbrain-495115.web.app;
    # proxy_pass $firebase_upstream;
    # ...
  }

  location /nginx-health { return 200 "ok\n"; access_log off; }
}
```

**Headers Cloudflare CF-Connecting-IP à copier depuis 10-xbrain.conf lignes 13-29** si on veut les logs IP réels (recommandé, copie verbatim).

---

### 12. Extension Chrome — `chrome-extension/manifest.json` + `popup.js`

**Aucun analogue interne.** Pattern externe Manifest V3 standard.

**Seule partie réutilisable depuis le codebase :** le pattern d'appel POST `/v1/memory` avec Bearer token + `X-Team-Scope`. Copier depuis `apps/mcp-deck/app/main.py` lignes 209-224 (pattern `_index_in_memory_api` — structure du payload, headers requis) :

```python
# Pattern payload memory-api à répliquer en JS (mcp-deck lines 190-215)
payload = {
    "item": {
        "id": deck_id,
        "content": f"Pitch deck: {title}",
        "team_scope": team_scope,
        "project_scope": None,
        "visibility": "team",
        "confidence": 1.0,
        "truth_level": "WORKING",       # ← sélectionné par l'utilisateur dans le popup
        "source": f"mcp:deck:{deck_id}",
        "validation_status": "pending",
        ...
    }
}
# headers:
# Authorization: Bearer <google_id_token>
# X-Team-Scope: <team_scope>
# Content-Type: application/json
```

En JavaScript (popup.js) :
```javascript
// Adapter directement depuis le pattern Python ci-dessus
const response = await fetch('https://api.dejavu.cat/v1/memory/upsert', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${idToken}`,
    'X-Team-Scope': teamScope,
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({ item: {
    content: selectedText,
    team_scope: teamScope,
    project_scope: projectScope || null,
    visibility: 'team',
    confidence: 1.0,
    truth_level: truthLevel,   // choix utilisateur
    source: `chrome:${window.location.hostname}`,
    validation_status: 'pending',
  }})
});
```

**Auth Google OAuth dans Manifest V3 :** utiliser `chrome.identity.getAuthToken()` (pas de redirect URI nécessaire, Chrome gère le flow). C'est le pattern standard MV3 — aucun analogue interne.

---

### 13. Dashboard `projects.dejavu.cat` — `index.html`

**Aucun analogue interne.** Site statique pur (HTML + JS vanilla ou Alpine.js).

**Pattern d'appel GitHub Org API** (externe, pas d'analogue interne) — copier le pattern httpx depuis `apps/memory-api/app/routes/admin_drive.py` lignes 262-284 (échange token → réponse JSON, gestion erreur 4xx) :

```python
# Pattern httpx appel externe à réutiliser pour le générateur dashboard (script Python)
async with httpx.AsyncClient(timeout=15.0) as client:
    resp = await client.post(
        "https://oauth2.googleapis.com/token",    # → remplacer par GitHub API
        data={...},
    )
    if resp.status_code != 200:
        log.error("github_api.call_failed", status=resp.status_code, body=resp.text[:500])
        raise HTTPException(502, f"GitHub API error (HTTP {resp.status_code})")
    data = resp.json()
```

---

### 14. `.github/workflows/deploy-brain.yml` (template GitHub Actions)

**Aucun analogue interne.** Nouveau fichier sans précédent dans le repo.

**Seul point d'ancrage interne :** la structure du payload `brain.yaml` (défini dans CONTEXT.md D1) et l'endpoint `POST /v1/memory/upsert` avec Bridge JWT (pattern mcp-deck lines 179-225). Le script `brain-index.sh` appelé par le workflow utilisera le même `curl` pattern que `infrastructure/scripts/register-mcp-tools.sh`.

Copier depuis `infrastructure/scripts/register-mcp-tools.sh` le pattern curl + JWT pour construire l'étape `brain-index` du workflow :
```bash
# Pattern curl + bearer token (register-mcp-tools.sh)
curl -s -X POST "http://memory-api:8000/v1/tools/register" \
  -H "Authorization: Bearer ${BRIDGE_JWT}" \
  -H "Content-Type: application/json" \
  -d "{...}"
```

---

## Patterns transverses (s'appliquent a tous les fichiers Phase 5)

### Auth — Bridge JWT inter-services

**Source :** `apps/mcp-deck/app/main.py` lignes 159-176 (`_mint_bridge_jwt`)
**Application :** graphiti-service (appels vers memory-api), GitHub Actions deploy script
```python
def _mint_bridge_jwt(team_scope: str) -> str:
    import time
    from joserfc import jwt as jose_jwt
    from joserfc.jwk import OctKey
    now = int(time.time())
    claims = {
        "iss": "mcp-deck",              # ← adapter issuer: "graphiti-service"
        "sub": "mcp-deck",              # ← adapter: "graphiti-service"
        "scope": "bridge",
        "team_scope": team_scope,
        "iat": now,
        "exp": now + 300,
    }
    key = OctKey.import_key(BRIDGE_SHARED_SECRET.encode())
    return jose_jwt.encode({"alg": "HS256"}, claims, key)
```

---

### Gestion erreur HTTP externe (httpx)

**Source :** `apps/memory-api/app/routes/admin_drive.py` lignes 263-284
**Application :** verify_github_token, dashboard generator, tous les appels API externes
```python
async with httpx.AsyncClient(timeout=15.0) as client:
    resp = await client.get(url, headers={...})
    if resp.status_code != 200:
        log.error("api.call_failed", status=resp.status_code, body=resp.text[:500])
        raise HTTPException(502, f"API error (HTTP {resp.status_code})")
    return resp.json()
```

---

### Pattern structlog (logging uniforme)

**Source :** tous les services existants (`apps/mcp-scraper/app/main.py` ligne 16, `apps/mcp-deck/app/main.py` ligne 25)
**Application :** tous les nouveaux fichiers Python Phase 5
```python
import structlog
log = structlog.get_logger(__name__)

# Usage : log.info("event.name", key1=val1, key2=val2)
# Pas de f-strings dans les log calls — structlog les formate en JSON
```

---

### Dépendance FastAPI (injection, Depends)

**Source :** `apps/memory-api/app/deps.py` + `apps/memory-api/app/routes/*.py`
**Application :** graphiti-service endpoints, memory-api nouveaux endpoints GitHub
```python
from fastapi import APIRouter, Depends, HTTPException
from app.deps import get_current_principal, get_session

router = APIRouter()

@router.post("/endpoint", status_code=201)
async def my_endpoint(
    body: MyBody,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    ...
```

---

### Pattern admin-only guard

**Source :** `apps/memory-api/app/routes/admin_drive.py` lignes 59-70 (`_is_admin`)
**Application :** tout endpoint Phase 5 restreint aux admins ou au service bridge
```python
def _is_admin(principal: dict[str, Any]) -> bool:
    if principal.get("kind") in ("service", "bridge"):
        return True
    sub = principal.get("sub", "")
    admin_subs = [s.strip() for s in (settings.ADMIN_USER_SUBS or "").split(",") if s.strip()]
    return sub in admin_subs
```

---

### Pattern Fernet encrypt/decrypt

**Source :** `apps/memory-api/app/routes/admin_drive.py` lignes 46-56 + 287-289
**Application :** stockage GITHUB_CLIENT_SECRET si nécessaire (ou réutilisation de la même clé Fernet)
```python
def _require_fernet():
    from cryptography.fernet import Fernet
    return Fernet(settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY.encode())

# Encrypt:
encrypted = fernet.encrypt(json.dumps(tokens).encode()).decode()
# Decrypt:
tokens = json.loads(fernet.decrypt(encrypted.encode()).decode())
```

---

## Fichiers sans analogue interne

| Fichier | Role | Data Flow | Raison |
|---|---|---|---|
| `chrome-extension/manifest.json` | config | — | Premier composant browser/extension du projet |
| `chrome-extension/popup.html` | component | request-response | Aucune UI HTML dans le codebase (tout est Docker/Python) |
| `projects-dashboard/index.html` | component | request-response | Premier site statique généré du projet |
| `.github/workflows/deploy-brain.yml` | config | — | Premier GitHub Actions workflow CI/CD du projet |

Pour ces 4 fichiers, le planner doit s'appuyer sur les patterns de la doc externe (MV3 Chrome Ext docs, GitHub Actions docs, Firebase Hosting deploy) plutôt que sur du code interne.

---

## Perimetre de recherche

**Répertoires analysés :** `apps/mcp-scraper/`, `apps/mcp-deck/`, `apps/memory-api/app/`, `apps/agent-runtime/app/tools/`, `apps/mcp-gateway/app/`, `infrastructure/nginx/conf.d/`, `infrastructure/docker-compose.yml`

**Fichiers lus :** 20 fichiers sources

**Date extraction patterns :** 2026-05-06
