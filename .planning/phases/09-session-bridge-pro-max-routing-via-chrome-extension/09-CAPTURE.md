# Phase 9 — claude.ai internal API capture

Date captured: **PENDING — user UAT validation step**
Browser: Chrome (target ≥ 116, MV3 SW WebSocket lifecycle behaviour)
Account type: Pro | Max | Free (any — format only depends on web UI)

> **Status of this file**: this is the BEST-GUESS contract assembled from
> `09-RESEARCH.md` §Pattern 4 (claude_ai_client.js) + assumptions A1..A10 of the
> Assumptions Log. Plan 09-02 ships code (`claude_ai_client.js`, `translate_sse.js`)
> against this contract WITHOUT waiting for a live DevTools capture.
>
> The user MUST do a live DevTools capture as part of Phase 9 UAT (09-06). When the
> capture diverges from the contract below, the `## Divergence Patches` section is
> the single place where the fix lands. Bump `CLAUDE_AI_API_VERSION` in
> `chrome-extension/translate_sse.js` on every observed format change.

---

## Raw curl

```bash
# TODO: paste live DevTools "Copy as cURL (bash)" output here, redact:
#   - Cookie:       <REDACTED>
#   - authorization: not present (cookie-based auth on api.claude.ai)
#   - any anthropic-anonymous-id / cf-* tokens
#
# The shape we are currently coding against (RESEARCH.md §Pattern 4):
#
# curl 'https://api.claude.ai/api/organizations/<ORG_UUID>/chat_conversations/<CONV_UUID>/completion' \
#   -X POST \
#   -H 'accept: text/event-stream, text/event-stream' \
#   -H 'accept-language: en-US,en;q=0.5' \
#   -H 'content-type: application/json' \
#   -H 'origin: https://claude.ai' \
#   -H 'referer: https://claude.ai/chat/<CONV_UUID>' \
#   -H 'anthropic-client-platform: web_claude_ai' \
#   -H 'anthropic-client-version: <REDACTED rolling string>' \
#   --cookie '<REDACTED>' \
#   --data-raw '{
#     "prompt": "[Human]\nHi",
#     "parent_message_uuid": "00000000-0000-4000-8000-000000000000",
#     "timezone": "Europe/Madrid",
#     "personalized_styles": [],
#     "locale": "en-US",
#     "tools": [],
#     "attachments": [],
#     "files": [],
#     "sync_sources": [],
#     "rendering_mode": "messages",
#     "model": "claude-opus-4-7"
#   }'
```

## Raw SSE response

```text
# TODO: paste first 10+ SSE events from live capture.
#
# Historical pre-2024 shape (legacy):
event: completion
data: {"completion":" Hello","stop_reason":null}

event: completion
data: {"completion":" there","stop_reason":null}

event: completion
data: {"completion":"","stop_reason":"end_turn"}

# Anthropic Messages-style shape (some 2025 captures):
event: message_start
data: {"type":"message_start","message":{"id":"msg_01...","role":"assistant","model":"claude-opus-4-7"}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}

event: message_stop
data: {"type":"message_stop"}
```

The translator in `chrome-extension/translate_sse.js` handles BOTH shapes.

## Organizations response

```json
[
  {
    "uuid": "<REDACTED org_uuid>",
    "name": "Personal",
    "settings": {},
    "capabilities": [],
    "rate_limit_tier": "default_claude_ai",
    "join_token": null,
    "active_flags": []
  }
]
```

Field used: `uuid` (fallback `id` if shape ever changes — handled in `claude_ai_client.js getOrgId()`).

---

## Assumption verification table

CONFIRMED = matches live capture / RESEARCH-cited public OSS evidence.
DIVERGED  = live capture contradicts the assumption — patch noted in `## Divergence Patches`.
N/A       = not testable from a single DevTools session (validated elsewhere).
PENDING   = live capture not yet performed; current implementation assumes "expected" status.

| # | Assumption | Status | Evidence line |
|---|------------|--------|---------------|
| A1 | Endpoint URL is `POST https://api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` | PENDING (expected CONFIRMED) | RESEARCH.md §Pattern 4 ; baked into `claude_ai_client.js` `handleClaude()` URL template |
| A2 | `anthropic-client-platform: web_claude_ai` header is sent by the web UI | PENDING (expected CONFIRMED) | RESEARCH.md §Pattern 4 comment ; baked into `claude_ai_client.js` headers |
| A3 | SSE event names — legacy `event: completion` AND/OR Messages-style `event: content_block_delta` / `message_stop` / `message_delta` | PENDING (expected CONFIRMED — both branches needed) | RESEARCH.md §Pattern 4 ; translator handles both branches |
| A4 | LibreChat sends apiKey as `Authorization: Bearer <key>` to baseURL | N/A — verified in plan 09-05 | LibreChat BYOK pattern, not testable from claude.ai capture |
| A5 | LibreChat encrypts user-pasted keys AES-256-CBC with CREDS_KEY/CREDS_IV | N/A — out of scope | LibreChat security docs, not testable here |
| A6 | `CLAUDE_AI_API_VERSION` rolling string is enough for change tracking | CONFIRMED | Constant defined `2026-05-capture` in `translate_sse.js` ; bump on every observed change |
| A7 | claude.ai cookies are SameSite=Lax so credentialed fetch from SW works | PENDING (expected CONFIRMED) | Inspect `chrome://settings/cookies/detail?site=claude.ai` ; if Strict, the WHOLE phase architecture is invalid |
| A8 | `GET /api/organizations` returns an array with `uuid` field (sometimes `id`) | PENDING (expected CONFIRMED) | `getOrgId()` already does `orgs[0].uuid \|\| orgs[0].id` fallback |
| A9 | TLS fingerprint not currently checked beyond Cloudflare baseline | N/A — operational risk | Cannot verify from a single Chrome session ; risk accepted, Pattern 5 monitoring covers detection |
| A10 | `parent_message_uuid` accepts the nil UUID `00000000-0000-4000-8000-000000000000` for first message in a conversation | PENDING (expected CONFIRMED) | RESEARCH.md §Pattern 4 ; baked into `openaiToClaudeAi()` default ; if DIVERGED, the actual value (probably a root-message UUID returned by `createConversation`) goes into a patch |

10 rows total. All PENDING/CONFIRMED/N/A — no DIVERGED at code-time. UAT (09-06) flips PENDING → CONFIRMED or DIVERGED.

---

## RISK lines — what blows up if a PENDING assumption flips DIVERGED

- **RISK A1**: if URL changed (e.g., dropped `chat_conversations/{conv_uuid}` segment, or split into `/completion` + `/abort`), `handleClaude` hits 404 → bridge logs `{status: 404, body: "..."}`. Fix = one `URL` template edit in `claude_ai_client.js`.
- **RISK A2**: if `anthropic-client-platform` header changed name or claude.ai now requires `anthropic-client-version`, fetch returns 401/403 → fix = headers object in `claude_ai_client.js`. Pin the new client-version string in the capture and update `CLAUDE_AI_API_VERSION` to `YYYY-MM-capture`.
- **RISK A3**: if claude.ai dropped legacy `event: completion` entirely and uses ONLY Messages-style, no chunks render → translator already covers Messages-style; will Just Work. Inverse (only legacy) also covered. Real risk = a THIRD format we haven't seen → add a new branch in `translateClaudeAiSSE`.
- **RISK A7**: if claude.ai sets cookies SameSite=Strict, the whole bridge architecture FAILS (credentialed cross-origin fetch from extension SW won't send cookies). Mitigation = re-evaluate Phase 9 (deferred to user — surfacing as a hard kill switch in 09-CAPTURE).
- **RISK A8**: if `GET /api/organizations` requires auth not provided by cookies (e.g., Bearer token from a separate endpoint), `getOrgId()` returns 401 — fix = pre-flight call to whatever endpoint mints the token (likely visible in DevTools by inspecting other XHRs at claude.ai page load).
- **RISK A10**: if claude.ai rejects nil UUID parent_message_uuid for first message, conversation creation flow needs an extra step to read the root message UUID returned by `POST /api/organizations/{org_id}/chat_conversations`. `createConversation()` already returns the conv response — extending it to also expose `root_message_uuid` is a 3-line change.

---

## Headers observed/sent — current canonical list

These are the headers `chrome-extension/claude_ai_client.js` sends on the completion POST.

| Header | Value | Notes |
|--------|-------|-------|
| `Content-Type` | `application/json` | Standard |
| `Accept` | `text/event-stream, text/event-stream` | Matches RESEARCH §Pattern 4; weird duplication is observed in pre-2024 captures, harmless |
| `Accept-Language` | `en-US,en;q=0.5` | Mirror what claude.ai web UI sends |
| `Origin` | `https://claude.ai` | Required for credentialed fetch CORS check |
| `Referer` | `https://claude.ai/chat/{conv_uuid}` | Probably required by Cloudflare bot rules |
| `anthropic-client-platform` | `web_claude_ai` | A2 — RESEARCH evidence |
| `anthropic-client-version` | _omitted at code-time, add on capture_ | Some captures don't include it; if 401, paste the live value here and patch |

Cookies are sent automatically by `credentials: 'include'` — extension's `host_permissions` for `https://claude.ai/*` and `https://api.claude.ai/*` (declared in plan 09-03 manifest update) is what makes this work.

---

## Body shape — current canonical JSON keys (sorted)

```text
attachments         []
files               []
locale              "en-US"
model               <mapped via mapModel()>
parent_message_uuid "00000000-0000-4000-8000-000000000000" (nil UUID for first message — A10)
personalized_styles []
prompt              "[System]\n...\n\n[Human]\n...\n\n[Assistant]\n..." (collapsed turns)
rendering_mode      "messages"
sync_sources        []
timezone            <Intl.DateTimeFormat().resolvedOptions().timeZone, fallback "UTC">
tools               []
```

If the live capture shows extra keys (e.g., `personalized_styles_v2`, `parent_leaf_uuid`, `attachments_v2`), append them with sensible defaults in `openaiToClaudeAi()`.

---

## Decisions for plan 09-02

These are the LOCKED values plan 09-02 codes against. Bump `CLAUDE_AI_API_VERSION` on every change.

### Final endpoint URL templates

| Operation | Method | URL |
|-----------|--------|-----|
| List organizations | `GET` | `https://claude.ai/api/organizations` |
| Create conversation | `POST` | `https://claude.ai/api/organizations/{org_id}/chat_conversations` |
| Stream completion | `POST` | `https://api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` |

### Final header set sent from `handleClaude()` completion POST

```text
Content-Type: application/json
Accept: text/event-stream, text/event-stream
Accept-Language: en-US,en;q=0.5
Origin: https://claude.ai
Referer: https://claude.ai/chat/{conv_uuid}
anthropic-client-platform: web_claude_ai
```

(Cookies sent automatically via `credentials: 'include'`.)

### Final body key set (`openaiToClaudeAi()` output)

`prompt`, `parent_message_uuid`, `timezone`, `personalized_styles`, `locale`, `tools`, `attachments`, `files`, `sync_sources`, `rendering_mode`, `model`. 11 keys total.

### SSE translator branches needed

Both branches MUST be implemented in `translate_sse.js`:
1. **Legacy completion** — `event: completion` + `data: {"completion": "...", "stop_reason": null|"end_turn"}`
2. **Messages-style** — `event: content_block_delta` + `data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}` ; plus `event: message_stop` and `event: message_delta` (with `delta.stop_reason`) for end-of-message.

### `CLAUDE_AI_API_VERSION` value

```text
2026-05-capture
```

This is the version string baked into `chrome-extension/translate_sse.js`. Bump on every observed format change. Format: `YYYY-MM-capture`.

---

## Divergence Patches

(Empty at plan-02 ship time. Each row added here corresponds to a live capture run that diverged from the contract above. Format: date | A# | what diverged | patch applied | new CLAUDE_AI_API_VERSION.)

| Date | A# | Diverged | Patch file | New version |
|------|----|----------|------------|-------------|
| —    | —  | —        | —          | —           |

---

## User action required for UAT (09-06)

1. Open Chrome → `https://claude.ai` → sign in (Pro/Max if available).
2. Open DevTools → Network tab → "Preserve log" + "Fetch/XHR" filter.
3. Start a new chat. Send `test capture for xbrain phase 9`.
4. Right-click the `.../completion` request → `Copy → Copy as cURL (bash)` → paste under `## Raw curl` above, redact tokens with `<REDACTED>`.
5. Right-click response → save first 10 SSE events under `## Raw SSE response`.
6. Right-click `GET /api/organizations` → paste JSON under `## Organizations response`.
7. Visit `chrome://settings/cookies/detail?site=claude.ai` → screenshot, check SameSite attribute on the main session cookie.
8. For each row of the Assumption verification table flag CONFIRMED or DIVERGED, fill `## Divergence Patches` for any DIVERGED, then run `node chrome-extension/tests/run_tests.mjs` to make sure the translator still passes.
9. If anything material changed, bump `CLAUDE_AI_API_VERSION` and commit.
