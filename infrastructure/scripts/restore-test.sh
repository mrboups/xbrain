#!/bin/bash
# E2E restore test on isolated environment — validates success criterion 5 of Phase 1.
#
# Prerequisites:
# - .env at project root (especially POSTGRES_*, LIBRECHAT_MONGO_URI, GCS_BACKUP_BUCKET)
# - At least one daily backup exists in gs://${GCS_BACKUP_BUCKET}/
# - Run from project root: bash infrastructure/scripts/restore-test.sh
set -euo pipefail

cd "$(dirname "$0")/.."  # cd infrastructure/

# Source .env from parent dir
if [ -f ../.env ]; then
  set -a
  . ../.env
  set +a
fi

PROJECT_NAME="xbrain-restore-test"
COMPOSE_FILE="docker-compose.restore-test.yml"

echo "[restore-test] === START ==="

# 1. Generate a stripped-down compose for restore test
cat > "${COMPOSE_FILE}" <<EOF
networks:
  xbrain_restore_net:
    driver: bridge
volumes:
  rt_pg:        { name: rt_pg }
  rt_qdrant:    { name: rt_qdrant }
  rt_mongo:     { name: rt_mongo }
  rt_openwebui: { name: rt_openwebui }
  rt_uploads:   { name: rt_uploads }
  rt_meili:     { name: rt_meili }
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes: [rt_pg:/var/lib/postgresql/data]
    networks: [xbrain_restore_net]
    healthcheck:
      test: ["CMD-SHELL","pg_isready -U ${POSTGRES_USER}"]
      interval: 5s
  qdrant:
    image: qdrant/qdrant:v1.17.1
    volumes: [rt_qdrant:/qdrant/storage]
    networks: [xbrain_restore_net]
    healthcheck:
      test: ["CMD-SHELL", "bash -c '</dev/tcp/localhost/6333'"]
      interval: 5s
  librechat-mongo:
    image: mongo:7
    command: ["--replSet","rs0","--bind_ip_all"]
    volumes: [rt_mongo:/data/db]
    networks: [xbrain_restore_net]
    healthcheck:
      test: ["CMD","mongosh","--quiet","--eval","try{rs.status().ok?0:1}catch(e){1}"]
      interval: 5s
EOF

# 2. Start isolated env
echo "[1/5] Start isolated stack..."
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" up -d
sleep 30

# 3. Init Mongo replset
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" exec -T librechat-mongo mongosh --quiet --eval \
  "try{rs.status()}catch(e){rs.initiate({_id:'rs0',members:[{_id:0,host:'librechat-mongo:27017'}]})}"

# 4. Run restore.sh in ad-hoc container
echo "[2/5] Run restore from latest backup..."
docker run --rm \
  --network "${PROJECT_NAME}_xbrain_restore_net" \
  -v rt_openwebui:/backup-volumes/openwebui-data \
  -v rt_uploads:/backup-volumes/librechat-uploads \
  -v rt_meili:/backup-volumes/librechat-meili \
  -v /home/user/secrets/gcs-backup-sa.json:/secrets/gcs-backup-sa.json:ro \
  -e POSTGRES_USER="${POSTGRES_USER}" \
  -e POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  -e POSTGRES_DB="${POSTGRES_DB}" \
  -e POSTGRES_HOST=postgres \
  -e LIBRECHAT_MONGO_URI="mongodb://librechat-mongo:27017/LibreChat?replicaSet=rs0" \
  -e QDRANT_URL="http://qdrant:6333" \
  -e GCS_BACKUP_BUCKET="${GCS_BACKUP_BUCKET}" \
  -e GCS_SERVICE_ACCOUNT_KEY=/secrets/gcs-backup-sa.json \
  xbrain/backup:phase1 \
  /scripts/restore.sh latest

# 5. Smoke tests
echo "[3/5] Smoke test Postgres..."
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" exec -T postgres \
  psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "SELECT count(*) AS msg_count FROM messages; SELECT count(*) AS team_count FROM teams; SELECT count(*) AS user_count FROM users;"

echo "[4/5] Smoke test Mongo..."
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" exec -T librechat-mongo \
  mongosh --quiet LibreChat --eval "print('messages count:', db.messages.countDocuments({}))"

echo "[5/5] Smoke test Qdrant..."
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" exec -T qdrant \
  curl -fsS http://localhost:6333/collections | jq .

# 6. Teardown
echo "[teardown] Removing isolated stack + volumes..."
docker compose -p "${PROJECT_NAME}" -f "${COMPOSE_FILE}" down -v
rm "${COMPOSE_FILE}"

echo "[restore-test] === SUCCESS ==="
echo "Success criterion 5 (Phase 1 done gate): VALIDATED"
