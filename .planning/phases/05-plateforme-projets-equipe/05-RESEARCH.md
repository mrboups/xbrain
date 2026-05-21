# Phase 5 — Plateforme Projets Équipe — Research

**Researched:** 2026-05-06
**Domain:** Graphiti / GitHub OAuth / Chrome Extensions MV3 / Cloud Run CI/CD / Firebase Hosting / Cloudflare Access
**Confidence:** MEDIUM (la plupart des domaines vérifiés via docs officielles ou GitHub source, quelques points Cloudflare Access restent partiellement assumed)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D1** — Pipeline déploiement : Cloud Run (services) + Firebase Hosting (statique). `brain.yaml` dans chaque repo. Pas de VM xbrain pour héberger les projets.
- **D2** — Graphiti tourne dans son propre container `graphiti-service` (port 8300), wrappé par une REST API FastAPI maison (~50 lignes). Backend Neo4j existant (port 7687). LLM : Claude Haiku. Multi-tenancy via `group_id = team_scope`.
- **D3** — Extension Chrome Manifest V3, auth Google OAuth (même provider que LibreChat).
- **D4** — LibreChat GitHub OAuth en parallèle de Google. Vérification membership GitHub Org via GitHub API avec cache 5min. Org : `your-github-org`. Google-only users : accès team complet.
- **D5** — Dashboard `projects.dejavu.cat` — site statique généré via GitHub Actions. Firebase Hosting. Protégé par Cloudflare Access (emails whitelistés, zero code).
- **D6** — MCP tools Phase 3/4 restent. Nouveaux tools s'enregistrent via `brain.yaml` avec `type: mcp_tool`.
- **D7** — RBAC par projet pour Google users déféré v2.

### Claude's Discretion
- Design exact du dashboard (UI, framework CSS)
- Format exact du `brain.yaml`
- Stratégie de cache pour les appels GitHub API (5min suggéré)
- Choix du modèle Claude pour Graphiti extraction (Haiku suggéré)
- Gestion des erreurs GitHub API rate limit

### Deferred Ideas (OUT OF SCOPE)
- RBAC par projet pour Google users
- Hot-reload temps-réel au push
- Versioning des projets déployés (rollback)
- Drive sync autres teams
- MCP tool registry public
- Notion/Slack/Linear connectors
</user_constraints>

---

## Summary

Phase 5 introduit cinq composants nouveaux dans le stack. Chacun correspond à une décision technique distincte avec ses propres pièges. Cette recherche répond aux 7 questions ouvertes identifiées et documente les gotchas non-évidents pour chaque domaine.

**Graphiti** (`graphiti-core[anthropic]`) est la bibliothèque Python à emballer dans un container FastAPI isolé. Le piège principal est l'event loop : tenter d'initialiser Graphiti au niveau module ou dans un thread provoque des `RuntimeError: Future attached to a different loop`. La solution canonique est le `lifespan` context manager FastAPI — exactement le pattern déjà en place dans `memory-api`. Le LLM Anthropic/Haiku fonctionne mais **nécessite impérativement une clé OpenAI en parallèle** pour les embeddings et le reranking — il n'existe pas d'alternative Anthropic pour ces deux fonctions.

**GitHub OAuth dans LibreChat** est trivial : 3 variables d'environnement + 1 ligne dans `librechat.yaml`. Aucune modification de code LibreChat requise.

**Vérification membership GitHub** est faisable avec un PAT en lecture seule, mais le cache est critique : sans cache, le rate limit (5000 req/h) est atteint rapidement sur des routes fréquentes. La stratégie `asyncio.Lock` ou `dict` en mémoire avec TTL 5min est suffisante.

**Cloud Run depuis GitHub Actions** s'appuie sur Workload Identity Federation (sans JSON key file) + `google-github-actions/deploy-cloudrun@v3`. Quatre IAM roles sont nécessaires, plus deux APIs GCP à activer.

**Firebase Hosting** s'automatise via `FirebaseExtended/action-hosting-deploy` (action officielle) ou `firebase deploy --no-interactive`. Service account avec 4 roles IAM.

**Extension Chrome MV3 + Google OAuth** : `chrome.identity.getAuthToken()` retourne un **access token**, pas un ID token. Pour obtenir un ID token vérifiable côté FastAPI, il faut `launchWebAuthFlow` avec `response_type=id_token` — ou passer par l'endpoint tokeninfo de Google. Le backend xbrain utilise déjà `verify_google_id_token` dans `auth.py` — il faut s'assurer que le token envoyé depuis l'extension est bien un ID token JWT signé.

**Cloudflare Access** est disponible sur le plan **gratuit pour jusqu'à 50 users** (Zero Trust Free). Le subdomain `projects.dejavu.cat` se protège en ajoutant une application "Self-hosted" dans Zero Trust > Access > Applications, avec Google comme identity provider.

**Recommandation principale :** Commencer par graphiti-service (le plus risqué techniquement à cause de l'event loop + la dépendance OpenAI embeddings), puis GitHub OAuth LibreChat (rapide), puis le pipeline Cloud Run / Firebase.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Extraction temporelle de faits | graphiti-service (container) | Neo4j (stockage) | Isolation event loop — bibliothèque async avec ses propres opinions sur le loop |
| Auth GitHub OAuth | LibreChat (frontend) | memory-api (vérification membership) | LibreChat gère le flow OAuth ; memory-api vérifie l'appartenance Org à chaque requête |
| Vérification membership GitHub Org | memory-api (middleware deps.py) | GitHub API (source de vérité) | Aligné sur le pattern existant `get_team_scope` dans deps.py |
| Déploiement projets | GitHub Actions (CI/CD) | Cloud Run / Firebase Hosting | Pipeline external au stack xbrain — pas de dépendance à la VM |
| Brain indexing au deploy | GitHub Actions (step curl) | memory-api (endpoint /v1/memory) | Le job deploy fait un POST direct à api.dejavu.cat après deploy |
| Extension Chrome auth | Browser (chrome.identity) | memory-api (vérification ID token) | Token obtenu côté browser, vérifié côté API — même flux que LibreChat Google OAuth |
| Dashboard projets | Firebase Hosting (statique) | GitHub Actions (génération) | Statique généré, pas de backend — protégé par Cloudflare Access en amont |
| Protection accès dashboard | Cloudflare Access (edge) | — | Zéro code, configuration Cloudflare uniquement |

---

## Q1 — Graphiti `graphiti-service` : FastAPI wrapper, group_id, async pitfalls

### Surface API minimale vérifiée

Le serveur officiel Graphiti (dossier `server/`) expose :
[VERIFIED: github.com/getzep/graphiti/blob/main/server/graph_service/routers/ingest.py]
[VERIFIED: github.com/getzep/graphiti/blob/main/server/graph_service/routers/retrieve.py]

```
POST /messages          → ingest episodes (liste de messages avec group_id)
POST /search            → recherche sémantique avec group_ids (liste)
GET  /get-memory        → recherche orientée mémoire (group_id singulier)
DELETE /group/{group_id} → purger un namespace complet
DELETE /entity-edge/{uuid}
DELETE /episode/{uuid}
POST /entity-node
```

Pour xbrain, la surface minimale nécessaire est :

```
POST /v1/ingest   → add_episode(content, group_id, reference_time)
POST /v1/search   → search(query, group_ids, max_facts)
GET  /v1/healthz  → status check
```

La détection de contradictions est automatique dans `add_episode` — pas d'endpoint dédié nécessaire. Graphiti invalide les faits contradictoires pendant l'ingestion via LLM.

### Initialisation avec Neo4j et group_id

[VERIFIED: help.getzep.com/graphiti/getting-started/quick-start]
[VERIFIED: help.getzep.com/graphiti/core-concepts/graph-namespacing]

```python
from graphiti_core import Graphiti
from graphiti_core.nodes import EpisodeType

# Initialisation (dans le lifespan FastAPI — voir ci-dessous)
graphiti = Graphiti(
    uri="bolt://neo4j:7687",      # env NEO4J_URI
    user="neo4j",                 # env NEO4J_USER
    password="<secret>",          # env NEO4J_PASSWORD
)
await graphiti.build_indices_and_constraints()   # à appeler UNE seule fois au démarrage

# Ingest avec group_id (multi-tenancy)
await graphiti.add_episode(
    name="episode-001",
    episode_body="Le budget Q2 est validé à 50k€",
    source=EpisodeType.text,
    source_description="LibreChat conversation",
    reference_time=datetime.now(timezone.utc),
    group_id="acme",           # = team_scope → isolation par équipe
)

# Recherche scopée
results = await graphiti.search(
    query="budget Q2",
    group_ids=["acme"],        # liste de namespaces
    num_results=10,
)
# results est une liste d'EdgeResult avec .fact (str)
```

**Important :** `group_id` est un champ arbitraire — aucune validation côté Graphiti. Pour xbrain, utiliser `team_scope` comme valeur. Pour supprimer toutes les données d'une team : `DELETE /group/{team_scope}`.

### Configuration LLM — piège critique : OpenAI obligatoire pour les embeddings

[VERIFIED: help.getzep.com/graphiti/configuration/llm-configuration]

Graphiti avec Anthropic/Claude pour le LLM d'extraction **requiert impérativement OpenAI pour les embeddings et le reranking**. Il n'existe pas de provider Anthropic pour ces deux fonctions dans graphiti-core v0.29.

```python
from graphiti_core.llm_client.anthropic_client import AnthropicClient, LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

graphiti = Graphiti(
    "bolt://neo4j:7687", "neo4j", password,
    llm_client=AnthropicClient(
        config=LLMConfig(
            api_key=ANTHROPIC_API_KEY,
            model="claude-haiku-4-5-20251001",       # modèle principal extraction
            small_model="claude-haiku-4-5-20251001"  # même modèle pour les tâches mineures
        )
    ),
    embedder=OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key=OPENAI_API_KEY,
            embedding_model="text-embedding-3-small"   # modèle embedding, ~$0.02/1M tokens
        )
    ),
    cross_encoder=OpenAIRerankerClient(
        config=LLMConfig(
            api_key=OPENAI_API_KEY,
            model="gpt-4.1-nano"                      # modèle reranking, coût minimal
        )
    )
)
```

**Installation :** `pip install "graphiti-core[anthropic]"` (le bracket `[anthropic]` installe `anthropic` SDK)

Variables d'environnement requises dans `graphiti-service` :
- `ANTHROPIC_API_KEY` (LLM extraction)
- `OPENAI_API_KEY` (embeddings + reranking — obligatoire même avec Anthropic comme LLM)
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`

### Async pitfall : event loop conflict — FIX canonique

[VERIFIED: medium.com/@saeedhajebi/a-production-ready-api-for-graphitis-powerful-but-flawed-memory-15f17a9c1b41]
[VERIFIED: github.com/getzep/graphiti/blob/main/server/graph_service/main.py]

**Le problème :** Tenter d'initialiser `Graphiti()` au niveau module, dans un thread, ou via `asyncio.to_thread()` provoque `RuntimeError: Future attached to a different loop` ou `RuntimeError: Event loop is closed`. La bibliothèque maintient des connexions persistantes Neo4j qui doivent vivre dans la même event loop que FastAPI.

**La solution :** Utiliser le `lifespan` context manager FastAPI (pattern déjà en place dans `memory-api/app/main.py`).

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

graphiti_client: Graphiti | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global graphiti_client
    graphiti_client = Graphiti(
        uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD,
        llm_client=AnthropicClient(...),
        embedder=OpenAIEmbedder(...),
        cross_encoder=OpenAIRerankerClient(...),
    )
    await graphiti_client.build_indices_and_constraints()
    yield
    await graphiti_client.close()   # fermeture propre des connexions Neo4j

app = FastAPI(lifespan=lifespan)
```

**Autre piège :** `add_episode` est CPU+IO bound (plusieurs appels LLM en cascade). Dans le serveur officiel Graphiti, les episodes sont mis dans une queue asyncio pour éviter les timeouts HTTP :

```python
# Pattern queue (du serveur officiel)
async def add_messages_task(content, group_id):
    await graphiti_client.add_episode(
        name=f"ep-{uuid4()}",
        episode_body=content,
        source=EpisodeType.text,
        reference_time=datetime.now(timezone.utc),
        group_id=group_id,
    )
    # Dure typiquement 3-10s selon la complexité du contenu

# Endpoint retourne 202 Accepted immédiatement
@app.post("/v1/ingest", status_code=202)
async def ingest(body: IngestBody):
    asyncio.create_task(add_messages_task(body.content, body.group_id))
    return {"status": "queued"}
```

**Troisième piège — SEMAPHORE_LIMIT :** Chaque `add_episode` fait plusieurs appels LLM simultanés (extraction entités, déduplication, résumé). Par défaut `SEMAPHORE_LIMIT=10` — avec Claude Haiku (rate limit Anthropic Tier 1 : 50 req/min), réduire à 3-5 pour éviter les 429.

### Docker — piège Neo4j startup race

`build_indices_and_constraints()` échoue si Neo4j n'est pas encore prêt. Utiliser `depends_on: neo4j: { condition: service_healthy }` dans docker-compose.yml + `start_period: 60s` sur le healthcheck (Neo4j prend ~45s à démarrer).

**Ce pattern est déjà appliqué** dans le PATTERNS.md (bloc graphiti-service, mem_limit: 512m, start_period: 60s).

---

## Q2 — GitHub OAuth dans LibreChat v0.8.5

### Verdict : 3 variables d'env + 1 ligne YAML

[VERIFIED: librechat.ai/docs/configuration/authentication/OAuth2-OIDC/github]

LibreChat supporte GitHub OAuth nativement via Passport.js. Aucune modification de code requise.

**Variables à ajouter dans `docker-compose.yml` (bloc `librechat`) :**

```yaml
GITHUB_CLIENT_ID: ${GITHUB_CLIENT_ID:-}
GITHUB_CLIENT_SECRET: ${GITHUB_CLIENT_SECRET:-}
GITHUB_CALLBACK_URL: /oauth/github/callback
```

L'URL complète de callback enregistrée dans GitHub App sera :
`https://x.dejavu.cat/oauth/github/callback`

**Modification `infrastructure/librechat/librechat.yaml` :**

```yaml
registration:
  socialLogins: ["google", "github"]   # ajout "github" à la liste existante
  allowedDomains:
    - "grooveos.app"
    - "gmail.com"
    # Note : pas de filtre domaine pour GitHub — la GitHub App restreint déjà l'accès
```

**Coexistence Google + GitHub :** Confirmé — `socialLogins` accepte une liste. Les deux providers fonctionnent en parallèle sur la page de login LibreChat. Un utilisateur peut se connecter avec l'un ou l'autre.

**Création de la GitHub App :**
1. GitHub Settings > Developer settings > OAuth Apps (pas GitHub Apps)
2. Homepage URL : `https://x.dejavu.cat`
3. Callback URL : `https://x.dejavu.cat/oauth/github/callback`
4. Désactiver Webhook
5. Permissions Email : Read-only (pour récupérer l'email principal)
6. Générer client secret

**Gotcha :** Les OAuth Apps GitHub (pas les GitHub Apps) ne permettent pas de restreindre par Org dans la configuration OAuth elle-même — n'importe quel compte GitHub peut initier le flow. La restriction par Org membership est gérée côté memory-api (voir Q3), pas côté LibreChat.

---

## Q3 — GitHub API membership checks : rate limits, cache, token type

### Rate limits

[VERIFIED: docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api]

| Token type | Rate limit | Note |
|------------|-----------|------|
| PAT (Fine-grained) | 5 000 req/h | Suffisant pour xbrain (petite équipe) |
| OAuth App token (user) | 5 000 req/h par user authentifié | Chaque user a son propre quota |
| GitHub App installation | 5 000 req/h minimum, scalable | Plus complexe à mettre en place |

**Conclusion :** Un PAT de service (lecture seule, scopes `read:org`) suffit pour la vérification membership côté serveur. Les 5000 req/h = 83 req/min, largement suffisant avec un cache 5min.

**Endpoint de vérification :**
```
GET /orgs/{org}/members/{username}
→ 204 No Content si membre
→ 302 Found (redirection) si pas membre public
→ 404 si pas membre du tout
```

Pour les outside collaborators :
```
GET /repos/{owner}/{repo}/collaborators/{username}
→ 204 si collaborateur
→ 404 si non
```

### Implémentation cache dans deps.py

[VERIFIED: codebase — pattern auth.py _jwks_cache lines 13-25]

Réutiliser le pattern dict + timestamp déjà en place dans `auth.py` (cache JWKs Google) :

```python
# Dans memory-api/app/auth.py (nouveau)
_github_membership_cache: dict[str, tuple[float, dict]] = {}
_GITHUB_CACHE_TTL = 300  # 5 minutes

async def check_github_org_membership(
    github_token: str,
    org: str,
    github_api_pat: str,            # PAT serveur en lecture seule
) -> dict:
    """Vérifie si le token GitHub appartient à l'org. Cache 5min."""
    now = time.time()
    cache_key = f"{github_token}:{org}"
    if cache_key in _github_membership_cache:
        ts, result = _github_membership_cache[cache_key]
        if now - ts < _GITHUB_CACHE_TTL:
            return result

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Récupérer le profil user depuis son token OAuth
        user_r = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        user_r.raise_for_status()
        user_data = user_r.json()
        username = user_data["login"]

        # 2. Vérifier la membership Org avec le PAT serveur (évite les quotas du user)
        org_r = await client.get(
            f"https://api.github.com/orgs/{org}/members/{username}",
            headers={
                "Authorization": f"Bearer {github_api_pat}",  # PAT serveur
                "Accept": "application/vnd.github+json",
            },
        )
        is_member = org_r.status_code == 204

    result = {
        "login": username,
        "email": user_data.get("email"),
        "name": user_data.get("name"),
        "is_org_member": is_member,
    }
    _github_membership_cache[cache_key] = (now, result)
    return result
```

**Variable d'env supplémentaire :** `GITHUB_API_PAT` — PAT serveur (fine-grained, permission `read:org`, durée longue). Stocker dans `.env` VM.

**Gotcha — token user vs PAT serveur :** L'endpoint `GET /orgs/{org}/members/{username}` ne retourne 204 que si :
- (a) le token authentifié a `read:org` scope ET
- (b) le membre est public dans l'org.
Pour les membres privés, utiliser le PAT serveur (membre de l'org) qui voit tous les membres. Ne pas dépendre uniquement du token OAuth du user.

### Token type recommandé : PAT Fine-grained (serveur)

Pour la vérification membership côté serveur :
- **PAT Fine-grained** avec scope `read:org` sur l'org `your-github-org`
- Durée : 1 an maximum, rotation annuelle
- **Pas de GitHub App** : complexité inutile pour une seule org

---

## Q4 — Cloud Run deploy depuis GitHub Actions

### GCP APIs à activer

[VERIFIED: docs.cloud.google.com/run/docs/deploying-source-code]

```bash
gcloud services enable run.googleapis.com cloudapis.googleapis.com \
    cloudbuild.googleapis.com artifactregistry.googleapis.com \
    --project=xbrain-495115
```

APIs requises :
- `run.googleapis.com` — Cloud Run Admin API
- `cloudbuild.googleapis.com` — Cloud Build (utilisé par `--source` pour builder l'image)
- `artifactregistry.googleapis.com` — Artifact Registry (stockage de l'image buildée)

### IAM roles pour le service account GitHub Actions

[VERIFIED: docs.cloud.google.com/run/docs/deploying-source-code]
[VERIFIED: cloud.google.com/blog/products/devops-sre/deploy-to-cloud-run-with-github-actions]

```bash
export SA="github-deploy@xbrain-495115.iam.gserviceaccount.com"
export PROJECT="xbrain-495115"
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')

# Roles pour le service account qui exécute le deploy
gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:${SA}" \
    --role="roles/run.sourceDeveloper"    # deploy --source

gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:${SA}" \
    --role="roles/artifactregistry.repoAdmin"   # push image

gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:${SA}" \
    --role="roles/iam.serviceAccountUser"   # impersonate le SA Cloud Run

# Le SA Cloud Build (défaut) doit avoir le role Cloud Run Builder
gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/run.builder"
```

### Workflow GitHub Actions minimal (Workload Identity Federation — sans JSON key)

[VERIFIED: cloud.google.com/blog/products/devops-sre/deploy-to-cloud-run-with-github-actions]
[VERIFIED: github.com/google-github-actions/deploy-cloudrun]

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write          # requis pour Workload Identity Federation

    steps:
      - uses: actions/checkout@v4

      - uses: google-github-actions/auth@v3
        with:
          workload_identity_provider: >-
            projects/${{ vars.GCP_PROJECT_NUMBER }}/locations/global/
            workloadIdentityPools/github-pool/providers/github-provider
          service_account: github-deploy@xbrain-495115.iam.gserviceaccount.com

      - uses: google-github-actions/setup-gcloud@v2

      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy ${{ vars.CLOUD_RUN_SERVICE_NAME }} \
            --source . \
            --region europe-west1 \
            --project xbrain-495115 \
            --allow-unauthenticated     # ou --no-allow-unauthenticated selon visibilité

      - name: Brain indexing
        env:
          MEMORY_API_URL: https://api.dejavu.cat
          BRIDGE_JWT: ${{ secrets.XBRAIN_BRIDGE_JWT }}
        run: |
          # Lire brain.yaml et indexer le contenu dans xbrain
          python3 infrastructure/scripts/brain-index.py brain.yaml
```

**Alternative sans Workload Identity Federation (JSON key — plus simple à mettre en place) :**

```yaml
      - uses: google-github-actions/auth@v3
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY_JSON }}
```

**Gotcha — `--source` vs Dockerfile :** `gcloud run deploy --source .` utilise Cloud Buildpacks si pas de Dockerfile, sinon utilise le Dockerfile. Pour les projets avec Dockerfile, le comportement est prévisible. Pour les projets sans Dockerfile, Cloud Buildpacks détecte le langage automatiquement.

**Gotcha — `europe-west1` :** Cloud Run en `europe-west1` est disponible. Vérifier que la région correspond au projet `xbrain-495115`.

**Setup Workload Identity Federation (une seule fois) :**

```bash
# Créer le pool
gcloud iam workload-identity-pools create github-pool \
    --location=global --project=xbrain-495115

# Créer le provider
gcloud iam workload-identity-pools providers create-oidc github-provider \
    --location=global \
    --workload-identity-pool=github-pool \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --project=xbrain-495115

# Autoriser l'identité GitHub à impersonner le SA
export POOL_ID=$(gcloud iam workload-identity-pools describe github-pool \
    --location=global --project=xbrain-495115 --format='value(name)')

gcloud iam service-accounts add-iam-policy-binding \
    github-deploy@xbrain-495115.iam.gserviceaccount.com \
    --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/your-github-org/REPO_NAME"
```

---

## Q5 — Firebase Hosting deploy depuis GitHub Actions

### Deux approches — recommandation : action officielle

[VERIFIED: firebase.google.com/docs/hosting/github-integration]
[VERIFIED: github.com/FirebaseExtended/action-hosting-deploy/blob/main/docs/service-account.md]

**Approche A (recommandée) : `FirebaseExtended/action-hosting-deploy`**

```yaml
name: Deploy Dashboard to Firebase Hosting

on:
  push:
    branches: [main]
  workflow_run:                 # déclenché par d'autres workflows si besoin
    workflows: [Deploy to Cloud Run]
    types: [completed]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build dashboard
        run: |
          # Génération du site statique (script Python ou JS)
          python3 scripts/generate_dashboard.py > public/index.html

      - uses: FirebaseExtended/action-hosting-deploy@v0
        with:
          repoToken: ${{ secrets.GITHUB_TOKEN }}
          firebaseServiceAccount: ${{ secrets.FIREBASE_SERVICE_ACCOUNT_XBRAIN }}
          projectId: xbrain-495115
          channelId: live         # deploy sur le canal live (pas preview)
```

**Service account Firebase — 4 roles IAM requis :**

```bash
export FSA="firebase-deploy@xbrain-495115.iam.gserviceaccount.com"
gcloud projects add-iam-policy-binding xbrain-495115 \
    --member="serviceAccount:${FSA}" \
    --role="roles/firebasehosting.admin"
gcloud projects add-iam-policy-binding xbrain-495115 \
    --member="serviceAccount:${FSA}" \
    --role="roles/firebaseauth.admin"           # uniquement si auth Firebase utilisée
gcloud projects add-iam-policy-binding xbrain-495115 \
    --member="serviceAccount:${FSA}" \
    --role="roles/serviceusage.apiKeysViewer"   # pour les deploys CLI
gcloud projects add-iam-policy-binding xbrain-495115 \
    --member="serviceAccount:${FSA}" \
    --role="roles/run.viewer"                   # si rewrites Cloud Run
```

**Approche B (alternative CLI) :**

```yaml
      - run: npm install -g firebase-tools
      - run: firebase deploy --only hosting --project xbrain-495115 --non-interactive
        env:
          FIREBASE_TOKEN: ${{ secrets.FIREBASE_CI_TOKEN }}  # firebase login:ci
```

**Gotcha — `FIREBASE_TOKEN` déprécié :** La méthode `firebase login:ci` + FIREBASE_TOKEN est dépréciée au profit des service accounts. Utiliser l'approche A.

**`firebase.json` minimal requis à la racine du repo dashboard :**

```json
{
  "hosting": {
    "public": "public",
    "ignore": ["firebase.json", "**/.*"],
    "rewrites": []
  }
}
```

**Initialisation Firebase Hosting (une seule fois) :**

```bash
firebase init hosting --project xbrain-495115
# Répondre "public" pour le dossier public
# Répondre "No" pour single-page app
# Répondre "No" pour GitHub Actions auto-setup (on le fait manuellement)
```

---

## Q6 — Extension Chrome Manifest V3 + Google OAuth → FastAPI backend

### Problème central : access token vs ID token

[VERIFIED: developer.chrome.com/docs/extensions/reference/api/identity]
[VERIFIED: developers.google.com/identity/gsi/web/guides/verify-google-id-token]

`chrome.identity.getAuthToken()` retourne un **access token OAuth2**, pas un ID token JWT. Le backend xbrain (`auth.py:verify_google_id_token`) attend un **ID token JWT signé** par Google.

**Deux solutions :**

**Solution A (recommandée) : `launchWebAuthFlow` avec `response_type=id_token`**

```javascript
// manifest.json
{
  "manifest_version": 3,
  "name": "xbrain Web Clipper",
  "version": "1.0",
  "permissions": ["identity", "activeTab", "storage"],
  "background": { "service_worker": "background.js" },
  "action": {
    "default_popup": "popup.html",
    "default_icon": "icon48.png"
  },
  "oauth2": {
    "client_id": "GOOGLE_CLIENT_ID.apps.googleusercontent.com",
    "scopes": ["openid", "email", "profile"]
  }
}
```

```javascript
// background.js — obtenir un ID token via launchWebAuthFlow
function getGoogleIdToken() {
  return new Promise((resolve, reject) => {
    const REDIRECT_URI = `https://${chrome.runtime.id}.chromiumapp.org/`;
    const CLIENT_ID = "GOOGLE_CLIENT_ID.apps.googleusercontent.com";
    const nonce = Math.random().toString(36).substring(2);

    const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
    authUrl.searchParams.set("client_id", CLIENT_ID);
    authUrl.searchParams.set("response_type", "id_token");   // ← ID token direct
    authUrl.searchParams.set("redirect_uri", REDIRECT_URI);
    authUrl.searchParams.set("scope", "openid email profile");
    authUrl.searchParams.set("nonce", nonce);

    chrome.identity.launchWebAuthFlow(
      { url: authUrl.toString(), interactive: true },
      (redirectUrl) => {
        if (chrome.runtime.lastError) {
          reject(chrome.runtime.lastError);
          return;
        }
        // ID token est dans le fragment (#id_token=...)
        const hash = new URL(redirectUrl).hash.substring(1);
        const params = new URLSearchParams(hash);
        resolve(params.get("id_token"));
      }
    );
  });
}
```

**Solution B (plus simple mais moins sécurisée) : utiliser l'endpoint tokeninfo Google**

```javascript
// Obtenir un access token via getAuthToken, puis appeler tokeninfo
chrome.identity.getAuthToken({ interactive: true }, async (accessToken) => {
  // Appeler l'endpoint tokeninfo pour obtenir les claims
  const info = await fetch(
    `https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=${accessToken}`
  ).then(r => r.json());
  // info.email, info.user_id (= sub Google)
  // Envoyer le access_token au backend pour vérification
});
```

Pour la Solution B, le backend doit appeler `https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=TOKEN` plutôt que de vérifier un JWT — cela implique une modification de `auth.py`. **Solution A est préférable** car elle réutilise `verify_google_id_token` sans modification.

**Appel vers memory-api (popup.js) :**

```javascript
// Pattern identique à mcp-deck (voir PATTERNS.md section 12)
async function sendToBrain(idToken, payload) {
  const response = await fetch("https://api.dejavu.cat/v1/memory/upsert", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${idToken}`,
      "X-Team-Scope": payload.teamScope,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      item: {
        content: payload.selectedText,
        team_scope: payload.teamScope,
        project_scope: payload.projectScope || null,
        visibility: "team",
        confidence: 1.0,
        truth_level: payload.truthLevel,
        source: `chrome:${payload.sourceUrl}`,
        validation_status: "pending",
      }
    }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
```

**Manifest V3 — contraintes importantes :**
- `manifest_version: 3` est requis pour les nouvelles extensions (MV2 déprécié)
- Les service workers (background) ne peuvent pas utiliser `XMLHttpRequest` — utiliser `fetch` uniquement
- `chrome.identity.launchWebAuthFlow` fonctionne en MV3 (contrairement à certaines API qui ont changé)
- Le `redirect_uri` doit être exactement `https://${chrome.runtime.id}.chromiumapp.org/` (généré par Chrome)

**Gotcha — CORS sur api.dejavu.cat :** L'extension appelle directement `api.dejavu.cat`. memory-api (FastAPI) doit avoir le header `Access-Control-Allow-Origin` approprié. Ajouter dans FastAPI :

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["chrome-extension://*"],   # ou l'ID exact de l'extension
    allow_credentials=True,
    allow_methods=["POST", "GET"],
    allow_headers=["Authorization", "X-Team-Scope", "Content-Type"],
)
```

---

## Q7 — Cloudflare Access pour `projects.dejavu.cat`

### Plan gratuit : oui, jusqu'à 50 users

[VERIFIED: cloudflare.com/plans/zero-trust-services/]

Cloudflare Zero Trust Free = gratuit jusqu'à **50 users**. Inclut Cloudflare Access (protection subdomains). Suffisant pour xbrain.

### Setup `projects.dejavu.cat` — étapes

[VERIFIED: developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-public-app/]
[VERIFIED: developers.cloudflare.com/cloudflare-one/integrations/identity-providers/google/]

**Prérequis :** `dejavu.cat` est déjà sur Cloudflare (c'est le domaine existant du projet). Le subdomain se crée comme un enregistrement DNS standard.

**Étape 1 — DNS :**

Dans Cloudflare DNS, créer un enregistrement :
```
Type: CNAME (ou A si IP statique)
Name: projects
Target: l'IP de la VM (__VM_HOST__) via enregistrement A
        OU pointer vers Firebase Hosting si hébergé là

Si Firebase Hosting :
  Type: CNAME
  Name: projects
  Target: xbrain-495115.web.app   (ou le custom domain Firebase)
```

Pour Firebase Hosting custom domain `projects.dejavu.cat` : vérifier la propriété dans la console Firebase (TXT record), puis ajouter CNAME dans Cloudflare.

**Étape 2 — Identity Provider Google dans Cloudflare Zero Trust :**

Zero Trust dashboard > Settings > Authentication > Add new > Google

Paramètres requis dans Google Cloud Console :
- Type : Web application
- Authorized redirect URI : `https://[your-team-name].cloudflareaccess.com/cdn-cgi/access/callback`

Copier le Client ID et Client Secret dans Cloudflare.

**Étape 3 — Application Access :**

Zero Trust > Access > Applications > Add an application > Self-hosted

```
Application name: xbrain Projects Dashboard
Session Duration: 24h
Application Domain: projects.dejavu.cat
Identity providers: Google (sélectionner ce qui a été configuré étape 2)
```

**Étape 4 — Policy :**

```
Policy name: Team Access
Action: Allow
Rule type: Emails
Values: team@grooveos.app, team@grooveos.app  (ou tous les emails équipe)
```

Alternative plus flexible :
```
Rule type: Email domain
Values: grooveos.app
```

**Résultat :** Tout accès à `https://projects.dejavu.cat` est intercepté par Cloudflare Access, qui affiche un écran de login Google. Seuls les emails whitelistés passent. **Zéro code côté serveur requis.**

**Gotcha — Cloudflare Tunnel vs proxy direct :** Si le dashboard est hébergé sur Firebase Hosting (externe, pas la VM), le CNAME pointe vers Firebase — Cloudflare Access protège via son proxy edge. Si le dashboard est servi par nginx sur la VM, le CNAME pointe vers la VM (déjà derrière Cloudflare) — même principe.

**Gotcha — Access JWT validation :** Par défaut, Cloudflare Access ne transmet pas de JWT à l'origin (Firebase Hosting dans ce cas). Pour un site statique, c'est correct — la protection est 100% au niveau edge Cloudflare. Si l'origin avait un backend dynamique, il faudrait valider le CF-Access-JWT-Assertion header.

---

## Common Pitfalls

### Pitfall 1 — Graphiti : OpenAI embeddings obligatoires
**Ce qui se passe :** L'import `from graphiti_core.llm_client.anthropic_client import AnthropicClient` réussit, le container démarre, mais `add_episode` échoue à la phase embedding avec `openai.AuthenticationError`.
**Cause :** Graphiti ne propose pas d'embedder Anthropic. Le provider Anthropic ne couvre que la partie LLM extraction, pas les embeddings ni le reranking.
**Fix :** Ajouter `OPENAI_API_KEY` dans `graphiti-service` même si Anthropic est le LLM principal. Coût marginal : `text-embedding-3-small` à ~$0.02/1M tokens.

### Pitfall 2 — Graphiti : `build_indices_and_constraints()` exécuté plusieurs fois
**Ce qui se passe :** Si graphiti-service redémarre, `build_indices_and_constraints()` est rappelé — c'est idempotent, pas de problème. Mais si deux instances tournent simultanément, il peut y avoir des conflits d'index Neo4j.
**Fix :** `mem_limit: 512m` + `restart: unless-stopped` (pas de scale horizontal pour l'instant).

### Pitfall 3 — LibreChat GitHub OAuth : callback URL mal configurée
**Ce qui se passe :** Redirect 404 après login GitHub car la callback URL dans la GitHub App ne correspond pas.
**Fix :** La callback URL dans GitHub App doit être exactement `https://x.dejavu.cat/oauth/github/callback`. `GITHUB_CALLBACK_URL` dans le `.env` est le chemin seulement : `/oauth/github/callback`.

### Pitfall 4 — GitHub membership : membres privés de l'Org non détectés
**Ce qui se passe :** Un membre de l'Org avec profil privé retourne 302 (redirect) au lieu de 204 sur `GET /orgs/{org}/members/{username}` quand appelé avec le token OAuth du user. L'implémentation le considère non-membre à tort.
**Fix :** Appeler l'endpoint membership avec le PAT serveur (qui est lui-même membre de l'Org) — les membres privés sont visibles pour un membre authentifié.

### Pitfall 5 — Extension Chrome : `chrome.runtime.id` change entre dev et prod
**Ce qui se passe :** L'extension non publiée sur Chrome Web Store a un ID aléatoire. Le `redirect_uri` `https://${chrome.runtime.id}.chromiumapp.org/` change à chaque installation non-packagée.
**Fix :** Pour les tests locaux, utiliser `chrome.identity.getAuthToken` (ID géré par Chrome). Pour la prod, publier l'extension sur Chrome Web Store — l'ID devient stable (lié à la clé de signature).

### Pitfall 6 — CORS depuis l'extension Chrome
**Ce qui se passe :** L'extension envoie des requêtes cross-origin vers `api.dejavu.cat`. Sans CORS configuré dans FastAPI, les requêtes échouent avec `CORS policy` error.
**Fix :** Ajouter `CORSMiddleware` dans memory-api avec `allow_origins=["chrome-extension://EXTENSION_ID"]` (voir Q6 ci-dessus).

### Pitfall 7 — Cloud Run `--source` : Artifact Registry pas activé
**Ce qui se passe :** `gcloud run deploy --source .` échoue avec `API not enabled` si Artifact Registry n'est pas activé.
**Fix :** Activer `artifactregistry.googleapis.com` avant le premier deploy.

### Pitfall 8 — Cloudflare Access : subdomain non proxifié
**Ce qui se passe :** Le DNS record `projects.dejavu.cat` existe mais le trafic n'est pas intercepté par Cloudflare Access car l'enregistrement DNS n'est pas en mode "Proxied" (nuage orange).
**Fix :** Dans Cloudflare DNS, s'assurer que le record `projects` est en mode Proxied (orange cloud), pas DNS-only (gris).

---

## Code Examples

### graphiti-service/app/main.py — squelette complet

```python
from __future__ import annotations
import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

import structlog
from fastapi import FastAPI, HTTPException
from graphiti_core import Graphiti
from graphiti_core.llm_client.anthropic_client import AnthropicClient, LLMConfig
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.nodes import EpisodeType
from pydantic import BaseModel

log = structlog.get_logger(__name__)

NEO4J_URI      = os.environ.get("NEO4J_URI", "bolt://neo4j:7687")
NEO4J_USER     = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL      = os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001")

graphiti_client: Graphiti | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global graphiti_client
    log.info("graphiti.init_start")
    graphiti_client = Graphiti(
        uri=NEO4J_URI,
        user=NEO4J_USER,
        password=NEO4J_PASSWORD,
        llm_client=AnthropicClient(
            config=LLMConfig(
                api_key=ANTHROPIC_API_KEY,
                model=LLM_MODEL,
                small_model=LLM_MODEL,
            )
        ),
        embedder=OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key=OPENAI_API_KEY,
                embedding_model="text-embedding-3-small",
            )
        ),
        cross_encoder=OpenAIRerankerClient(
            config=LLMConfig(api_key=OPENAI_API_KEY, model="gpt-4.1-nano")
        ),
    )
    await graphiti_client.build_indices_and_constraints()
    log.info("graphiti.init_done")
    yield
    await graphiti_client.close()
    log.info("graphiti.closed")


app = FastAPI(title="xbrain graphiti-service", version="0.1.0", lifespan=lifespan)


class IngestBody(BaseModel):
    content: str
    group_id: str
    source: str = "memory-api"
    reference_time: datetime | None = None


class SearchBody(BaseModel):
    query: str
    group_ids: list[str]
    max_facts: int = 10


@app.post("/v1/ingest", status_code=202)
async def ingest(body: IngestBody):
    if graphiti_client is None:
        raise HTTPException(503, "Graphiti not initialized")
    log.info("graphiti.ingest", group_id=body.group_id, len=len(body.content))

    async def _task():
        await graphiti_client.add_episode(
            name=f"ep-{uuid4()}",
            episode_body=body.content,
            source=EpisodeType.text,
            source_description=body.source,
            reference_time=body.reference_time or datetime.now(timezone.utc),
            group_id=body.group_id,
        )
        log.info("graphiti.ingest_done", group_id=body.group_id)

    asyncio.create_task(_task())
    return {"status": "queued"}


@app.post("/v1/search")
async def search(body: SearchBody):
    if graphiti_client is None:
        raise HTTPException(503, "Graphiti not initialized")
    results = await graphiti_client.search(
        query=body.query,
        group_ids=body.group_ids,
        num_results=body.max_facts,
    )
    return {"facts": [r.fact for r in results], "count": len(results)}


@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok", "graphiti": graphiti_client is not None}
```

### Vérification membership GitHub (auth.py addition)

```python
# Source: pattern adapté de auth.py _jwks_cache + docs.github.com
_github_membership_cache: dict[str, tuple[float, dict]] = {}
_GITHUB_CACHE_TTL = 300


async def check_github_org_membership(
    github_token: str, org: str, server_pat: str
) -> dict:
    cache_key = f"{github_token[:16]}:{org}"  # tronquer le token pour la clé cache
    now = time.time()
    if cache_key in _github_membership_cache:
        ts, result = _github_membership_cache[cache_key]
        if now - ts < _GITHUB_CACHE_TTL:
            return result

    async with httpx.AsyncClient(timeout=10.0) as client:
        user_r = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {github_token}",
                     "Accept": "application/vnd.github+json"},
        )
        user_r.raise_for_status()
        username = user_r.json()["login"]

        org_r = await client.get(
            f"https://api.github.com/orgs/{org}/members/{username}",
            headers={"Authorization": f"Bearer {server_pat}",
                     "Accept": "application/vnd.github+json"},
        )
        is_member = org_r.status_code == 204

    result = {
        "login": username,
        "email": user_r.json().get("email"),
        "is_org_member": is_member,
    }
    _github_membership_cache[cache_key] = (now, result)
    return result
```

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Extraction temporelle + contradiction detection | Logique LLM custom | `graphiti-core` | 4 timestamps par relation, invalidation automatique, déjà battle-tested |
| Verification JWT Google dans l'extension | Décodage JWT manuel | `launchWebAuthFlow` + `response_type=id_token` | Chrome gère le flow complet + refresh |
| Embed/reranker Anthropic-only | Wrapper custom | OpenAI `text-embedding-3-small` + `gpt-4.1-nano` | Graphiti ne supporte pas Anthropic pour ces deux fonctions |
| Rate limiting GitHub API | Compteur custom | Cache dict Python 5min TTL | Suffisant pour petite équipe, zéro dépendance externe |
| CI/CD Firebase | Script gcloud custom | `FirebaseExtended/action-hosting-deploy@v0` | Action officielle, gère preview channels + live, idempotent |

---

## Environment Availability

| Dependency | Required By | Available | Notes |
|------------|------------|-----------|-------|
| Neo4j (xbrain-neo4j) | graphiti-service | ✓ | Déjà dans docker-compose.yml, v2026.04.0 |
| OpenAI API | graphiti-service (embeddings) | A confirmer | OPENAI_API_KEY existe dans le stack (agent-runtime) — à propager à graphiti-service |
| Anthropic API | graphiti-service (LLM) | ✓ | ANTHROPIC_API_KEY déjà dans le stack |
| GitHub OAuth App | LibreChat GitHub auth | A créer | Créer dans GitHub Settings > Developer Settings > OAuth Apps |
| GitHub PAT serveur | membership verification | A créer | Fine-grained PAT, scope `read:org` sur your-github-org |
| Cloud Run API | deploy pipeline | A activer | `gcloud services enable run.googleapis.com` sur xbrain-495115 |
| Cloud Build API | `--source` deploy | A activer | `gcloud services enable cloudbuild.googleapis.com` |
| Firebase Hosting | dashboard | A initialiser | `firebase init hosting` sur xbrain-495115 |
| Cloudflare Access | projects.dejavu.cat | ✓ | Plan gratuit suffisant (< 50 users) |
| Chrome Extension ID stable | extension prod | Requiert publication | Chrome Web Store publication pour ID fixe |

---

## Assumptions Log

| # | Claim | Section | Risk si faux |
|---|-------|---------|-------------|
| A1 | Cloudflare Access protège Firebase Hosting externe via DNS proxy | Q7 | Si Firebase Hosting bypasse Cloudflare (IP direct), la protection ne fonctionne pas. Fix : vérifier que le DNS est bien en mode Proxied + utiliser le custom domain Firebase plutôt que web.app |
| A2 | `your-github-org` est l'org GitHub cible | Q3, Q4 | Si l'org n'existe pas encore, il faut la créer avant de configurer le PAT et les webhooks |
| A3 | `gpt-4.1-nano` est disponible sur le compte OpenAI utilisé | Q1 | Si le modèle n'existe pas, remplacer par `gpt-4o-mini` pour le reranker |
| A4 | `chrome.identity.launchWebAuthFlow` avec `response_type=id_token` retourne un ID token compatible avec `verify_google_id_token` de auth.py | Q6 | L'extension pourrait recevoir un access token si la config OAuth flow est mal construite. Test d'intégration requis avant de merger. |
| A5 | Le SEMAPHORE_LIMIT par défaut de Graphiti (10) est trop élevé pour Claude Haiku Tier 1 (50 req/min Anthropic) | Q1 | Si le tier Anthropic est plus élevé, la limitation n'est pas nécessaire. Mettre `SEMAPHORE_LIMIT=3` par défaut et ajuster à l'usage. |

---

## Open Questions

1. **Org GitHub `your-github-org` existe-t-elle ?**
   - Ce qui est connu : le CONTEXT.md mentionne `your-github-org` comme org cible
   - Ce qui est flou : si l'org n'existe pas encore, toute la chaîne GitHub membership check est bloquante
   - Recommandation : vérifier à github.com/your-github-org avant de démarrer l'implémentation

2. **Custom domain Firebase pour `projects.dejavu.cat` vs CNAME direct vers la VM ?**
   - Ce qui est connu : Firebase Hosting supporte les custom domains, Cloudflare DNS peut pointer vers l'IP VM
   - Ce qui est flou : si le dashboard est petit/statique, le servir depuis nginx sur la VM (avec Cloudflare Access devant) est plus simple que Firebase Hosting custom domain
   - Recommandation : héberger sur Firebase Hosting (cohérent avec D5), mais prévoir le fallback nginx si Firebase custom domain setup bloque

3. **OPENAI_API_KEY dans graphiti-service — partager la clé de agent-runtime ?**
   - Ce qui est connu : `OPENAI_API_KEY` existe dans agent-runtime et openwebui-pipeline
   - Ce qui est flou : partager la même clé crée un quota commun — les embeddings graphiti + les appels agent-runtime comptent ensemble
   - Recommandation : utiliser la même clé en Phase 5 (coût marginal text-embedding-3-small minimal), séparer si les coûts le justifient en v2

---

## Sources

### PRIMARY (HIGH confidence)
- `github.com/getzep/graphiti/blob/main/server/graph_service/routers/ingest.py` — endpoints ingest, group_id usage [VERIFIED via WebFetch]
- `github.com/getzep/graphiti/blob/main/server/graph_service/routers/retrieve.py` — endpoints search [VERIFIED via WebFetch]
- `github.com/getzep/graphiti/blob/main/server/graph_service/zep_graphiti.py` — initialize_graphiti avec Neo4j [VERIFIED via WebFetch]
- `help.getzep.com/graphiti/configuration/llm-configuration` — Anthropic config + OpenAI embedding requis [VERIFIED via WebFetch]
- `help.getzep.com/graphiti/core-concepts/graph-namespacing` — group_id multi-tenancy [VERIFIED via WebFetch]
- `help.getzep.com/graphiti/getting-started/quick-start` — minimal code add_episode + search [VERIFIED via WebFetch]
- `librechat.ai/docs/configuration/authentication/OAuth2-OIDC/github` — variables env GitHub OAuth [VERIFIED via WebFetch]
- `docs.cloud.google.com/run/docs/deploying-source-code` — APIs GCP + IAM roles pour --source [VERIFIED via WebFetch]
- `cloud.google.com/blog/products/devops-sre/deploy-to-cloud-run-with-github-actions` — workflow YAML Workload Identity [VERIFIED via WebFetch]
- `github.com/FirebaseExtended/action-hosting-deploy/blob/main/docs/service-account.md` — 4 roles IAM Firebase [VERIFIED via WebFetch]
- `developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-public-app/` — setup Access subdomain [VERIFIED via WebFetch]
- `developers.cloudflare.com/cloudflare-one/integrations/identity-providers/google/` — Google OAuth Cloudflare [VERIFIED via WebFetch]
- `cloudflare.com/plans/zero-trust-services/` — plan gratuit 50 users [VERIFIED via WebSearch]
- `developer.chrome.com/docs/extensions/reference/api/identity` — getAuthToken retourne access token [VERIFIED via WebFetch]
- `developers.google.com/identity/gsi/web/guides/verify-google-id-token` — vérification ID token côté serveur [VERIFIED via WebFetch]
- `docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api` — rate limits GitHub API [VERIFIED via WebFetch]

### SECONDARY (MEDIUM confidence)
- `medium.com/@saeedhajebi/a-production-ready-api-for-graphitis-powerful-but-flawed-memory-15f17a9c1b41` — event loop pitfalls lifespan fix [CITED: community article, corroboré par source code officiel]

---

## Metadata

**Research date :** 2026-05-06
**Valid until :** 2026-06-06 (graphiti-core : stable mais actif, LibreChat : actif)
**Confidence breakdown :**
- Graphiti FastAPI wrapper : HIGH — code source officiel lu directement
- GitHub OAuth LibreChat : HIGH — docs officielles
- GitHub membership API : HIGH — docs officielles + pattern codebase existant
- Cloud Run GitHub Actions : HIGH — docs GCP + blog officiel Google
- Firebase Hosting : HIGH — docs officielles Firebase
- Chrome Extension MV3 : MEDIUM — docs Chrome officielles + gap sur flow ID token vs access token (A4 est assumed)
- Cloudflare Access : MEDIUM — docs lues mais free tier pricing confirmé via search seulement
