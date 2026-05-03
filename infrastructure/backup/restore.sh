#!/bin/bash
set -euo pipefail

# Usage: restore.sh <DATE>
# Default targets = container env vars (postgres, mongo, qdrant)
# Si <DATE> = "latest", trouve le dernier backup dispo sur GCS

DATE_ARG="${1:-latest}"
TARGET_PG="${POSTGRES_HOST:-postgres}"
TARGET_MONGO="${LIBRECHAT_MONGO_URI}"
TARGET_QDRANT="${QDRANT_URL}"

WORK=/tmp/restore-$(date +%s)
mkdir -p "${WORK}"
echo "[restore] === START (${DATE_ARG}) ==="

# Auth gcloud — use VM metadata SA by default, fall back to key file if provided
if [ -n "${GCS_SERVICE_ACCOUNT_KEY:-}" ] && [ -s "${GCS_SERVICE_ACCOUNT_KEY:-}" ]; then
  gcloud auth activate-service-account --key-file="${GCS_SERVICE_ACCOUNT_KEY}" --quiet
fi

# Resolve "latest"
if [ "${DATE_ARG}" = "latest" ]; then
  DATE=$(gsutil ls "gs://${GCS_BACKUP_BUCKET}/" | grep -E '[0-9]{4}-[0-9]{2}-[0-9]{2}/$' | sort | tail -1 | awk -F/ '{print $(NF-1)}')
  echo "[resolve] latest = ${DATE}"
else
  DATE="${DATE_ARG}"
fi

# Download all artifacts for this date
echo "[download] gsutil cp gs://${GCS_BACKUP_BUCKET}/${DATE}/* ${WORK}/"
gsutil -m cp "gs://${GCS_BACKUP_BUCKET}/${DATE}/*" "${WORK}/"

# --- 1. Postgres ---
PG_DUMP=$(ls "${WORK}"/postgres-*.dump 2>/dev/null | head -1)
if [ -n "${PG_DUMP:-}" ]; then
  echo "[1/4] Postgres restore from ${PG_DUMP}..."
  PGPASSWORD="${POSTGRES_PASSWORD}" pg_restore --clean --if-exists --no-owner \
    -h "${TARGET_PG}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" "${PG_DUMP}"
fi

# --- 2. Mongo ---
MONGO_DUMP=$(ls "${WORK}"/librechat-mongo-*.archive.gz 2>/dev/null | head -1)
if [ -n "${MONGO_DUMP:-}" ]; then
  echo "[2/4] Mongo restore from ${MONGO_DUMP}..."
  mongorestore --uri="${TARGET_MONGO}" --gzip --archive="${MONGO_DUMP}" --drop
fi

# --- 3. Qdrant ---
QDRANT_TAR=$(ls "${WORK}"/qdrant-*.tar.gz 2>/dev/null | head -1)
if [ -n "${QDRANT_TAR:-}" ]; then
  echo "[3/4] Qdrant restore from ${QDRANT_TAR}..."
  mkdir -p "${WORK}/qdrant-extracted"
  tar xzf "${QDRANT_TAR}" -C "${WORK}/qdrant-extracted"
  for snap in "${WORK}/qdrant-extracted"/*.snapshot; do
    coll=$(basename "${snap}" | sed -E 's/-[0-9]{8}-[0-9]{6}\.snapshot$//')
    echo "  - restoring ${coll} from ${snap}"
    curl -fsS -X POST "${TARGET_QDRANT}/collections/${coll}/snapshots/upload?priority=snapshot" \
      -H "Content-Type: multipart/form-data" \
      -F "snapshot=@${snap}"
  done
fi

# --- 4. Volumes : restore tarballs ---
echo "[4/4] Volumes restore..."
for vol in openwebui-data librechat-uploads librechat-meili; do
  TAR=$(ls "${WORK}"/${vol}-*.tar.gz 2>/dev/null | head -1) || true
  if [ -n "${TAR:-}" ] && [ -d "/backup-volumes/${vol}" ]; then
    echo "  - ${vol}: extract to /backup-volumes/${vol}"
    rm -rf "/backup-volumes/${vol}/"*
    tar xzf "${TAR}" -C "/backup-volumes/${vol}"
  fi
done

rm -rf "${WORK}"
echo "[restore] === DONE ==="
