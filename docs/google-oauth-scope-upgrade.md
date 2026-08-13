# Google OAuth Scope Upgrade — Runbook

> **SECTION 7 NE FONCTIONNE PLUS — vérifié 2026-08-13.** Les sections 1 à 6
> (pourquoi les scopes, ce qu'il faut cocher dans la console Google, les variables
> `.env`, le flow d'incremental auth) restent justes et restent le document à lire.
> **Les commandes de vérification de la section 7, elles, échouent** — elles ont été
> écrites contre une forme du code qui n'a jamais existé sous ce nom :
>
> - `7.1` importe `app.drive_client.test_drive_connection` dans `xbrain-drive-sync`.
>   Il n'y a pas de module `drive_client` (les fichiers sont `drive_poller.py`,
>   `ingestion_client.py`, `webhook_server.py`) et pas de fonction
>   `test_drive_connection` dans le repo. → `ModuleNotFoundError`.
> - `7.2` importe `app.auth.make_bridge_jwt` dans `xbrain-memory-api`. Cette fonction
>   n'existe nulle part. → `ImportError`, donc `$TEST_JWT` vide et le `curl` qui suit
>   part sans token.
> - Le flow write-back de la section 6 parle d'un sidecar **`mcp-drive-write`**. Il
>   n'existe pas : lecture et écriture Drive sont toutes deux dans `mcp-drive-read`.
>
> Les commandes `docker logs` de 7.3 et la vérification console de 7.4 fonctionnent.
> Vérifier plutôt via `docker compose ps` (drive-sync healthy) et les logs.
>
> Rappel : `drive-sync` et `mcp-calendar` sont dans le profil **`integrations`**,
> donc absents d'une install OSS-light. Contenu en français, antérieur à la règle
> « docs produit en anglais ».

**Version:** Phase 3  
**Audience:** Admin (human) — action requise avant le déploiement de `drive-sync` et `mcp-calendar`  
**Estimated time:** 10 minutes  
**Prerequisite:** Accès admin au projet GCP `xbrain-495115`

---

## Section 1 — Contexte

### Pourquoi cette mise à jour est nécessaire

Les services `drive-sync` et `mcp-calendar` déployés en Phase 3 requièrent des scopes Google OAuth que
le client OAuth existant n'expose pas encore :

| Service | Scope requis |
|---------|-------------|
| `drive-sync` (sync incrémental 5min) | `drive.readonly` |
| `mcp-drive-read` (lecture live via MCP) | `drive.readonly` |
| `mcp-calendar` (calendrier agent) | `calendar.readonly` |
| Drive write-back (opt-in explicite seulement) | `drive.file` |

Sans cette mise à jour, les tokens OAuth existants seront rejetés par l'API Google avec
`403 insufficientPermissions` dès que `drive-sync` ou `mcp-calendar` tentera d'appeler l'API.

### Impact sur les utilisateurs existants

**Aucune disruption pour les utilisateurs qui ne se connectent pas à Drive ou Calendar.**

xbrain utilise un flow d'**incremental auth** (`include_granted_scopes=true`). Cela signifie :

- Les utilisateurs déjà authentifiés (email + profil Google) conservent leurs tokens existants.
- Aucun re-consentement n'est demandé automatiquement à la connexion.
- Le nouveau consentement Drive/Calendar n'est demandé que lors d'une action explicite :
  - Admin mappe un dossier Drive → consentement `drive.readonly`
  - Utilisateur active l'outil Calendar → consentement `calendar.readonly`
  - Utilisateur déclenche un write-back Drive → consentement `drive.file` (étape séparée)

---

## Section 2 — Scopes à ajouter dans Google Cloud Console

### Les 3 scopes Phase 3

| Scope URI | Classification Google | Usage dans xbrain | Ajouter au consentement initial ? |
|-----------|----------------------|-------------------|----------------------------------|
| `https://www.googleapis.com/auth/drive.readonly` | **Sensitive** | Sync Drive 5min (`drive-sync`) + lecture live (`mcp-drive-read`) | Oui — lors du setup de sync |
| `https://www.googleapis.com/auth/calendar.readonly` | **Sensitive** | Calendrier agent (`mcp-calendar`) | Oui — lors du setup Calendar |
| `https://www.googleapis.com/auth/drive.file` | **Sensitive** | Write-back vers Drive (INT-03, opt-in uniquement) | **Non** — demandé séparément à l'action write-back |

### Règle de classification Google (Sensitive scopes)

Les scopes `drive.readonly`, `calendar.readonly` et `drive.file` sont classés **Sensitive** par Google.
Pour un déploiement **interne uniquement** (membres de l'équipe, pas d'application publiée) :

- **Aucune vérification OAuth requise.** Google n'exige la vérification que pour les apps publiées
  (audience "External" avec plus de 100 utilisateurs ou accès à des données sensibles d'utilisateurs
  en dehors de l'organisation).
- En mode **Testing** (publishing status = "Testing"), seuls les comptes listés dans "Test users"
  peuvent consentir.
- En mode **In production** avec audience interne ou usage restreint (< 100 users, accès limité à
  l'équipe), les scopes Sensitive sont accordés sans vérification supplémentaire.

**Recommandation :** si le projet GCP est en "Testing", soit ajouter tous les membres de l'équipe
comme Test users, soit passer à "In production" (pas de vérification OAuth requise pour usage interne).

---

## Section 3 — Étapes dans Google Cloud Console

### 3.1 — Ajouter les scopes au client OAuth

1. Ouvrir [Google Cloud Console](https://console.cloud.google.com/) et sélectionner le projet
   **`xbrain-495115`** (compte `team@example.com`).

2. Menu de gauche → **"APIs & Services"** → **"OAuth consent screen"**.

3. Cliquer **"EDIT APP"**.

4. Faire défiler jusqu'à la section **"Scopes"** → cliquer **"+ ADD OR REMOVE SCOPES"**.

5. Dans la barre de recherche, ajouter les scopes suivants **un par un** :
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/calendar.readonly`

   > **Ne pas ajouter `drive.file` à cette étape.** Il sera demandé séparément lors des actions
   > write-back (Section 6).

6. Cliquer **"UPDATE"** puis **"SAVE AND CONTINUE"** jusqu'à la fin du wizard.

7. Vérifier que les 2 scopes apparaissent dans la liste **"Sensitive scopes"** sur l'écran de
   récapitulatif.

### 3.2 — Vérifier les Authorized redirect URIs

Tant que l'on est dans la console, vérifier que les URIs de callback sont bien enregistrées :

1. Menu de gauche → **"APIs & Services"** → **"Credentials"**.

2. Cliquer sur le client OAuth `xbrain` (ou le nom configuré).

3. Section **"Authorized redirect URIs"** — vérifier que les URIs suivantes sont présentes :
   ```
   http://__VM_HOST__/oauth/google/callback
   http://__VM_HOST__/openwebui/oauth/google/callback
   ```
   Si des URIs avec domaine HTTPS ont été ajoutées (ex. `https://<XBRAIN_BASE_DOMAIN>/...`), les conserver.
   *(L'exemple citait `dejavu.cat`, domaine abandonné à la migration du 2026-05-07.)*

4. Cliquer **"SAVE"**.

### 3.3 — Activer les APIs Google Drive et Calendar

Si pas déjà fait :

1. Menu → **"APIs & Services"** → **"Library"**.
2. Chercher **"Google Drive API"** → cliquer → **"ENABLE"**.
3. Chercher **"Google Calendar API"** → cliquer → **"ENABLE"**.

---

## Section 4 — Variables d'environnement à ajouter dans `.env`

Les credentials OAuth existants (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`) restent inchangés.
Ajouter les variables suivantes à `.env` sur la VM **avant** le déploiement Phase 3 :

```bash
# Google OAuth — scopes Phase 3
# Ces valeurs sont des constantes Google — ne pas modifier.
GOOGLE_DRIVE_READONLY_SCOPE=https://www.googleapis.com/auth/drive.readonly
GOOGLE_CALENDAR_READONLY_SCOPE=https://www.googleapis.com/auth/calendar.readonly
GOOGLE_DRIVE_FILE_SCOPE=https://www.googleapis.com/auth/drive.file

# Encryption key for OAuth credentials stored in team_drive_mappings.oauth_credentials_enc
# Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# IMPORTANT: store in a secrets manager or .env ONLY — ne jamais committer cette valeur en git.
OAUTH_CREDENTIALS_ENCRYPTION_KEY=__FILL_FERNET_KEY__
```

### Commande de génération de la clé Fernet

```bash
# Sur la VM ou en local (Python 3.8+) :
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copier la sortie (format `base64url`, ~44 caractères) dans `.env` en remplacement de
`__FILL_FERNET_KEY__`.

> **Sécurité :** La clé `OAUTH_CREDENTIALS_ENCRYPTION_KEY` chiffre les `access_token` et
> `refresh_token` Google des utilisateurs stockés dans `team_drive_mappings.oauth_credentials_enc`.
> Une rotation de clé nécessite une migration de toutes les credentials chiffrées — planifier avec soin.

---

## Section 5 — Flow d'incremental auth (implémenté par drive-sync)

Ce flow est automatiquement déclenché par `memory-api` lors du setup Drive. Décrit ici pour
référence admin et pour diagnostiquer d'éventuels problèmes.

### Déclencheur

L'admin mappe un dossier Drive via :

```bash
POST /v1/admin/drive-mapping
{
  "team_scope": "team-acme",
  "drive_folder_id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs",
  "folder_name": "Docs Acme"
}
```

### Étapes du flow

1. `memory-api` vérifie si l'admin a un token `drive.readonly` valide pour `team-acme`.

2. Si non → construit une `authorization_url` Google avec les paramètres :
   ```
   scope          = <existing_granted_scopes> + drive.readonly
   include_granted_scopes = true     # scopes déjà accordés restent valides
   access_type    = offline          # refresh_token inclus dans la réponse
   prompt         = consent          # force l'affichage même si partiellement accordé
   state          = <anti-CSRF token signé HMAC, 32 bytes aléatoires>
   redirect_uri   = GOOGLE_CALLBACK_URL
   ```

3. `memory-api` retourne l'`authorization_url` à l'appelant :
   ```json
   { "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth?..." }
   ```

4. L'admin visite l'URL → consent screen Google → autorise.

5. Google redirige vers `GOOGLE_CALLBACK_URL` avec `?code=...&state=...`.

6. `memory-api` :
   - Valide le `state` param (anti-CSRF)
   - Échange le `code` contre `access_token` + `refresh_token`
   - Chiffre les credentials avec `OAUTH_CREDENTIALS_ENCRYPTION_KEY` (Fernet AES-128-CBC)
   - Stocke dans `team_drive_mappings.oauth_credentials_enc`

7. `drive-sync` démarre son polling incrémental via `changes.list` avec le `refresh_token`.

### Rotation du refresh_token

Google révoque le `refresh_token` si :
- L'utilisateur révoque l'accès dans `myaccount.google.com/permissions`
- Le client OAuth est mis à jour et le scope change
- Le compte Google est désactivé

En cas de révocation : relancer le flow depuis l'étape 1 (re-mapper le dossier Drive).

---

## Section 6 — Séparation drive.readonly vs drive.file

### Principe

| Scope | Quand demandé | Ce que voit l'utilisateur |
|-------|--------------|--------------------------|
| `drive.readonly` | Setup sync Drive (Section 5) | "Consulter et télécharger tous vos fichiers Google Drive" |
| `drive.file` | Action write-back explicite uniquement | "Afficher, modifier, créer et supprimer uniquement les fichiers Google Drive que vous utilisez avec cette application" |

### Règle d'or : ne jamais bundler drive.file avec le consentement initial

**Raison :** `drive.readonly` seul couvre 95% des cas d'usage (sync + live read).
Demander `drive.file` dès l'onboarding :
- Augmente la friction (consent fatigue)
- Réduit le taux d'adoption
- Donne l'impression d'une app "trop gourmande en permissions"

`drive.file` est demandé **uniquement** quand l'utilisateur dit explicitement "Écrire ce résumé
dans Drive" — à ce moment, un second flow incremental auth est déclenché, ajoutant `drive.file`
aux scopes existants.

### Flow write-back (référence)

```
User: "Écris ce résumé dans Drive"
  → agent-runtime appelle mcp-gateway /tools/drive-write/call
  → mcp-drive-write vérifie si drive.file accordé pour ce user
  → Si non : retourne {"requires_auth": true, "authorization_url": "...drive.file scope..."}
  → LibreChat affiche le lien → user autorise → write-back s'exécute
```

---

## Section 7 — Vérification post-setup

### 7.1 — Vérifier que drive-sync peut se connecter à Drive

Après avoir effectué le flow OAuth (Section 5) et déployé Phase 3 :

```bash
# Sur la VM :
docker exec xbrain-drive-sync python -c "
from app.drive_client import test_drive_connection
import asyncio
asyncio.run(test_drive_connection())
"
```

Sortie attendue :
```
Drive connection OK — team=team-acme, folder=Docs Acme, files_found=42
```

### 7.2 — Vérifier que mcp-calendar peut lister les events

```bash
# Récupérer un JWT de test (remplacer par un vrai token) :
TEST_JWT=$(docker exec xbrain-memory-api python -c "
from app.auth import make_bridge_jwt
print(make_bridge_jwt('test-user', 'default'))
")

# Appeler l'outil calendar via mcp-gateway :
curl -s \
  -H "Authorization: Bearer $TEST_JWT" \
  -H "Content-Type: application/json" \
  -X POST http://localhost:8080/tools/calendar/call \
  -d '{"date_range": "today"}' | python -m json.tool
```

Sortie attendue :
```json
{
  "status": "ok",
  "events": [...]
}
```

### 7.3 — Vérifier les logs OAuth (debug)

```bash
# Logs memory-api pour voir les échanges OAuth :
docker logs xbrain-memory-api --since 5m | grep -i oauth

# Logs drive-sync pour voir le polling :
docker logs xbrain-drive-sync --since 5m | grep -E "(poll|changes|error)"
```

### 7.4 — Vérifier les scopes dans la console Google

Pour confirmer que les scopes ont bien été accordés à un utilisateur :

1. Aller sur [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
2. Trouver l'application `xbrain`
3. Vérifier que `Google Drive` et `Google Calendar` apparaissent dans la liste des accès

---

## Références

- [Google OAuth 2.0 — Incremental Authorization](https://developers.google.com/identity/protocols/oauth2/web-server#incrementalAuth)
- [Google Drive API Scopes](https://developers.google.com/drive/api/guides/api-specific-auth)
- [Google Calendar API Scopes](https://developers.google.com/calendar/api/auth)
- [Google OAuth Verification — When required](https://support.google.com/cloud/answer/9110914)
- [Fernet key generation — cryptography library](https://cryptography.io/en/latest/fernet/)
- Phase 3 CONTEXT : `.planning/phases/03-graphe-extraction-integrations/03-CONTEXT.md`
- REQUIREMENTS : `.planning/REQUIREMENTS.md` (INT-01, INT-03, INT-04, MCP-06)
