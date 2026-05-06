# GitOps Setup — Runbook GCP one-shot

Ce runbook est à exécuter **une seule fois** pour configurer le projet GCP `xbrain-495115` pour les déploiements GitOps (Cloud Run + Firebase Hosting). Il suppose que :
- `gcloud` CLI est installé et authentifié avec le compte `team@grooveos.app`
- `firebase-tools` est installé (`npm install -g firebase-tools`)
- Le projet GCP `xbrain-495115` existe

## Étape 1 — Activer les APIs GCP

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  --project=xbrain-495115
```

APIs activées :
- `run.googleapis.com` — Cloud Run Admin API
- `cloudbuild.googleapis.com` — Cloud Build (utilisé par `gcloud run deploy --source`)
- `artifactregistry.googleapis.com` — Artifact Registry (stockage images Docker buildées)
- `iamcredentials.googleapis.com` — IAM Credentials API (requis pour Workload Identity Federation)

## Étape 2 — Service account Cloud Run deploy

Créer un service account dédié aux déploiements GitHub Actions → Cloud Run.

```bash
export PROJECT="xbrain-495115"
export SA="github-deploy@${PROJECT}.iam.gserviceaccount.com"
export PROJECT_NUMBER=$(gcloud projects describe $PROJECT --format='value(projectNumber)')

# Créer le service account
gcloud iam service-accounts create github-deploy \
  --display-name="GitHub Actions Deploy (Cloud Run)" \
  --project=$PROJECT

# Attribuer les 4 rôles IAM requis
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" \
  --role="roles/run.sourceDeveloper"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" \
  --role="roles/artifactregistry.repoAdmin"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${SA}" \
  --role="roles/iam.serviceAccountUser"

# Le SA Cloud Build par défaut doit pouvoir déployer sur Cloud Run
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/run.builder"
```

## Étape 3 — Workload Identity Federation (sans JSON key file)

Workload Identity Federation permet aux GitHub Actions de s'authentifier avec GCP sans stocker de clé JSON. Plus sécurisé que `credentials_json`.

```bash
export PROJECT="xbrain-495115"
export SA="github-deploy@${PROJECT}.iam.gserviceaccount.com"

# Créer le Workload Identity Pool
gcloud iam workload-identity-pools create github-pool \
  --location=global \
  --display-name="GitHub Actions Pool" \
  --project=$PROJECT

# Créer le provider OIDC GitHub
gcloud iam workload-identity-pools providers create-oidc github-provider \
  --location=global \
  --workload-identity-pool=github-pool \
  --display-name="GitHub Provider" \
  --issuer-uri=https://token.actions.githubusercontent.com \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --project=$PROJECT

# Récupérer l'ID complet du pool
export POOL_ID=$(gcloud iam workload-identity-pools describe github-pool \
  --location=global \
  --project=$PROJECT \
  --format='value(name)')

echo "POOL_ID: $POOL_ID"
# Exemple: projects/495115/locations/global/workloadIdentityPools/github-pool

# Autoriser les repos de l'org your-github-org à impersonner le SA
# IMPORTANT: remplacer your-github-org par l'org GitHub exacte
gcloud iam service-accounts add-iam-policy-binding $SA \
  --project=$PROJECT \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository_owner/your-github-org"
```

**Note sur l'audience :** La commande ci-dessus autorise **tous les repos** de l'org `your-github-org`. Pour restreindre à un repo spécifique, remplacer la dernière ligne par :
```bash
--member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/your-github-org/REPO_NAME"
```

**Secret GitHub à configurer (Variable — pas Secret) :**
Dans chaque repo GitHub, ajouter une **variable** (pas un secret) `GCP_PROJECT_NUMBER` avec la valeur du numéro de projet GCP :
```
GCP_PROJECT_NUMBER = 495115
```

Le `workload_identity_provider` dans le workflow sera :
```
projects/495115/locations/global/workloadIdentityPools/github-pool/providers/github-provider
```

## Étape 4 — Service account Firebase Hosting deploy

Créer un service account séparé pour les déploiements Firebase Hosting.

```bash
export PROJECT="xbrain-495115"
export FSA="firebase-deploy@${PROJECT}.iam.gserviceaccount.com"

# Créer le service account Firebase
gcloud iam service-accounts create firebase-deploy \
  --display-name="GitHub Actions Deploy (Firebase Hosting)" \
  --project=$PROJECT

# Attribuer les 4 rôles IAM requis
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${FSA}" \
  --role="roles/firebasehosting.admin"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${FSA}" \
  --role="roles/firebase.developAdmin"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${FSA}" \
  --role="roles/serviceusage.apiKeysViewer"

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:${FSA}" \
  --role="roles/run.viewer"

# Générer la clé JSON du service account (nécessaire pour firebase-deploy)
gcloud iam service-accounts keys create firebase-deploy-key.json \
  --iam-account=$FSA \
  --project=$PROJECT

echo "Clé générée : firebase-deploy-key.json"
echo "Copier le contenu de ce fichier dans le GitHub Secret FIREBASE_SERVICE_ACCOUNT_XBRAIN"
```

**IMPORTANT :** Après avoir copié la clé JSON dans GitHub Secrets, **supprimer le fichier local** :
```bash
rm firebase-deploy-key.json
```

## Étape 5 — Initialisation Firebase Hosting

```bash
# S'assurer d'être authentifié
firebase login

# Initialiser Firebase Hosting pour le projet xbrain-495115
firebase init hosting --project xbrain-495115
# Répondre aux questions :
#   - What do you want to use as your public directory? → public
#   - Configure as a single-page app? → No (sauf si SPA)
#   - Set up automatic builds and deploys with GitHub? → No (géré manuellement)
```

Cela crée `firebase.json` et `.firebaserc` à la racine du repo. Committer ces fichiers.

**Pour le dashboard `projects.dejavu.cat` :** Configurer un custom domain dans la console Firebase Hosting. Firebase fournira un enregistrement TXT de vérification et un CNAME à ajouter dans Cloudflare DNS.

## Étape 6 — Secrets GitHub à configurer

Pour **chaque repo** utilisant le pipeline GitOps xbrain, configurer dans `Settings > Secrets and variables` :

### Secrets (valeurs sensibles)

| Secret | Où l'obtenir | Requis pour |
|--------|-------------|-------------|
| `XBRAIN_BRIDGE_JWT` | Générer via `register-mcp-tools.sh` ou manuellement avec `BRIDGE_SHARED_SECRET` | Cloud Run + Firebase (brain indexing) |
| `FIREBASE_SERVICE_ACCOUNT_XBRAIN` | Contenu de `firebase-deploy-key.json` (étape 4) | Firebase Hosting uniquement |

### Variables (valeurs non-sensibles)

| Variable | Valeur | Requis pour |
|----------|--------|-------------|
| `GCP_PROJECT_NUMBER` | `495115` | Cloud Run uniquement (Workload Identity) |
| `CLOUD_RUN_SERVICE_NAME` | Nom du service Cloud Run (ex: `scraper-api`) | Cloud Run uniquement |

### Génération du XBRAIN_BRIDGE_JWT

Le Bridge JWT est un JWT HS256 signé avec `BRIDGE_SHARED_SECRET` (variable d'env de la VM xbrain). Pour générer :

```bash
# Sur la VM xbrain, ou dans le container mcp-gateway
python3 -c "
import time
from authlib.jose import jwt

secret = 'VOTRE_BRIDGE_SHARED_SECRET'
now = int(time.time())
payload = {
    'iss': 'github-actions',
    'sub': 'deploy-bot',
    'scope': 'bridge',
    'team_scope': 'acme',  # adapter à la team
    'iat': now,
    'exp': now + 365 * 24 * 3600,  # 1 an
}
token = jwt.encode({'alg': 'HS256'}, payload, secret)
print(token.decode('ascii') if isinstance(token, bytes) else token)
"
```

**Note sécurité :** Rotation possible à tout moment via GitHub Secrets UI. L'ancien JWT est immédiatement invalidé.

## Vérification post-setup

```bash
# Vérifier les APIs activées
gcloud services list --enabled --project=xbrain-495115 | grep -E "run|cloudbuild|artifact|iam"

# Vérifier le Workload Identity Pool
gcloud iam workload-identity-pools describe github-pool \
  --location=global --project=xbrain-495115

# Vérifier les service accounts
gcloud iam service-accounts list --project=xbrain-495115 | grep -E "github-deploy|firebase-deploy"

# Test deploy manuel Cloud Run (depuis un repo avec Dockerfile)
gcloud run deploy test-service \
  --source . \
  --region europe-west1 \
  --project xbrain-495115 \
  --allow-unauthenticated
```

## Références

- [Cloud Run deploy from GitHub Actions](https://cloud.google.com/blog/products/devops-sre/deploy-to-cloud-run-with-github-actions)
- [Workload Identity Federation setup](https://cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)
- [Firebase Hosting GitHub Actions](https://firebase.google.com/docs/hosting/github-integration)
- [google-github-actions/auth@v3](https://github.com/google-github-actions/auth)
- [FirebaseExtended/action-hosting-deploy](https://github.com/FirebaseExtended/action-hosting-deploy)
