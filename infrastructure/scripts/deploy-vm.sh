#!/bin/bash
set -euo pipefail

VM_HOST="${VM_HOST:-__VM_HOST__}"
VM_USER="${VM_USER:-user}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/xbrain_key}"
REMOTE_DIR="/home/${VM_USER}/xbrain"

echo "==> [1/5] Sanity check local"
test -f .env || { echo "ERROR: .env missing. Run 'cp .env.example .env' and fill values."; exit 1; }
make env-check

echo "==> [2/5] Rsync code vers ${VM_USER}@${VM_HOST}:${REMOTE_DIR}"
rsync -avz --delete \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='__pycache__' \
  --exclude='.venv' \
  --exclude='backups' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  -e "ssh -i ${SSH_KEY} -o BatchMode=yes" \
  ./ "${VM_USER}@${VM_HOST}:${REMOTE_DIR}/"

echo "==> [3/5] docker compose pull (images upstream)"
ssh -i "${SSH_KEY}" -o BatchMode=yes "${VM_USER}@${VM_HOST}" \
  "cd ${REMOTE_DIR} && docker compose -f infrastructure/docker-compose.yml --env-file .env pull librechat librechat-mongo librechat-meili openwebui postgres qdrant nginx"

echo "==> [4/5] docker compose build (images locales)"
ssh -i "${SSH_KEY}" -o BatchMode=yes "${VM_USER}@${VM_HOST}" \
  "cd ${REMOTE_DIR} && docker compose -f infrastructure/docker-compose.yml --env-file .env build memory-api librechat-bridge openwebui-pipeline"

echo "==> [5/5] docker compose up -d + wait healthy"
ssh -i "${SSH_KEY}" -o BatchMode=yes "${VM_USER}@${VM_HOST}" \
  "cd ${REMOTE_DIR} && docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --remove-orphans"

echo "==> Wait 90s for healthchecks..."
sleep 90

echo "==> Status:"
ssh -i "${SSH_KEY}" -o BatchMode=yes "${VM_USER}@${VM_HOST}" \
  "cd ${REMOTE_DIR} && docker compose -f infrastructure/docker-compose.yml ps"

echo ""
echo "==> Smoke tests:"
echo "    LibreChat:    http://${VM_HOST}/"
echo "    Open WebUI:   http://${VM_HOST}/openwebui/"
echo "    memory-api:   http://${VM_HOST}/api/v1/healthz"
echo ""
echo "Run: curl -fsS http://${VM_HOST}/api/v1/healthz"
