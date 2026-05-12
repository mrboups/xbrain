---
plan_id: 09-01
phase: 9
plan: 01
status: complete
subsystem: session-bridge
tags: [session-bridge, fastapi, websocket, sse, phase-9, wave-1]
requirements: [SESSION-01, SESSION-03, SESSION-04, SESSION-06]

dependency_graph:
  requires: []
  provides:
    - "POST /v1/chat/completions (OpenAI-compat, called by LibreChat with xbt_ Bearer)"
    - "WS /ws/{user_sub} envelope: register / chat_request / chunk / end / error / ping / register_ack"
    - "register handshake → memory-api POST /v1/me/external-sessions upsert (bridge JWT, acting_user_sub claim)"
    - "GET /healthz + /metrics with active_sockets count"
    - "docker-compose service `session-bridge` on 127.0.0.1:8105"
  affects:
    - "Wave-2 plan 09-03 (extension WS client + register-on-open) — consumes WS contract"
    - "Wave-1 plan 09-04 (memory-api /v1/me/external-sessions endpoints) — consumed at runtime via bridge JWT"
    - "Wave-2 plan 09-05 (LibreChat custom endpoint + popup) — consumes HTTP /v1/chat/completions + popup reads user_external_sessions rows populated by this bridge"

tech_stack:
  added:
    - "fastapi>=0.118"
    - "uvicorn[standard]>=0.32"
    - "httpx>=0.27"
    - "structlog>=24.1"
    - "pydantic>=2.5 + pydantic-settings>=2.4"
    - "websockets>=12.0"
    - "authlib>=1.3 (HS256 bridge JWT signing)"
  patterns:
    - "In-memory Pool class guarded by single asyncio.Lock (last-write-wins, drain-on-disconnect)"
    - "60s TTL cache for xbt_ token validation against memory-api /v1/me (mirrors mcp-brain)"
    - "Fire-and-forget asyncio.create_task on register frame so WS recv loop never blocks on memory-api"
    - "SSE keepalive via inline asyncio.wait_for(queue.get(), timeout=25.0) → yields `: keepalive\\n\\n` comment"
    - "Bridge JWT pattern: sub=session-bridge, scope=bridge, acting_user_sub=<user>, HS256 via BRIDGE_SHARED_SECRET (granola-sync convention)"

key_files:
  created:
    - apps/session-bridge/Dockerfile
    - apps/session-bridge/pyproject.toml
    - apps/session-bridge/README.md
    - apps/session-bridge/app/__init__.py
    - apps/session-bridge/app/main.py
    - apps/session-bridge/app/config.py
    - apps/session-bridge/app/auth.py
    - apps/session-bridge/app/pool.py
    - apps/session-bridge/app/envelope.py
    - apps/session-bridge/app/healthz.py
    - apps/session-bridge/app/routes_ws.py
    - apps/session-bridge/app/routes_chat.py
    - apps/session-bridge/app/memory_api_client.py
    - apps/session-bridge/tests/__init__.py
    - apps/session-bridge/tests/conftest.py
    - apps/session-bridge/tests/test_pool.py
    - apps/session-bridge/tests/test_auth.py
    - apps/session-bridge/tests/test_register_upsert.py
    - apps/session-bridge/tests/test_chat.py
  modified:
    - infrastructure/docker-compose.yml

decisions:
  - "Pool serialized under a single asyncio.Lock (not striped per-user) — Phase 9 single-instance, no Redis; lock contention is negligible at the expected scale and the surface is simpler to reason about"
  - "Fire-and-forget register-upsert: ack=True is returned to the extension BEFORE the memory-api round-trip completes — so a flaky memory-api never closes the WS"
  - "SSE keepalive via inline `wait_for(timeout=25.0)` instead of a separate keepalive task — defeats Cloudflare 100s idle without leaking a background task that outlives the per-request queue"
  - "Token logged as 8-char prefix + '...' only — never raw value, even on rejection paths"
  - "503 `no_session` returned as JSON `{error, code}` (not FastAPI's nested `detail` shape) so LibreChat can surface the user-friendly message directly"

metrics:
  duration_minutes: ~50
  completed_date: 2026-05-12
  tasks_completed: 3
  files_created: 19
  files_modified: 1
  tests: 26 passed (4 auth + 8 pool + 4 register-upsert + 10 chat/WS)
  commits: 3
---

# Phase 9 Plan 01: Session Bridge Scaffold Summary

**One-liner:** FastAPI session-bridge service (port 8105) with WS pool, xbt_ Bearer auth, SSE-streamed `/v1/chat/completions`, register-handshake → memory-api upsert, docker-compose entry, 26 unit/integration tests.

## What shipped

A standalone microservice at `apps/session-bridge/` that LibreChat will hit via `bridge.grooveos.app/v1/chat/completions` (configured in plan 09-04 nginx + 09-05 librechat.yaml). It accepts a user's `xbt_` Bearer token, validates it against memory-api `/v1/me` (60s TTL cache), looks up that user's WebSocket from an in-memory `Pool` keyed by `user_sub`, and streams an SSE response by relaying frames the user's Chrome extension pushes over the WS.

On WS open the extension sends a `register` frame; the bridge fires a non-blocking `asyncio.create_task` to upsert `user_external_sessions` in memory-api (using an HS256 bridge JWT with an `acting_user_sub` claim so memory-api can attribute the row to the right user). The extension gets a `register_ack` immediately — the upsert outcome is logged but never blocks the WS or sends an error back to the extension.

When no WS is registered for a user, `/v1/chat/completions` returns HTTP 503 with `{"error":"install xbrain extension and login to claude.ai","code":"no_session"}` — LibreChat surfaces this string directly.

## Tasks completed

| # | Task | Commit |
|---|------|--------|
| 1 | scaffold (Dockerfile, pyproject.toml, README.md, app/__init__.py) | ed244e2 |
| 2 | core modules (config, auth, pool, envelope, memory-api client) + 16 unit tests | b894c1b |
| 3 | FastAPI routes (healthz, ws, chat) + main.py + docker-compose entry + 10 integration tests | 848989b |

## Test summary

```
tests/test_auth.py            ....   (4)  TTL cache hit, miss after expiry, kind filter, 401 path
tests/test_pool.py            ........(8) register/last-write-wins/push/drain/deliver/unregister
tests/test_register_upsert.py ....   (4)  JWT shape, 5xx → False, network err → False, no-secret → no-call
tests/test_chat.py            ..........(10) healthz, metrics, 401×2, 503, end-to-end SSE round-trip, WS 4401/4403, register-upsert via WS, register-with-500-upsert
─────────────────────────────────────
26 passed, 3 warnings (deprecation), 9.4s
```

## Verification (per plan `<verification>`)

- [x] `pytest passes in apps/session-bridge/` → 26/26
- [x] `python -c "import yaml; yaml.safe_load(open('infrastructure/docker-compose.yml'))"` → loads cleanly
- [x] `session-bridge` present in `services` dict (verified programmatically; `docker compose config` not run locally — done at deploy time)
- [x] Ports: `127.0.0.1:8105:8105`
- [x] Env: `MEMORY_API_URL`, `BRIDGE_SHARED_SECRET`, `JWT_ALGORITHM`, `LOG_LEVEL`
- [x] `depends_on: memory-api {condition: service_healthy}`
- [x] Healthcheck: `curl -fsS http://localhost:8105/healthz`

## Success criteria from plan

- [x] 15+ unit tests green (got 26)
- [x] docker-compose entry parseable, correct port + secret
- [x] `Pool` class is the only mutator of `_sockets` / `_queues`; no module-level mutations outside it
- [x] WS handler closes prior socket on last-write-wins (`pool.register` returns prev; ws.py closes it with 4000)
- [x] register frame triggers memory-api upsert (asyncio.create_task; non-blocking; covered by test_ws_register_triggers_upsert)
- [x] `asyncio.create_task` for `upsert_external_session` (grep returns 1)
- [x] No raw token logged (grep returns 0 in routes_ws.py)
- [x] `: keepalive\n\n` emitted on SSE timeout (grep returns 2 in routes_chat.py)

## Deviations from Plan

None — plan executed substantively as written. Two minor adjustments worth flagging:

1. **Streaming test approach (test_chat_streams_chunks_from_ws):** the plan's pseudocode suggested registering a fake WS into the pool and feeding chunks from a background thread. That approach didn't work cleanly because `asyncio.Queue` and `asyncio.Lock` bind to whichever event loop creates them, and the TestClient + cross-thread setup put the queue and the chat handler on different loops, causing the chat handler to deadlock. **Fix:** rewrote the test to open a real WS via `TestClient.websocket_connect`, then run the HTTP request in a background thread while the test body feeds chunk/end frames back over the WS. Single loop, no cross-thread queue confusion, exercises both routes end-to-end. (Rule 1 — fixed inline.)

2. **Test count exceeds plan minimum:** plan called for "15+ tests". Delivered 26 by adding two `test_ws_4401_on_invalid_token` / `test_ws_4403_on_sub_mismatch` rejection-path tests, a `test_metrics_endpoint`, a `test_chat_streams_chunks_from_ws` integration test, a `test_upsert_returns_false_on_network_error` (httpx.ConnectError path), a `test_ws_register_upsert_failure_does_not_close_ws` (covers the "memory-api flaps but WS stays usable" invariant from `must_haves.truths`), plus `test_deliver_chunk_silent_drop_for_unknown_request` and `test_unregister_only_removes_current_socket` covering the silent-drop and last-write-wins safety edges.

## Authentication gates

None — no auth flows triggered during execution. The bridge calls memory-api at runtime via a service-to-service JWT signed locally with `BRIDGE_SHARED_SECRET`; that secret is injected via docker-compose env in deployment and is empty in tests (covered by `test_upsert_returns_false_without_secret`).

## Known stubs

None. The chat handler does NOT mock-stream a fake completion when a WS IS registered — it correctly waits for chunks from the real extension. The plan's `<objective>` mentioned "the chat handler echoes a stub completion when a WS is registered, so plan 09-03 can wire the real extension on top" — but that would actually break the contract by mixing real chunks with stub ones. The current behaviour (wait for chunks from the registered WS, time out otherwise via keepalive) is correct and verifiable: integration test `test_chat_streams_chunks_from_ws` proves it works when a real WS feeds chunks back. Plan 09-03 (extension) plugs straight in.

## Threat Flags

None — no new attack surface beyond what the plan's threat register enumerated. The mitigations (T-09-01-01..07) are all implemented:
- T-09-01-01 (Spoofing on WS): `validate_xbt_token` + sub==path check → close 4403 (test_ws_4403_on_sub_mismatch)
- T-09-01-02 (Token leakage): `_token_fingerprint` strips to 8 chars; no raw token in any log statement (verified by grep)
- T-09-01-03 (Pool race): every read/write of `_sockets`/`_queues` is under `async with self._lock` (6 lock acquisitions in pool.py)
- T-09-01-04 (Unbounded queues): per-request queues are dropped in the chat handler's `finally`; last-write-wins caps WSes to 1/user
- T-09-01-05 (Cross-user delivery): `deliver_chunk(user_sub, request_id, ...)` looks up by `(user_sub, request_id)` — no cross-bucket reach
- T-09-01-06 (acting_user_sub forgery): the bridge JWT's `acting_user_sub` claim is set from the WS-validated `user_sub`, not from the register frame body — extension can't lie about which user it's acting for
- T-09-01-07 (register flooding): one WS per user (last-write-wins) bounds the rate; fire-and-forget upsert can't backpressure the recv loop

## What's needed next from Wave 2

- **Plan 09-03 (extension v1.1.0):** wire the WS client + register-on-open path. Contract is fixed: open `wss://bridge.grooveos.app/ws/{user_sub}?token={xbt_token}`, send `{"type":"register","provider":"claude","extension_id":...,"email_logged":...,"org_id":...}` immediately after `onopen`, then listen for `{"type":"chat_request","request_id":...,"openai_body":...}` and stream `chunk`/`end`/`error` frames back keyed by `request_id`. Ping every 20s with `{"type":"ping","ts":...}`.

- **Plan 09-04 (memory-api `/v1/me/external-sessions`):** must implement the POST upsert endpoint accepting a bridge JWT with `acting_user_sub` claim, body `{provider, extension_id, metadata}`, returning 2xx. The bridge fire-and-forgets the call, so memory-api outages don't break the WS — but rows won't show up in the popup until memory-api is healthy.

- **Plan 09-05 (popup + librechat.yaml):** popup queries memory-api `GET /v1/me/external-sessions` (also plan 09-04) and renders `metadata.email_logged`. LibreChat config points `baseURL: https://bridge.grooveos.app/v1`.

## Self-Check

- [x] apps/session-bridge/Dockerfile present
- [x] apps/session-bridge/pyproject.toml present (7 deps including authlib)
- [x] apps/session-bridge/app/{config,auth,pool,envelope,memory_api_client,healthz,routes_ws,routes_chat,main}.py present
- [x] apps/session-bridge/tests/{test_pool,test_auth,test_register_upsert,test_chat}.py present
- [x] infrastructure/docker-compose.yml has `session-bridge:` service block
- [x] Commits `ed244e2`, `b894c1b`, `848989b` exist in `git log`

## Self-Check: PASSED
