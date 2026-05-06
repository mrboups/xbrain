---
phase: 5
plan: "05-04"
subsystem: chrome-extension
tags: [chrome, extension, manifest-v3, google-oauth, web-clipper, cors]
dependency_graph:
  requires: [05-02]
  provides: [extension Chrome xbrain web clipper, auth Google OAuth launchWebAuthFlow, CORS chrome-extension dans memory-api]
  affects: [apps/memory-api/app/main.py]
tech_stack:
  added: [Chrome Extension Manifest V3, chrome.identity API, launchWebAuthFlow, CORSMiddleware FastAPI]
  patterns: [ID token via launchWebAuthFlow response_type=id_token, chrome.storage.session cache TTL 3600s, CORS allow_origins chrome-extension://*]
key_files:
  created:
    - chrome-extension/manifest.json
    - chrome-extension/background.js
    - chrome-extension/popup.html
    - chrome-extension/popup.js
    - chrome-extension/content.js
    - chrome-extension/icon48.png
    - chrome-extension/icon128.png
  modified:
    - apps/memory-api/app/main.py
decisions:
  - "launchWebAuthFlow avec response_type=id_token (Solution A) — retourne un ID token JWT compatible avec verify_google_id_token sans modifier auth.py"
  - "chrome-extension://* wildcard pour CORS au lieu de l'ID fixe — acceptable car auth Bearer token est le vrai contrôle (T-05-04-03 accepted)"
  - "chrome.storage.session pour cache token (pas localStorage) — isolé par extension, TTL 3600s"
metrics:
  duration: "~25 min"
  completed: "2026-05-06T02:29:39Z"
  tasks_completed: 3
  tasks_total: 3
  files_created: 7
  files_modified: 1
---

# Phase 5 Plan 04: Extension Chrome web clipper — Summary

**One-liner:** Extension Chrome MV3 web clipper avec auth Google OAuth via `launchWebAuthFlow` et cache ID token, popup sélecteur truth_level/team_scope, content script GET_SELECTION, et CORS `chrome-extension://*` dans memory-api FastAPI.

## What Was Built

### Task 1 — Structure extension Manifest V3 + auth Google OAuth (`975ccb4`)

**`chrome-extension/manifest.json`** — Manifest V3 complet :
- `permissions: ["identity", "activeTab", "storage"]`
- `host_permissions: ["https://api.dejavu.cat/*"]`
- `oauth2` block avec `client_id: "__GOOGLE_CLIENT_ID__"` (placeholder à remplacer)
- Content script injecté sur `<all_urls>`
- Service worker background

**`chrome-extension/background.js`** — Service worker qui :
- Expose `GET_ID_TOKEN` via `chrome.runtime.onMessage`
- Appelle `getGoogleIdToken()` : `launchWebAuthFlow` avec `response_type=id_token` + nonce aléatoire, extrait le token du fragment `#id_token=...`
- Cache le token dans `chrome.storage.session` (TTL 3600s)
- Expose `SEND_TO_BRAIN` : POST à `https://api.dejavu.cat/v1/memory/upsert` avec les 7 champs du contrat de tagging
- Utilise `fetch` uniquement (MV3 interdit XMLHttpRequest dans les service workers)

**`chrome-extension/icon48.png` + `icon128.png`** — PNG placeholder 48×48 et 128×128, fond bleu xbrain (`#3b82f6`), générés avec le module `struct`+`zlib` Python standard.

### Task 2 — Popup UI + content script (`4b037b3`)

**`chrome-extension/popup.html`** — Interface 320px avec :
- Sélecteur `team_scope` (option `acme` par défaut, extensible)
- Input `project_scope` (optionnel)
- Radio group `truth_level` (EPHEMERAL défaut, WORKING, VALIDATED, CANONICAL) — style CSS `:checked` avec fond bleu
- Textarea `content` (6 lignes, pré-rempli via content script)
- Hint source : hostname de la page courante
- Bouton "Envoyer au brain" + div statut (success/error/loading)

**`chrome-extension/popup.js`** — Logique popup :
1. `DOMContentLoaded` → `getActiveTab()` + `getSelectionFromPage()` via `GET_SELECTION`
2. Click "Envoyer au brain" : `GET_ID_TOKEN` → background, construction payload tagging, `SEND_TO_BRAIN` → background
3. Gestion erreur 401 : vider `chrome.storage.session` + message "réessayer" (re-auth transparente)
4. Contenu réinitialisé après envoi réussi

**`chrome-extension/content.js`** — 3 lignes effectives :
- Écoute `GET_SELECTION`, retourne `{selectedText, url, title}`
- Aucune lecture automatique du DOM (T-05-04-05 accepted)

### Task 3 — CORS middleware memory-api (`ca56b19`)

**`apps/memory-api/app/main.py`** — Ajout du `CORSMiddleware` :
- Import `from fastapi.middleware.cors import CORSMiddleware`
- `allow_origins=["chrome-extension://*"]` — wildcard ID-agnostique
- `allow_methods` : GET/POST/PUT/PATCH/DELETE/OPTIONS
- `allow_headers` : Authorization, X-Team-Scope, Content-Type, Accept
- Positionné après `app = FastAPI(...)`, avant les `include_router`

## Deviations from Plan

None — plan exécuté exactement tel qu'écrit.

Note technique : L'icône PNG a été générée avec les modules Python standard (`struct`, `zlib`) car `pillow` n'est pas disponible dans l'environnement d'exécution. Le résultat est fonctionnel (PNG valide 48×48 et 128×128, fond bleu uni).

## Known Stubs

| Stub | File | Line | Reason |
|------|------|------|--------|
| `"client_id": "__GOOGLE_CLIENT_ID__"` | `chrome-extension/manifest.json` | 21 | Placeholder — remplacer par le Google OAuth Client ID lors de l'installation. Même credentials que LibreChat Google OAuth. |
| `const CLIENT_ID = "__GOOGLE_CLIENT_ID__"` | `chrome-extension/background.js` | 13 | Idem — double déclaration pour robustesse. |

Ces stubs sont intentionnels : l'ID Google OAuth est lié à l'environnement de déploiement et ne doit pas être commité en clair. Instructions d'installation dans le README de l'extension.

## Threat Surface Scan

Aucun nouveau endpoint réseau ou chemin d'auth non présent dans le `<threat_model>` du plan.

Les 5 menaces du threat register ont été traitées conformément à leurs dispositions :
- T-05-04-01 (Spoofing) : `verify_google_id_token` dans `auth.py` — mitigé (inchangé, déjà en place)
- T-05-04-02 (Info Disclosure) : `chrome.storage.session` isolé par extension + TTL 3600s — accepted
- T-05-04-03 (EoP) : `chrome-extension://*` CORS permissif — accepted (auth Bearer token est le vrai contrôle)
- T-05-04-04 (Repudiation) : `source: "chrome:<hostname>"` + `validation_status: "pending"` dans le payload — mitigé
- T-05-04-05 (Tampering) : content.js minimal, écoute seulement — accepted

## Self-Check: PASSED

Files created:
- `chrome-extension/manifest.json` — FOUND
- `chrome-extension/background.js` — FOUND
- `chrome-extension/popup.html` — FOUND
- `chrome-extension/popup.js` — FOUND
- `chrome-extension/content.js` — FOUND
- `chrome-extension/icon48.png` — FOUND
- `chrome-extension/icon128.png` — FOUND
- `apps/memory-api/app/main.py` — FOUND (modifié)

Commits:
- `975ccb4` — Task 1 (manifest + background + icons)
- `4b037b3` — Task 2 (popup + content script)
- `ca56b19` — Task 3 (CORS middleware)
