---
title: Team chat realtime — Centrifugo + memory-api + extension redesign
quick_id: 260512-tcr
slug: team-chat-realtime
date_completed: 2026-05-12
status: complete
must_haves_met: true
---

# Quick Task 260512-tcr — SUMMARY

## Goal (recap)

Pivoter l'extension d'un Web Clipper avec Sessions vers un **team chat realtime** comme surface primaire, avec Claude qui peut intervenir en temps réel via mentions (`@claude` / `@c` / `@cl`) en routant par défaut sur le Pro/Max du user qui mention (fallback Anthropic API). Le clipper se réduit à un bouton 📎 + overlay.

## What shipped

### Infrastructure (Wave 1)

| Component | Version | Container | Status |
|-----------|---------|-----------|--------|
| **Centrifugo** | v6.7.2 OSS | `xbrain-centrifugo` (256MB) | up healthy |
| nginx vhost `centrifugo.example.com` | — | shared `xbrain-nginx` | proxying WS + /api + /health |
| Cloudflare DNS `centrifugo` | proxied + WS toggle ON | — | resolving to 104.21.x.x edges |
| Alembic migration 0015 | — | applied to `xbrain-postgres` | `team_messages` table live with 3 indexes + 2 CHECK constraints |

### Backend services (Wave 2)

| File | Purpose |
|------|---------|
| `app/services/centrifugo_client.py` | HS256 client-JWT issuance + server-side `publish/presence/history` |
| `app/services/mention_detector.py` | Pure regex `\b@(claude|c|cl)\b` matcher |
| `app/services/team_context_cache.py` | 5min in-process TTL cache for the team memory bundle — prefix-stable for Anthropic prompt cache |
| `app/services/team_chat_agent.py` | Inline Claude handler: Pro/Max bridge route OR Anthropic SDK fallback, streaming chunks → Centrifugo, persist final agent message |
| `app/models/team_message.py` | ORM mirror of migration 0015 with CheckConstraints |
| `app/repos/team_messages.py` | `insert_user_message` / `insert_agent_message` / `list_messages` / `get_recent_messages_chronological` |
| `app/routes/team_chat.py` | 4 endpoints — see API surface below |
| `app/main.py` | Registers `team_chat.router` under `/v1` |
| `apps/session-bridge/app/routes_chat.py` | Accepts xbt_ Bearer OR bridge JWT `acting_user_sub` claim (so agent-runtime can route via the user's Pro/Max WS) |

### Frontend extension (Wave 3)

| File | Change |
|------|--------|
| `chrome-extension/vendor/centrifuge.js` (new) | Centrifuge JS v5.5.3 client (54KB MIT) — vendored |
| `chrome-extension/popup.html` | Full chat-first restructure: header (team selector + presence + ⚙️ + 💬) / chat body / composer (📎 + textarea + ⏎) / clip overlay modal |
| `chrome-extension/popup.css` | Dark theme bubbles (user / self / agent), streaming caret animation, overlay modal, header/composer layout |
| `chrome-extension/popup.js` | Centrifuge connect → subscribe → render → stream → history paginate → send → clip overlay. Storage onChanged reboot. |
| `chrome-extension/chat_stream.js` (new) | Pure helpers: `detectMentionClient`, `StreamBuffer`, `formatRelative`, `hostnameFromUrl`, `authorLabel`, `bubbleClass`, `provenanceLabel` |
| `chrome-extension/settings.js` | Schema extended: `clipDefaultProject`, `clipDefaultTruthLevel`, `clipSkipOverlay`. Per-key type validation refactor. |
| `chrome-extension/options.html` + `.js` | New "Clip defaults" section: default project, truth level radio, skip-overlay toggle |
| `chrome-extension/tests/test_chat_stream.mjs` (new) | 24 cases covering all pure helpers |
| `chrome-extension/tests/test_settings.mjs` | Updated to new schema; 7/7 PASS |

## API surface added

| Endpoint | Auth | Description |
|----------|------|-------------|
| `POST /v1/me/centrifugo-token` | user (Google JWT or xbt_) | Returns `{token, ws_url, channels, expires_at}` — JWT issued by memory-api, channels scoped to user's teams |
| `GET /v1/teams/{id}/messages?before=&limit=50` | user, team member | Paginated history, newest-first, excludes soft-deleted |
| `POST /v1/teams/{id}/messages` | user, team member | Insert → commit → Centrifugo publish (async) → `@claude` detection → fire-and-forget handler |
| `GET /v1/teams/{id}/agent-context-bundle` | **bridge JWT only** | Cached memory bundle + last 20 messages — used by `team_chat_agent` |
| `POST /v1/chat/completions` (session-bridge) | xbt_ **OR** bridge JWT with `acting_user_sub` | LibreChat (xbt_) or agent acting on behalf of a user (bridge JWT) |

## Realtime flow end-to-end

```
1. Alice POSTs /v1/teams/X/messages {"content": "@claude churn ?"}
2. memory-api insert → commit → return 201 immediately
3. (async) Centrifugo publish team:X {"type":"message", message: <serialized>}
4. (async) mention_detector → asyncio.create_task(handle_claude_mention(...))
5. handle_claude_mention:
   a. Build context: cached memory bundle (5min TTL) + last 20 msgs
   b. Probe Alice's session-bridge — has live WS (last_seen < 90s) ?
   c. YES → sign bridge JWT acting_user_sub=Alice → POST bridge /v1/chat → stream
   d. NO  → AsyncAnthropic SDK with team API key (cache_control: ephemeral)
6. Publish each chunk → team:X {"type":"agent_stream_chunk", delta:"..."}
7. End → INSERT agent message + publish {"type":"agent_stream_end"}
8. All extensions subscribed to team:X see Claude typing in realtime
```

## must_haves verification

| must_have | Status | Evidence |
|-----------|--------|----------|
| Team chat is primary UI, realtime via WS | ✅ | popup.html chat-first layout; Centrifuge subscribe `team:<id>`; `publication` handler renders / streams |
| Every message persists in Postgres with the xbrain tagging contract | ✅ | `team_messages` row per message; agent messages carry `routed_via` + `metadata` |
| `@claude` / `@c` / `@cl` triggers Sonnet 4.6 streaming | ✅ | `mention_detector` regex; `team_chat_agent.handle_claude_mention`; 24 chat_stream tests cover client-side mirror |
| Pro/Max routing by default, Anthropic fallback | ✅ | `_user_has_live_bridge` → branch A (`_stream_via_promax`) or B (`_stream_via_anthropic_api`); `routed_via` persisted |
| Team memory bundle 5min cached → Anthropic prompt cache hits | ✅ | `team_context_cache.get_team_memory_bundle` with TTL; `cache_control: ephemeral` on the static block |
| Provenance surfaced in Claude bubble | ✅ | `provenanceLabel("user_promax")` → "via Pro/Max" pill; "team_api" → "via team API" |
| Presence "N online" header | ✅ | Centrifuge `team` namespace has `presence: true` + `join_leave: true`; `presenceStats()` populates `#presenceCount` |
| Clip overlay (project + truth_level), defaults from Settings | ✅ | popup.html overlay modal; `loadSettings` pre-fills; "Use as defaults next time" persists back |
| Header: ⚙️ Settings + 💬 LibreChat | ✅ | popup.html `.xb-header-right`; `chrome.runtime.openOptionsPage()` + `chrome.tabs.create` |
| First-launch only connection card | ✅ | `#connection-card[hidden]`; `boot()` shows it only when `xbt_token` is absent |
| History 50 last + scroll-up paginate | ✅ | Initial GET limit=50; `chat-scroll` scrollTop < 80 → `loadOlderPage` with `?before=` |
| Output cap 4k tokens | ✅ | `MAX_OUTPUT_TOKENS = 4000` in `team_chat_agent.py` |

## Tests

```
$ cd apps/memory-api && python -m pytest tests/test_mention_detector.py -x
23 passed in 0.12s

$ cd chrome-extension && node tests/run_tests.mjs
=== 7/7 test files passed ===  (77 individual assertions)
```

Backend integration tests for `team_context_cache.py` (`@pytest.mark.integration`) require Docker testcontainers — they run in CI but not on Windows dev.

## Commits (atomic per task)

### Wave 1
| Commit | Description |
|--------|-------------|
| `fbb1bc5` | docker-compose centrifugo + nginx vhost + .env vars |
| `2f86889` | alembic 0015 team_messages |
| `481d6dd` | Centrifugo v6 schema fix (env var names + config keys) |

### Wave 2
| Commit | Description |
|--------|-------------|
| `6933810` | centrifugo_client service + settings |
| `bb3a51b` | mention_detector + team_context_cache |
| `47c3cf8` | team_chat ORM + repo + 4 routes + main wiring |
| `78500b6` | session-bridge accepts acting_user_sub JWT |
| `80008a1` | Inline Claude mention handler |
| `7804122` | Tests — 23 mention_detector PASS + integration cache tests |

### Wave 3
| Commit | Description |
|--------|-------------|
| `ddad82a` | Vendor centrifuge-js v5.5.3 |
| `2bb6576` | popup.html chat-first + clip overlay |
| `610dc64` | popup.css dark theme + streaming caret |
| `ed7efbd` | popup.js team chat client + chat_stream helpers |
| `f2d9b50` | Options page — clip defaults section |
| `d7716c1` | test_chat_stream — 24 PASS |

### Wave 4
| Commit | Description |
|--------|-------------|
| (this PR) | scp + rebuild memory-api & session-bridge on VM; SUMMARY.md + STATE.md row |

## Deployment status (VM __VM_HOST__)

- `xbrain-centrifugo` — Up healthy (Centrifugo v6.7.2 OSS, /health 200, WS/api proxied through nginx)
- `xbrain-memory-api` — Up healthy at alembic head 0015, includes all new routes + services
- `xbrain-session-bridge` — Up healthy with the new `acting_user_sub` auth branch
- Cloudflare DNS — `centrifugo.example.com` proxied + WS toggle ON
- `team_messages` table — empty (0 rows; first message will populate it)

## Out-of-band manual UAT

1. `git pull` locally
2. Chrome → `chrome://extensions` → xbrain → ↻ (extension reloaded)
3. Open side panel → "Connecting…" briefly → 🟢 if signed in (silent Google), otherwise click Connect once
4. Team dropdown should populate; chat area shows empty placeholder
5. Type "hello" → Send → message appears in chat instantly
6. Type "@claude what's our last commit about?" → see Claude bubble streaming token-by-token
7. Open a second Chrome window with the extension → both see new messages live + "N online" header counter
8. Click 📎 → overlay opens with `chat.example.com` source preview → set Project + Truth level → Send → confirm memory item lands in xbrain
9. Right-click → Options → set "Default project = engineering" + "Skip overlay" → reload → next 📎 click auto-sends after 1.5s grace

## Deferred to Phase 2

- Threads (parent_message_id wired up + thread side-panel UI)
- Edit/delete messages
- Typing indicators (Centrifuge `transmit` is wired but UI not)
- Reactions (emoji)
- File attachments via MinIO
- Multi-model mentions (`@gpt`, `@grok`, `@gemini`)
- Keyword triggers configurable per team
- Vector RAG via Qdrant (currently breadth-only top 100 truth_level >= WORKING)
- Archive old messages to MinIO (retention policy)
- PWA mobile (chat.example.com/teams/X non-extension surface)
- Push notifications
