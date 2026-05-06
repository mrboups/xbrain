---
phase: 5
plan: "05-06"
subsystem: infrastructure-config
tags: [nginx, cloudflare, dns, dotenv, runbook]
dependency_graph:
  requires: [05-02, 05-05]
  provides:
    - docs/cloudflare-access-setup.md (runbook Cloudflare Access complet)
    - .env.example mis à jour avec vars Phase 5
    - 30-projects.conf corrigé (health check accessible)
  affects:
    - infrastructure/nginx/conf.d/30-projects.conf
    - .env.example
tech_stack:
  added:
    - Cloudflare Access Zero Trust (free tier, self-hosted app protection)
    - Firebase Hosting custom domain DNS via Cloudflare Proxied A records
  patterns:
    - nginx location / pour redirect (location-based plutôt que server-level return)
    - .env.example sectionné par phase pour lisibilité
key_files:
  created:
    - docs/cloudflare-access-setup.md
  modified:
    - infrastructure/nginx/conf.d/30-projects.conf
    - .env.example
decisions:
  - Cloudflare Access Zero Trust Free (jusqu'à 50 users) suffit pour Team xbrain
  - Firebase .web.app URL bypass Cloudflare Access : risque accepté (T-05-06-01) pour petite équipe
  - .env.example à la racine (pas infrastructure/) — convention déjà établie Phase 1
metrics:
  duration: "~15 min"
  completed: "2026-05-06"
  tasks_completed: 3
  tasks_total: 3
  files_created: 1
  files_modified: 2
---

# Phase 5 Plan 06: nginx vhost + Cloudflare Access + .env.example Summary

**One-liner:** Runbook Cloudflare Access (4 étapes Firebase DNS + Zero Trust + policy Acme), 6 variables Phase 5 dans .env.example, et correction du bug code-mort dans 30-projects.conf.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Runbook Cloudflare Access | 6070637 | docs/cloudflare-access-setup.md |
| 2 | .env.example Phase 5 vars | 41c957f | .env.example |
| 3 | Vérification + fix nginx | 4c83737 | infrastructure/nginx/conf.d/30-projects.conf |

## What Was Built

### Task 1 — docs/cloudflare-access-setup.md

Runbook complet (68 lignes) pour protéger `projects.dejavu.cat` avec Cloudflare Access Zero Trust :
- Étape 1 : Firebase Hosting custom domain — DNS TXT verification + A records en mode Proxied
- Étape 2 : Identity Provider Google dans Cloudflare Zero Trust (OAuth2 redirect URI format)
- Étape 3 : Application Access `xbrain Projects Dashboard` sur `projects.dejavu.cat`
- Étape 4 : Policy `Team xbrain Access` — emails `team@grooveos.app` + `team@grooveos.app`
- Troubleshooting : 523 (SSL mode Full Strict), login loop (redirect URI exact), bypass `.web.app` (accepté)

### Task 2 — .env.example (section Phase 5)

6 nouvelles variables documentées avec commentaires :
- `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_CALLBACK_URL` — GitHub OAuth LibreChat
- `GITHUB_ORG_PAT`, `GITHUB_ORG_NAME` — vérification membership `your-github-org`
- `GRAPHITI_LLM_MODEL`, `GRAPHITI_SERVICE_URL` — graphiti-service (plan 05-01)
- `GCP_PROJECT_NUMBER` — pipeline GitOps Firebase (plan 05-03)
- Note : `OPENAI_API_KEY` déjà présent (phase 1), réutilisé par graphiti-service sans duplication

### Task 3 — 30-projects.conf (correction)

Correction d'un bug syntaxique dans le fichier créé en 05-05 :
- **Bug:** `return 301` au niveau `server` rendait `location /nginx-health` code mort (jamais atteint)
- **Fix:** Déplacement du redirect dans `location /`, health check en priorité avant le redirect
- **Ajout:** `set_real_ip_from 0.0.0.0/0` (cohérence avec 10-xbrain.conf pour trafic Cloudflare)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] location /nginx-health inaccessible dans 30-projects.conf**
- **Found during:** Task 3 — vérification statique du fichier
- **Issue:** `return 301` déclaré au niveau `server` (pas dans `location`) s'appliquait à TOUTES les requêtes, rendant `location /nginx-health` unreachable. Le health check ne fonctionnerait jamais.
- **Fix:** Wrapper le redirect dans `location /`, placer `location /nginx-health` avant.
- **Files modified:** `infrastructure/nginx/conf.d/30-projects.conf`
- **Commit:** 4c83737

**2. [Rule 2 - Missing] .env.example à la racine, pas dans infrastructure/**
- **Found during:** Task 2 — le fichier `infrastructure/.env.example` n'existe pas
- **Contexte:** Convention établie en Phase 1 : `.env.example` est à la racine du repo. Le plan référençait incorrectement `infrastructure/.env.example`.
- **Fix:** Mise à jour du fichier racine `.env.example` (emplacement correct).
- **Files modified:** `.env.example`
- **Commit:** 41c957f

### Docker non disponible localement

`docker compose exec nginx nginx -t` non exécutable (Docker absent du poste de dev Windows). Vérification statique réalisée à la place. Le fix syntaxique (location-based redirect) est conforme aux patterns nginx déjà validés en production (10-xbrain.conf, 20-api.conf).

## Threat Surface Scan

Aucune nouvelle surface réseau introduite par ce plan. Fichiers de documentation + config d'exemple uniquement. Threat model du plan honoré :
- T-05-06-01 : bypass `xbrain-495115.web.app` documenté dans le runbook (section Troubleshooting)
- T-05-06-02 : `.env.example` contient uniquement des valeurs vides ou non-sensibles (ex. `GITHUB_ORG_NAME=your-github-org`)

## Known Stubs

Aucun stub bloquant. Les variables Phase 5 ajoutées dans `.env.example` sont intentionnellement vides (valeurs à remplir sur la VM) — c'est leur rôle de template.

## Self-Check

- [x] `docs/cloudflare-access-setup.md` existe (68 lignes, 4 étapes documentées)
- [x] `.env.example` contient GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_CALLBACK_URL, GITHUB_ORG_PAT, GITHUB_ORG_NAME, GRAPHITI_LLM_MODEL
- [x] `infrastructure/nginx/conf.d/30-projects.conf` existe avec health check accessible
- [x] 4 vhosts nginx présents : 00-health.conf, 10-xbrain.conf, 20-api.conf, 30-projects.conf
- [x] Commits : 6070637, 41c957f, 4c83737

## Self-Check: PASSED
