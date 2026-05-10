---
phase: 9
name: Session Bridge — Pro/Max Routing via Chrome Extension
status: ready_for_planning
gathered: 2026-05-11
source: long-form architectural discussion 2026-05-10/11
---

# Phase 9 Context — Session Bridge

## Domain

Build a new microservice + extension extension that routes Claude chat requests from LibreChat through the xbrain Chrome extension installed on a user's browser, so that the user's own Claude Pro/Max subscription quota is consumed instead of the team API key. ChatGPT Plus routing is explicitly deferred (Phase 10).

## Decisions (LOCKED)

### Architecture

- **Routing direction**: LibreChat → `session-bridge` (HTTP/WS) → user's extension service worker → `api.claude.ai` (with `credentials: 'include'`). NOT a direct proxy from server with curl_cffi — we route through the user's browser to leverage real Chrome TLS fingerprint, real cookies, real IP, real Cloudflare clearance.
- **Why not server-side proxy with curl_cffi**: server IP gets rate-limited by Cloudflare, refresh tokens conflict across machines, ban risk centralized to xbrain instead of per-user, no Arkose support if needed later.
- **Why not Claude Code OAuth token shared**: refresh token rotation breaks multi-machine setups (the last refresher wins, others get 401). The extension architecture sidesteps this entirely.

### Scope (Phase 9 only)

- **IN**: Claude Pro/Max routing via Chrome extension (Phase A backbone + Phase B Claude bridge from the architectural discussion)
- **OUT**: ChatGPT Plus routing (Phase C, deferred to Phase 10 — different format, Arkose challenges, conversation state mapping)
- **OUT**: Opt-in extraction routing (where extraction passive routes through user's extension instead of team API key) — deferred until volume justifies

### Components to build

1. **`apps/session-bridge`** (new microservice, port 8105)
   - FastAPI app
   - Endpoint `POST /v1/chat/completions` — OpenAI-compat, called by LibreChat
   - Endpoint `WS /ws/{user_sub}` — persistent WebSocket from extension
   - In-memory `USER_SOCKETS: dict[str, WebSocket]` (no Redis needed in Phase 9 — single-instance bridge)
   - Auth: validates `Authorization: Bearer <xbt_token>` against memory-api `/v1/users/me`
   - Streams SSE response back to LibreChat by relaying chunks from the WebSocket

2. **xbrain Chrome extension (extends Phase 4 + Phase 8 work)**
   - `extension/background.js` (service worker): WebSocket persistent to `wss://bridge.grooveos.app/ws/{user_sub}`, auto-reconnect on disconnect
   - Handler `handleClaude(req)`: fetch credentialed against `https://api.claude.ai/api/organizations/{org_id}/chat_completions` with `credentials: 'include'`, stream response chunks back via WebSocket
   - `extension/popup.html` / `popup.js`: new "Sessions" section showing Claude session status (🟢 Active / 🔴 None), email loggé on claude.ai, refresh + disconnect buttons
   - `extension/manifest.json`: add `host_permissions` for `https://api.claude.ai/*`, `https://claude.ai/*`

3. **`infrastructure/librechat/librechat.yaml`** — new custom endpoint
   ```yaml
   - name: "Claude (mon abonnement)"
     apiKey: "user_provided"
     baseURL: "https://bridge.grooveos.app/v1"
     models:
       default: ["claude-opus-4-7", "claude-sonnet-4-6"]
       fetch: false
     modelDisplayLabel: "Claude (Pro/Max)"
   ```

4. **`apps/memory-api`** — new endpoints
   - `GET /v1/me/external-sessions` — list user's connected extension sessions
   - `DELETE /v1/me/external-sessions/{provider}` — revoke a session (forces extension reconnect)

5. **DB migration** (Alembic 0014_external_sessions.py)
   ```sql
   CREATE TABLE user_external_sessions (
       id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
       user_id UUID REFERENCES users(id) ON DELETE CASCADE,
       provider VARCHAR(32) NOT NULL,   -- 'claude' (Phase 9), 'chatgpt' (Phase 10)
       extension_id VARCHAR(64),
       last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       metadata JSONB,                   -- {email_logged, org_id}
       UNIQUE(user_id, provider)
   );
   CREATE INDEX idx_external_sessions_user ON user_external_sessions(user_id);
   ```

6. **`infrastructure/nginx/conf.d/`** — new vhost `bridge.grooveos.app`
   - `/v1/` → proxy_pass to session-bridge:8105 (HTTP, SSE streaming, proxy_buffering off)
   - `/ws/` → proxy_pass to session-bridge:8105 (WebSocket upgrade, proxy_read_timeout 86400s)

7. **`infrastructure/docker-compose.yml`** — add `session-bridge` service
   ```yaml
   session-bridge:
     build: { context: ../apps/session-bridge }
     ports: ["127.0.0.1:8105:8105"]
     environment:
       MEMORY_API_URL: http://memory-api:8000
     networks: [xbrain_net]
     restart: unless-stopped
   ```

8. **DNS** — Cloudflare A record `bridge.grooveos.app` → VM IP `__VM_HOST__`, Proxied

### Translation Layer

claude.ai internal API uses a different format than the Anthropic public API. The session-bridge (or the extension handler) must translate:

- **Request**: OpenAI ChatCompletions → claude.ai internal `/chat_completions` with `conversation_uuid`, `parent_message_uuid`, `rendering_mode: "messages"`
- **Response**: claude.ai SSE (`event: completion`, `data: {"completion": "..."}`) → OpenAI SSE (`data: {"choices": [{"delta": {"content": "..."}}]}`)

The translation can happen on either side (session-bridge or extension). **Decision**: do it in the **extension** because the extension already has cookies and can directly hit claude.ai — server-side translation would require relaying raw streams, which is more brittle.

### Authentication flow

1. User installs extension, signs in to xbrain (xbt_ personal API token already in extension storage from Phase 8)
2. Extension service worker opens WebSocket: `wss://bridge.grooveos.app/ws/{user_sub}?token={xbt_token}`
3. session-bridge validates token via memory-api `GET /v1/users/me` (cached 60s), maps `user_sub` → socket
4. User browses to claude.ai, signs in (browser session, cookies set on claude.ai domain)
5. LibreChat user picks "Claude (mon abonnement)" endpoint, pastes their xbt_token as apiKey
6. LibreChat POSTs to `bridge.grooveos.app/v1/chat/completions` with `Authorization: Bearer xbt_...`
7. session-bridge validates token, looks up `USER_SOCKETS[user_sub]`, pushes the chat request to extension
8. Extension fetches `api.claude.ai/api/organizations/{org_id}/chat_completions` with credentials: 'include'
9. Streams chunks back via WebSocket, session-bridge SSE-streams to LibreChat
10. LibreChat displays response in chat UI

### Failure modes

- **No WebSocket connected** (extension not installed OR browser closed): session-bridge returns HTTP 503 with body `{"error": "install xbrain extension and login to claude.ai", "code": "no_session"}`. LibreChat surfaces this to the user.
- **No claude.ai cookie** (user not logged in to claude.ai): extension's fetch returns 401, relays to session-bridge as error chunk, LibreChat shows error.
- **Cloudflare 403** (rare — fingerprint detection): extension catches, relays as error. User instructed to refresh claude.ai tab.
- **WebSocket dropped mid-stream**: session-bridge cancels the in-flight request, returns 504. Extension auto-reconnects on next message.

### Multi-machine considerations

- A user with 2 machines (laptop + desktop, both with extension) → both extensions connect to session-bridge with same `user_sub`
- `USER_SOCKETS` is `dict[str, WebSocket]` — last connect wins, previous one is closed (clean architecture, user only uses one browser at a time effectively)
- Alternative: `dict[str, list[WebSocket]]` with round-robin — deferred unless users complain

### Security model

- xbt_token authenticates the user to session-bridge (existing personal API token from Phase 8 reused, no new auth surface)
- session-bridge **never sees** the user's Claude credentials (they live in the browser as cookies, never sent to server)
- session-bridge **never persists** chat content (transit only, no logging of message bodies beyond Langfuse if enabled)
- Cookies-domain-locked: `api.claude.ai` cookies are inaccessible to anything other than claude.ai origin and the extension with explicit host_permission
- The extension does NOT have permission to read xbrain memory/contacts/tasks — it's purely a routing tool
- ToS: this is grey-area vs Anthropic ToS (users routing automated traffic via subscription). xbrain ToS must explicitly inform users that their Claude account ban risk is their own (clear warning at setup time)

### Operational concerns

- **Casse-fréquence estimée**: Anthropic could change claude.ai internal format → casse l'extension. Estimated 2-3 months between breakage events based on similar OSS projects (`claude-code-router`).
- **Maintenance**: 1-2 days of dev per breakage to update extension fetch logic + format translation
- **Monitoring**: Langfuse traces for routed requests, count of active WebSocket sessions in session-bridge `/metrics`

## Specific implementation references

- claude.ai internal API endpoint: `POST https://api.claude.ai/api/organizations/{org_id}/chat_completions`
- Org ID retrieval: `GET https://claude.ai/api/organizations` (returns list, take first)
- Existing extension code: lives in `extension/` directory (need to read to confirm exact paths and Phase 4/8 patterns)
- xbt_token format: prefix `xbt_`, SHA-256 hashed in `user_api_tokens` table (built in Phase 8 mcp-brain work, see `260509-a1b-mcp-brain-remote-server` quick task)

## Canonical references

- Architectural discussion (this session) covered all major decisions — context file is the authoritative source for Phase 9, supersedes ad-hoc references
- Memory: `project_xbrain_phase7_complete.md`, `project_xbrain_phase4_live.md`, `project_xbrain_phase1_infra.md` — VM is `__VM_HOST__`, Docker Compose orchestration, nginx vhost pattern established
- Existing nginx config: `infrastructure/nginx/conf.d/10-xbrain.conf` (referenced earlier in conversation, has `chat.grooveos.app`, `adm.grooveos.app`, `lang.grooveos.app`, `mcp.grooveos.app` patterns)
- `librechat.yaml` patterns: see existing Anthropic/OpenAI/xAI/Claude Reasoning blocks (`apiKey: user_provided` pattern used in BYOK already supported)

## Open questions for research phase

These should be investigated before final planning:

1. Does the extension's `chrome.scripting.executeScript` work reliably from a service worker when there's NO active claude.ai tab? (Phase 4 patterns suggest yes for `host_permissions`, but verify)
2. What is the EXACT current request format for `api.claude.ai/api/organizations/{org_id}/chat_completions`? (Inspect via DevTools on a real claude.ai session — payload shape may have changed)
3. Are there headers beyond `Authorization`, `Content-Type`, `User-Agent` that claude.ai's internal endpoint requires? (e.g. `anthropic-client-platform`, `anthropic-anonymous-id`)
4. WebSocket reconnection strategy: exponential backoff with jitter? Health-check ping frequency? (Standard patterns exist but pick one)
5. SSE streaming through nginx: any specific buffering directives needed beyond `proxy_buffering off` for sub-second token delivery?

These are TECHNICAL questions for the research agent, not requirements decisions. The requirements above are locked.
