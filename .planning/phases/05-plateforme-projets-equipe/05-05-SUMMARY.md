---
phase: 5
plan: "05-05"
subsystem: projects-dashboard
tags: [firebase, dashboard, github-api, static-site, github-actions, nginx]
dependency_graph:
  requires: [05-03]
  provides:
    - projects.dejavu.cat dashboard statique
    - script generate_dashboard.py (GitHub API + memory-api)
    - GitHub Actions deploy workflow dashboard
    - firebase.json + .firebaserc (Firebase Hosting)
    - nginx 30-projects.conf fallback vhost
  affects: []
tech_stack:
  added:
    - Firebase Hosting (xbrain-495115 project)
    - FirebaseExtended/action-hosting-deploy@v0 (GitHub Actions)
    - requests (HTTP calls dans generate_dashboard.py)
    - pyyaml (dépendance CI)
  patterns:
    - HTML+CSS vanilla auto-suffisant (pas de framework)
    - html.escape() sur tous les champs API (T-05-05-04 mitigation XSS)
    - Error-tolerant API fetch (exit 0 toujours, données partielles si API down)
    - Nginx fallback redirect vers Firebase (DNS safety net)
key_files:
  created:
    - projects-dashboard/scripts/generate_dashboard.py
    - projects-dashboard/public/index.html
    - projects-dashboard/public/.gitkeep
    - projects-dashboard/firebase.json
    - projects-dashboard/.firebaserc
    - projects-dashboard/.github/workflows/deploy-dashboard.yml
    - infrastructure/nginx/conf.d/30-projects.conf
  modified: []
decisions:
  - html.escape() appliqué sur TOUS les champs issus de GitHub API et memory-api (T-05-05-04)
  - Script exit 0 toujours : données partielles préférables à une erreur CI bloquante
  - public/.gitkeep versionné pour que le répertoire existe avant la génération en CI
  - Nginx 30-projects.conf = redirect 301 vers Firebase (safety net DNS, pas un vrai server)
metrics:
  duration: "~25 minutes"
  completed: "2026-05-06"
  tasks_completed: 3
  tasks_total: 3
---

# Phase 5 Plan 05: projects.dejavu.cat Dashboard Summary

Dashboard statique Firebase Hosting généré par GitHub Actions depuis GitHub API + memory-api, avec filtres JS, grille de cards par projet, et nginx fallback redirect vers Firebase.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | generate_dashboard.py + HTML dashboard | 2fd399b | `projects-dashboard/scripts/generate_dashboard.py`, `projects-dashboard/public/index.html` |
| 2 | Firebase Hosting config + GitHub Actions deploy | af2fde5 | `firebase.json`, `.firebaserc`, `public/.gitkeep`, `.github/workflows/deploy-dashboard.yml` |
| 3 | nginx vhost fallback 30-projects.conf | 6db8950 | `infrastructure/nginx/conf.d/30-projects.conf` |

## What Was Built

### generate_dashboard.py (564 lignes)

Script Python auto-suffisant qui :

1. **Interroge GitHub API** — `GET /orgs/{GITHUB_ORG}/repos?per_page=100` pour la liste des repos, puis `GET /repos/{ORG}/{repo}/collaborators` pour chaque repo (avec PAT `GITHUB_API_PAT`).
2. **Interroge memory-api** — `GET /v1/admin/projects?team_scope=acme` avec JWT Bridge pour récupérer les projets enregistrés dans xbrain.
3. **Jointure par slug** — repos GitHub + projets xbrain mappés par `slug`/`name`. Les repos sans brain.yaml apparaissent avec statut "non-indexé".
4. **Génère `public/index.html`** — page HTML+CSS vanilla auto-suffisante :
   - En-tête "xbrain Projects" + date de génération + compteur projets/indexés
   - Filtres JS : deux `<input>` (filter by member, filter by project) avec `addEventListener('input', filterCards)`
   - Grille `auto-fill minmax(300px, 1fr)` — cards avec : nom (lien URL), badge deploy target (Firebase bleu/#1a73e8 / Cloud Run vert/#34a853 / VM gris/#757575), statut live/non-indexé, avatars GitHub (img src `https://github.com/{login}.png?size=24`), project_scope/team_scope
   - Footer "Généré le {date} — xbrain v5"
   - CSS inline `<style>`, font-family system-ui, border-radius 10px

**Sécurité XSS (T-05-05-04)** : `html.escape()` appliqué sur TOUS les champs issus des APIs (nom, description, slug, login, scope, URL) avant insertion dans le HTML.

**Error handling** : si GitHub API retourne 401/403/rate-limit ou si memory-api est inaccessible, le script génère la page avec les données disponibles + bannière "Données partielles". Exit 0 toujours.

**Fallback urllib** : si `requests` n'est pas installé, bascule vers `urllib.request` standard library.

### Firebase Hosting config

- `firebase.json` : `public: "public"`, headers `Cache-Control: no-cache` + `X-Frame-Options: DENY` sur toutes les routes.
- `.firebaserc` : projet `xbrain-495115` (GCP project xbrain).
- `public/.gitkeep` : répertoire `public/` versionné pour que CI le trouve avant la première génération.

### GitHub Actions deploy-dashboard.yml

Workflow déclenché sur :
- `push` sur `main`
- `workflow_dispatch` (déclenchement manuel)
- `schedule: cron: '0 */6 * * *'` (toutes les 6h pour données fraîches)

Steps : `checkout@v4` → Python 3.12 → `pip install requests pyyaml` → `generate_dashboard.py` → `FirebaseExtended/action-hosting-deploy@v0` avec `entryPoint: ./projects-dashboard`.

Secrets requis : `GITHUB_API_PAT`, `XBRAIN_BRIDGE_JWT`, `FIREBASE_SERVICE_ACCOUNT_XBRAIN`.

### nginx 30-projects.conf

Vhost fallback sur la VM pour `projects.dejavu.cat` :
- `return 301 https://xbrain-495115.web.app$request_uri` — redirect permanent vers Firebase Hosting.
- En opération normale, le DNS Cloudflare CNAME pointe directement vers Firebase (nginx ne voit pas le trafic). Ce fichier est un safety net si le DNS change temporairement.
- `resolver 127.0.0.11 valid=30s` + `real_ip_header CF-Connecting-IP`.

## Verification Results

| Check | Result |
|-------|--------|
| `generate_dashboard.py` avec GITHUB_API_PAT=fake XBRAIN_BRIDGE_JWT=fake | PASS — génère index.html (données partielles) |
| HTML parseable par `html.parser.HTMLParser` | PASS |
| `firebase.json` JSON valide | PASS |
| `deploy-dashboard.yml` YAML valide | PASS |
| `FirebaseExtended/action-hosting-deploy@v0` présent dans workflow | PASS |
| Tous les fichiers créés présents sur disque | PASS |

## Deviations from Plan

None — plan exécuté exactement tel qu'écrit. Les fichiers `generate_dashboard.py` et `public/index.html` existaient déjà dans le répertoire de travail (non commités) ; ils ont été intégrés dans le commit Task 1 avec vérification complète.

## Known Stubs

None. Le dashboard génère des données réelles depuis GitHub API + memory-api. Avec des fausses credentials, il affiche la bannière "données partielles" (comportement attendu, pas un stub).

## Threat Flags

None. Les surfaces de sécurité (Firebase Hosting + GitHub Actions Secrets) sont dans le threat model du plan. La mitigation T-05-05-04 (XSS via `html.escape()`) est implémentée.

## Self-Check: PASSED

- [x] `projects-dashboard/scripts/generate_dashboard.py` existe
- [x] `projects-dashboard/public/index.html` existe
- [x] `projects-dashboard/public/.gitkeep` existe
- [x] `projects-dashboard/firebase.json` existe
- [x] `projects-dashboard/.firebaserc` existe
- [x] `projects-dashboard/.github/workflows/deploy-dashboard.yml` existe
- [x] `infrastructure/nginx/conf.d/30-projects.conf` existe
- [x] Commit 2fd399b existe (Task 1)
- [x] Commit af2fde5 existe (Task 2)
- [x] Commit 6db8950 existe (Task 3)
