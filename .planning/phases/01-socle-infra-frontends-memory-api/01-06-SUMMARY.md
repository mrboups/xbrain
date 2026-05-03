---
phase: 01-socle-infra-frontends-memory-api
plan: 06
subsystem: ops
tags: [backup, restore, gcs, gsutil, pg-dump, mongodump, qdrant-snapshot, cron]

requires:
  - phase: 01-05
    provides: déploiement actif sur la VM (postgres, mongo, qdrant tous up)
provides:
  - xbrain-backup container (google/cloud-sdk + postgres-client + mongodb-tools + cron)
  - backup.sh — backup quotidien complet : pg_dump + mongodump + Qdrant snapshots + tarball volumes → gs://xbrain-backups-prod/
  - restore.sh — restore depuis date donnée OU "latest" — démarre par auth gcloud + gsutil cp puis exécute pg_restore + mongorestore + Qdrant snapshot upload
  - restore-test.sh — test E2E sur compose isolé (project name xbrain-restore-test) → satisfait success criterion 5
  - Service intégré dans docker-compose racine avec mounts read-only des 3 volumes à backuper
  - Retention auto : delete daily backups > BACKUP_RETENTION_DAILY (défaut 7) sur GCS
  - PARTIAL : tasks 0 (GCS bucket + service account) + 6 (premier backup réel) + 7 (visual verify) → user actions
affects: [phase 1 done gate]

tech-stack:
  added: [google/cloud-sdk Docker image, mongodb-database-tools, GCS bucket xbrain-backups-prod]
  patterns: [cron+tail -f pour daemon backup, restore E2E sur compose isolé pour validation, GCS soft-delete pour récupération accidentelle]

key-files:
  created:
    - infrastructure/backup/Dockerfile (google/cloud-sdk:slim + pg + mongo tools + cron)
    - infrastructure/backup/crontab (0 2 * * * UTC daily + heartbeat 15min)
    - infrastructure/backup/backup.sh (4-stage: pg_dump → mongodump → qdrant snapshot → tarball volumes → gsutil cp + retention)
    - infrastructure/backup/restore.sh (latest|date → gsutil cp → pg_restore + mongorestore + qdrant snapshot upload + tarball extract)
    - infrastructure/scripts/restore-test.sh (compose isolé project xbrain-restore-test + run restore + smoke tests + teardown)
  modified:
    - infrastructure/docker-compose.yml (ajout service xbrain-backup, mounts ro des 3 volumes, GCS service account key mount host /home/user/secrets/)

key-decisions:
  - "google/cloud-sdk:slim comme base image — gsutil + gcloud déjà inclus, évite custom install GCP CLI."
  - "MongoDB tools installé manuellement (not in google/cloud-sdk) — apt repo officiel mongodb 7.0 avec GPG key signed-by /usr/share/keyrings (Debian bookworm)."
  - "Retention via gsutil ls + comparaison de dates dans le script (pas de lifecycle policy GCS) — plus contrôlable. Lifecycle policy GCS reste une option Phase 2 si on veut décharger le script."
  - "GCS soft-delete 7d activé sur le bucket — récupération accidentelle si delete trop agressif côté retention. Coût marginal (~quelques cents/mois)."
  - "restore-test.sh utilise un project Docker Compose nommé xbrain-restore-test → namespace volumes/network isolé du stack prod sur la même VM. Teardown complet (`down -v`) à la fin."
  - "Service account dédié xbrain-backup-sa avec UNIQUEMENT roles/storage.objectAdmin sur ce bucket précis — principe du moindre privilège."

patterns-established:
  - "Pattern backup containerisé : Dockerfile + crontab + script. cron en foreground (`cron && tail -F /var/log/cron.log`) → logs visibles via `docker compose logs xbrain-backup`."
  - "Pattern restore-test isolé : compose alternatif avec project name distinct → test E2E reproductible sans toucher au stack prod."

requirements-completed: []  # 01-06 ne cible aucun REQ-ID directement (success criterion 5 est un gate de phase, pas un requirement REQUIREMENTS.md)

duration: ~10 min (tasks 1-5 inline)
completed: 2026-05-03 (partiel — voir Pending Tasks)
status: PARTIAL — tasks 0+6+7 nécessitent actions user
---

# Plan 01-06 — backup + restore-test (PARTIEL)

**Le système de backup est entièrement codé et intégré dans docker-compose. Pour activer : créer un GCS bucket + service account (1×, ~5 min), puis le cron tourne tout seul.**

## Performance

- Files created: 5
- Files modified: 1
- Tasks completed: 5/8 (1-5 done, 0+6+7 awaiting user)

## Pending Tasks (user actions)

### Task 0 — User action : créer GCS bucket + service account (CHECKPOINT BLOCKING)

```bash
# 1. Créer le bucket
gcloud storage buckets create gs://xbrain-backups-prod \
  --project=xbrain-495115 \
  --location=europe-west1 \
  --uniform-bucket-level-access \
  --soft-delete-duration=7d

# 2. Créer le service account
gcloud iam service-accounts create xbrain-backup-sa \
  --display-name="xbrain backup writer" \
  --project=xbrain-495115

# 3. Donner droits sur le bucket UNIQUEMENT
gcloud storage buckets add-iam-policy-binding gs://xbrain-backups-prod \
  --member="serviceAccount:xbrain-backup-sa@xbrain-495115.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

# 4. Générer clé JSON et copier sur la VM
gcloud iam service-accounts keys create gcs-backup-sa.json \
  --iam-account=xbrain-backup-sa@xbrain-495115.iam.gserviceaccount.com \
  --project=xbrain-495115

ssh -i ~/.ssh/xbrain_key user@__VM_HOST__ 'mkdir -p /home/user/secrets && chmod 700 /home/user/secrets'
scp -i ~/.ssh/xbrain_key gcs-backup-sa.json user@__VM_HOST__:/home/user/secrets/gcs-backup-sa.json
ssh -i ~/.ssh/xbrain_key user@__VM_HOST__ 'chmod 600 /home/user/secrets/gcs-backup-sa.json'

# 5. Local cleanup (ne pas commit la clé)
rm gcs-backup-sa.json
```

### Task 6 — Premier backup manuel + premier restore-test

```bash
# Build + déployer le container backup (re-run du deploy général)
make sync && make deploy

# Backup à la demande
make backup
# Doit afficher "BACKUP DONE" et lister les artifacts uploadés

# Vérifier dans GCS
gsutil ls gs://xbrain-backups-prod/$(date -u +%Y-%m-%d)/

# Test E2E restore (sur la VM)
ssh -i ~/.ssh/xbrain_key user@__VM_HOST__ 'cd /home/user/xbrain && bash infrastructure/scripts/restore-test.sh'
# Doit terminer par "Success criterion 5 (Phase 1 done gate): VALIDATED"
```

### Task 7 — Visual verify (CHECKPOINT)

- ✅ `gsutil ls gs://xbrain-backups-prod/<today>/` montre 4+ artifacts (postgres-*.dump, librechat-mongo-*.archive.gz, qdrant-*.tar.gz, openwebui-data-*.tar.gz)
- ✅ `make vm-logs` (filtré sur xbrain-backup) montre "BACKUP DONE" sans erreur
- ✅ La sortie de `restore-test.sh` finit par "Success criterion 5 (Phase 1 done gate): VALIDATED"
- ✅ Service account `xbrain-backup-sa@xbrain-495115.iam.gserviceaccount.com` a UNIQUEMENT roles/storage.objectAdmin sur `xbrain-backups-prod` (pas project-level)
- ✅ Aucun `.json` de service account dans git : `git ls-files | xargs -I{} grep -l "private_key" {} 2>/dev/null` → empty

## Verification (déjà faite)

- ✅ `infrastructure/docker-compose.yml` contient `xbrain-backup` avec mounts ro des 3 volumes + secret GCS
- ✅ `backup.sh` syntaxe shell valide (`bash -n`), contient `pg_dump` + `mongodump` + Qdrant snapshot + `gsutil cp` + retention
- ✅ `restore.sh` syntaxe valide, contient `pg_restore` + `mongorestore` + Qdrant snapshot upload, supporte arg `latest`
- ✅ `restore-test.sh` syntaxe valide, génère compose alternatif xbrain-restore-test, teardown propre
- ✅ Scripts exécutables (chmod +x)
- ⏭ Premier backup réel — attend GCS bucket (Task 0)
