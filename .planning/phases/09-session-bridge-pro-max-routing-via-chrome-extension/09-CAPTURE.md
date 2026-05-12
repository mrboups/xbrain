# Phase 9 — claude.ai internal API capture

Date captured: **2026-05-12** (via Playwright-driven session on the maintainer's claude.ai Max account)
Browser: Chromium (Playwright bundled, identical engine class to user's Chrome ≥ 116)
Account type: **Max** (`Play Asbl` org, `rate_limit_tier: default_claude_max_5x`, `capabilities: ["claude_max", "chat"]`)

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
# Live 2026-05-12 capture — captured via fetch from claude.ai page context
# (Playwright). Cookies were attached by `credentials: 'include'` and are
# not introspectable from page JS (HttpOnly); they were not pasted here.
#
# Endpoint that returned 200 + text/event-stream (A1 DIVERGED — claude.ai
# host, NOT api.claude.ai):
#
# POST https://claude.ai/api/organizations/9338272c-03d5-40f7-83ad-28565dd04e89/chat_conversations/0168d778-ce35-4753-9fe7-027481f5e623/completion
#   Content-Type: application/json
#   Accept: text/event-stream, text/event-stream
#   Accept-Language: en-US,en;q=0.5
#   Origin: https://claude.ai
#   Referer: https://claude.ai/chat/0168d778-ce35-4753-9fe7-027481f5e623
#   anthropic-client-platform: web_claude_ai
#
#   {"prompt":"[Human]\nReply with EXACTLY: pong-xbrain",
#    "parent_message_uuid":"00000000-0000-4000-8000-000000000000",
#    "timezone":"Europe/Madrid",
#    "personalized_styles":[],"locale":"en-US","tools":[],
#    "attachments":[],"files":[],"sync_sources":[],
#    "rendering_mode":"messages","model":"claude-opus-4-7"}
#
# Counter-test: same body on api.claude.ai → `TypeError: Failed to fetch`
# (host either unresolvable or CORS-blocked for this path). Proof that
# the prior hypothesis A1 was wrong.
#
# Historical (pre-capture) hypothesis kept for reference:
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

Live 2026-05-12 capture — full stream returned by the request above. The
assistant replied `pong-xbrain` per the prompt instructions.

```text
event: message_start
data: {"type":"message_start","message":{"id":"chatcompl_013jt3jkBayhBm1LTmQEQFtk","type":"message","role":"assistant","model":"","parent_uuid":"019e1c4f-9a85-7173-a5eb-c973bda9e52b","uuid":"019e1c4f-9a85-7173-a5eb-c9741a6ef50b","content":[],"stop_reason":null,"stop_sequence":null,"stop_details":null,"trace_id":"6fdc5014283c5ad13ca76299b58c296b","request_id":"req_011CaxjK3sm5JC81KMsxt1T2"},"discarded_parent_message_uuid":null}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"start_timestamp":"2026-05-12T13:10:41.067802Z","stop_timestamp":null,"flags":null,"type":"text","text":"","citations":[]}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" p"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ong-xbrain"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0,"stop_timestamp":"2026-05-12T13:10:41.178968Z"}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null,"stop_details":null}}

event: message_limit
data: {"type":"message_limit","message_limit":{"type":"within_limit","resetsAt":null,"remaining":null,"perModelLimit":null,"representativeClaim":"five_hour","overageDisabledReason":"org_level_disabled","overageInUse":false,"windows":{"5h":{"status":"within_limit","resets_at":1778593800,"utilization":0.09},"7d":{"status":"within_limit","resets_at":1779001200,"utilization":0.33}}}}

event: message_stop
data: {"type":"message_stop"}
```

Observations:
- **Messages-style ONLY** — no `event: completion` legacy event in this capture.
  The translator's legacy branch (`if ("completion" in evt.data)`) is therefore
  dead code on the current claude.ai server build but kept for resilience.
- **`event: message_limit`** is a new event not in our pre-capture research.
  The translator drops it cleanly via `if (!text && !finish) return null`,
  so no special handling required. Useful debugging signal if surfaced via
  `console.debug` in future versions.
- `message_start.message.model = ""` (empty string) — model name not echoed back.
  Caller already passes the canonical `model` argument to
  `translateClaudeAiSSE`, so this is harmless.
- The first content delta is `" p"` (leading space) — translator concatenates
  deltas verbatim, preserving Anthropic's whitespace.

## Organizations response

Live 2026-05-12 capture of `GET https://claude.ai/api/organizations`:

```json
[
  {
    "id": 1508779,
    "uuid": "9338272c-03d5-40f7-83ad-28565dd04e89",
    "name": "Play Asbl",
    "settings": { "...": "(40+ feature flags, omitted)" },
    "capabilities": ["claude_max", "chat"],
    "parent_organization_uuid": null,
    "rate_limit_tier": "default_claude_max_5x",
    "billing_type": "stripe_subscription",
    "free_credits_status": "available",
    "data_retention": "default",
    "merchant_of_record": "anthropic",
    "created_at": "2023-09-04T18:02:13.149911Z"
  }
]
```

Both `id` (integer) AND `uuid` (string) present — the `uuid` form is what the
completion endpoint expects in its path. `getOrgId()` reads `[0].uuid` first
and falls back to `[0].id` per A8 → confirmed working.

---

## Assumption verification table

CONFIRMED = matches live capture / RESEARCH-cited public OSS evidence.
DIVERGED  = live capture contradicts the assumption — patch noted in `## Divergence Patches`.
N/A       = not testable from a single DevTools session (validated elsewhere).
PENDING   = live capture not yet performed; current implementation assumes "expected" status.

| # | Assumption | Status | Evidence line |
|---|------------|--------|---------------|
| A1 | Endpoint URL is `POST https://api.claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` | **DIVERGED** — endpoint lives on `claude.ai`, not `api.claude.ai`. Patched 2026-05-12 in `claude_ai_client.js`. | Live capture 2026-05-12: `https://claude.ai/api/.../completion` → 200 + text/event-stream ; `https://api.claude.ai/api/.../completion` → TypeError: Failed to fetch |
| A2 | `anthropic-client-platform: web_claude_ai` header is sent by the web UI | CONFIRMED | Live capture 2026-05-12: request with header `anthropic-client-platform: web_claude_ai` returned 200 + valid SSE stream |
| A3 | SSE event names — legacy `event: completion` AND/OR Messages-style `event: content_block_delta` / `message_stop` / `message_delta` | **PARTIALLY DIVERGED** — Messages-style ONLY observed (no legacy events), plus an undocumented `event: message_limit` event. Translator handles correctly (legacy branch becomes dead code, message_limit silently dropped). | Live capture 2026-05-12 SSE response : `message_start`, `content_block_start`, `content_block_delta` ×2, `content_block_stop`, `message_delta` (with `stop_reason: end_turn`), `message_limit`, `message_stop` |
| A4 | LibreChat sends apiKey as `Authorization: Bearer <key>` to baseURL | N/A — verified in plan 09-05 | LibreChat BYOK pattern, not testable from claude.ai capture |
| A5 | LibreChat encrypts user-pasted keys AES-256-CBC with CREDS_KEY/CREDS_IV | N/A — out of scope | LibreChat security docs, not testable here |
| A6 | `CLAUDE_AI_API_VERSION` rolling string is enough for change tracking | CONFIRMED | Bumped `2026-05-capture` → `2026-05-12-capture-v2` in `translate_sse.js` after the 2026-05-12 capture |
| A7 | claude.ai cookies are SameSite=Lax so credentialed fetch from SW works | **PENDING UAT** — empirical signals positive (credentialed fetch from claude.ai page succeeded). Definitive test requires extension reload + WS register flow on user's Chrome. | Cookie list from `document.cookie`: 14 non-HttpOnly cookies (`anthropic-device-id`, `sessionKeyLC`, `lastActiveOrg`, …). Main session cookie likely HttpOnly + not introspectable from page JS. MV3 `host_permissions` grants 1P treatment, expected to bypass SameSite=Lax. Hard kill switch only if SameSite=Strict — no observed evidence of that. |
| A8 | `GET /api/organizations` returns an array with `uuid` field (sometimes `id`) | CONFIRMED | Live capture 2026-05-12: response `[{ id: 1508779, uuid: "9338272c-03d5-…", name: "Play Asbl", capabilities: ["claude_max","chat"], rate_limit_tier: "default_claude_max_5x", … }]`. `getOrgId()` `[0].uuid` works. |
| A9 | TLS fingerprint not currently checked beyond Cloudflare baseline | N/A — operational risk | Cannot verify from a single Chrome session ; risk accepted, Pattern 5 monitoring covers detection |
| A10 | `parent_message_uuid` accepts the nil UUID `00000000-0000-4000-8000-000000000000` for first message in a conversation | CONFIRMED | Live capture 2026-05-12: POST body `{parent_message_uuid: "00000000-0000-4000-8000-000000000000", …}` → 200 with `message_start.parent_uuid: "019e1c4f-9a85-7173-a5eb-c973bda9e52b"` (server assigned a real UUID, the nil placeholder was accepted) |

10 rows total. After 2026-05-12 live capture: 6 CONFIRMED, 1 DIVERGED (A1, patched), 1 PARTIALLY DIVERGED (A3, translator handles), 1 PENDING UAT (A7, extension-level), 1 N/A (A4/A5/A9 = operational, off-band).

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
| Stream completion | `POST` | `https://claude.ai/api/organizations/{org_id}/chat_conversations/{conv_uuid}/completion` (was `api.claude.ai`, patched 2026-05-12 — A1 DIVERGED) |

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
2026-05-12-capture-v2
```

Bumped 2026-05-12 from `2026-05-capture` after live capture surfaced A1 DIVERGED.
Format: `YYYY-MM-DD-capture[-vN]`. Baked into `chrome-extension/translate_sse.js`.

---

## Divergence Patches

(Empty at plan-02 ship time. Each row added here corresponds to a live capture run that diverged from the contract above. Format: date | A# | what diverged | patch applied | new CLAUDE_AI_API_VERSION.)

| Date       | A# | Diverged                                                                                                          | Patch file                                            | New version             |
|------------|----|-------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------|-------------------------|
| 2026-05-12 | A1 | Completion endpoint hostname: was `api.claude.ai`, actually `claude.ai`. `api.claude.ai` returns `Failed to fetch`. | `chrome-extension/claude_ai_client.js` `COMPLETION_URL` template (removed `api.` subdomain) | `2026-05-12-capture-v2` |
| 2026-05-12 | A3 | Only Messages-style SSE observed (no `event: completion`). New event `event: message_limit` appears in stream.    | No code change — legacy branch becomes dead code; `message_limit` is silently dropped by translator's null-guard. Comment added in `translate_sse.js`. | `2026-05-12-capture-v2` |

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
