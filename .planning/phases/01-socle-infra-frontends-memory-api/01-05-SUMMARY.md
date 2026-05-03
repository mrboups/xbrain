---
phase: 01-socle-infra-frontends-memory-api
plan: 05
subsystem: infra
tags: [librechat, openwebui, mongodb-replset, meilisearch, nginx, deploy, docker-compose]

requires:
  - phase: 01-02
    provides: memory-api running in docker-compose
  - phase: 01-03
    provides: librechat-bridge service awaiting librechat-mongo
  - phase: 01-04
    provides: openwebui-pipeline (target of Open WebUI's OPENAI_API_BASE_URL)
provides:
  - librechat.yaml — config 3 LLMs (Anthropic, OpenAI, Grok) + social login Google
  - 4 services ajoutés dans docker-compose : librechat-mongo (replset rs0), librechat-meili, librechat, openwebui
  - init-mongo-replset.sh — script init replica set au premier boot Mongo (nécessaire change streams)
  - nginx 10-xbrain.conf — routing complet : / → LibreChat, /openwebui/ → Open WebUI, /api/ → memory-api, SSE buffering off
  - deploy-vm.sh — script idempotent rsync + pull + build + up -d + sleep 90 + status
  - PARTIAL : tasks 5-8 (env-check, OAuth checkpoint, deploy execution, visual verify) → user actions
affects: [01-06-PLAN (deploy doit avoir tourné avant que les backups aient quelque chose à sauver)]

tech-stack:
  added: [LibreChat v0.8.5, Open WebUI v0.9.0, MongoDB 7 (replica set rs0), MeiliSearch v1.10, nginx 1.27-alpine]
  patterns: [single nginx ingress sub-pathed, MongoDB replset single-node pour change streams, SSE buffering off pour streaming LLM]

key-files:
  created:
    - infrastructure/librechat/librechat.yaml (3 endpoints custom + Google social login)
    - infrastructure/scripts/init-mongo-replset.sh (rs0 init au premier boot)
    - infrastructure/nginx/conf.d/10-xbrain.conf (routing 3 surfaces + WebSocket/SSE)
    - infrastructure/scripts/deploy-vm.sh (rsync + compose pull/build/up sur la VM)
  modified:
    - infrastructure/docker-compose.yml (ajout 4 services LibreChat stack + Open WebUI)
    - infrastructure/nginx/conf.d/00-health.conf (suppression catch-all 503, garde uniquement /nginx-health)

key-decisions:
  - "MongoDB en mode replset rs0 single-node — obligatoire pour les change streams (consommés par librechat-bridge). Initialisé via /docker-entrypoint-initdb.d/ au premier boot."
  - "nginx sub-pathed pour Open WebUI sous /openwebui/ — évite besoin de domaine + sous-domaines Phase 1. Peut casser certains assets relatifs Open WebUI — workaround Phase 1 acceptable, refactor possible Phase 2 avec subdomains."
  - "proxy_buffering off + WebSocket upgrade dans nginx — nécessaire pour le streaming SSE des LLM (sinon le user voit la réponse complète d'un coup au lieu de tokens streamed)."
  - "deploy-vm.sh fait rsync (pas git pull) — permet de tester du code non commit. Pour la prod un git pull serait plus reproducible. Phase 1 = privilégier l'itération rapide."
  - "client_max_body_size 50m dans nginx — accommode les uploads PDF (limite LibreChat 20MB par fichier × 5 fichiers = 100MB théorique mais 50MB suffit pour Phase 1)."
  - "WEBUI_URL=http://__VM_HOST__/openwebui hardcoded — déplacer dans .env quand on aura un domaine."

patterns-established:
  - "Pattern 'one nginx config per concern' : 00-health.conf isolé du routing applicatif → permet à nginx de passer healthy même si le routing 10-xbrain.conf est broken."
  - "Pattern MongoDB replset single-node : necessary evil pour change streams sur dev. Mode replset minimal (1 node) suffit, pas de réplication réelle."

requirements-completed:
  - CHAT-01
  - CHAT-02
  - CHAT-04
  - CHAT-05
  - CHAT-08

duration: ~10 min (tasks 1-4 inline)
completed: 2026-05-03 (partiel — voir Pending Tasks)
status: PARTIAL — tasks 5-8 nécessitent actions user
---

# Plan 01-05 — frontends + nginx + deploy (PARTIEL)

**Code complet pour le déploiement. Le déploiement réel attend 2 actions user : (a) configurer Google OAuth client avec les 2 redirect URIs, (b) lancer `bash infrastructure/scripts/deploy-vm.sh` une fois `.env` rempli.**

## Performance

- Files created: 4
- Files modified: 2
- Tasks completed: 4/8 (1-4 done, 5-8 awaiting user)

## Pending Tasks (user actions)

### Task 5 — Pre-flight check (user can run anytime)
```bash
cp .env.example .env
# Éditer .env : remplacer tous les __FILL__ par vraies valeurs
# Pour les __FILL_RANDOM_*__, utiliser : openssl rand -base64 32 (ou 48, ou hex 32/16)
make env-check    # vérifie qu'aucune var critique manque
ssh -i ~/.ssh/xbrain_key user@__VM_HOST__ 'docker --version'  # vérifie SSH + Docker VM
```

### Task 6 — User action : configurer Google OAuth client (CHECKPOINT BLOCKING)
1. https://console.cloud.google.com → projet `xbrain-495115` → APIs & Services → Credentials
2. Create Credentials → OAuth client ID → Web application
3. Authorized redirect URIs (les **deux**, copier-coller exact) :
   - `http://__VM_HOST__/oauth/google/callback`
   - `http://__VM_HOST__/openwebui/oauth/google/callback`
4. Copier Client ID et Client Secret dans `.env` (pour les vars `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `OAUTH_GOOGLE_CLIENT_ID`, `OAUTH_GOOGLE_CLIENT_SECRET` — c'est la **même valeur** sous deux noms différents)

### Task 7 — Lancer le déploiement
```bash
bash infrastructure/scripts/deploy-vm.sh
```

Surveillance pendant le boot :
```bash
make vm-ps              # voir l'état healthy de chaque container
make vm-logs            # tail logs si quelque chose ne démarre pas
```

Pitfalls connus :
- **MongoDB rs.initiate** : si volume `librechat_mongo` existait déjà sans rs0, init script ne re-tourne pas. Init manuel : `ssh ... 'docker compose -f infrastructure/docker-compose.yml exec librechat-mongo mongosh --eval "rs.initiate({_id:\"rs0\",members:[{_id:0,host:\"librechat-mongo:27017\"}]})"'`
- **OOM e2-medium 4 GB** : surveiller `docker stats`. Total mem_limit ≈ 2.5 GB + Postgres+Qdrant data + OS. Si Postgres restart en boucle → upgrade VM e2-standard-2.
- **librechat-bridge unhealthy** : dépend de Mongo en rs0. Vérifier `rs.status().ok == 1` côté Mongo.

### Task 8 — Visual verify (CHECKPOINT)
Manual smoke test à `http://__VM_HOST__` :
1. LibreChat à la racine, sign in Google → choisir Anthropic/Claude → envoyer "ping" → réponse OK
2. `/openwebui/` Sign in Google (même compte) → dropdown model → claude-3-5-sonnet → "ping" → réponse OK
3. `curl http://__VM_HOST__/api/v1/healthz` → `{"status":"ok"}`

## Verification (déjà faite)

- ✅ `infrastructure/docker-compose.yml` contient les 10 services (nginx, postgres, qdrant, memory-api, librechat-bridge, openwebui-pipeline, librechat-mongo, librechat-meili, librechat, openwebui)
- ✅ librechat.yaml a les 3 endpoints (Anthropic, OpenAI, xAI)
- ✅ nginx 10-xbrain.conf a 3 upstream + proxy_buffering off + connection_upgrade
- ✅ scripts exécutables (chmod +x)
- ⏭ docker compose config sur la VM — à faire pendant Task 7
