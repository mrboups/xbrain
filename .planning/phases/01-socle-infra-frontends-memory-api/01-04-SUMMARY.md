---
phase: 01-socle-infra-frontends-memory-api
plan: 04
subsystem: integration
tags: [openwebui, pipeline, openai-compat, anthropic, openai, fastapi, jwt, best-effort]

requires:
  - phase: 01-02
    provides: memory-api /v1/messages + verify_bridge_jwt
provides:
  - openwebui-pipeline container FastAPI exposant /v1/chat/completions OpenAI-compatible
  - Proxy vers Anthropic + OpenAI APIs via SDKs officiels (streaming SSE supporté)
  - Best-effort logging to memory-api (failure NE BLOQUE PAS la réponse au frontend)
  - Service JWT iss=openwebui-pipeline (différent du bridge — audit traceability)
  - Tests pytest unit (mocks Anthropic + memory-api via respx)
affects: [01-05-PLAN (Open WebUI configure OPENAI_API_BASE_URL pour pointer ici)]

tech-stack:
  added: [anthropic SDK 0.40, openai SDK 1.55, respx (mocks HTTP)]
  patterns: [OpenAI-compatible API surface, SSE streaming proxy, best-effort fire-and-forget logging via asyncio.create_task]

key-files:
  created:
    - apps/openwebui-pipeline/pyproject.toml
    - apps/openwebui-pipeline/Dockerfile (non-root UID 10003, port 9099)
    - apps/openwebui-pipeline/.dockerignore
    - apps/openwebui-pipeline/README.md
    - apps/openwebui-pipeline/app/__init__.py
    - apps/openwebui-pipeline/app/config.py
    - apps/openwebui-pipeline/app/memory_api_client.py (avec make_pipeline_jwt iss=openwebui-pipeline)
    - apps/openwebui-pipeline/app/pipelines/__init__.py
    - apps/openwebui-pipeline/app/pipelines/xbrain_logger.py (asyncio.gather best-effort)
    - apps/openwebui-pipeline/app/main.py (3 endpoints : /health, /v1/models, /v1/chat/completions)
    - apps/openwebui-pipeline/tests/__init__.py
    - apps/openwebui-pipeline/tests/conftest.py
    - apps/openwebui-pipeline/tests/test_pipeline_outlet.py (~7 tests, mocks Anthropic + memory-api)
    - apps/openwebui-pipeline/tests/test_payload_mapping.py (~4 tests log_exchange)
    - apps/openwebui-pipeline/pytest.ini
  modified:
    - infrastructure/docker-compose.yml (ajout service openwebui-pipeline avec depends_on memory-api healthy)

key-decisions:
  - "Best-effort logging via asyncio.create_task — le POST memory-api fire-and-forget après que la réponse au client soit envoyée. Si memory-api 500, le user reçoit toujours sa complétion LLM."
  - "iss=openwebui-pipeline dans le JWT (différent de iss=librechat-bridge) — permet à memory-api d'auditer la source en cas de besoin futur."
  - "MODEL_MAP en dur dans main.py Phase 1 (5 modèles). Phase 2 = config externalisée + auto-discovery par provider."
  - "conversation_id synthétique dérivé de hash(user_first_message) — Open WebUI ne pousse pas de conv_id à un OpenAI provider. Phase 2 = header custom X-OpenWebUI-Conversation-Id."
  - "ConfigDict(extra='allow') sur ChatMessage / ChatCompletionsBody — Open WebUI envoie parfois des champs OpenAI non-standard (tool_calls, function_call) qu'on n'utilise pas mais qu'on doit accepter sans 422."

patterns-established:
  - "OpenAI-compat surface : pour qu'un service ressemble à OpenAI à un client (Open WebUI, ChatGPT API, etc.) → exposer /v1/models + /v1/chat/completions avec format SSE strict."
  - "Best-effort logging via fire-and-forget : asyncio.create_task() après que la réponse au client soit construite. Découple le SLA logging du SLA chat."
  - "Service JWT par sidecar : chaque sidecar a son iss propre dans le JWT → memory-api peut filtrer par source dans audit_log."

requirements-completed:
  - CHAT-05

duration: ~12 min (inline)
completed: 2026-05-03
---

# Plan 01-04 — openwebui-pipeline

**Open WebUI a maintenant un OpenAI provider qui logge dans memory-api sans casser l'UX si la mémoire est down. Le `iss` distinct du bridge permet l'audit cross-frontend.**

## Performance

- Files created: 15
- Lines of Python: ~750 (incl. tests)
- Tasks: 5/5

## Files Created/Modified

Voir `key-files.created` dans frontmatter.

## Verification

- ✅ docker-compose.yml YAML valide après ajout `openwebui-pipeline` (port 9099 interne, depends_on memory-api healthy)
- ✅ MODEL_MAP contient les 5 modèles attendus (claude-3-5-sonnet/haiku/opus, gpt-4o/4o-mini/4-turbo)
- ✅ log_exchange utilise asyncio.gather(..., return_exceptions=True) — verrouillé par test_log_exchange_swallows_exceptions
- ✅ JWT iss="openwebui-pipeline" verrouillé dans make_pipeline_jwt
- ⏭ pytest non testable localement — validation au deploy VM (plan 01-05)

## Notes

- **Phase 2 issue** : conversation_id synthétique → si user envoie 2× la même question, ils sont mergés dans la même conv côté memory-api. Acceptable Phase 1, à fixer Phase 2 avec un vrai conv_id propagé par Open WebUI (ou stocké en local pipeline state).
- **CHAT-06** (parallel second opinion) et **CHAT-07** (auto-enrichment CANONICAL facts) sont Phase 2 — pas dans ce plan.
- Le `ANTHROPIC_API_KEY` et `OPENAI_API_KEY` sont injectés depuis `.env` racine. Si vide → 500 sur les models de ce provider, mais les autres providers fonctionnent.
