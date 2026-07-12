# Phase 9: Session Bridge — Pro/Max Routing via Chrome Extension — Research

**Researched:** 2026-05-11
**Domain:** browser-mediated request routing, FastAPI WebSocket relay, Chrome MV3 service worker, claude.ai internal API, nginx + Cloudflare streaming
**Confidence:** MEDIUM-HIGH (architecture, FastAPI, nginx, Chrome MV3 — HIGH ; claude.ai internal payload exact shape — MEDIUM, must be re-verified live before coding)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Routing direction**: LibreChat → `session-bridge` (HTTP/WS) → user's extension service worker → `api.claude.ai` (with `credentials: 'include'`). NOT a direct proxy from server with curl_cffi.

**Scope (Phase 9 only)**:
- IN: Claude Pro/Max routing via Chrome extension.
- OUT: ChatGPT Plus routing (Phase 10).
- OUT: Opt-in extraction routing (deferred).

**Components to build (locked)**:
1. `apps/session-bridge` (new FastAPI microservice, port 8105). Endpoints: `POST /v1/chat/completions` (OpenAI-compat, called by LibreChat) and `WS /ws/{user_sub}` (persistent WebSocket from extension). In-memory `USER_SOCKETS: dict[str, WebSocket]` (no Redis in Phase 9). Auth: `Authorization: Bearer <xbt_token>` validated against memory-api `/v1/users/me`.
2. xbrain Chrome extension extended: `background.js` opens persistent WS to `wss://bridge.example.com/ws/{user_sub}`, auto-reconnect. Handler `handleClaude(req)` does credentialed fetch to `api.claude.ai/api/organizations/{org_id}/chat_completions`. New "Sessions" section in `popup.html`. Manifest gets `host_permissions` for `https://api.claude.ai/*` and `https://claude.ai/*`.
3. `infrastructure/librechat/librechat.yaml` — new custom endpoint `"Claude (mon abonnement)"` with `apiKey: "user_provided"`, `baseURL: "https://bridge.example.com/v1"`, models `["claude-opus-4-7", "claude-sonnet-4-6"]`.
4. `apps/memory-api` — `GET /v1/me/external-sessions`, `DELETE /v1/me/external-sessions/{provider}`.
5. Alembic 0014 `user_external_sessions` table (UUID PK, user_id FK CASCADE, provider, extension_id, last_seen_at, metadata JSONB, UNIQUE(user_id, provider)).
6. `infrastructure/nginx/conf.d/` — new vhost `bridge.example.com` with `/v1/` (HTTP SSE) and `/ws/` (WebSocket upgrade, proxy_read_timeout 86400s).
7. `infrastructure/docker-compose.yml` — `session-bridge` service on `127.0.0.1:8105`.
8. DNS — Cloudflare A record `bridge.example.com` → `__VM_HOST__`, Proxied.

**Translation Layer (locked decision)**: OpenAI ↔ claude.ai SSE translation happens **in the extension**, not the bridge. Bridge relays opaque chunks.

**Failure modes (locked)**: 503 if no WS connected, 401 surfaced if no claude.ai cookie, error chunk if Cloudflare 403, 504 if WS dropped mid-stream.

**Multi-machine (locked)**: `USER_SOCKETS` is single-WS-per-user — last connect wins (previous WS closed).

**Security model (locked)**: xbt_token authenticates user. Bridge never sees Claude credentials. Bridge never persists chat content. Extension has no memory/contacts/tasks access.

### Claude's Discretion

- Exact WebSocket reconnection backoff (jitter parameters, max delay)
- Exact heartbeat ping interval (must be ≤ 25s to keep MV3 SW alive)
- In-bridge auth cache TTL (anchored at 60s in CONTEXT — confirm during planning)
- Exact format of the WS message envelope between bridge ↔ extension (request_id / chunk / end / error frames)

### Deferred Ideas (OUT OF SCOPE)

- ChatGPT Plus routing (Phase 10 — different format, Arkose, conversation state mapping)
- Opt-in extraction routing via extension (deferred until volume)
- `dict[str, list[WebSocket]]` multi-machine round-robin (deferred unless users complain)
- Redis-backed `USER_SOCKETS` (deferred — Phase 9 is single-instance bridge)
</user_constraints>

<phase_requirements>
## Phase Requirements

Phase 9 has no v1 REQ-IDs (post-v1 capability set SESSION-01..06). The ROADMAP defines six success criteria; we map them to research support below.

| Criterion (ROADMAP) | Research Support |
|---|---|
| 1. End-to-end LibreChat → extension → claude.ai with Pro/Max quota decrement | §1 claude.ai API shape, §2 MV3 fetch credentialed, §4 SSE relay pattern |
| 2. Explicit error when extension absent / claude.ai not logged in | §4 connection pool + 503 pattern, §6 401 from claude.ai surfaced |
| 3. `session-bridge` docker container with `/v1/chat/completions` + `/ws/{user_sub}` reachable via `bridge.example.com` | §4 FastAPI pattern, §5 nginx vhost + Cloudflare WS |
| 4. Popup shows session status; `user_external_sessions` track extensions | §2 extension popup pattern, §10 metadata table |
| 5. claude.ai SSE translated to OpenAI SSE for LibreChat | §1 claude.ai format, §7 OpenAI SSE format, §4 relay structure |
| 6. `verify-phase9.sh` PASS — at minimum: healthcheck, vhost 200 on auth body, WS echo, E2E with mock | §11 verification design |
</phase_requirements>

## Summary

Phase 9 builds a browser-mediated request bridge so that LibreChat can route Claude chat traffic through each user's own Chrome extension, consuming the user's Claude Pro/Max subscription quota instead of the team's Anthropic API key. The architectural backbone is locked: a new FastAPI microservice (`session-bridge`, port 8105) exposes an OpenAI-compatible `/v1/chat/completions` endpoint to LibreChat and a persistent WebSocket `/ws/{user_sub}` to each connected Chrome extension instance. Bridge relays requests opaquely; extension does the actual `credentials: 'include'` fetch to `api.claude.ai/api/organizations/{org_id}/chat_completions` and translates Claude SSE to OpenAI SSE before piping chunks back.

The main technical risks are (1) **claude.ai internal API drift** — payload shape and required headers (`anthropic-client-platform`, `anthropic-client-version`, etc.) have changed multiple times historically; the exact current shape must be captured from a live claude.ai DevTools session **before writing the extension handler**, (2) **Chrome MV3 service worker termination** — must use the Chrome 116+ "active WebSocket extends SW lifetime" behavior combined with a 20s self-ping inside the WS, (3) **Cloudflare 100s idle timeout on SSE** — bridge must inject keep-alive comment lines on long-running streams, (4) **WebSocket reconnection** — exponential backoff with jitter, plus `chrome.alarms` as a wake fallback.

**Primary recommendation:** Treat the extension's `handleClaude(req)` function as the single point of fragility. Encapsulate the entire claude.ai request construction + SSE parsing in one file, with an explicit `CLAUDE_AI_API_VERSION` constant. Every breakage in the next 12 months will be one file edit. Plan a **live capture step as the very first task of Phase 9 implementation** — open DevTools on a live claude.ai chat, copy the network request as curl, and treat that as the source of truth for the extension's fetch call.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| User authentication to bridge | API / Backend (memory-api) | Bridge (cache) | xbt_ token validation lives in memory-api; bridge caches `/v1/me` result 60s |
| Persistent connection from user | Browser / Extension SW | Bridge (WS endpoint) | Extension owns the WS lifecycle; bridge is passive accept-only |
| OpenAI Chat Completions ingress | Bridge (`/v1/chat/completions`) | — | LibreChat speaks OpenAI; bridge is the OpenAI-compat surface |
| claude.ai credentialed fetch | Browser / Extension SW | — | Cookies live in the user's browser only — server can never replicate this |
| claude.ai SSE → OpenAI SSE translation | Browser / Extension SW | — | **Locked**: translation in extension. Bridge relays opaque chunks. |
| Request multiplexing (multiple in-flight per user) | Bridge | Extension SW | Bridge tags each request with `request_id`; extension echoes it back per chunk |
| TLS termination + WebSocket upgrade | CDN (Cloudflare) → nginx | — | Cloudflare Proxy handles TLS; nginx vhost terminates HTTP/1.1 upgrade |
| Session inventory storage | Database (Postgres) | memory-api | Table `user_external_sessions` track last_seen_at + metadata |
| Popup UI for session status | Browser / Extension popup | memory-api `/v1/me/external-sessions` | Popup polls; memory-api reads Postgres |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---|---|---|---|
| `fastapi` | 0.118+ | session-bridge HTTP + WebSocket | Already used everywhere in xbrain (`mcp-brain`, `memory-api`, `graphiti-service`); native WebSocket support; SSE friendly [VERIFIED: codebase] |
| `uvicorn[standard]` | 0.32+ | ASGI server with websockets + httptools | Used by all xbrain Python services [VERIFIED: codebase] |
| `httpx` | 0.27+ | async HTTP client for memory-api `/v1/me` token validation | Already pinned in memory-api / mcp-brain [VERIFIED: codebase] |
| `pydantic` | 2.x | request/response models | Standard FastAPI dep [VERIFIED: codebase] |
| `structlog` | 24.x | structured logging | Project convention — every xbrain service uses structlog [VERIFIED: codebase grep] |

### Supporting (extension side)

| Library | Version | Purpose | When to Use |
|---|---|---|---|
| Chrome `chrome.storage.session` API | MV3 | persist `xbt_token`, `user_sub` across SW restarts | Already used in Phase 5 extension [VERIFIED: codebase] |
| Chrome `chrome.alarms` API | MV3 ≥ chrome 117 | wake SW periodically as belt-and-braces beyond WS-keepalive | Recommended fallback for SW lifecycle [CITED: developer.chrome.com] |
| Native `WebSocket` global | MV3 SW context | persistent bidirectional channel to bridge | Standard; supported in MV3 service workers since Chrome 116 [CITED: developer.chrome.com] |
| Native `fetch` with `credentials: 'include'` | MV3 SW + host_permissions | hit api.claude.ai with user cookies | Works when `host_permissions` includes `https://api.claude.ai/*` [CITED: developer.chrome.com] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|---|---|---|
| FastAPI WebSocket | `aiohttp` WS | aiohttp is fine but the rest of xbrain uses FastAPI — consistency wins |
| Polling `chrome.alarms` keepalive | `chrome.runtime.onConnect` long port hack | Old pre-Chrome-116 trick; not needed since WS messages now extend SW lifetime. Use alarms only as fallback. |
| Server-side curl_cffi proxy | (LOCKED OUT) browser routing | Explicitly rejected in CONTEXT.md — server IP gets rate-limited, refresh tokens conflict across machines, ban risk centralized [LOCKED] |
| Shared Claude Code OAuth token | (LOCKED OUT) browser routing | Refresh token rotation breaks multi-machine [LOCKED] |
| Redis pub/sub for cross-instance USER_SOCKETS | single-instance bridge with in-memory dict | Phase 9 is one bridge instance; defer Redis until horizontal scale needed [LOCKED] |

### Installation

```bash
# apps/session-bridge/pyproject.toml
fastapi = ">=0.118"
uvicorn = {version = ">=0.32", extras = ["standard"]}
httpx = ">=0.27"
pydantic = ">=2.5"
structlog = ">=24.1"
websockets = ">=12.0"  # transitive via uvicorn[standard] but pin explicitly
```

### Version verification

`fastapi 0.118` and `uvicorn 0.32` confirmed in use across xbrain services (grep `pyproject.toml`). Extension is vanilla MV3 — no npm deps. [VERIFIED: codebase]

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────┐     OpenAI SSE      ┌──────────────────────┐
│   LibreChat      │ ──────────────────▶ │  session-bridge      │
│  (chat.example.com) │   POST /v1/chat/    │  (FastAPI, 8105)     │
└──────────────────┘   completions       │                      │
        ▲              Authorization:    │  ┌────────────────┐  │
        │              Bearer xbt_xxx    │  │ USER_SOCKETS   │  │
        │ SSE          (per-user key in  │  │ dict[str, WS]  │  │
        │ chunks       LibreChat UI)     │  └────────────────┘  │
        │                                │  ┌────────────────┐  │
        │                                │  │ token cache    │  │
        │                                │  │ (60s TTL)      │  │
        │                                │  └────────────────┘  │
        │                                └──────────┬───────────┘
        │                                           │ httpx
        │                                           ▼
        │                                  ┌──────────────────┐
        │                                  │  memory-api      │
        │                                  │  /v1/me          │
        │                                  └──────────────────┘
        │                                           ▲
        │                                           │ creates row
        │                              wss://       │ user_external_sessions
        │                              bridge.../   │
        │                              ws/{sub}     │
┌───────┴──────────┐    chunks/error  ┌──┴──────────┴──────────┐    cred fetch    ┌─────────────┐
│ user picks       │ ◀─────────────── │  Chrome extension      │ ───────────────▶ │ api.claude  │
│ "Claude          │                  │  background.js (MV3 SW)│  credentials:    │ .ai          │
│ (mon abonnement)"│ ◀──────────────  │  handleClaude(req)     │  'include'       │              │
│ pastes xbt_xxx   │  WS msg w/req_id │  SSE → OpenAI translate│                  │ cookies      │
└──────────────────┘                  └────────────────────────┘ ◀──── SSE ─────── │ (claude.ai)  │
                                                                                  └─────────────┘
```

### Recommended Project Structure

```
apps/session-bridge/
├── Dockerfile                    # python:3.12-slim, uvicorn entrypoint
├── pyproject.toml                # deps pinned (fastapi, uvicorn, httpx, structlog)
├── README.md                     # ops notes — restart impact, log greps
└── app/
    ├── __init__.py
    ├── main.py                   # FastAPI app, startup/shutdown hooks
    ├── config.py                 # pydantic-settings (MEMORY_API_URL, TOKEN_TTL_S)
    ├── auth.py                   # validate_xbt_token() — httpx → memory-api /v1/me, TTL cache
    ├── pool.py                   # USER_SOCKETS dict, register/unregister, send_to_user
    ├── routes_ws.py              # WS /ws/{user_sub} — accept, register, recv loop
    ├── routes_chat.py            # POST /v1/chat/completions — translate → ws → SSE
    ├── envelope.py               # request_id, chunk, end, error frame schemas
    └── healthz.py                # /healthz, /metrics (active socket count)

chrome-extension/
├── manifest.json                 # host_permissions += claude.ai, api.claude.ai
├── background.js                 # extended: openBridgeWS(), handleClaude(req)
├── claude_ai_client.js           # NEW: encapsulates claude.ai fetch + SSE parse
├── ws_keepalive.js               # NEW: 20s ping + chrome.alarms fallback
├── popup.html                    # extended: Sessions section
├── popup.js                      # extended: poll /v1/me/external-sessions
└── content.js                    # unchanged (existing Phase 5 web clipper)
```

### Pattern 1: WS-relayed OpenAI-compat /v1/chat/completions

**What:** the HTTP `/v1/chat/completions` handler pushes the LibreChat request to the user's WS, returns an SSE response by reading chunks the extension WS-sends back.

**When to use:** the ONE pattern Phase 9 lives by. Everything else is plumbing.

**Sketch:**

```python
# Source: pattern derived from FastAPI SSE tutorial + standard async queue relay
# https://fastapi.tiangolo.com/tutorial/server-sent-events/

import asyncio, uuid, json
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from app.pool import USER_SOCKETS, USER_QUEUES  # see pool.py
from app.auth import validate_xbt_token

router = APIRouter()

@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    token = auth.removeprefix("Bearer ")
    me = await validate_xbt_token(token)  # → {sub, user_id, ...}
    sub = me["sub"]

    ws = USER_SOCKETS.get(sub)
    if ws is None:
        raise HTTPException(503, detail={
            "error": "install xbrain extension and login to claude.ai",
            "code": "no_session",
        })

    body = await request.json()  # OpenAI ChatCompletion body
    req_id = str(uuid.uuid4())

    # Per-request response queue
    q: asyncio.Queue = asyncio.Queue()
    USER_QUEUES.setdefault(sub, {})[req_id] = q

    # Push to extension. Extension is responsible for translating OpenAI body
    # to claude.ai internal payload (locked decision).
    await ws.send_json({
        "type": "chat_request",
        "request_id": req_id,
        "openai_body": body,
        # Stream is true by default for LibreChat; we always stream.
    })

    async def event_stream():
        keepalive_task = asyncio.create_task(_keepalive(q))
        try:
            while True:
                # Cloudflare 100s idle kills the connection — keepalive comments cover it.
                msg = await q.get()
                if msg["type"] == "chunk":
                    # extension already produced OpenAI SSE format
                    yield f"data: {json.dumps(msg['openai_chunk'])}\n\n"
                elif msg["type"] == "end":
                    yield "data: [DONE]\n\n"
                    return
                elif msg["type"] == "error":
                    yield f"data: {json.dumps({'error': msg['detail']})}\n\n"
                    return
        finally:
            keepalive_task.cancel()
            USER_QUEUES.get(sub, {}).pop(req_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx + Cloudflare bypass
            "Connection": "keep-alive",
        },
    )

async def _keepalive(q: asyncio.Queue):
    # Every 25s emit SSE comment to defeat Cloudflare 100s idle timeout.
    while True:
        await asyncio.sleep(25)
        # The route function yields SSE comments via a sentinel; alternative:
        # have the route's event_stream check time.monotonic() each loop.
        # Simpler: instead of a separate keepalive task, do it inline.
        pass
```

Better simpler pattern — inline timeout on `q.get()`:

```python
async def event_stream():
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"   # SSE comment, ignored by client
                continue
            if msg["type"] == "chunk":
                yield f"data: {json.dumps(msg['openai_chunk'])}\n\n"
            elif msg["type"] == "end":
                yield "data: [DONE]\n\n"
                return
            elif msg["type"] == "error":
                yield f"data: {json.dumps({'error': msg['detail']})}\n\n"
                return
    finally:
        USER_QUEUES.get(sub, {}).pop(req_id, None)
```

[CITED: FastAPI SSE — fastapi.tiangolo.com/tutorial/server-sent-events/]

### Pattern 2: WS connection pool — accept, register, recv loop

**What:** `routes_ws.py` accepts the extension's WS, validates `xbt_token` from query string, registers `(user_sub → ws)`, then loops receiving JSON envelopes from the extension and dispatching them to the right `USER_QUEUES[sub][request_id]`.

```python
# app/routes_ws.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
import structlog
from app.auth import validate_xbt_token
from app.pool import USER_SOCKETS, USER_QUEUES

log = structlog.get_logger(__name__)
router = APIRouter()

@router.websocket("/ws/{user_sub}")
async def ws_endpoint(
    websocket: WebSocket,
    user_sub: str,
    token: str = Query(...),
):
    try:
        me = await validate_xbt_token(token)
    except Exception:
        await websocket.close(code=4401, reason="invalid token")
        return

    if me["sub"] != user_sub:
        await websocket.close(code=4403, reason="sub mismatch")
        return

    await websocket.accept()
    log.info("ws.connected", sub=user_sub)

    # Last-write-wins: close any previously registered WS for this sub.
    prev = USER_SOCKETS.get(user_sub)
    if prev is not None:
        try:
            await prev.close(code=4000, reason="superseded")
        except Exception:
            pass

    USER_SOCKETS[user_sub] = websocket
    USER_QUEUES.setdefault(user_sub, {})

    try:
        while True:
            msg = await websocket.receive_json()
            # Envelope: {"request_id": "...", "type": "chunk|end|error", ...}
            req_id = msg.get("request_id")
            queues = USER_QUEUES.get(user_sub, {})
            q = queues.get(req_id)
            if q is None:
                # Stale chunk after timeout — drop silently.
                continue
            await q.put(msg)
    except WebSocketDisconnect:
        log.info("ws.disconnected", sub=user_sub)
    finally:
        if USER_SOCKETS.get(user_sub) is websocket:
            USER_SOCKETS.pop(user_sub, None)
        # Drain any in-flight queues with an error frame so HTTP handlers unblock.
        for q in USER_QUEUES.get(user_sub, {}).values():
            await q.put({"type": "error", "detail": "extension_disconnected"})
        USER_QUEUES.pop(user_sub, None)
```

### Pattern 3: Extension WS connection with MV3-safe keepalive

**What:** the extension SW opens a WebSocket to the bridge, pings every 20s to keep both the WS *and* the SW alive (Chrome ≥ 116 extends SW lifetime via WS message traffic), with exponential-backoff reconnect on close.

```javascript
// chrome-extension/background.js (extension)
// Source: developer.chrome.com/docs/extensions/mv3/tut_websockets/

const BRIDGE_WS_URL_TEMPLATE = "wss://bridge.example.com/ws/{sub}?token={token}";
let ws = null;
let reconnectAttempt = 0;
let pingTimer = null;

async function openBridgeWS() {
  const { xbt_token, user_sub } = await chrome.storage.session.get([
    "xbt_token",
    "user_sub",
  ]);
  if (!xbt_token || !user_sub) {
    console.warn("[xbrain] no token yet, skipping WS open");
    return;
  }
  const url = BRIDGE_WS_URL_TEMPLATE
    .replace("{sub}", encodeURIComponent(user_sub))
    .replace("{token}", encodeURIComponent(xbt_token));

  ws = new WebSocket(url);

  ws.onopen = () => {
    console.log("[xbrain] WS open");
    reconnectAttempt = 0;
    startPing();
  };

  ws.onmessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    if (msg.type === "chat_request") {
      await handleClaude(msg);   // implements credentialed fetch + SSE translate
    }
  };

  ws.onclose = (event) => {
    console.warn("[xbrain] WS closed", event.code, event.reason);
    stopPing();
    ws = null;
    scheduleReconnect();
  };

  ws.onerror = (e) => { console.error("[xbrain] WS error", e); };
}

function startPing() {
  pingTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping", ts: Date.now() }));
    }
  }, 20_000);  // 20s < 30s MV3 SW idle timeout
}
function stopPing() { if (pingTimer) clearInterval(pingTimer); pingTimer = null; }

function scheduleReconnect() {
  // Exponential backoff: 1s, 2s, 4s, 8s, 16s, capped at 30s, with ±20% jitter.
  reconnectAttempt = Math.min(reconnectAttempt + 1, 6);
  const base = Math.min(2 ** reconnectAttempt * 1000, 30_000);
  const jitter = base * (Math.random() * 0.4 - 0.2);
  const delay = Math.max(500, base + jitter);
  console.log(`[xbrain] reconnect in ${Math.round(delay)}ms`);
  setTimeout(openBridgeWS, delay);
}

// chrome.alarms fallback — if SW *does* get killed despite our pings,
// wake it every minute to re-open the WS.
chrome.alarms.create("xbrain_ws_watchdog", { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "xbrain_ws_watchdog" && !ws) {
    openBridgeWS();
  }
});

// Open on SW boot.
openBridgeWS();
```

[CITED: developer.chrome.com/docs/extensions/mv3/tut_websockets/]

### Pattern 4: Extension claude.ai handler (the fragile bit)

```javascript
// chrome-extension/claude_ai_client.js
// CRITICAL: Headers and payload shape MUST be re-verified against a live
// claude.ai DevTools capture before deploying. Anthropic changes these.

const CLAUDE_AI_API_VERSION = "2025-11";   // bump on every observed change

async function getOrgId() {
  // claude.ai stores org list at this endpoint. First org is default.
  const r = await fetch("https://claude.ai/api/organizations", {
    credentials: "include",
    headers: { "Accept": "application/json" },
  });
  if (!r.ok) throw new Error(`org_id fetch failed: ${r.status}`);
  const orgs = await r.json();
  if (!Array.isArray(orgs) || orgs.length === 0) {
    throw new Error("no organizations returned");
  }
  // Field name varies across versions: "uuid" historically, sometimes "id".
  return orgs[0].uuid || orgs[0].id;
}

async function createConversation(orgId) {
  // claude.ai requires a conversation_uuid; for fresh chats, POST to:
  // https://claude.ai/api/organizations/{org_id}/chat_conversations
  // Returns { uuid, name, summary, ... }
  const r = await fetch(
    `https://claude.ai/api/organizations/${orgId}/chat_conversations`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": "https://claude.ai",
        "Referer": "https://claude.ai/new",
      },
      body: JSON.stringify({
        uuid: crypto.randomUUID(),
        name: "",
      }),
    },
  );
  if (!r.ok) throw new Error(`conv create failed: ${r.status}`);
  return (await r.json()).uuid;
}

// Translate OpenAI ChatCompletion body → claude.ai internal payload
function openaiToClaudeAi(openaiBody, convUuid, parentMsgUuid) {
  // Collapse messages array into a single prompt string.
  // claude.ai web UI doesn't support full system+role threading the way
  // the public Messages API does. The simplest reliable shape is:
  // - take the last user message as `prompt`
  // - prepend earlier turns as plain text (system/user/assistant labelled)
  const turns = openaiBody.messages.map((m) => {
    if (m.role === "system") return `[System]\n${m.content}`;
    if (m.role === "user") return `[Human]\n${m.content}`;
    if (m.role === "assistant") return `[Assistant]\n${m.content}`;
    return m.content || "";
  });
  const prompt = turns.join("\n\n");

  return {
    prompt,
    parent_message_uuid: parentMsgUuid || "00000000-0000-4000-8000-000000000000",
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    personalized_styles: [],
    locale: "en-US",
    tools: [],
    attachments: [],
    files: [],
    sync_sources: [],
    rendering_mode: "messages",
    // Map model names from xbrain canonical → claude.ai web UI slug
    model: mapModel(openaiBody.model),
  };
}

function mapModel(openaiModel) {
  // claude.ai uses internal slugs that differ from public API ids.
  // Inspect live: payload sent by web UI when you switch models.
  const map = {
    "claude-opus-4-7": "claude-opus-4-7",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
  };
  return map[openaiModel] || "claude-sonnet-4-6";
}

async function handleClaude(msg) {
  const { request_id, openai_body } = msg;
  try {
    const orgId = await getOrgId();
    const convUuid = await createConversation(orgId);
    const payload = openaiToClaudeAi(openai_body, convUuid, null);

    const r = await fetch(
      `https://api.claude.ai/api/organizations/${orgId}/chat_conversations/${convUuid}/completion`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "Accept": "text/event-stream, text/event-stream",
          "Accept-Language": "en-US,en;q=0.5",
          "Origin": "https://claude.ai",
          "Referer": `https://claude.ai/chat/${convUuid}`,
          // anthropic-* headers OBSERVED in live captures but vary —
          // re-verify before shipping. Common ones:
          // "anthropic-client-platform": "web_claude_ai",
          // "anthropic-client-version": "<rolling>",
        },
        body: JSON.stringify(payload),
      },
    );

    if (!r.ok) {
      const text = await r.text();
      sendFrame({
        request_id,
        type: "error",
        detail: { status: r.status, body: text.slice(0, 500) },
      });
      return;
    }

    // Stream SSE response chunks back as OpenAI chunks.
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let createdAt = Math.floor(Date.now() / 1000);
    let openaiMessageId = `chatcmpl-${request_id}`;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE: split on double newline.
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const eventBlock = parseSSE(block);
        if (!eventBlock) continue;
        const openaiChunk = translateClaudeAiSSE(
          eventBlock,
          openaiMessageId,
          openai_body.model,
          createdAt,
        );
        if (openaiChunk) {
          sendFrame({ request_id, type: "chunk", openai_chunk: openaiChunk });
        }
      }
    }

    sendFrame({ request_id, type: "end" });
  } catch (e) {
    sendFrame({ request_id, type: "error", detail: { message: String(e) } });
  }
}

function parseSSE(block) {
  // block is one or more "event: x\ndata: y" lines
  const out = { event: null, data: null };
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) out.event = line.slice(6).trim();
    else if (line.startsWith("data:")) {
      const raw = line.slice(5).trim();
      try { out.data = JSON.parse(raw); } catch { out.data = raw; }
    }
  }
  return out;
}

function translateClaudeAiSSE(evt, id, model, created) {
  // Historical claude.ai event shape:
  //   event: completion
  //   data: { "completion": " text", "stop_reason": null, "stop": null, ... }
  // Newer 2025 captures sometimes use Anthropic Messages-style events:
  //   event: content_block_delta
  //   data: { "type": "content_block_delta", "delta": { "type": "text_delta", "text": "..." } }
  // Handle BOTH.
  if (!evt || !evt.data) return null;

  let text = "";
  let finish = null;

  if (typeof evt.data === "object") {
    if ("completion" in evt.data && typeof evt.data.completion === "string") {
      text = evt.data.completion;
      if (evt.data.stop_reason) finish = "stop";
    } else if (evt.data.type === "content_block_delta" && evt.data.delta?.text) {
      text = evt.data.delta.text;
    } else if (evt.data.type === "message_stop") {
      finish = "stop";
    } else if (evt.data.type === "message_delta" && evt.data.delta?.stop_reason) {
      finish = "stop";
    }
  }

  if (!text && !finish) return null;

  return {
    id,
    object: "chat.completion.chunk",
    created,
    model,
    choices: [{
      index: 0,
      delta: text ? { content: text } : {},
      finish_reason: finish,
    }],
  };
}

function sendFrame(envelope) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(envelope));
  }
}
```

**Status of this pattern:** [ASSUMED] — the EXACT payload shape, header set, and SSE event names of `api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` as of 2026-05 are **not publicly documented**. The shape above is the historical pre-2024 format ([CITED: github.com/KoushikNavuluri/Claude-API claude_api.py]) plus the Anthropic Messages SSE shape ([CITED: docs.anthropic.com/claude/reference/messages-streaming]) as a fallback. **First implementation task must be a live DevTools capture on example.com's own claude.ai session and update the code accordingly.**

### Anti-Patterns to Avoid

- **Don't translate OpenAI → claude.ai on the server.** Locked decision: translation is in the extension. The bridge sees opaque chunks. This is non-negotiable per CONTEXT.md.
- **Don't share USER_SOCKETS across replicas.** Phase 9 is single-instance. If you find yourself wanting Redis pub/sub, file it as a Phase 10+ deferral and stop.
- **Don't poll claude.ai with `credentials: 'omit'`** — that defeats the entire architecture. Every fetch from the extension to `*.claude.ai` MUST be `credentials: 'include'`.
- **Don't try to validate Anthropic ToS programmatically.** That's a legal/communications problem, not code. Surface the risk to users in a setup dialog; that's it.
- **Don't store xbt_tokens in `chrome.storage.local`.** Use `chrome.storage.session` (already the Phase 5 pattern — survives SW restarts but cleared on browser close, which is correct).
- **Don't proxy_buffering on for SSE.** Both nginx AND any upstream-of-bridge proxy must have `proxy_buffering off` or chunks arrive in one batch at the end.
- **Don't omit the SSE keepalive comment** on long-running streams — Cloudflare kills idle HTTP after 100s [CITED: indiehackers.com/post/remember-the-problems-encountered-when-deploying-sse-message-push-to-cloudflare].

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---|---|---|---|
| MV3 SW WebSocket keepalive | bespoke `setTimeout` loop with no awareness of MV3 lifecycle | the Chrome ≥ 116 "WS message extends SW lifetime" behavior + 20s ping pattern from official Chrome docs | Pre-Chrome-116 hacks (chrome.runtime.onConnect long-port trick) are obsolete and add complexity [CITED: developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle] |
| SSE event parser | manual line-by-line split with edge cases | the two-step buffer + `\n\n` split shown above (covers all SSE quirks: multi-line data, comments, multiple events per chunk) | Hand-rolled SSE parsers consistently miss `event:` lines, multi-line `data:`, and comment lines; the standard buffer pattern is well-trodden [CITED: simonwillison.net/llms/streaming-llm-apis] |
| xbt_ token validation | inline DB query in session-bridge | httpx call to memory-api `/v1/me` with 60s in-memory TTL cache | Single source of truth for token state lives in memory-api; reimplementing the SHA-256 lookup duplicates revocation logic and creates drift risk [VERIFIED: deps.py:103] |
| Cloudflare WS keep-alive | naive long-idle WS | application-level ping every 20s (already needed for MV3 SW) — Cloudflare default WS idle is ~100s | Cloudflare 100s idle disconnects unproxied silent connections [CITED: community.cloudflare.com/t/websockets-keep-disconnecting] |
| FastAPI WebSocket connection registry | global mutable dict touched from multiple coroutines without care | wrap `USER_SOCKETS` and `USER_QUEUES` in a `Pool` class with `register/unregister/get/send` methods, all serialized via `asyncio.Lock` | Naked dict accessed from `routes_ws` AND `routes_chat` creates race conditions on disconnect-mid-request; class with lock is 30 lines and removes the race |
| OAuth/PKCE for extension auth | new flow | reuse the Phase 5 `launchWebAuthFlow` + Phase 8 `xbt_` token flow that already exist | The extension already has an `xbt_token` (Phase 8 mcp-brain quick-task `260509-a1b`). Reuse — no new auth surface [VERIFIED: codebase] |

**Key insight:** Phase 9 should add zero new auth surfaces, zero new encryption choices, zero new persistence layers. It's a connection-pool + a request translator. Every piece of new logic that isn't one of those is a code smell.

## Runtime State Inventory

Phase 9 is **mostly greenfield** but touches existing systems. The inventory below documents what existing runtime state is affected:

| Category | Items Found | Action Required |
|---|---|---|
| Stored data | New table `user_external_sessions` (Postgres) — no existing data to migrate; Phase 9 also writes to nothing existing. xbt_token in `user_api_tokens` table — **read-only** access from session-bridge (no schema change). | Code: write to new table only. No migration. |
| Live service config | LibreChat `librechat.yaml` gets new custom endpoint block. **Mongo `users.userKeys`** (LibreChat's per-user BYOK store) will store the user's xbt_token under endpoint name `"Claude (mon abonnement)"` — value is encrypted by LibreChat using `CREDS_KEY`/`CREDS_IV` [CITED: deepwiki.com LibreChat security configuration]. | Code: update librechat.yaml. No data migration (per-user keys appear as users paste them.) |
| OS-registered state | None — session-bridge is a new container. nginx vhost is a new file. | None. |
| Secrets/env vars | New env vars on session-bridge container: `MEMORY_API_URL=http://memory-api:8000`. No new top-level secrets. LibreChat's existing `CREDS_KEY`/`CREDS_IV` (already present in `.env`) protects the user's pasted xbt_token. | Code: add to `.env.example`. No new SOPS-managed secrets. |
| Build artifacts / installed packages | Chrome extension version bump (`manifest.json` version 1.0.0 → 1.1.0) — extension store / sideload reinstall required on every user's browser. session-bridge new docker image `xbrain/session-bridge:phase9`. | Manual reinstall step for testers (extension); standard `docker compose build session-bridge` for service. |

**Nothing found in category:** Categories above are all populated; none are empty.

## Common Pitfalls

### Pitfall 1: MV3 service worker dying mid-stream

**What goes wrong:** the user sends a 30-second-long claude.ai response, halfway through Chrome decides the SW is idle (because the WS hasn't sent OR received a message in 30s — possible if the claude.ai response is in a long "thinking" pause), kills the SW, the WS dies, the bridge gets WebSocketDisconnect, LibreChat sees 504.

**Why it happens:** the Chrome 116+ rule is "WS message exchange extends SW lifetime", but if the upstream claude.ai response stalls (Anthropic models can be slow), neither side sends messages on our WS for 30s.

**How to avoid:** the extension's WS keepalive ping (every 20s) fires regardless of whether claude.ai is streaming. Plus, when actively forwarding a claude.ai response, the extension is decoding chunks every few hundred ms so the WS is hot.

**Warning signs:** intermittent 504 in `session-bridge` logs correlated with claude.ai "long-thinking" responses (extended thinking models, large context).

### Pitfall 2: Cloudflare 100s SSE idle timeout

**What goes wrong:** LibreChat's HTTP request to `bridge.example.com/v1/chat/completions` is held open by an `EventSource`-style SSE response. If no `data:` line is sent within 100s, Cloudflare returns a 524 and drops the connection. Users see "Chat error: connection lost".

**Why it happens:** Cloudflare's default 100s idle timeout applies to HTTP, and SSE is HTTP. Same problem as Phase 5's drive-sync slow polls.

**How to avoid:** the FastAPI handler emits `: keepalive\n\n` (SSE comment — ignored by EventSource clients) every 25s via the `asyncio.wait_for(q.get(), timeout=25.0)` pattern shown in Pattern 1.

**Warning signs:** 524 errors in nginx logs only on long claude.ai responses (>100s).

### Pitfall 3: claude.ai breaking the payload shape

**What goes wrong:** Anthropic ships a frontend change that renames a field (e.g., `parent_message_uuid` → `parent_uuid`), adds a required header (`anthropic-client-build`), or rotates the SSE event names. The extension's `handleClaude` starts failing with 400 or 422; users see "no response".

**Why it happens:** `api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` is an internal API with no contract. Comparable OSS projects (`claude-code-router`, `unofficial-claude-api`) break every 2–3 months on average.

**How to avoid:**
1. Centralize all claude.ai request construction in ONE file (`claude_ai_client.js`).
2. Add a one-line `CLAUDE_AI_API_VERSION` constant — bump on every observed change for changelog hygiene.
3. Pin the extension version per-claude-version mapping in `chrome-extension/README.md`.
4. Bridge logs the full `(status, body[:500])` of any non-2xx from claude.ai — so when users report breakage, the fix is "look at the bridge logs, see the new error, patch the extension".
5. Surface ban risk in xbrain ToS at setup time (locked decision).

**Warning signs:** sudden cluster of 400/422 errors in `session-bridge` logs all originating from `extension → claude.ai`. Set a Langfuse alert.

### Pitfall 4: Bridge race on disconnect-mid-request

**What goes wrong:** extension WS disconnects after the bridge has already pushed a `chat_request` envelope but before any chunk has come back. The HTTP `/v1/chat/completions` handler is blocked on `q.get()`. Without a drain step, it waits forever (until the SSE keepalive timeout, but that's tight).

**Why it happens:** WS shutdown handler must signal all in-flight queues that the upstream is gone.

**How to avoid:** in `routes_ws.py`'s `finally` block (Pattern 2), iterate `USER_QUEUES[user_sub]` and `await q.put({"type": "error", "detail": "extension_disconnected"})` for each. The HTTP handler unblocks, returns the error frame, and frees the connection.

**Warning signs:** stuck connections (`netstat` shows ESTABLISHED with no data flow); `active_sockets` metric drops but request count stays elevated.

### Pitfall 5: Multi-machine clobber surprise

**What goes wrong:** user has laptop + desktop, both with extension. They start a chat on laptop. The desktop SW boots, opens its own WS, the bridge boots out the laptop WS (last-write-wins). Laptop chat fails mid-stream.

**Why it happens:** locked architectural decision — `dict[str, WebSocket]` is single-WS-per-user.

**How to avoid:** show in extension popup which device is "Active" via metadata (`extension_id` UUID per install). User sees the conflict and closes one tab. Acceptable UX for Phase 9.

**Warning signs:** users report "chat dies as soon as I open another tab".

### Pitfall 6: LibreChat encryption of pasted xbt_token

**What goes wrong:** user pastes `xbt_xxx` into LibreChat's BYOK dialog for the new endpoint. LibreChat AES-256-CBC encrypts it using `CREDS_KEY`/`CREDS_IV` and stores in MongoDB. If `CREDS_KEY` rotates, the user's stored token becomes garbage and they must re-paste.

**Why it happens:** [VERIFIED: LibreChat security docs] LibreChat uses CREDS_KEY/CREDS_IV for user-provided keys, and any rotation requires re-entry.

**How to avoid:** document in user-facing onboarding: "if you ever rotate CREDS_KEY, all users must re-enter their xbt_token". No code change.

**Warning signs:** support tickets after a `.env` rotation.

## Code Examples

(Most code patterns are already in the Patterns section above. Below are smaller utility patterns.)

### Token cache with 60s TTL (auth.py)

```python
# apps/session-bridge/app/auth.py
import time, httpx
from typing import Any

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL = 60.0

async def validate_xbt_token(token: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _CACHE.get(token)
    if cached and cached[0] > now:
        return cached[1]
    async with httpx.AsyncClient(timeout=5.0) as cli:
        r = await cli.get(
            f"{settings.MEMORY_API_URL}/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 200:
        raise PermissionError(f"token rejected: {r.status_code}")
    me = r.json()
    if me.get("kind") != "user_api_token":
        raise PermissionError("only xbt_ tokens allowed for session-bridge")
    _CACHE[token] = (now + _TTL, me)
    return me
```

Source pattern: same as mcp-brain's `memory_client.get_me()` [VERIFIED: codebase apps/mcp-brain/app/main.py].

### nginx vhost for bridge.example.com

```nginx
# infrastructure/nginx/conf.d/50-bridge.conf
# (Follows the same Cloudflare real-IP setup as 10-xbrain.conf — declared
# at the top of that file via set_real_ip_from / real_ip_header.)

# === session-bridge at bridge.example.com ===
server {
  listen 80;
  server_name bridge.example.com;
  client_max_body_size 2m;

  # Healthcheck
  location /nginx-health { return 200 "ok\n"; access_log off; }

  # === WebSocket endpoint for extension ===
  location /ws/ {
    set $bridge_upstream http://session-bridge:8105;
    proxy_pass $bridge_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;

    # WebSocket upgrade
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;

    # Keep alive for long-lived sessions (24h ceiling).
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
    proxy_buffering off;
  }

  # === OpenAI-compat HTTP endpoint for LibreChat ===
  location /v1/ {
    set $bridge_upstream http://session-bridge:8105;
    proxy_pass $bridge_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header Authorization $http_authorization;

    # SSE streaming — disable buffering at every layer
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    chunked_transfer_encoding off;
    proxy_set_header X-Accel-Buffering no;

    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
  }
}
```

[CITED: nginx.org/en/docs/http/websocket.html ; objectgraph.com/blog/optimizing-sse-nginx-streaming]

### LibreChat custom endpoint snippet (already in CONTEXT.md, format-validated)

```yaml
# infrastructure/librechat/librechat.yaml — append under endpoints.custom
- name: "Claude (mon abonnement)"
  apiKey: "user_provided"
  baseURL: "https://bridge.example.com/v1"
  models:
    default: ["claude-opus-4-7", "claude-sonnet-4-6"]
    fetch: false
  titleConvo: true
  titleModel: "claude-sonnet-4-6"
  modelDisplayLabel: "Claude (Pro/Max)"
```

**Note on header transmission:** LibreChat sends the user-pasted apiKey as `Authorization: Bearer <key>` to the custom endpoint's `baseURL/chat/completions` URL. [CITED: github.com/danny-avila/LibreChat/discussions/3639] No prefix added — whatever the user pastes is the bearer value. So user pastes literally `xbt_xxx` (no "Bearer " prefix).

### Alembic migration 0014 — `user_external_sessions` table

```python
# apps/memory-api/alembic/versions/0014_external_sessions.py
"""user_external_sessions

Revision ID: 0014_external_sessions
Revises: 0013_<prev>
Create Date: 2026-05-XX
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_external_sessions"
down_revision = "0013_<prev>"  # planner: confirm latest

def upgrade():
    op.create_table(
        "user_external_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  server_default=sa.text("gen_random_uuid()"),
                  primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("extension_id", sa.String(64), nullable=True),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.UniqueConstraint("user_id", "provider", name="uq_external_sessions_user_provider"),
    )
    op.create_index("idx_external_sessions_user", "user_external_sessions", ["user_id"])

def downgrade():
    op.drop_index("idx_external_sessions_user", table_name="user_external_sessions")
    op.drop_table("user_external_sessions")
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|---|---|---|---|
| MV3 SW keepalive via `chrome.runtime.onConnect` long-port hack | Active WebSocket message exchange extends SW lifetime natively | Chrome 116 (Aug 2023) | Phase 9 can rely on simple 20s WS ping; no port-hack needed [CITED: developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle] |
| `chrome.alarms` minimum 1 minute period | Minimum 30 seconds | Chrome 117 | Watchdog alarm can fire 2x per SW lifetime window |
| LibreChat custom endpoints required server-side apiKey | `apiKey: "user_provided"` lets users BYOK via UI | LibreChat v0.7.0+ | Phase 9 uses this pattern — no new server-side secrets [CITED: librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint] |
| MinIO/Bitnami Docker Hub images | Chainguard (`cgr.dev`) only | Oct 2025 | Unrelated to Phase 9 but already adopted [VERIFIED: CLAUDE.md] |

**Deprecated / outdated:**
- `chrome.runtime.onConnect` long-port keepalive hack — no longer needed for WS-using extensions on Chrome 116+.
- Server-side IP rotation for claude.ai scraping (curl_cffi + residential proxies) — too brittle, banned by ToS, locked OUT by CONTEXT.md.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|---|---|---|
| A1 | The current claude.ai chat endpoint is `POST https://api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` (with `chat_conversations` plural and `/completion` suffix). Older code uses `/append_message` against `claude.ai` (no `api.` subdomain). | §1 Pattern 4 | Extension fetches 404. Fix: live-capture from DevTools and update URL. 1-line patch. |
| A2 | Header `anthropic-client-platform: web_claude_ai` is sent by the live web UI. | §1 Pattern 4 | Cloudflare 403 / bot challenge. Fix: capture and add headers. |
| A3 | The SSE event names alternate between historic `event: completion` and Messages-style `event: content_block_delta`. The translator handles both. | §1 Pattern 4 | Some chunks dropped silently. Fix: log raw events in DEBUG, observe, extend translator. |
| A4 | LibreChat sends the user-provided apiKey as `Authorization: Bearer <key>` with no munging (user pastes `xbt_xxx`, server sees `Bearer xbt_xxx`). | §LibreChat snippet | Bridge sees no `Bearer` prefix or sees `apikey ` prefix instead — fix: bridge `auth.py` accepts both with/without prefix. |
| A5 | LibreChat encrypts user-pasted keys with AES-256-CBC using `CREDS_KEY`/`CREDS_IV` in MongoDB (collection `users` or similar). [CITED: deepwiki.com but not directly verified in codebase.] | §Pitfall 6 | Less protection than expected. Operational risk; not a blocker for Phase 9. |
| A6 | `CLAUDE_AI_API_VERSION` constant pattern (rolling string) is adequate version tracking. | §Pitfall 3 | Need semver-like tracking; trivial extension. |
| A7 | The user's claude.ai cookies survive between the moment they log in and the moment the extension hits `api.claude.ai` with `credentials: 'include'`. Cookies are typically `sessionKey` (HttpOnly, SameSite=Lax, Secure, ~30d lifetime) — but `SameSite=Strict` would break this. As of historical captures, they were Lax. | §6 | If cookies are now `SameSite=Strict`, the extension fetch from a SW context might be considered cross-site (claude.ai → api.claude.ai is same-site though, so Lax/Strict should both work). Re-verify in capture. |
| A8 | claude.ai's `GET /api/organizations` returns an array with `uuid` field. | §1 Pattern 4 | Field name changed to `id`. Code handles both via `orgs[0].uuid \|\| orgs[0].id`. |
| A9 | Anthropic's TLS fingerprint detection is not currently sophisticated enough to flag real Chrome browsers driven by a service worker. | §Pitfalls intro | If Anthropic detects extension-driven traffic, accounts get banned. Mitigation is user-disclosure ToS (already in CONTEXT.md security model). |
| A10 | `parent_message_uuid` accepts the "nil" UUID `00000000-0000-4000-8000-000000000000` for the first message in a conversation. | §1 Pattern 4 | First message returns 422. Fix: omit field; some versions of the API require this. |
| A11 | Per CONTEXT.md: existing extension code lives in `chrome-extension/` and uses Phase 5 + Phase 8 patterns. Confirmed against codebase. | — | None — verified by `ls chrome-extension/`. |

**These assumptions MUST be resolved by a live DevTools capture as Task 1 of plan 09-01. The output of that capture becomes the source of truth for tasks 09-02+ implementing `claude_ai_client.js`.**

## Open Questions

1. **What is the EXACT current shape of `api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion`?**
   - What we know: historical shape (KoushikNavuluri/Claude-API, st1vms/unofficial-claude-api) uses `prompt`, `parent_message_uuid`, `timezone`, `model`, `rendering_mode`, `attachments`, `files`. The endpoint URL has migrated at least twice (`/api/append_message` → `/api/organizations/.../chat_conversations/.../completion`).
   - What's unclear: as of 2026-05, exact field names, required vs optional, whether `model` accepts public-API ids or web-UI slugs.
   - Recommendation: live capture (Task 1 of plan 09-01) — open DevTools on a claude.ai chat, copy as cURL, paste into RESEARCH-09-CAPTURE.md as appendix.

2. **What `anthropic-*` headers are required vs optional?**
   - What we know: the public Anthropic API requires `anthropic-version`. claude.ai web UI historically sent `anthropic-client-platform`, `anthropic-client-version`, sometimes `anthropic-anonymous-id` (uuid stored in localStorage).
   - What's unclear: do they enforce these now? Bot detection / Cloudflare challenge could depend on them.
   - Recommendation: copy ALL request headers from the live DevTools capture into the extension's fetch. Don't try to minimize.

3. **Will the extension work if no claude.ai tab is open?**
   - What we know: `host_permissions` lets a SW make cross-origin fetches with cookies independent of any tab being open. The cookie store is shared across the profile. [CITED: developer.chrome.com/docs/extensions/develop/concepts/network-requests]
   - What's unclear: nothing — this is standard behavior. The cookie must exist (the user must have logged in to claude.ai at some point with cookies not expired), but no live tab is needed.
   - Recommendation: test explicitly during plan 09-09 verify — close all claude.ai tabs, then send a LibreChat message — must work.

4. **Does Cloudflare's free plan support WebSockets when the origin is Proxied (orange cloud)?**
   - What we know: Cloudflare supports WebSockets across all plans (including Free) when the site is Proxied. There's a "WebSockets" toggle under Network settings — default ON for Free as of 2024+.
   - What's unclear: any documented per-IP WS connection limit on Free?
   - Recommendation: verify dashboard toggle is ON for example.com. Document in Phase 9 entry-gate.

5. **Will MongoDB's encryption of LibreChat's per-user `xbt_token` survive a `CREDS_KEY` rotation?**
   - What we know: LibreChat AES-256-CBC encrypts user-provided keys; rotation of `CREDS_KEY` invalidates them.
   - What's unclear: how to communicate this to users.
   - Recommendation: add documentation note to `docs/sessions.html` (per Phase 6 doc patterns).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| Python 3.12 | session-bridge build | ✓ | 3.12 (per other xbrain apps) | — |
| FastAPI 0.118+ | session-bridge | ✓ | 0.118 | — |
| Postgres | Alembic migration 0014 | ✓ | 17 | — |
| MongoDB (LibreChat per-user keys) | LibreChat BYOK storage | ✓ | 8.0.20 | — |
| Chrome ≥ 116 | extension WS-keepalive-extends-SW behavior | Assumed (modern Chromes) | — | Older Chromes fall back to chrome.alarms watchdog |
| Cloudflare WebSocket support | bridge.example.com | ✓ (Free plan supports WS) | — | If disabled by accident, dashboard toggle |
| nginx 1.25+ (existing in xbrain) | new vhost | ✓ | — | — |

**Missing dependencies with no fallback:** none.

**Missing dependencies with fallback:** Chrome ≤ 115 users — fall back to `chrome.alarms` watchdog (already in Pattern 3 code).

## Validation Architecture

`workflow.nyquist_validation` not explicitly disabled in config — Phase 9 includes test architecture.

### Test Framework
| Property | Value |
|---|---|
| Framework | `pytest` 8.x + `pytest-asyncio` + `httpx` (consistent with other xbrain Python services) |
| Config file | `apps/session-bridge/pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `cd apps/session-bridge && pytest -x` |
| Full suite command | `bash infrastructure/scripts/verify-phase9.sh` |

### Phase Requirements → Test Map

| Req | Behavior | Test Type | Automated Command | File Exists? |
|---|---|---|---|---|
| SC-1 E2E LibreChat→claude.ai | full happy path with mock extension | integration | `pytest apps/session-bridge/tests/test_e2e.py -x` | ❌ Wave 0 |
| SC-2 503 if no WS connected | bridge returns 503 with `no_session` code | unit | `pytest apps/session-bridge/tests/test_chat.py::test_503_when_no_socket -x` | ❌ Wave 0 |
| SC-3 nginx vhost 200 on POST /v1/chat | curl test via verify-phase9.sh | smoke | `bash infrastructure/scripts/verify-phase9.sh::test_vhost` | ❌ Wave 0 |
| SC-4 popup polls /v1/me/external-sessions correctly | manual UAT | manual-only | — (UI test, no automation) | — |
| SC-5 claude.ai SSE → OpenAI SSE | unit test with fixture SSE chunks | unit | `pytest apps/session-bridge/tests/test_translate_extension.py` | ❌ Wave 0 (extension-side test via puppeteer is overkill; cover translator with JS unit test or skip) |
| SC-6 verify-phase9.sh PASS | full | smoke | `bash infrastructure/scripts/verify-phase9.sh` | ❌ Wave 0 |
| Pool race on disconnect | unit | unit | `pytest apps/session-bridge/tests/test_pool.py::test_drain_on_disconnect` | ❌ Wave 0 |
| xbt_token validation cache TTL | unit | unit | `pytest apps/session-bridge/tests/test_auth.py::test_cache_ttl` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `cd apps/session-bridge && pytest -x` (≤ 5s, runs all unit tests)
- **Per wave merge:** full `pytest` + `bash infrastructure/scripts/verify-phase9.sh` against running stack
- **Phase gate:** `verify-phase9.sh` PASS: N/N green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `apps/session-bridge/pyproject.toml` with `[tool.pytest.ini_options]` (pytest-asyncio mode=auto)
- [ ] `apps/session-bridge/tests/__init__.py`
- [ ] `apps/session-bridge/tests/conftest.py` — fixtures: mock memory-api (`respx`), in-memory pool, fake WS
- [ ] `apps/session-bridge/tests/test_pool.py`
- [ ] `apps/session-bridge/tests/test_chat.py`
- [ ] `apps/session-bridge/tests/test_auth.py`
- [ ] `apps/session-bridge/tests/test_translate_extension.py` (Python test of the translator IF we extract it as a server-side module for unit-test purposes — note: locked decision keeps it in extension. Recommended: write a pure-JS translator + JS test in `chrome-extension/test_translate.test.js` using a small node test runner)
- [ ] `infrastructure/scripts/verify-phase9.sh` — 6 tests minimum (healthcheck, vhost reachable, WS echo, auth 401, no-session 503, E2E with mock extension)
- [ ] Framework install: deps already in pyproject.toml

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---|---|---|
| V2 Authentication | yes | xbt_ Bearer token (existing Phase 8 pattern); SHA-256 hashed at rest in `user_api_tokens.token_hash`; HTTPS-only via Cloudflare |
| V3 Session Management | yes | WebSocket auth via query-string `?token=`, validated on accept; sub-mismatch close-code 4403; idle WS timeout enforced by Cloudflare/nginx |
| V4 Access Control | yes | `user_sub` from token MUST match `{user_sub}` in WS path; cross-user push prevented at pool layer |
| V5 Input Validation | yes | OpenAI body validated via Pydantic; WS envelopes validated via Pydantic; max body size enforced by nginx (`client_max_body_size 2m`) |
| V6 Cryptography | yes | LibreChat's `CREDS_KEY`/`CREDS_IV` AES-256-CBC for user-pasted xbt_token at rest; xbt_token transit over HTTPS; never log raw token (only token hash prefix) |
| V9 Communications | yes | TLS via Cloudflare (HTTP/3 + 1.3); WS via `wss://` enforced (no plaintext `ws://`) |
| V11 Business Logic | yes | Rate-limit per-user requests in bridge (deferred unless abuse); ban-risk disclosure in user ToS at setup (Anthropic ToS grey area, locked decision) |

### Known Threat Patterns for FastAPI + WebSocket + Chrome ext + nginx

| Pattern | STRIDE | Standard Mitigation |
|---|---|---|
| Token leakage via WS URL query (logs) | Information Disclosure | Strip `?token=...` from nginx access logs (`log_format` exclusion); rotate `xbt_` on suspicion of leak |
| Cross-user request push (bug in pool.py) | Tampering / E.o.P | Pool API only accepts `(user_sub, request_id, payload)` — never raw WS handle access outside pool module; unit test for sub-mismatch |
| WS DoS by single user opening 1000 sockets | DoS | Pool's last-write-wins behavior naturally limits to 1 socket per user; no explicit cap needed in Phase 9 |
| Extension impersonation (someone else's xbt_ stolen) | Spoofing | Rely on Phase 8 token revocation UX (`/v1/me/api-tokens/{id}` DELETE); add audit log entry per WS connect: `(user_sub, ip, connected_at)` |
| Bridge replay attack (replay LibreChat → bridge `/v1/chat/completions`) | Tampering | Bearer-token-only auth, no nonce/replay protection — but the content is the message itself, so replay = send-same-message-twice; not a security issue, just UX |
| Cloudflare bypass via direct origin IP | Spoofing | nginx vhost binds `bridge.example.com`; default server returns 302; origin IP firewalled to Cloudflare ranges only (existing Phase 1 pattern) |
| MV3 SW message origin spoofing (postMessage from injected content) | Spoofing | All cross-runtime messages MUST use `chrome.runtime.onMessage` (not `window.postMessage`); validate `sender.id === chrome.runtime.id` |

## Project Constraints (from CLAUDE.md)

| Directive | How Phase 9 Complies |
|---|---|
| Open-source + self-hostable only, no managed-cloud-only services | All components OSS: FastAPI (MIT), uvicorn (BSD), Chrome ext (MV3 free), nginx (BSD), LibreChat (MIT). Cloudflare used only as CDN/proxy (replaceable). |
| Docker Compose on GCP VM Ubuntu 24.04, e2-standard-4 currently | New `session-bridge` container ≤ 150 MB RAM (lightweight FastAPI app) — fits headroom. |
| Multi-frontend invariant | Phase 9 adds one custom endpoint to LibreChat only. Open WebUI not affected. ChatGPT-via-API and Claude Code unaffected. |
| Every data point carries the 7-field tagging contract | Phase 9 produces no memory writes (transit-only chat traffic per locked security model). The only DB write is `user_external_sessions` — operational metadata, not memory_items, so tagging contract does not apply. |
| GSD workflow enforced via hooks; no direct edits | Plan + execute via `/gsd-plan-phase 9` and `/gsd-execute-phase 9`. |
| Reply in French unless purely technical / English | Acknowledged. RESEARCH.md is in English (technical content). |

## Risks to Locked Decisions

Reviewed CONTEXT.md against research findings. The following items, while not contradictions, deserve a flag for the discuss-phase / planner:

1. **Translation in extension (locked) vs. complexity of MV3 SW** — putting the OpenAI ↔ claude.ai translator in the extension means every claude.ai format change requires a new extension version, which means users must update Chrome extensions manually. If we ever ship via Chrome Web Store, that's days of review per change. **Mitigation:** distribute as unpacked extension via `chrome-extension/` directory (already the Phase 5 pattern — load unpacked from filesystem). No store review needed. Confirm with user this remains acceptable.

2. **Single-WS-per-user (locked, last-write-wins) vs. user expectation** — users on laptop+desktop will see chats break on every device switch. This is acknowledged in CONTEXT.md, but the popup should *prominently* show "Connected on this device — your other devices were disconnected". UX detail for plan 09-04 (popup).

3. **Token in WS URL query string vs. token in header** — passing `?token=` is the only way to authenticate a WS during the HTTP upgrade handshake (browsers don't support custom headers on the WS constructor). Accepted by every major OSS framework, but adds risk that the token appears in nginx access logs. **Recommend:** add log_format directive that elides query strings on `/ws/` location. Trivial.

4. **No Redis cross-instance state (locked, single-instance bridge) vs. operational continuity** — if `session-bridge` container restarts, ALL extensions disconnect and reconnect (good — exponential backoff handles it) BUT all in-flight chats fail. This is acceptable for a transit-only service. Document the restart impact in `apps/session-bridge/README.md` so ops knows.

5. **Bridge never persists chat content (locked) vs. Langfuse traces (mentioned in CONTEXT.md operational concerns)** — Langfuse if enabled will trace request/response bodies. This is in tension with "never persists chat content". **Recommend:** Langfuse integration disabled for session-bridge by default; if enabled, only metadata (timing, status, request_id, user_sub) is sent — never message bodies. This is a planning-stage decision.

None of the above blocks Phase 9 implementation. All are nuances for the planner.

## Sources

### Primary (HIGH confidence)
- Chrome Developers — WebSockets in service workers — https://developer.chrome.com/docs/extensions/mv3/tut_websockets/ (Chrome 116+ keepalive behavior, exact 20s ping pattern)
- Chrome Developers — Service Worker lifecycle — https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle (30s idle timeout, event extension)
- Chrome Developers — Cross-origin network requests — https://developer.chrome.com/docs/extensions/develop/concepts/network-requests (host_permissions + credentials behavior)
- FastAPI — Server-Sent Events tutorial — https://fastapi.tiangolo.com/tutorial/server-sent-events/
- nginx — WebSocket proxying — https://nginx.org/en/docs/http/websocket.html
- LibreChat — Custom Endpoint Object Structure — https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/custom_endpoint (`apiKey: user_provided` semantics)
- Anthropic — Streaming messages — https://platform.claude.com/docs/en/build-with-claude/streaming (SSE event names for reference)
- xbrain codebase — `apps/memory-api/app/deps.py` lines 102–127 (xbt_ token validation pattern), `apps/mcp-brain/app/main.py` (Bearer + memory-api `/v1/me` pattern), `infrastructure/nginx/conf.d/10-xbrain.conf` (nginx Cloudflare real-IP + WS upgrade pattern), `infrastructure/nginx/conf.d/40-mcp.conf` (streaming pattern)

### Secondary (MEDIUM confidence)
- LibreChat discussion #3639 — apiKey transmission as Authorization header — https://github.com/danny-avila/LibreChat/discussions/3639
- LibreChat encryption — https://deepwiki.com/LibreChat-AI/librechat.ai/6.4-security-configuration (AES-256-CBC, CREDS_KEY/CREDS_IV — single source, not codebase-verified)
- Indie Hackers — Cloudflare SSE 100s timeout — https://www.indiehackers.com/post/remember-the-problems-encountered-when-deploying-sse-message-push-to-cloudflare-e437a468a6
- KoushikNavuluri/Claude-API — historical claude.ai payload shape — https://github.com/KoushikNavuluri/Claude-API/blob/main/claude-api/claude_api.py (2023-vintage, format has evolved)
- st1vms/unofficial-claude-api — alternative reference for claude.ai cookie-based access — https://github.com/st1vms/unofficial-claude-api
- musistudio/claude-code-router — comparable OSS routing project — https://github.com/musistudio/claude-code-router (architectural reference, not format reference)
- Simon Willison — How streaming LLM APIs work — https://til.simonwillison.net/llms/streaming-llm-apis
- WebSocket.org — nginx WebSocket proxy guide — https://websocket.org/guides/infrastructure/nginx/
- objectgraph — Optimizing SSE through nginx — https://objectgraph.com/blog/optimizing-sse-nginx-streaming/

### Tertiary (LOW confidence — to verify in plan 09-01 live capture)
- Exact request shape and header set for `POST api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` as of 2026-05 — **MUST live-capture before implementation**
- Exact `anthropic-client-platform` / `anthropic-client-version` values required — **MUST live-capture**
- Whether claude.ai requires `parent_message_uuid` for first-message-in-conv or accepts nil/missing — **MUST live-capture**

## Metadata

**Confidence breakdown:**
- Standard stack (FastAPI, uvicorn, httpx, nginx): HIGH — all already in xbrain
- Architecture patterns (WS pool, SSE relay, MV3 keepalive): HIGH — covered by official Chrome docs + standard FastAPI patterns
- claude.ai internal API exact shape: MEDIUM-LOW — must be verified live before extension implementation; assumptions log A1–A10 capture the risk
- Common pitfalls (Cloudflare timeout, MV3 SW, pool races): HIGH — known patterns with documented mitigations
- Security model: HIGH — derives from existing Phase 8 xbt_ token pattern; no new surfaces

**Research date:** 2026-05-11
**Valid until:** 2026-06-11 (claude.ai format is the volatile component — assume re-verification needed monthly)
