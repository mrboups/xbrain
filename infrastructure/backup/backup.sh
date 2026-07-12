#!/bin/bash
set -euo pipefail

# Vars depuis env du container :
# POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_HOST
# LIBRECHAT_MONGO_URI
# QDRANT_URL
# GCS_BACKUP_BUCKET (ex: xbrain-backups-prod)
# GCS_SERVICE_ACCOUNT_KEY (path interne du JSON)
# BACKUP_RETENTION_DAILY (défaut 7)

DATE=$(date -u +%Y-%m-%d)
TS=$(date -u +%Y%m%d-%H%M%S)
WORK=/tmp/backup-${TS}
mkdir -p "${WORK}"
echo "[$(date -Iseconds)] === BACKUP START (${TS}) ==="

# --- 1. PostgreSQL (memory-api) ---
echo "[1/4] Postgres dump..."
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
  -h "${POSTGRES_HOST:-postgres}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" \
  -Fc -Z 6 -f "${WORK}/postgres-${TS}.dump"
echo "  → $(du -h ${WORK}/postgres-${TS}.dump | cut -f1)"

# --- 2. MongoDB (LibreChat) — OPTIONAL ---
# LibreChat lives in the `saas` compose profile (Phase 15). An OSS-light or `ops`-only install has
# no Mongo at all, so this step must SKIP rather than abort the whole backup (set -e would otherwise
# take the Postgres + Qdrant steps down with it). We retry first, so that a *deployed* Mongo that is
# merely slow to come up is not mistaken for an absent one (the depends_on edge that used to
# guarantee ordering was removed — it was illegal across profiles).
echo "[2/4] Mongo dump..."
MONGO_ARCHIVE="${WORK}/librechat-mongo-${TS}.archive.gz"
if [ -z "${LIBRECHAT_MONGO_URI:-}" ]; then
  echo "  → SKIP: LIBRECHAT_MONGO_URI unset (LibreChat not deployed)"
else
  MONGO_OK=0
  for attempt in 1 2 3; do
    if mongodump --uri="${LIBRECHAT_MONGO_URI}" --gzip --archive="${MONGO_ARCHIVE}"; then
      MONGO_OK=1
      break
    fi
    echo "  … mongodump attempt ${attempt}/3 failed; retrying in 10s"
    sleep 10
  done
  if [ "${MONGO_OK}" = "1" ]; then
    echo "  → $(du -h "${MONGO_ARCHIVE}" | cut -f1)"
  else
    rm -f "${MONGO_ARCHIVE}"
    echo "  → WARN SKIP: mongodump failed 3x — LibreChat Mongo unreachable (saas profile off?). Postgres + Qdrant backups continue."
  fi
fi

# --- 3. Qdrant snapshot ---
echo "[3/4] Qdrant snapshot..."
COLLECTIONS=$(curl -fsS "${QDRANT_URL}/collections" | jq -r '.result.collections[].name' || echo "")
mkdir -p "${WORK}/qdrant"
for coll in ${COLLECTIONS}; do
  echo "  - snapshotting ${coll}..."
  SNAP=$(curl -fsS -X POST "${QDRANT_URL}/collections/${coll}/snapshots" | jq -r '.result.name')
  curl -fsS "${QDRANT_URL}/collections/${coll}/snapshots/${SNAP}" -o "${WORK}/qdrant/${coll}-${TS}.snapshot"
  curl -fsS -X DELETE "${QDRANT_URL}/collections/${coll}/snapshots/${SNAP}" >/dev/null || true
done
if [ -n "${COLLECTIONS}" ]; then
  tar czf "${WORK}/qdrant-${TS}.tar.gz" -C "${WORK}/qdrant" .
  echo "  → $(du -h ${WORK}/qdrant-${TS}.tar.gz | cut -f1)"
fi
rm -rf "${WORK}/qdrant"

# --- 4. Volumes openwebui_data + librechat_uploads + librechat_meili (tarball) ---
echo "[4/4] Tarball volumes..."
for vol in openwebui-data librechat-uploads librechat-meili; do
  if [ -d "/backup-volumes/${vol}" ]; then
    tar czf "${WORK}/${vol}-${TS}.tar.gz" -C "/backup-volumes/${vol}" .
    echo "  - ${vol}: $(du -h ${WORK}/${vol}-${TS}.tar.gz | cut -f1)"
  fi
done

# --- 5. Upload GCS (gcloud uses VM-attached SA via metadata server, no key file) ---
echo "[upload] gsutil cp via VM metadata SA..."
if [ -n "${GCS_SERVICE_ACCOUNT_KEY:-}" ] && [ -s "${GCS_SERVICE_ACCOUNT_KEY:-}" ]; then
  # Legacy path: key-file auth (kept for non-GCP deployments)
  gcloud auth activate-service-account --key-file="${GCS_SERVICE_ACCOUNT_KEY}" --quiet
fi
gsutil -m cp -r "${WORK}"/* "gs://${GCS_BACKUP_BUCKET}/${DATE}/"

# --- 6. Cleanup local ---
rm -rf "${WORK}"

# --- 7. Retention : delete daily backups > N jours sur GCS ---
RETAIN=${BACKUP_RETENTION_DAILY:-7}
CUTOFF=$(date -u -d "${RETAIN} days ago" +%Y-%m-%d 2>/dev/null || date -u -v -${RETAIN}d +%Y-%m-%d)
echo "[retention] Listing backups older than ${CUTOFF}..."
gsutil ls "gs://${GCS_BACKUP_BUCKET}/" 2>/dev/null | while read line; do
  prefix=$(basename "${line}")
  if [[ "${prefix}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}/$ ]]; then
    bkup_date=${prefix%/}
    if [[ "${bkup_date}" < "${CUTOFF}" ]]; then
      echo "  - deleting ${line}"
      gsutil -m rm -r "${line}" || true
    fi
  fi
done

echo "[$(date -Iseconds)] === BACKUP DONE ==="
