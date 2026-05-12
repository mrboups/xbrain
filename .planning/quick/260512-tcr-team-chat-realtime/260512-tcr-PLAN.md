---
title: Team chat realtime — Centrifugo + memory-api + extension redesign
quick_id: 260512-tcr
slug: team-chat-realtime
mode: validate
date: 2026-05-12
status: pending-user-go
estimated_effort: 4-5 days
must_haves:
  truths:
    - "Open the extension → team chat is the primary UI, messages stream in realtime via WebSocket."
    - "Every message persists as a memory item in Postgres with the xbrain tagging contract (team_scope, truth_level=EPHEMERAL, source, etc.)."
    - "Typing '@claude' (or '@c' / '@cl') triggers Claude Sonnet 4.6 streaming a response back into the same channel, token by token."
    - "Claude responses route through the triggering user's Pro/Max session-bridge by default; fallback to Anthropic API key only when the user has no live bridge connection."
    - "Each Claude response includes team memory context: top 100 items truth_level>=WORKING, cached 5min in memory-api, sent with Anthropic prompt cache_control: ephemeral so the prefix gets reused across mentions."
    - "Each Claude bubble shows provenance: 'via Alice Pro/Max' or 'via team API' for cost transparency."
    - "Header shows N online via Centrifugo presence."
    - "Clip button (📎) sends current page/selection to xbrain via overlay (project + truth_level), defaults pre-filled from Settings."
    - "Top-right has ⚙️ Settings icon + 💬 Open LibreChat icon."
    - "Connection card (Connect xbrain / Link GitHub) appears only on first launch when no xbt_token is stored."
    - "History: 50 last messages on open, scroll-up loads +50."
    - "Output capped at 4k tokens per Claude response."
    - "Threads, edit/delete, typing indicators, reactions, attachments → all deferred to Phase 2."
  artifacts:
    - infrastructure/docker-compose.yml (new centrifugo service)
    - infrastructure/centrifugo/config.json (Centrifugo settings)
    - infrastructure/nginx/conf.d/60-centrifugo.conf (new vhost)
    - infrastructure/scripts/verify-team-chat.sh (smoke tests)
    - apps/memory-api/alembic/versions/00XX_team_messages.py
    - apps/memory-api/app/models/team_message.py
    - apps/memory-api/app/repos/team_messages.py
    - apps/memory-api/app/routes/team_chat.py
    - apps/memory-api/app/services/centrifugo_client.py
    - apps/memory-api/app/services/mention_detector.py
    - apps/memory-api/app/services/team_context_cache.py (NEW — 5min in-process cache for team memory bundle)
    - apps/memory-api/app/routes/team_chat.py (also exposes GET /v1/teams/{id}/agent-context-bundle for internal use by agent-runtime)
    - apps/agent-runtime/app/handlers/team_chat_mention.py (Pro/Max bridge routing + Anthropic fallback)
    - apps/session-bridge/app/routes_chat.py (extend auth to accept bridge JWTs with acting_user_sub claim)
    - chrome-extension/popup.html (full redesign to chat-first layout)
    - chrome-extension/popup.js (centrifuge-js client, chat rendering, scroll/history)
    - chrome-extension/popup.css (chat bubbles, header, presence dots)
    - chrome-extension/clip_overlay.html + .js (clip modal)
    - chrome-extension/settings.js (extend with clip defaults + default_model)
    - chrome-extension/options.html (extend with new settings)
    - chrome-extension/tests/test_mention_detector.mjs (regex unit tests)
  key_links:
    - https://centrifugal.dev/docs/getting-started/quickstart (Centrifugo docs)
    - https://github.com/centrifugal/centrifuge-js (JS client)
    - https://www.anthropic.com/news/prompt-caching (cache_control for agent context)
    - .planning/quick/260512-glk-link-github-account/260512-glk-SUMMARY.md (parent task — GitHub linking)
---

# Quick Task 260512-tcr — Team chat realtime

## Goal

Pivoter l'extension d'un Web Clipper avec Sessions vers un **team chat realtime** comme surface primaire, avec Claude qui peut intervenir en temps réel via mentions, et le clipper réduit à un bouton 📎 + overlay.

## Decisions (locked)

| # | Decision | Choix |
|---|----------|-------|
| 1 | Threads | Flat v1, threads Phase 2 |
| 2 | Mention syntax | Regex `\b@(claude|c|cl)\b` → Claude Sonnet 4.6 |
| 3 | Agent message persistence | Stored as memory item: `kind=agent`, `truth_level=EPHEMERAL`, `source=team-chat:claude-sonnet`, `team_scope=<team>` |
| 4 | Streaming granularity | Token-by-token chunks via Centrifugo publish |
| 5 | History | 50 last messages on open, scroll-up loads +50 |
| 6 | Presence | Included v1 — online dots next to user names + "N online" counter. Typing indicators → Phase 2 |
| 7 | Open LibreChat button | Opens `https://chat.grooveos.app/` root in new tab |
| 8 | Retention | Full retention now, archive later |
| 9 | Connection card | Appears only at first launch when no xbt_token stored |
| 10 | Default project / truth_level | Optional in Settings; overlay always opens but pre-filled |
| 11 | Default model for @claude | `claude-sonnet-4-6` (overridable in Settings) |
| 12 | Claude routing | **Pro/Max bridge by default** (consumes triggering user's Pro/Max quota, zero cost to xbrain). Fallback to Anthropic API key when no live bridge connection. Provenance surfaced in Claude bubble. |
| 13 | Context cache | memory-api builds + caches team memory bundle (top 100 items, truth_level>=WORKING) for 5min per team. Claude prompts use `cache_control: ephemeral` on the static block → Anthropic prompt cache hits give ~90% input cost reduction. |
| 14 | RAG scope v1 | Breadth-first — last 100 memory items truth_level>=WORKING (no vector search). Phase 2 swaps to Qdrant top-K by similarity. |
| 15 | Output cap | 4k tokens per Claude response (defensive — Sonnet 4.6 can produce 8k but capped to bound cost/latency) |
| 16 | Centrifugo version | v6 (latest) |

## Stack — what we add

| Component | Tech | License | Why |
|-----------|------|---------|-----|
| Realtime broker | **Centrifugo** | Apache 2.0 | Best-in-class WS pub/sub for self-hosted chat. JWT auth, presence + history API native, server-side publish for agent injection. ~50MB RAM. |
| JS client | **centrifuge-js** | Apache 2.0 | Official client, subscriptions + presence + reconnect logic built-in. |
| Persistence | **PostgreSQL** (existing) | — | Same DB as memory-api. New table `team_messages` with FK to teams + users. |
| Agent | **agent-runtime** + Anthropic SDK (existing) | — | Reuse the LangGraph runtime for mention handling. |

## Wireframe (locked)

```
┌─────────────────────────────────────────────────┐
│ 🧠 xbrain   [▼ Team A]   • 3 online    ⚙️  💬  │  ← header
├─────────────────────────────────────────────────┤
│                                                 │
│  🟢 Alice • 10:42                               │
│      Hey, what's the latest…                   │
│                                                 │
│  ⚫ Bob • 10:43                                 │
│      We shipped v1.2 today                     │
│                                                 │
│  🟢 You • 10:45                                 │
│      @claude pourquoi notre churn monte ?      │
│                                                 │
│  🤖 Claude (sonnet) • 10:45 ▍                  │  ← streaming
│      D'après votre memory team, le pic…        │
│                                                 │
├─────────────────────────────────────────────────┤
│  📎  [ Type a message…                ⏎    ]   │
└─────────────────────────────────────────────────┘
```

Overlay clip (on 📎 click):
```
┌──────────────────────────────────┐
│  📎 Send to xbrain               │
├──────────────────────────────────┤
│  Source: ● Page  ○ Selection     │
│  Project (optional)              │
│  [ fundraising              ]    │
│  Truth level                     │
│  [●EPHEMERAL][○WORKING][○VAL]    │
│  ☐ Use these defaults next time  │
│       [ Cancel ]  [ Send ]       │
└──────────────────────────────────┘
```

## Tasks

### Task 1 — Centrifugo provisioning

**Files**: `infrastructure/docker-compose.yml`, `infrastructure/centrifugo/config.json`, `infrastructure/nginx/conf.d/60-centrifugo.conf`, `.env` template, Cloudflare DNS

- Add `centrifugo` service to docker-compose: image `centrifugo/centrifugo:v5` (or v6 latest LTS), port 8000 internal
- `config.json`: HMAC JWT secret, API key for server-side publish, allowed_origins (extension + chat.grooveos.app)
- nginx vhost `centrifugo.grooveos.app` with WS upgrade headers + 86400s timeout (mirrors `50-bridge.conf`)
- Cloudflare DNS: A record `centrifugo` → VM IP, proxied + WebSockets toggle ON
- `.env.template`: `CENTRIFUGO_TOKEN_HMAC_SECRET`, `CENTRIFUGO_API_KEY`

**Verify**: `curl https://centrifugo.grooveos.app/health` → 200

### Task 2 — memory-api persistence + endpoints

**Files**: `apps/memory-api/alembic/versions/XXXX_team_messages.py`, `app/models/team_message.py`, `app/repos/team_messages.py`, `app/routes/team_chat.py`, `app/services/centrifugo_client.py`, `app/services/mention_detector.py`

- Migration: `team_messages` table with columns `id (UUID)`, `team_id (FK)`, `author_user_id (FK nullable)`, `agent_name (str nullable)`, `content (text)`, `created_at (timestamptz)`, `kind (enum: 'user'|'agent')`. Indexes: `(team_id, created_at DESC)` for history pagination, `(created_at)`.
- ORM model + repo with `insert_message`, `list_messages_before(team_id, before_id, limit)`, `count_recent(team_id)`.
- `centrifugo_client.py`: HTTP client for server-side `publish` API (HMAC API key) + `generate_user_token(user_sub, channels)` for JWT issuance.
- `mention_detector.py`: regex `\B@(claude|c|cl)\b` (case-insensitive) → returns `{"agent": "claude-sonnet-4-6"}` or None.
- Routes:
  - `POST /v1/me/centrifugo-token` → returns `{token, ws_url, channels}` (channels = `team:<team_id>` for each team user belongs to + `user:<sub>` for direct notifications)
  - `POST /v1/teams/{team_id}/messages` body `{content}` → INSERT row → publish to `team:<id>` → if mention detected, also enqueue agent task → return inserted row
  - `GET /v1/teams/{team_id}/messages?before=<msg_id>&limit=50` → paginated history (newest first)

**Verify**: pytest cases for mention_detector regex, message insert + publish, history pagination.

### Task 3 — agent-runtime mention handler (Pro/Max routing + cache)

**Files**: `apps/agent-runtime/app/handlers/team_chat_mention.py`, `apps/memory-api/app/services/team_context_cache.py`, `apps/session-bridge/app/routes_chat.py` (auth extension)

**3a. Routing strategy**:
1. Receive `{team_id, triggering_user_sub, triggering_message_id, agent_name}`
2. Check if `triggering_user_sub` has a live session-bridge WS connection (via bridge `/v1/healthz?has_socket=<sub>` or by querying memory-api `user_external_sessions` rows refreshed within 60s)
3. **Branch A — live Pro/Max bridge**: POST bridge `/v1/chat` with `Authorization: Bearer <bridge JWT signed BRIDGE_SHARED_SECRET, acting_user_sub=triggering_user_sub>` + the message + cached context. Bridge routes via the user's claude.ai WS. Cost = 0$ for xbrain, consumes the user's Pro/Max quota.
4. **Branch B — no live bridge**: fallback to direct Anthropic SDK using `settings.ANTHROPIC_API_KEY`. Cost goes to xbrain.
5. Persist message row with `routed_via: "user_promax"` or `"team_api"` for transparency.

**3b. Session-bridge auth extension** (~1h):
- `routes_chat.py` already accepts `Authorization: Bearer <xbt_>` from LibreChat. Add a second branch: if the Bearer token decodes as a JWT signed with `BRIDGE_SHARED_SECRET` and `scope=bridge` and has `acting_user_sub` claim → treat as "agent acting on behalf of <acting_user_sub>" and route to that user's WS pool entry.
- Mirrors the pattern in `memory_api_client._make_bridge_jwt` (already used for upsert direction).

**3c. Context bundle**:
- Call `memory-api/v1/teams/{id}/agent-context-bundle` → returns `{memory_block: str, last_messages: list}` (memory_block cached 5min by team_context_cache)
- Construct Anthropic system prompt:
  ```
  [SYSTEM] You are Claude, embedded in xbrain team chat for team <slug>. …
  [STATIC BLOCK A — cache_control: ephemeral]
    Team memory snapshot (top 100 items, truth_level >= WORKING):
    <memory_block>
  [DYNAMIC BLOCK B — fresh]
    Last 20 chat messages: <last_messages>
  [USER] <triggering_message.content>
  ```
- This prefix-matches across mentions → 90% input cost reduction within the 5min cache window.

**3d. Streaming**:
- Each chunk from Anthropic stream → publish to Centrifugo channel `team:<id>` with `{type: "stream_chunk", message_id, delta, agent_name}`
- Persist the full final content as ONE `team_messages` row with `kind=agent`, `agent_name=claude-sonnet-4-6`, `routed_via=...`, `metadata={token_usage}`
- Final publish: `{type: "stream_end", message_id, token_usage}`

**Verify**: integration test mocking Anthropic stream + bridge HTTP → assert Centrifugo publish + DB row + routed_via correctness.

### Task 3-bis — memory-api team context cache + bundle endpoint

**Files**: `apps/memory-api/app/services/team_context_cache.py`, route in `app/routes/team_chat.py`

- `TeamContextCache(ttl_s=300)` — in-process dict `{team_id: (expires_at_monotonic, bundle_str)}`
- `get_or_build(team_id, session)`:
  - Cache hit fresh → return cached
  - Else → query top 100 `memory_items WHERE team_scope=<slug> AND truth_level IN ('WORKING','VALIDATED','CANONICAL') ORDER BY created_at DESC` → format as markdown bullet list → cache + return
- Endpoint `GET /v1/teams/{id}/agent-context-bundle` (internal-only — only bridge JWT or local agent-runtime can hit it; reject external user calls): returns `{memory_block, last_messages: [...20 most recent team_messages]}`
- Auth: require `kind=bridge` (so agent-runtime signs a bridge JWT before calling)

**Verify**: pytest cases — fresh fetch, cache hit, cache miss after expiry, content truncation if memory items push past 100k tokens.

### Task 4 — Extension chat UI redesign

**Files**: `chrome-extension/popup.html` (FULL REWRITE), `popup.js` (substantial rewrite), `popup.css` (chat styles), `clip_overlay.html`, `clip_overlay.js`, `settings.js` (extend defaults), `options.html` (new settings rows), `manifest.json` (centrifuge-js bundled in `vendor/`)

- Vendor `centrifuge-js` (~30KB minified) into `vendor/centrifuge.min.js`
- popup.html restructure:
  - Header bar: team selector (existing logic, repositioned), `• N online`, ⚙️ icon, 💬 icon
  - Connection card (collapsible, shown only when `xbt_token` missing): re-uses current Connect xbrain + Link GitHub flow
  - Chat body: `<div id="chat-stream">` with auto-scroll-to-bottom on new messages, scroll-up loader
  - Footer: 📎 button + textarea + send button
- popup.js:
  - On load → fetch Centrifugo token via `POST /v1/me/centrifugo-token` → connect WS → subscribe `team:<current_team_id>` + presence
  - On new message arrival → append to chat-stream → auto-scroll
  - On stream_chunk → append to existing in-flight agent message bubble
  - On team selector change → unsubscribe old `team:X` → subscribe new
  - Scroll-up handler → GET history `before=<oldest_msg_id>` → prepend
  - 📎 click → open `clip_overlay.html` (chrome.windows.create) or inline modal → on confirm POST to `/v1/memory/upsert`
- popup.css: dark theme bubbles, presence dots, streaming cursor blink animation
- options.html: new section "Chat defaults" with project, truth_level, "skip overlay", default_model
- manifest.json: ensure `web_accessible_resources` includes vendor/

**Verify**: load extension manually → see chat, send message, mention @claude, watch streaming, scroll up to load history.

### Task 5 — Deploy + E2E UAT + artifacts

- Deploy Centrifugo + memory-api + agent-runtime to VM
- Add Cloudflare DNS record
- Run `verify-team-chat.sh` smoke script
- Manual UAT checklist:
  1. Open extension → connection card shown if not connected → connect → card disappears
  2. Send message → appears for me + (open extension as user B) appears for B
  3. Type @claude question → Claude streams response → both see it
  4. Switch team → chat switches to other team
  5. Scroll up → loads older messages
  6. Click 📎 → overlay → confirm → memory item created in xbrain (visible via `/v1/memory/search`)
  7. Click ⚙️ → settings page with chat defaults
  8. Click 💬 → opens chat.grooveos.app in new tab
- Write SUMMARY.md + STATE.md row + push

## Database schema (locked)

```sql
CREATE TABLE team_messages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id       uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  author_user_id uuid REFERENCES users(id) ON DELETE SET NULL,   -- null for agent messages
  agent_name    varchar(64),                                      -- e.g. "claude-sonnet-4-6"
  kind          varchar(16) NOT NULL CHECK (kind IN ('user', 'agent')),
  content       text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  -- Phase 2 fields, nullable for forward-compat:
  parent_message_id uuid,                                         -- threads (Phase 2)
  edited_at         timestamptz,                                  -- edit/delete (Phase 2)
  deleted_at        timestamptz
);
CREATE INDEX idx_team_messages_team_created ON team_messages (team_id, created_at DESC);
```

Constraint: `(kind='user' AND author_user_id NOT NULL) OR (kind='agent' AND agent_name NOT NULL)`.

## Centrifugo channels (locked)

| Channel | Subscribers | Publishers |
|---------|-------------|------------|
| `team:<team_id>` | All members of that team | memory-api (on new message) + agent-runtime (stream chunks) |
| `user:<user_sub>` | Just that user | memory-api (for direct notifications — Phase 2) |

JWT claims for connection: `sub`, `exp`, `channels` (allowed list).

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Centrifugo HMAC secret leaks → impersonation | Store in `.env`, never commit; rotate via Centrifugo config reload |
| Agent runaway response (>10k tokens) | Hard cap in agent-runtime to 4k output tokens for v1 |
| WS connection storms on team switch | Debounce subscribe/unsubscribe (200ms) |
| Browser CORS on Centrifugo WS | Centrifugo `allowed_origins` includes `chrome-extension://*` |
| Centrifugo restart loses presence | Acceptable — clients reconnect within 5s, presence rebuilds |
| Mention regex matches "@cloud" or "@claire" | Word-boundary regex `\b@(claude|c|cl)\b(?=\s|$)` — strict |

## Out-of-scope (Phase 2+)

- Threads (parent_message_id wired up + thread side-panel UI)
- Edit/delete messages (RPC + UI affordances)
- Typing indicators
- Reactions (emoji)
- File attachments via MinIO
- Multi-model mentions (`@gpt`, `@grok`, `@gemini`)
- Keyword triggers configurable per team (regex matchers in Settings)
- Archive old messages to MinIO (retention policy)
- Web app version (chat.grooveos.app/teams/X — non-extension surface)

## Order of execution (suggested waves)

| Wave | Tasks (parallelizable) | Blocking? |
|------|------------------------|-----------|
| 1 | Task 1 (Centrifugo infra) + Task 2 partial (migration + skeleton endpoints returning 501) | Blocks Wave 2 |
| 2 | Task 2 complete (endpoints + Centrifugo client + mention detector) + Task 3 (agent handler) | Blocks Wave 3 |
| 3 | Task 4 (extension UI) — depends on Task 2 endpoints existing | Blocks Wave 4 |
| 4 | Task 5 (deploy + UAT + artifacts) | Final |
