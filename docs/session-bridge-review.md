# Claude Pro/Max Session Linking — Codebase Review

> How a user's personal Claude Pro/Max subscription is linked to xbrain so that
> LibreChat (and team-chat) inference is billed to **their** quota instead of the
> team API key. Phase 9 — "Session Bridge".

## 1. The core idea

Requests are **not** proxied server-side to claude.ai. They are routed **through
the user's own browser** (the Chrome extension), which makes a credentialed
`fetch` against claude.ai's internal API using the user's real cookies, IP, and
Chrome TLS fingerprint. This avoids server-IP rate-limiting, refresh-token
conflicts, and centralized ban risk.

```
LibreChat  ──HTTP──>  session-bridge  ──WS push──>  Chrome extension  ──fetch(credentials:include)──>  api.claude.ai
(chat.grooveos.app)   (bridge.grooveos.app:8105)     (user's browser)                                   (user's Pro/Max quota)
        ▲                                                   │
        └───────────────── SSE stream relayed back ─────────┘
```

## 2. Components

| Component | Path | Role |
|---|---|---|
| **session-bridge** | `apps/session-bridge/` | FastAPI microservice, port **8105**. Bridges LibreChat ↔ extension. |
| `routes_chat.py` | `apps/session-bridge/app/routes_chat.py` | `POST /v1/chat/completions` (OpenAI-compat). Auth → resolve user → push to their WS → relay SSE. |
| `routes_ws.py` | `apps/session-bridge/app/routes_ws.py` | `WS /ws/{user_sub}` — persistent socket from the extension. |
| `pool.py` | `apps/session-bridge/app/pool.py` | In-memory `USER_SOCKETS` map (single-instance, no Redis in Phase 9). |
| `auth.py` | `apps/session-bridge/app/auth.py` | `validate_xbt_token()` → calls memory-api `/v1/me`, 60s TTL cache. |
| **Extension SW** | `chrome-extension/background.js` | Maintains persistent WS to the bridge; dispatches `chat_request` frames. |
| Claude client | `chrome-extension/claude_ai_client.js` | `handleClaude()` — credentialed fetch to claude.ai, streams chunks back over WS. |
| **LibreChat config** | `infrastructure/librechat/librechat.yaml` | Custom endpoint "Claude Pro/Max" → `baseURL: https://bridge.grooveos.app/v1`. |
| **nginx vhost** | `infrastructure/nginx/conf.d/50-bridge.conf` | `bridge.grooveos.app` → `session-bridge:8105`. WS + SSE tuned (86400s WS timeout, buffering off). |

## 3. Endpoints

| Endpoint | Method | Host | Auth | Purpose |
|---|---|---|---|---|
| `/v1/chat/completions` | POST | `bridge.grooveos.app` | `Bearer xbt_…` (mode A) **or** bridge JWT w/ `acting_user_sub` (mode B) | LibreChat / agent-runtime calls this; bridge relays to the user's browser. |
| `/ws/{user_sub}?token=xbt_…` | WS | `bridge.grooveos.app` | `xbt_` token in query string, validated against memory-api `/v1/me`; `sub` must match path | The extension's persistent socket. |
| `/healthz`, `/metrics` | GET | internal | none | health/observability. |

**Two auth modes on `/v1/chat/completions`** (`routes_chat.py`):
- **Mode A** — `Bearer xbt_…` from LibreChat/user clients → `validate_xbt_token()` → route to that user's WS.
- **Mode B** — bridge JWT (`scope=bridge`, `acting_user_sub` claim) from agent-runtime → routes Claude's team-chat replies through the **triggering** user's Pro/Max subscription (quick task 260512-tcr decision #12).

Keepalive comments every 25s defeat Cloudflare's 100s idle timeout on the SSE stream.

## 4. The extension side

- WS URL template: `wss://bridge.grooveos.app/ws/{sub}?token={token}` (`background.js:601`).
- On a `chat_request` frame → `handleClaude(msg, sendFrame)` (`background.js:841`).
- `handleClaude` (`claude_ai_client.js`) fetches `https://claude.ai/api/organizations/{org_id}/…` with `credentials: "include"` and streams SSE chunks back through the WS.
- Org ID resolved via `https://claude.ai/api/organizations` (`background.js:684`).
- The `xbt_` token is minted at GitHub sign-in (`memory-api auth_github.py _mint_xbt_for_user`) and stored in the extension's `chrome.storage.local` (keys `xbt_token`, `user_sub`, `api_token_id`).
- `host_permissions` (manifest): `claude.ai/*`, `api.claude.ai/*`, `*.grooveos.app/*`.

## 5. LibreChat endpoint config

```yaml
# infrastructure/librechat/librechat.yaml
- name: "Claude Pro/Max"
  apiKey: "user_provided"              # user pastes their xbt_ token as the "API key"
  baseURL: "https://bridge.grooveos.app/v1"
  models:
    default: ["claude-opus-4-7", "claude-sonnet-4-6"]
    fetch: false
  titleConvo: true
  titleModel: "claude-sonnet-4-6"
  modelDisplayLabel: "Claude Pro/Max"
```

LibreChat sends the pasted token as `Authorization: Bearer <xbt_token>` → bridge validates it.

## 6. End-to-end connect checklist (operator)

1. Extension installed + **signed in** (fresh `xbt_` token; opens the WS).
2. WS to `bridge.grooveos.app` open (no 403 — 403 means the `xbt_` is stale/invalid in memory-api).
3. User **logged into claude.ai** in the same Chrome with their Pro/Max account.
4. In LibreChat, select endpoint **"Claude Pro/Max"**, paste the `xbt_` token as the API key, pick a model.
5. Chat → routed through bridge → extension → claude.ai → user's quota.

---

## 7. Data-leak cleanup impact audit (2026-05-22)

**Question:** did the security scrub / history-rewrite work break the session link?

**Answer: NO.** The session-bridge path was not functionally altered by the cleanup.

| Bridge-critical file | Touched by a scrub commit? | Notes |
|---|---|---|
| `apps/session-bridge/**` | ❌ No | Last changed by Phase 9 + the bridge-JWT feature (5189e30). Untouched by scrubs. |
| `chrome-extension/background.js` | ❌ No | Last changed by Phase 12 manifest-key + auth fixes. Bridge URLs intact. |
| `chrome-extension/claude_ai_client.js` | ❌ No | Phase 9 only. |
| `infrastructure/nginx/conf.d/50-bridge.conf` | ❌ No | Phase 9 only. |
| `infrastructure/librechat/librechat.yaml` | ⚠️ Yes — `f139c7d` | **Only** changed `registration.allowedDomains` from `acme.example.com` → `grooveos.app`. This is the **email-registration** allowlist, NOT the "Claude Pro/Max" endpoint. `gmail.com` is still allowed, so sign-in is unaffected. The endpoint block is untouched. |

Verified clean:
- No `excalibur` / `acme` / `__VM_HOST__` residue in any bridge-critical file.
- Bridge URLs (`bridge.grooveos.app`, `api.grooveos.app`, `claude.ai`, `api.claude.ai`) all intact.
- `BRIDGE_SHARED_SECRET` config intact (`session-bridge/app/config.py:19`) and matches between LibreChat + memory-api (verified live, sha256 match).

**The real reasons the session wasn't connecting are OPERATIONAL, not from the cleanup:**

1. **Stale `xbt_` token** — the 2026-05-21 DB wipe invalidated all tokens (and the user rows). The extension still cached a pre-wipe `xbt_` → WS 403 "invalid_token". **Fix:** reload extension + re-sign-in to mint a fresh `xbt_`.
2. **Signin 502** — `memory-api` `mem_limit: 384m` (Phase 1 e2-medium budget) is too low; the signin path (PyJWT RS256 + GitHub token exchange + profile fetch + DB writes) pushes RSS to ~100%, OOM-kills the uvicorn worker mid-request, the connection drops → Cloudflare returns 502. **Fix:** raise `mem_limit` to 768m (VM has 8GB / ~2.3GB free). `OOMKilled=true`, `RestartCount=28` confirmed live.
3. **`GITHUB_ORG=excalibur-game`** on the VM `.env` — stale org name (user's real org is `aibrussels`). Does **not** block signin (Step 6 org-check is skipped because `excalibur-game` isn't in the user's `org_logins`), but org-driven team auto-grant won't fire for `aibrussels` until this is corrected to the real org slug.

None of these stem from the scrub — they are DB-wipe + sizing + stale-config side effects.
