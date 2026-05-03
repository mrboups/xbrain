# librechat-bridge

Sidecar Python qui watch le MongoDB de LibreChat (change streams) et forward chaque nouveau message à `memory-api` avec le contrat de tagging 7 champs.

## Architecture

```
LibreChat ──▶ MongoDB (rs0) ◀── librechat-bridge ──POST /v1/messages──▶ memory-api
```

- Connexion via `motor` (async MongoDB driver)
- Pour chaque insert dans la collection `messages` : enrichit avec team_scope (résolu via memory-api `/v1/me`), construit le payload contrat, POST avec un service JWT signé `BRIDGE_SHARED_SECRET`
- Resume token Mongo persisté dans `/tmp/bridge_resume_token.json` → reprend après crash sans dupliquer

## Env vars consommées

```
LIBRECHAT_MONGO_URI           # mongodb://librechat-mongo:27017/LibreChat?replicaSet=rs0
MEMORY_API_URL                # http://memory-api:8000
BRIDGE_SHARED_SECRET          # secret HS256 partagé avec memory-api
JWT_ALGORITHM                 # HS256 par défaut
BRIDGE_DEFAULT_TEAM_SCOPE     # team_scope de fallback si user pas mappé (default: "default")
BRIDGE_BACKFILL_FROM          # "startup" ou "never" (defaults: startup → traite tout au premier boot)
LOG_LEVEL                     # INFO par défaut
BRIDGE_HEARTBEAT_PATH         # /tmp/bridge-alive — touché toutes les 20s pour healthcheck
```

## Healthcheck

Le watcher touche `/tmp/bridge-alive` toutes les 20s. Le HEALTHCHECK Docker vérifie que ce fichier est récent (< 60s). Si la connexion Mongo est perdue, le heartbeat continue (seul le change stream est gelé) → on reste healthy mais on log des warnings.

## Tests

```bash
pip install -e ".[dev]"
pytest -v
```

Les tests utilisent `mongomock-motor` pour simuler Mongo en mémoire — pas besoin de Docker.
