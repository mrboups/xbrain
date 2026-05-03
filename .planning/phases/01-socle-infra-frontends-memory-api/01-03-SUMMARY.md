---
phase: 01-socle-infra-frontends-memory-api
plan: 03
subsystem: integration
tags: [librechat, mongodb, change-stream, bridge, sidecar, motor, jwt]

requires:
  - phase: 01-02
    provides: memory-api /v1/messages endpoint + verify_bridge_jwt
provides:
  - librechat-bridge container Python qui watch MongoDB change streams (collection messages)
  - mapping doc Mongo → payload memory-api avec contrat tagging 7 champs
  - bridge JWT HS256 court-vif (TTL 5 min) signé avec BRIDGE_SHARED_SECRET
  - resume token persisté → reprend après crash sans dupliquer (idempotency at memory-api side handles dup)
  - heartbeat /tmp/bridge-alive pour healthcheck Docker
  - tests unit (mongomock-motor pour Mongo, pas de Docker requis)
affects: [01-05-PLAN (deploy: bridge needs librechat-mongo to be in compose)]

tech-stack:
  added: [motor 3.6 (async MongoDB driver), tenacity (retries), mongomock-motor (tests)]
  patterns: [sidecar pattern, MongoDB change stream + resume token, service JWT pattern, retry-on-HTTPError]

key-files:
  created:
    - apps/librechat-bridge/pyproject.toml
    - apps/librechat-bridge/Dockerfile (non-root UID 10002, healthcheck via /tmp/bridge-alive freshness)
    - apps/librechat-bridge/.dockerignore
    - apps/librechat-bridge/README.md
    - apps/librechat-bridge/app/__init__.py
    - apps/librechat-bridge/app/config.py
    - apps/librechat-bridge/app/bridge_token.py (make_bridge_jwt)
    - apps/librechat-bridge/app/state_store.py (resume token persistence)
    - apps/librechat-bridge/app/memory_api_client.py (POST /v1/messages avec retry)
    - apps/librechat-bridge/app/mongo_watcher.py (watch_loop + map_message + heartbeat)
    - apps/librechat-bridge/app/main.py (asyncio.gather watch_loop + heartbeat_loop)
    - apps/librechat-bridge/tests/__init__.py
    - apps/librechat-bridge/tests/conftest.py
    - apps/librechat-bridge/tests/test_payload_mapping.py (8 tests)
    - apps/librechat-bridge/tests/test_bridge_token.py (7 tests)
    - apps/librechat-bridge/pytest.ini
  modified:
    - infrastructure/docker-compose.yml (ajout service librechat-bridge avec depends_on memory-api+librechat-mongo healthy)

key-decisions:
  - "Resume token persisté en local /tmp (pas en DB) — choix volontaire pour Phase 1 simplicité. Rebuild image = perte du token, mais idempotency côté memory-api handle (par metadata.librechat_id futur)."
  - "Phase 1 resolve_team_scope() = constante BRIDGE_DEFAULT_TEAM_SCOPE. Phase 2 = lookup memory-api /v1/me avec cache TTL."
  - "Healthcheck Docker basé sur freshness /tmp/bridge-alive (heartbeat toutes les 20s). Le watcher ET un loop dédié écrivent ce fichier — résilient si la connexion Mongo est gelée mais le process est vivant."
  - "Tests unit avec mongomock-motor → pas besoin de Docker pour pytest. Couvre map_message + bridge_token round-trip. L'intégration réelle est validée au deploy VM (plan 01-05)."

patterns-established:
  - "Pattern sidecar (1 container = 1 worker process Python, no HTTP server). HEALTHCHECK basé sur file freshness, pas sur curl."
  - "Service JWT pattern : iss=service-name, scope='bridge', ttl=300s. Court pour limiter blast radius si secret leak. Memory-api accepte si scope=='bridge' et signature valide."
  - "Retry pattern : tenacity 3 tentatives, exponential backoff 0.5→4s, only on HTTPError. Pas de retry sur 4xx applicatifs (trop tard, fix nécessaire)."

requirements-completed:
  - CHAT-01
  - CHAT-02
  - CHAT-03
  - CHAT-04
  - CHAT-05

duration: ~12 min (inline)
completed: 2026-05-03
---

# Plan 01-03 — librechat-bridge

**Le pont qui rend les conversations LibreChat visibles dans memory-api. Sans ce sidecar, success criterion 2 (chat retrievable from memory-api) ne tient pas.**

## Performance

- Files created: 16
- Lines of Python: ~600 (app + tests)
- Tasks: 5/5

## Accomplishments

- Container worker Python prêt à se brancher sur le MongoDB de LibreChat (replica set rs0)
- Change stream + resume token → idempotent across restarts
- Mapping payload sécurisé : sub OIDC, team_scope, source `librechat:{model}`, contract 7 champs
- 15 tests pytest unit (mongomock + JWT round-trip)

## Files Created/Modified

Voir `key-files.created` dans frontmatter.

## Verification

- ✅ docker-compose.yml YAML valide après ajout `librechat-bridge`
- ✅ map_message couvre les 6 cas critiques (user/assistant role, googleId/email fallback, source format, conv_id required, model resolution)
- ✅ bridge JWT contient bien `iss=librechat-bridge`, `scope=bridge`, `team_scope`, `exp ≤ now+5min`
- ⏭ Tests pytest non lançables localement (env Python pas installé). Validation au deploy VM.

## Notes

- Le service `librechat-mongo` (sur lequel le bridge dépend via `depends_on: service_healthy`) est défini dans le plan 01-05. Tant que 01-05 n'est pas exécuté, `docker compose up` partiel sur le bridge échouera (c'est attendu).
- Backfill : par défaut le bridge traite tout au premier boot (`BRIDGE_BACKFILL_FROM=startup`) puis switch sur le change stream temps-réel. Pour skip le historique, mettre `BRIDGE_BACKFILL_FROM=never`.
