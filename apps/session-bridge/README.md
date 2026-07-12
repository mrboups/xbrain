# session-bridge (Phase 9)

FastAPI microservice that routes LibreChat chat requests through a user's Chrome
extension to their Claude Pro/Max subscription. Single-instance, in-memory
WebSocket pool (`USER_SOCKETS: dict[str, WebSocket]`), one WS per `user_sub`,
last-write-wins.

Port: **8105** (bound to `127.0.0.1` on the VM, fronted by nginx vhost
`bridge.example.com`).

## Endpoints

- `GET /healthz` — liveness + `active_sockets` count
- `GET /metrics` — same body as `/healthz` (JSON, no Prometheus format yet)
- `POST /v1/chat/completions` — OpenAI-compat. Called by LibreChat with
  `Authorization: Bearer xbt_...`. Returns SSE stream relayed from the extension.
  - `401` on missing/invalid token
  - `503 {"error": "install xbrain extension and login to claude.ai", "code": "no_session"}`
    when no WS is registered for the user
- `WS /ws/{user_sub}?token=xbt_...` — extension connects here. Token validated
  against memory-api `/v1/me`. Path `user_sub` must match the token's owner
  (closes with `4403 sub mismatch` otherwise).

## WS envelope

- Extension → bridge:
  - `{"type":"register","provider":"claude","extension_id":...,"email_logged":...,"org_id":...}` — handshake; bridge upserts `user_external_sessions` via memory-api
  - `{"request_id":...,"type":"chunk","openai_chunk":{...}}`
  - `{"request_id":...,"type":"end"}`
  - `{"request_id":...,"type":"error","detail":{...}}`
  - `{"type":"ping","ts":...}` — keepalive (ignored server-side)
- Bridge → extension:
  - `{"type":"chat_request","request_id":...,"openai_body":{...}}`
  - `{"type":"register_ack","ok":true|false,"error":null|"..."}`

## Environment

| Var | Default | Notes |
|---|---|---|
| `MEMORY_API_URL` | `http://memory-api:8000` | Inside Docker network |
| `BRIDGE_SHARED_SECRET` | _empty_ | **REQUIRED** in prod. HMAC secret for the bridge JWT signed when calling memory-api `POST /v1/me/external-sessions` (claim `acting_user_sub` = the WS-authenticated user). |
| `JWT_ALGORITHM` | `HS256` | Bridge JWT algorithm |
| `TOKEN_TTL_S` | `60.0` | xbt_ token validation cache TTL |
| `LOG_LEVEL` | `info` | structlog level |

## Operational notes

**Restart impact:** in-flight chat completions fail with an `extension_disconnected`
error frame (HTTP handler unblocks). Extensions reconnect via WS auto-reconnect
(see `chrome-extension/background.js`). Brief downtime (≤ 5s) is expected on
deploy.

**Log greps:**
- `grep ws.connected` — successful WS auth + accept
- `grep ws.rejected` — token validation failures (4401/4403)
- `grep no_session` — chat hit when no WS registered (extension absent)
- `grep register.upsert.ok / register.upsert.failed / register.upsert.error` —
  memory-api `/v1/me/external-sessions` upsert outcome on the WS register frame
- `grep register.upsert.skipped` — `BRIDGE_SHARED_SECRET` unset (dev only)

**Singleton invariant:** because the WS pool is in-memory, this service MUST run
as a single replica. Horizontal scale requires a Redis-backed pool (out of scope
for Phase 9 — see CONTEXT.md anti-scope).
