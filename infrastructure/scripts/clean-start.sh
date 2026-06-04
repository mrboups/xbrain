#!/bin/bash
# clean-start.sh — reset the GrooveOS brain to a fresh, empty state (for testing).
#
# What it does (in order):
#   1. Backs up Postgres (+ LibreChat Mongo) to /home/user/xbrain-backups/.
#   2. Full DB wipe via POST /v1/admin/wipe-database ("WIPE EVERYTHING"):
#      TRUNCATEs every data table + clears Qdrant + Neo4j + drops LibreChat Mongo
#      + clears MinIO decks, then re-seeds the meeting-recap agent.
#   3. Re-registers the MCP tools (the registry table is truncated by the wipe →
#      gateway would otherwise 404 every tool).
#   4. Restarts mcp-gateway (re-discovers tools) + librechat (recreates Mongo).
#   5. Prints the fresh counts.
#
# PRESERVED (not touched): the schema (alembic), all config files (librechat.yaml,
# the product KB, nginx) and the .env (so Centrifugo / GitHub / API keys survive).
# After running: users/teams/brains/chats/media are gone → sign in fresh via GitHub.
#
# Usage (on the VM):  sudo bash infrastructure/scripts/clean-start.sh -y
set -uo pipefail

if [ "${1:-}" != "-y" ] && [ "${1:-}" != "--yes" ]; then
  echo "This IRREVERSIBLY wipes ALL data (users, teams, brains, chats, media)."
  echo "Re-run to confirm:  sudo bash infrastructure/scripts/clean-start.sh -y"
  exit 1
fi

XBRAIN_DIR="${XBRAIN_DIR:-/home/user/xbrain}"
ENV_FILE="$XBRAIN_DIR/infrastructure/.env"
BACKUP_DIR="${BACKUP_DIR:-/home/user/xbrain-backups}"
TS="$(date -u +%Y%m%d-%H%M%S)"

echo "=== GrooveOS clean-start ($TS) ==="

# 1. Backup -----------------------------------------------------------------
mkdir -p "$BACKUP_DIR"
echo "[1/5] Backup -> $BACKUP_DIR"
docker exec xbrain-postgres pg_dump -U xbrain -Fc xbrain > "$BACKUP_DIR/pg-$TS.dump" \
  && echo "  postgres: $(du -h "$BACKUP_DIR/pg-$TS.dump" | cut -f1)" \
  || { echo "  PG DUMP FAILED — aborting (no wipe)"; exit 1; }
if docker exec xbrain-librechat-mongo sh -c 'command -v mongodump' >/dev/null 2>&1; then
  docker exec xbrain-librechat-mongo mongodump --db LibreChat --gzip --archive \
    > "$BACKUP_DIR/mongo-$TS.archive.gz" 2>/dev/null \
    && echo "  mongo: $(du -h "$BACKUP_DIR/mongo-$TS.archive.gz" | cut -f1)"
fi

# 2. Wipe via the superadmin endpoint (bridge JWT minted from BRIDGE_SHARED_SECRET)
echo "[2/5] Wipe database"
SECRET="$(grep -m1 '^BRIDGE_SHARED_SECRET=' "$ENV_FILE" | cut -d= -f2-)"
if [ -z "$SECRET" ]; then echo "  BRIDGE_SHARED_SECRET not found in $ENV_FILE — abort"; exit 1; fi
docker exec -e WIPE_SECRET="$SECRET" -w /app xbrain-librechat node -e '
  const jwt = require("jsonwebtoken");
  const now = Math.floor(Date.now()/1000);
  const t = jwt.sign(
    {iss:"librechat-bridge", sub:"clean-start", team_scope:"default", scope:"bridge", iat:now, exp:now+300},
    process.env.WIPE_SECRET, {algorithm:"HS256"});
  fetch("http://memory-api:8000/v1/admin/wipe-database", {
    method:"POST",
    headers:{Authorization:"Bearer "+t, "Content-Type":"application/json"},
    body: JSON.stringify({confirm:"WIPE EVERYTHING"}),
  }).then(async r => { console.log("  wipe status: " + r.status); process.exit(r.status===200?0:1); })
    .catch(e => { console.log("  ERR " + e.message); process.exit(1); });
' || { echo "  WIPE FAILED — aborting re-init"; exit 1; }

# 3. Re-register MCP tools (registry was truncated) -------------------------
echo "[3/5] Re-register MCP tools"
bash "$XBRAIN_DIR/infrastructure/scripts/register-mcp-tools.sh" 2>&1 | grep -iE "SUCCESS|PARTIAL|FAIL" | tail -2

# 4. Restart gateway (re-discover tools) + librechat (recreate dropped Mongo)
echo "[4/5] Restart mcp-gateway + librechat"
( cd "$XBRAIN_DIR/infrastructure" && docker compose restart mcp-gateway librechat >/dev/null 2>&1 )

# 5. Verify -----------------------------------------------------------------
echo "[5/5] Fresh state:"
docker exec xbrain-postgres psql -U xbrain -d xbrain -tAc \
  "SELECT '  users=' || (SELECT count(*) FROM users) || ' teams=' || (SELECT count(*) FROM teams) || ' memory_items=' || (SELECT count(*) FROM memory_items) || ' agents=' || (SELECT count(*) FROM agent_definitions);"

echo "=== Done. Brain is empty — sign in fresh via GitHub. Backup: $BACKUP_DIR/pg-$TS.dump ==="
