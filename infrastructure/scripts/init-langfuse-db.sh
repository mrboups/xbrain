#!/usr/bin/env bash
# Idempotent: create the `langfuse` database in the shared Postgres instance.
# Run once on the VM after `docker compose up -d postgres` and before bringing
# up the Langfuse stack. Safe to re-run.
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-xbrain-postgres}"
PG_USER="${POSTGRES_USER:-xbrain}"
DB_NAME="${LANGFUSE_DB_NAME:-langfuse}"

echo "→ ensuring database '${DB_NAME}' exists in container ${PG_CONTAINER}…"

exists=$(docker exec "$PG_CONTAINER" psql -U "$PG_USER" -tAc \
  "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}';" || echo "")

if [[ "$exists" == "1" ]]; then
  echo "✓ database '${DB_NAME}' already exists — nothing to do"
  exit 0
fi

docker exec "$PG_CONTAINER" psql -U "$PG_USER" -c \
  "CREATE DATABASE ${DB_NAME} OWNER ${PG_USER};"

echo "✓ database '${DB_NAME}' created (owner=${PG_USER})"
