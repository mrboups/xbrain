---
phase: 4
plan: "04-06"
subsystem: drive-sync
tags: [drive, admin, alembic, multi-folder, project-scope]
dependency_graph:
  requires: []
  provides: [multi-folder-drive-mapping, project-scope-tagging]
  affects: [admin_drive, drive_poller, team_drive_mappings]
tech_stack:
  added: []
  patterns: [composite-unique-index, upsert-returning-id, oauth-state-uuid]
key_files:
  created:
    - apps/memory-api/alembic/versions/0005_multi_folder_drive.py
  modified:
    - apps/memory-api/app/routes/admin_drive.py
    - apps/drive-sync/app/drive_poller.py
decisions:
  - "OAuth state param changed from team_scope string to mapping_id UUID — supports N folders per team (T-04-06-SEC-02 accepted)"
  - "drive-sync deployed via docker cp + restart instead of full rebuild — disque VM 99% plein"
metrics:
  duration: "~25 minutes"
  completed: "2026-05-05"
  tasks_completed: 3
  files_changed: 3
requirements:
  - INT-03
---

# Phase 4 Plan 06: Multi-folder Drive mapping par équipe — Summary

## One-liner

Migration Alembic 0005 + refactor admin_drive.py + drive_poller.py pour permettre N dossiers Drive par team avec project_scope distinct par dossier.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| T-04-06-01 | Migration 0005 — drop UNIQUE(team_scope), add UNIQUE(team_scope, folder_id) + project_scope col | 934f8f7 |
| T-04-06-02 | Refactor admin_drive.py — POST/GET/DELETE multi-folder, OAuth state=mapping_id | 8a8ce3e |
| T-04-06-03 | drive_poller.py — SELECT id+project_scope, UPDATE WHERE id, pass project_scope à ingestion | 16f22a1 |

## Validation Results

- `alembic current` retourne `0005 (head)` — migration appliquée proprement
- `POST /v1/admin/drive-mapping` `{team_scope:"acme", folder_id:"folder1_test", project_scope:"marketing"}` → 201, retourne `id` UUID + `authorization_url` avec `state=<mapping_id>`
- Second `POST` `{team_scope:"acme", folder_id:"folder2_test", project_scope:"sales"}` → 201, id différent (pas de conflit)
- `GET /v1/admin/drive-mapping?team_scope=acme` → tableau de 2 objets
- `DELETE /v1/admin/drive-mapping/{id}` → 204, row supprimée
- Upsert même (team_scope, folder_id) → même id retourné, project_scope mis à jour

## Deviations from Plan

### Auto-handled Issues

**1. [Rule 3 - Blocker] drive-sync rebuild échoue — disque VM plein (99%)**
- **Found during:** Deploy T-04-06-03
- **Issue:** `docker compose build drive-sync` échoue avec `no space left on device` lors de l'export de l'image. Le disque de la VM est à 99% (28G/29G utilisés).
- **Fix:** Déploiement via `docker cp` du fichier modifié dans le container en cours + `docker compose restart drive-sync`. Le code actuel du container a bien été mis à jour (vérifié via `docker exec grep project_scope`). L'image tagged `xbrain/drive-sync:phase3` n'a pas été regénérée sur ce déploiement.
- **Impact:** Fonctionnel immédiatement. Au prochain rebuild complet (après nettoyage disque), l'image sera correctement regénérée depuis les sources.
- **Files modified:** N/A (contournement de déploiement)

**2. [Rule 2 - Missing functionality] alembic not in PATH dans container memory-api**
- **Found during:** T-04-06-01 migration apply
- **Fix:** `alembic` est installé dans `/usr/local/lib/python3.12/site-packages/bin/alembic` (non dans `/usr/local/bin`). Utilisation du chemin complet. Ce pattern est déjà connu des plans précédents.

## Known Stubs

None — tous les endpoints sont fonctionnels avec données réelles PostgreSQL.

## Threat Flags

None — aucune nouvelle surface réseau introduite. Les endpoints admin restent derrière authentification bridge/admin. T-04-06-SEC-01 et T-04-06-SEC-02 évalués et acceptés dans le plan.

## Self-Check: PASSED

- 0005_multi_folder_drive.py : FOUND (alembic/versions/)
- admin_drive.py : FOUND (app/routes/)
- drive_poller.py : FOUND (apps/drive-sync/app/)
- Commits 934f8f7, 8a8ce3e, 16f22a1 : FOUND (git log)
- alembic current = 0005 (head) : VERIFIED on VM
- Multi-folder test : PASSED (2 rows créées, GET retourne tableau, DELETE retourne 204)
