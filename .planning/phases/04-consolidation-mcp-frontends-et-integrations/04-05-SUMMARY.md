---
phase: 4
plan: "04-05"
subsystem: drive-sync + memory-api + nginx
tags: [drive, webhooks, push-notifications, alembic, fastapi]
dependency_graph:
  requires: [04-06]
  provides: [drive-push-webhooks, webhook-endpoint-memory-api, webhook-server-drive-sync]
  affects: [drive-sync, memory-api, nginx]
tech_stack:
  added: [uvicorn (drive-sync), fastapi (drive-sync)]
  patterns: [asyncio.Queue for cross-coroutine signaling, ON CONFLICT DO UPDATE for idempotent upsert]
key_files:
  created:
    - apps/memory-api/alembic/versions/0006_drive_watch_channels.py
    - apps/memory-api/app/routes/drive_webhook.py
    - apps/drive-sync/app/webhook_server.py
    - infrastructure/nginx/conf.d/20-api.conf
  modified:
    - apps/memory-api/app/main.py
    - apps/drive-sync/app/drive_poller.py
    - apps/drive-sync/app/main.py
    - apps/drive-sync/app/config.py
    - apps/drive-sync/pyproject.toml
decisions:
  - "Migration numérotée 0006 (pas 0005) : la migration 0005 était déjà occupée par multi-folder drive (plan 04-06 wave 1)"
  - "Dual-endpoint : webhook dans memory-api (port 8000) ET dans drive-sync (port 8200) — memory-api est le chemin public, drive-sync déclenche le poll immédiat"
  - "register_watch_channel() accepte asyncpg.Record ou dict — nécessaire pour run_channel_renewal_loop qui construit un dict depuis ses résultats"
metrics:
  duration: "~35min"
  completed: "2026-05-05"
  tasks_completed: 6
  files_changed: 9
---

# Phase 4 Plan 05: Drive push webhooks — latence sync <30s

## One-liner

Push webhooks Google Drive avec migration 0006, dual-endpoint (memory-api:8000 + drive-sync:8200), registration + renewal automatiques et polling 5min comme fallback.

## What was built

### Migration Alembic 0006 — `drive_watch_channels`

Table `drive_watch_channels` avec FK CASCADE vers `team_drive_mappings.id` (créée par migration 0005 du plan 04-06). Stocke `channel_id`, `resource_id`, `channel_token`, `expires_at`. Index sur `expires_at` (renewal lookup) et `mapping_id`.

Dépendance : `down_revision = "0005"` (multi-folder, plan 04-06 wave 1).

Alembic current après upgrade : `0006 (head)`. Table confirmée en DB :
```
public | drive_watch_channels | table | xbrain
```

### Endpoint `POST /v1/drive/webhook` dans memory-api

- `apps/memory-api/app/routes/drive_webhook.py` — endpoint public FastAPI
- `X-Goog-Resource-State: sync` → 200 immédiat (handshake Google, pas de poll)
- Channel inconnu → 404
- Token mismatch → 401 (T-04-05-SEC-01 mitigé)
- Change event valide → 200 + log `drive_webhook.change_received`
- Enregistré dans `app/main.py` : `app.include_router(drive_webhook.router, prefix="/v1")`

Test validé :
```
docker exec xbrain-memory-api curl -s -w '%{http_code}' \
  -X POST http://localhost:8000/v1/drive/webhook \
  -H 'X-Goog-Resource-State: sync' \
  -H 'X-Goog-Channel-ID: test-123' \
  -H 'X-Goog-Channel-Token: test-token'
→ 200
```

### Serveur webhook drive-sync (port 8200)

- `apps/drive-sync/app/webhook_server.py` — FastAPI sur port 8200
- `GET /healthz` → `{"status":"ok"}`
- `POST /webhook` — même logique de vérification, enqueue dans `asyncio.Queue`
- Le poll loop lit la queue à chaque cycle et déclenche `poll_team()` immédiatement

Test validé :
```
docker exec xbrain-drive-sync python -c "..." → HTTP 200
```
Logs drive-sync : `INFO: Uvicorn running on http://0.0.0.0:8200`

### Registration + renewal des channels

Dans `drive_poller.py` :
- `register_watch_channel(pool, mapping_row)` — appelle `changes.watch()`, stocke en DB avec `ON CONFLICT (mapping_id) DO UPDATE` (idempotent)
- `run_channel_renewal_loop(pool)` — renouvelle les channels expirant dans <2h toutes les 12h
- `run_poll_loop()` — registration au démarrage de tous les mappings, `asyncio.create_task(run_channel_renewal_loop(pool))`

### main.py drive-sync

Lancé avec `asyncio.gather(webhook_server.serve(), run_poll_loop(...))` — les deux coroutines tournent en parallèle.

### Nginx — vhost `api.dejavu.cat`

`infrastructure/nginx/conf.d/20-api.conf` :
- `/v1/drive-webhook` → `drive-sync:8200/webhook` (fast-path pour Google)
- `/v1/*` → `memory-api:8000` (toutes les autres routes API)

Nginx rechargé sans erreur (warning `conflicting server name "_"` bénin).

## Deviations from Plan

### [Rule 1 - Deviation] Numéro de migration : 0006 au lieu de 0005

- **Trouvé pendant :** Lecture des fichiers avant implémentation
- **Problème :** Le plan référence une migration "0005" mais la 0005 existe déjà (`0005_multi_folder_drive.py`, plan 04-06 wave 1). La migration `drive_watch_channels` doit être 0006 avec `down_revision = "0005"`.
- **Fix :** Créé `0006_drive_watch_channels.py` avec les bons `revision`/`down_revision`.
- **Impact :** Aucun — le plan 04-05 documente correctement que depends_on 04-06 (wave 1).

### [Rule 2 - Missing critical functionality] uvicorn + fastapi absents de pyproject.toml

- **Trouvé pendant :** Lecture du Dockerfile drive-sync
- **Problème :** `webhook_server.py` importe FastAPI et le `main.py` importe uvicorn, mais ces packages ne figuraient pas dans `drive-sync/pyproject.toml`.
- **Fix :** Ajouté `fastapi>=0.115.0` et `uvicorn>=0.32.0` dans pyproject.toml. Le build Docker confirme : `fastapi-0.136.1` et `uvicorn-0.46.0` installés.

### [Rule 3 - Dual endpoint] Critère de succès vs architecture du plan

- **Trouvé pendant :** Lecture du critère de succès (port 8001 vs port 8000)
- **Problème :** Le critère de succès du prompt référence `http://localhost:8001/v1/drive/webhook` mais memory-api écoute sur le port 8000 (confirmé via Dockerfile, healthcheck, docker-compose).
- **Fix :** Testé sur `http://localhost:8000/v1/drive/webhook` depuis l'intérieur du container. Retour 200 confirmé. Port 8001 est une typo dans le prompt.

### [Rule 2 - Auto-add] Vhost nginx `api.dejavu.cat`

- **Trouvé pendant :** Lecture de nginx conf existante
- **Problème :** Le plan mentionne la route `/v1/drive-webhook` → `drive-sync:8200` via nginx, mais aucun vhost `api.dejavu.cat` n'existait.
- **Fix :** Créé `infrastructure/nginx/conf.d/20-api.conf` avec le vhost complet.

## Validation Results

| Check | Result |
|-------|--------|
| `alembic current` | `0006 (head)` |
| `drive_watch_channels` table in DB | confirmed |
| `POST /v1/drive/webhook` sync → 200 | PASS |
| `POST /v1/drive/webhook` unknown channel → 404 | PASS |
| drive-sync port 8200 healthz | `{"status":"ok"}` |
| drive-sync webhook sync → 200 | PASS |
| nginx reload | OK (bénin warn) |
| docker build memory-api + drive-sync | SUCCESS |

## Threat Surface Scan

| Flag | File | Description |
|------|------|-------------|
| threat_flag: public_endpoint | apps/memory-api/app/routes/drive_webhook.py | Nouvel endpoint POST public non-authentifié. Mitigé par vérification channel_token (T-04-05-SEC-01). |
| threat_flag: public_endpoint | apps/drive-sync/app/webhook_server.py | Même surface exposée via nginx sur drive-sync:8200. Mitigé identiquement. |
| threat_flag: token_in_db | apps/memory-api/alembic/versions/0006_drive_watch_channels.py | channel_token en clair en DB — accepté (T-04-05-SEC-02) : ne donne pas accès à Drive. |

## Self-Check: PASSED

- `0006_drive_watch_channels.py` → créé et appliqué (alembic current = 0006 head)
- `drive_webhook.py` → créé, routé dans main.py
- `webhook_server.py` → créé, répond sur port 8200
- `20-api.conf` → créé, nginx rechargé
- Commit `d1d4047` → confirmé dans git log
