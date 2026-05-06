# brain.yaml — Schéma de déploiement GitOps xbrain

`brain.yaml` est le fichier de configuration GitOps xbrain. Il doit être placé **à la racine** du repo GitHub du projet. Il définit le comportement de déploiement et d'indexing dans le brain xbrain.

## Exemple annoté complet

```yaml
# brain.yaml — fichier de déploiement GitOps xbrain
# Doit être à la racine du repo GitHub

name: deck-fundraising          # nom lisible du projet (string, requis)
type: static                    # static | service | mcp_tool (requis)
slug: fundraising               # identifiant URL-safe unique dans la team (requis)
team_scope: acme           # team xbrain propriétaire (requis)
project_scope: fundraising      # sous-projet dans la team (optionnel, défaut = slug)

deploy:
  target: firebase              # firebase | cloudrun (requis)
  region: europe-west1          # région GCP (optionnel, défaut europe-west1)

brain:
  enrich: true                  # indexer le contenu dans xbrain au deploy (bool, défaut false)
  trigger: on_deploy            # on_deploy | daily | on_output (défaut on_deploy)
  content: README.md            # fichier(s) à indexer (string ou list, défaut README.md)
  truth_level: EPHEMERAL        # truth_level initial des items indexés (défaut EPHEMERAL)
```

## Référence des champs

### Champs racine

| Champ | Type | Obligatoire | Description |
|-------|------|-------------|-------------|
| `name` | string | **requis** | Nom lisible du projet, affiché dans le dashboard |
| `type` | enum | **requis** | `static` \| `service` \| `mcp_tool` — détermine le mode de déploiement et d'enregistrement |
| `slug` | string | **requis** | Identifiant URL-safe, unique par `team_scope`. Lowercase, alphanum + tirets uniquement |
| `team_scope` | string | **requis** | Team xbrain propriétaire du projet. Doit correspondre au scope du `XBRAIN_BRIDGE_JWT` |
| `project_scope` | string | optionnel | Sous-projet dans la team. Défaut : valeur de `slug` |

### Bloc `deploy`

| Champ | Type | Obligatoire | Description |
|-------|------|-------------|-------------|
| `deploy.target` | enum | **requis** | `firebase` pour sites statiques (Reveal.js, SPAs) ; `cloudrun` pour services avec process |
| `deploy.region` | string | optionnel | Région GCP. Défaut : `europe-west1`. Exemple : `europe-west9` |

### Bloc `brain`

| Champ | Type | Obligatoire | Description |
|-------|------|-------------|-------------|
| `brain.enrich` | bool | optionnel | Si `true`, indexe les fichiers dans xbrain après chaque deploy. Défaut : `false` |
| `brain.trigger` | enum | optionnel | `on_deploy` \| `daily` \| `on_output`. Défaut : `on_deploy` |
| `brain.content` | string \| list | optionnel | Fichier(s) à indexer. Peut être une string (`README.md`) ou une liste (`[README.md, docs/spec.md]`). Défaut : `README.md` |
| `brain.truth_level` | enum | optionnel | Niveau de vérité initial des items indexés : `EPHEMERAL` \| `WORKING` \| `VALIDATED` \| `CANONICAL`. Défaut : `EPHEMERAL` |

## Règles de validation

### Règle 1 — `slug`
- Lowercase uniquement
- Caractères autorisés : `[a-z0-9-]` (alphanum + tirets)
- Longueur : 2–64 caractères
- **Unique par `team_scope`** — deux projets de la même team ne peuvent pas avoir le même slug

Valide : `fundraising`, `my-project-2024`, `api-v2`
Invalide : `My Project`, `api_v2`, `a` (trop court)

### Règle 2 — `type: mcp_tool`
Quand `type: mcp_tool`, le script `brain-index.sh` effectue automatiquement un appel supplémentaire à `POST /v1/tools/register` sur l'API xbrain pour enregistrer le service comme outil MCP dans le gateway (voir D6 du CONTEXT.md Phase 5).

Prérequis : le service doit être déployé sur Cloud Run (`deploy.target: cloudrun`) et exposer un endpoint MCP compatible.

### Règle 3 — `deploy.target` selon `type`
| `type` | `deploy.target` recommandé |
|--------|---------------------------|
| `static` | `firebase` |
| `service` | `cloudrun` |
| `mcp_tool` | `cloudrun` (requis — doit servir des requêtes HTTP) |

### Règle 4 — `brain.enrich: false` par défaut
L'indexing brain est **opt-in**. Si `brain.enrich: false` (ou si le bloc `brain` est absent), le script `brain-index.sh` s'exécute mais ne fait aucun appel API.

### Règle 5 — Champs requis minimaux
Si `brain.yaml` est présent mais qu'il manque un champ requis (`name`, `type`, `slug`, `team_scope`, `deploy.target`), le workflow GitHub Actions **échoue en erreur** (exit 1) — le deploy ne se fait pas.

## Exemples supplémentaires

### Projet Cloud Run (service backend)

```yaml
name: xbrain Memory API
type: service
slug: memory-api
team_scope: acme
project_scope: xbrain-core

deploy:
  target: cloudrun
  region: europe-west1

brain:
  enrich: false
```

### MCP tool auto-enregistré

```yaml
name: Scraper MCP
type: mcp_tool
slug: scraper-tool
team_scope: acme

deploy:
  target: cloudrun
  region: europe-west1

brain:
  enrich: true
  content: README.md
  truth_level: WORKING
```

Quand `type: mcp_tool`, brain-index.sh POST vers `/v1/tools/register` pour enregistrer le service dans le gateway MCP. L'URL du service (issue de Cloud Run) est détectée automatiquement via `gcloud run services describe`.

### Indexing multi-fichiers

```yaml
name: Deck Produit
type: static
slug: deck-produit
team_scope: acme
project_scope: marketing

deploy:
  target: firebase

brain:
  enrich: true
  content:
    - README.md
    - docs/spec.md
    - ROADMAP.md
  truth_level: WORKING
```

## Contrat de tagging xbrain

Chaque item indexé par `brain-index.sh` reçoit automatiquement les 7 champs du contrat de tagging xbrain :

| Champ | Valeur |
|-------|--------|
| `team_scope` | valeur de `brain.yaml:team_scope` |
| `project_scope` | valeur de `brain.yaml:project_scope` (ou slug si absent) |
| `visibility` | `team` |
| `confidence` | `1.0` |
| `truth_level` | valeur de `brain.yaml:brain.truth_level` (défaut : `EPHEMERAL`) |
| `source` | `github:<owner>/<repo>` |
| `validation_status` | `pending` |

## Secrets GitHub requis

Pour que le workflow GitHub Actions fonctionne, configurer dans les **GitHub Secrets** du repo :

| Secret | Description |
|--------|-------------|
| `XBRAIN_BRIDGE_JWT` | JWT de service xbrain signé avec `BRIDGE_SHARED_SECRET`. Générer via `register-mcp-tools.sh` ou manuellement |
| `GCP_PROJECT_NUMBER` | Numéro du projet GCP (ex: `495115`) — requis uniquement pour Cloud Run (Workload Identity Federation) |
| `FIREBASE_SERVICE_ACCOUNT_XBRAIN` | Clé JSON du service account Firebase — requis uniquement pour Firebase Hosting |

Voir `docs/gitops-setup.md` pour les instructions de configuration GCP.
