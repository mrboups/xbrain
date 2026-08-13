# Session Bridge — routing requests through a browser-authenticated web session

> A technical method for letting a backend use a user's **already-logged-in web
> session** with an AI provider (e.g. `claude.ai`) — without ever extracting or
> storing the user's credentials. The user's own browser stays the executor.

---

## 1. The core idea

There is **no cookie/token extraction**. The session `sessionKey` is never copied
out of the browser, never stored server-side, never replayed from a server IP.

Instead the user's browser stays the **executor**. A browser extension keeps a
live socket open to a small relay service; when the backend needs an answer, the
extension makes a normal `fetch(..., { credentials: "include" })` against the
provider **from inside the user's own browser** — their real cookies, their IP,
their browser TLS fingerprint. The streamed answer is relayed back over the same
socket.

So "use the session" means: **publish that a live provider session exists in this
browser, and accept proxied requests against it.** Nothing secret leaves the
browser. This also avoids server-IP rate-limiting and centralized ban risk.

```
OpenAI-compatible client ──HTTP──> relay service ──WS push──> browser extension ──fetch(credentials:include)──> provider
  (e.g. LibreChat, an agent)        (WS + SSE relay)           (user's browser)                                 (claude.ai)
        ▲                                                           │
        └──────────────────────────  SSE stream relayed back  ──────┘
```

---

## 2. Components (roles, not products)

| Component | Role |
|---|---|
| **Browser extension** (MV3 service worker) | Holds a persistent WebSocket to the relay. On each request, runs a credentialed `fetch` against the provider from inside the browser and streams the result back as frames. |
| **Relay service** | Exposes an **OpenAI-compatible** `POST /v1/chat/completions`. Resolves which user's socket the request belongs to, pushes the request down that socket, and relays the SSE stream back up. Keeps an in-memory `user → socket` pool. |
| **Auth/token service** | Issues a per-user bearer token at sign-in. The relay validates it (with a short TTL cache). |
| **Consumer** | Anything that speaks the OpenAI chat API: a chat frontend, an agent runtime, a script. |

---

## 3. The two halves

| | **Producer side** (browser → backend) | **Consumer side** (backend → browser) |
|---|---|---|
| **What** | The extension announces a live session and holds the socket open | A consumer sends a chat request; the answer streams back |
| **Carrier** | Persistent WebSocket `wss://<relay>/ws/{user_id}?token={bearer}` | A `chat_request` frame on that same socket |
| **Identity** | The user's bearer token | `Bearer <token>` (the user) **or** a service JWT naming the acting user |

---

## 4. Wire protocol

**On socket open**, the extension sends a `register` frame so the relay knows
which provider/session this browser offers (provider details best-effort, nullable):

```jsonc
{ "type": "register", "provider": "claude", "extension_id": "<id>",
  "email_logged": "user@example.com", "org_id": "<org-uuid>" }
```

**Per request**, the consumer's HTTP call becomes a `chat_request` pushed down the
socket; the extension answers with a stream of frames keyed by `request_id`:

```
chat_request  →  { request_id, openai_body: {...} }            // relay → extension
chunk         ←  { request_id, type: "chunk", openai_chunk }   // extension → relay (repeated)
end           ←  { request_id, type: "end" }
error         ←  { request_id, type: "error", detail: {...} }  // on any failure
```

Inside the extension, handling one request is: resolve the org id → create a
conversation → POST the completion with `credentials: "include"` → read the
provider's SSE stream and **translate** each event into an OpenAI-style chunk, so
the consumer sees a standard OpenAI stream and needs no special client.

---

## 5. Two auth modes on `/v1/chat/completions`

- **Mode A — user token.** The consumer sends `Authorization: Bearer <token>`.
  The relay validates it and routes to **that** user's socket. This is the normal
  "a frontend chats on my own session" path.
- **Mode B — service JWT.** A backend (e.g. an agent runtime) sends a JWT with a
  `scope=bridge` claim plus an `acting_user_*` claim. The answer is routed through
  the **named** user's session — so a server-side action can run on the session of
  whoever triggered it, not on a shared key.

---

## 6. Setup runbook (per user)

All of these must be true at the same time:

1. **Install the extension.**
2. **Sign in** from the extension → the auth service mints a per-user bearer
   token, stored in `chrome.storage.local`. Storing the token is what triggers the
   socket to open (`storage.onChanged` → open WS).
3. **Be logged into the provider** (e.g. `claude.ai`) in the **same browser
   profile**, on the account whose session/quota should be used.
4. **Verify the socket is up** (popup status indicator; relay has the user in its
   pool).
5. **Point an OpenAI-compatible client at the relay.** Set its `baseURL` to the
   relay's `/v1` and pass the bearer token as the "API key". A chat frontend like
   **LibreChat** does this with a custom OpenAI-compatible endpoint (see §7).

---

## 7. Consuming it from an OpenAI-compatible frontend (e.g. LibreChat)

The relay is just an OpenAI-compatible server, so any such frontend can use it by
declaring a custom endpoint that points at it and treating the per-user bearer
token as the API key:

```yaml
# a custom OpenAI-compatible endpoint
- name: "Browser Session"
  apiKey: "user_provided"            # user pastes their per-user bearer token
  baseURL: "https://<relay-host>/v1" # the relay's OpenAI-compatible base
  models:
    default: ["<model-a>", "<model-b>"]
    fetch: false
  titleConvo: true
```

The frontend then sends each message as `Authorization: Bearer <token>` to
`/v1/chat/completions` — Mode A above. From the user's point of view it looks like
any other model in the picker; under the hood every message is executed by their
own browser against the provider.

---

## 8. Reliability notes

- **MV3 service workers are killed on idle.** A `chrome.alarms` watchdog plus a
  `storage.onChanged` listener re-open the socket automatically — no user action.
- **Reconnect** uses exponential backoff with jitter; a periodic keepalive ping
  holds the socket open.
- **Long SSE streams:** emit SSE keepalive comments every ~25s to defeat the ~100s
  idle timeout common to reverse proxies/CDNs, and disable proxy response
  buffering on the relay vhost so chunks flush in real time.
- **Auth-failure close codes** (e.g. `4401`/`4403`) should **not** trigger a retry
  loop — wait until the token is refreshed, otherwise you hammer the relay.

---

## 9. The one fragile point: provider format drift

The provider's internal SSE/endpoint shapes are **unofficial** and change without
notice. Contain that risk:

- Keep all endpoint URLs, request body keys, and SSE→OpenAI translation in a
  **single module**, behind a version constant (e.g. `PROVIDER_API_VERSION`).
- When replies go empty/garbled, re-capture against a live session, fix that one
  module, and bump the constant. Nothing else should need to change.

---

## 10. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Socket closes with `4401`/`4403` | Bearer token stale/invalidated | Reload extension, re-sign-in to mint a fresh token (auto-reopens the socket) |
| Completions return 401/403 from the provider | Not logged into the provider in that browser profile, or session expired | Log into the provider, then "refresh session" in the extension |
| Replies empty / garbled | Provider changed its SSE or endpoint shape | Re-capture, fix the translation module, bump the version constant (§9) |
| Stream cuts off after ~100s | Proxy/CDN idle timeout | Ensure SSE keepalive comments + proxy buffering off (§8) |
| Socket drops on idle | MV3 worker killed | Expected — watchdog + storage listener reopen it (§8) |
