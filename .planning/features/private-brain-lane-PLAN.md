# The brain tag — a composer lane that stays out of the team chat

**Status:** planned, not started. Backend half is the security-critical part.
**Source:** mapped and planned 2026-08-06 by a 4-agent Opus workflow
(`wf_b98ecd91-f15`) against the live code, from the 2026-08-05 backlog entry
"A private lane to the brain".

## The locked decision (unchanged)

A note sent through this tag lands in the **team's** brain and **every member can
recall it**. The tag governs the chat surface only. The honest description is
"this does not clutter the chat", never "this is private".

## Summary

A new composer tag marks a message as "not in the chat". The server, not the client, is the whole of the control.

ONE new column carries it: `team_messages.private_to_user_id` (nullable FK to users). NULL = the team sees it, which is every row that exists today, so the migration changes nothing retroactively. Non-NULL = only that person sees it in any chat or brain-monitor surface. It is one column rather than a `visibility` enum because a `visibility='private'` value names no owner, and the agent's reply row has `author_user_id = NULL` by construction (apps/memory-api/app/repos/team_messages.py:59) — so a second column would be needed anyway, and two fields that mean one thing disagree the first time either changes. This deviates from the backlog's wording ("the visibility field carries the distinction") and is the one thing to confirm before the migration lands.

The publish path: `post_team_message` picks `user:<author sub>` instead of `team:<team_id>` at its single publish site (apps/memory-api/app/routes/team_chat.py:344-349). This is the assertion that matters most — the Centrifugo `team` namespace is `history_size: 100`, `history_ttl: "604800s"`, `force_recovery: true` (infrastructure/centrifugo/config.json:12-21), so one wrong publish is replayed to every member for seven days and no hotfix undoes it. The `user` namespace is 50 frames / 24h and has no forced recovery, which is why the private lane cannot lean on socket recovery for history.

The read path: the predicate `(private_to_user_id IS NULL OR private_to_user_id = :viewer)` goes into the four queries in repos/team_messages.py and into the `v_brain_events` SQL view, as a REQUIRED keyword argument with no default — a caller that forgets it fails loudly instead of returning everything. Eleven read surfaces are covered; three are named as deliberately NOT covered (superadmin count aggregates, the janitor purge, the admin wipe) because filtering those makes the dashboard lie and leaves private rows un-purged past 30 days.

The agent's answer inherits the question. `_do_handle` already loads the triggering row (team_chat_agent.py:567); its `private_to_user_id` becomes one `channel` variable and one `private_to` variable used at all five publish sites and at the insert. Without this the question is invisible and the reply is public, which reveals the question in outline.

Two things the mapping got right and are worth restating: `PostMessageBody` is `extra="forbid"` (team_chat.py:275), so a client shipped ahead of the server 422s on EVERY send, not just private ones — the server deploys first, always; and the `user:` channel is cross-team by construction, so the five agent stream frames must start carrying `team_id` or a private note in team A paints into team B's open thread.

The note still lands in the team's brain, unchanged, at team_chat.py:330 → brain_ingest.py:67 with `visibility=Visibility.TEAM`. That is the locked decision and it gets a test that fails if someone later "helpfully" filters it.

## Verified against the repo before writing (2026-08-06)

- alembic head is **0033**, so 0034 is the next revision.
- `repos/team_messages.py` holds the `select(TeamMessage)` statements the
  predicate must reach.
- `team_messages` has **no `visibility` column at all** — the column with the
  `private`/`team`/`org`/`public` CHECK constraint lives on the *other* table,
  `messages` (`app/models/message.py:18`). The backlog's sentence about the
  tagging contract's `visibility` field carrying this distinction therefore has
  nothing to attach to on this table, which settles the plan's open question in
  favour of `private_to_user_id`.
- `author_user_id` is **nullable** (`app/models/team_message.py:48`) and an
  agent reply row has it NULL, so a `visibility` enum could never name the owner
  of a private answer on its own.

## Tasks

### Task 1 — Migration 0034 — the column, its index, and the v_brain_events view

**Files:**
- `apps/memory-api/alembic/versions/0034_team_message_private_lane.py`
- `apps/memory-api/app/models/team_message.py`
- `apps/memory-api/alembic/versions/0021_brain_events_media.py`
- `apps/memory-api/tests/test_migration_0034.py`

**Action:** New revision chaining from the current head `0033_team_agent_provider` (revision id at alembic/versions/0033_team_agent_provider.py:32-33 — nothing references it as a down_revision). ADD COLUMN `private_to_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL` to `team_messages`; nullable with no server_default, so every existing row keeps today's team-visible behaviour and no backfill runs. Mirror it on the ORM model next to `deleted_by` (models/team_message.py:93-95). Add `CREATE INDEX IF NOT EXISTS idx_team_messages_team_created_priv ON team_messages(team_id, created_at DESC) INCLUDE (private_to_user_id)` — the existing `idx_team_messages_team_created` (alembic 0015:55-58) cannot serve the new OR-predicate index-only; leave the old index in place (redundancy costs write throughput, not correctness). Then CREATE OR REPLACE the `v_brain_events` view: copy the FULL DDL from alembic 0021_brain_events_media.py:60-140 verbatim and APPEND one column — `tm.private_to_user_id` on the `team_message` branch (0021:86-99) and `NULL::UUID AS private_to_user_id` on the other six branches. A REPLACE can only append columns, never reorder or drop them, so the appended position is not a style choice. Additive and forward-only, no branch on the edition flag (asserted by tests/test_migration_editions.py). Test file follows tests/test_migration_0033.py's shape.

**Done:** `alembic upgrade head` runs clean on a database at 0033; `SELECT private_to_user_id FROM v_brain_events LIMIT 1` succeeds; every pre-existing team_messages row has NULL; tests/test_migration_editions.py still passes.

### Task 2 — The read predicate, in the one repo file that owns it

**Files:**
- `apps/memory-api/app/repos/team_messages.py`

**Action:** Four queries gain a REQUIRED keyword-only `viewer_user_id: UUID` and the predicate `or_(TeamMessage.private_to_user_id.is_(None), TeamMessage.private_to_user_id == viewer_user_id)`: `list_messages` (:74, stmt at :94-104), `get_recent_messages_chronological` (:261, stmt at :272-285), `get_live_message` (:109, stmt at :123-130), `count_unread_since` (:225, stmt at :247-257). No default value on the argument — a default is a filter that fails open, and this project has shipped a `blocked_at` bypass once. `count_unread_since` already excludes the caller's own rows via `author_user_id != exclude_user_id`, so the new clause only has to drop OTHER people's private rows. Both inserters gain `private_to_user_id: UUID | None = None`: `insert_user_message` (:19) and `insert_agent_message` (:42).

**Done:** `grep -n "select(TeamMessage)" apps/memory-api/app/repos/team_messages.py` returns no statement without the OR-clause, and every call site in the repo fails to import until it passes `viewer_user_id`.

### Task 3 — Accept the flag, insert it, publish it on the author's own channel

**Files:**
- `apps/memory-api/app/routes/team_chat.py`

**Action:** `PostMessageBody` (:274-278) gains `private: bool = False`. It stays `extra="forbid"` — that is what makes a client-ahead-of-server skew a visible 422 rather than a note that silently goes out publicly. In `post_team_message` (:281): compute `private_to = user.id if body.private else None` and pass it into `insert_user_message` (:305). Compute the channel ONCE — `channel = f"user:{user.source_user_id}" if private_to else f"team:{team_id}"` — and use that variable at the single publish site (:344-349). SKIP the entire `@`-mention web-push block (:385-417) when `private_to` is set: `web_push.build_mention_payload` puts `_preview(content)` on a teammate's lock screen (services/web_push.py:74-88) and that is a second wire no channel swap touches. `_serialize_message` (:150) adds `"private": bool(m.private_to_user_id)` — a boolean, never the owner UUID. Update `list_team_messages` (:232) to pass `viewer_user_id=user.id` into `tm_repo.list_messages` (:246). Leave the `brain_ingest.ingest_team_message` call (:330-341) exactly as it is — the locked decision.

**Done:** No literal `f"team:{team_id}"` survives inside `post_team_message`; posting with `private: true` records a publish on `user:<sub>` and none on `team:<id>`; a message containing `@someone` and `private: true` calls `web_push.send_to_user_bg` zero times.

### Task 4 — The agent's answer inherits the question — channel, row, and prompt window

**Files:**
- `apps/memory-api/app/services/team_chat_agent.py`

**Action:** In `_do_handle` (:553): the triggering row is already loaded at :567-571 — read `private_to = triggering_message.private_to_user_id` and derive `channel = f"user:{triggering_user_sub}" if private_to else f"team:{team_id}"` once, at the top. Use `channel` at all five publish sites: `agent_stream_start` (:648-657), `agent_stream_chunk` on the promax path (:676-683), `agent_stream_chunk` on the fallback-key path (:697-704), `agent_stream_error` (:764-786), `agent_stream_end` (:851-859). Add `"team_id": str(team_id)` to all five frames — they carry only message_id/agent_name/provider/routed_via today, and the `user:` channel is cross-team, so without it a private answer in team A renders into team B's open thread. Pass `private_to_user_id=private_to` into `insert_agent_message` (:830-843). The raw `UPDATE team_messages SET id = :new_id` (:844-847) does not touch the column — confirm, do not change. Pass `viewer_user_id=triggering_message.author_user_id` into `get_recent_messages_chronological` (:583-585): that column IS the summoner's user id and is already loaded, so no extra query. This one change also fixes `_recent_urls` (:599-602, :1066-1101), which scrapes URLs out of the same list and fetches those pages into the prompt.

**Done:** `grep -n 'team:{team_id}' apps/memory-api/app/services/team_chat_agent.py` returns nothing inside `_do_handle`; a private @agent turn records five frames, all on `user:<sub>`, all carrying `team_id`; the persisted reply row has the same `private_to_user_id` as its parent.

### Task 5 — Catch-me-up: fix the polarity and the missing-caller fallthrough

**Files:**
- `apps/memory-api/app/services/team_chat_agent.py`
- `apps/memory-api/app/routes/team_chat.py`

**Action:** `catch_me_up` (:1512) resolves the caller AFTER querying the window (:1558-1568) and, when the caller row is missing, keeps everyone's messages. With a private lane that becomes 'summarize other members' private notes to you'. Move the `select(User).where(User.source_user_id == caller_user_sub)` lookup ABOVE the `tm_repo.list_messages` call (:1550-1556), return early with a log line when it is None, and pass `viewer_user_id=caller.id`. The existing own-message filter stays as it is — combined with the repo predicate, no private row of any author can enter the window. In routes/team_chat.py pass `viewer_user_id=user.id` into both `count_unread_since` calls: the `/unread-summary` route (:949) and the `/catch-me-up` non-empty gate (:1000).

**Done:** A team where the only unread row is another member's private note reports `count: 0` from `/unread-summary` and returns `{"status": "nothing_to_summarize"}` from `/catch-me-up`, with `create_task` never called.

### Task 6 — agent-context-bundle needs an acting user before it can be filtered at all

**Files:**
- `apps/memory-api/app/routes/team_chat.py`

**Action:** `get_agent_context_bundle` (:1150-1194) authenticates a bridge JWT and returns the last 20 messages with NO user parameter — there is currently nothing to filter by. Add a REQUIRED query param `acting_user_sub: str`, resolve it to a `users.id` (404 on unknown), and pass it as `viewer_user_id` into `get_recent_messages_chronological` (:1180). Verified before choosing 'required': a repo-wide grep for `agent-context-bundle` hits only this file, a comment at routes/boards.py:27, and `.planning/` documents — apps/agent-runtime does not call it, so making the param required breaks no live caller. Optional would leave the one endpoint that dumps twenty messages behind a shared secret permanently unfiltered.

**Done:** The endpoint returns 422 without `acting_user_sub`; with member B's sub it omits member A's private rows from `last_messages`.

### Task 7 — Delete and star: 404 for a non-owner, and their frames follow the row

**Files:**
- `apps/memory-api/app/routes/team_chat.py`

**Action:** Both routes read through `get_live_message`, which now carries the viewer — pass `viewer_user_id=user.id` at the delete call (:553) and the star call (:771). A non-owner then gets the same 404 the wrong-team case gets, with no existence oracle, and the star path stops being a membership-only write on a row the caller may not see (it currently promotes the linked brain items and publishes a frame). Route the two publishes to the owner's channel when the row is private: `message_deleted` (:672-681) and `message_starred` (:863-873). A star or deletion frame naming a message id on `team:` that no teammate can see is the outline leak in miniature.

**Done:** Member B starring or deleting member A's private message id gets 404 and zero Centrifugo frames on any channel; member A starring their own private message produces one frame on `user:<A>` and none on `team:`.

### Task 8 — Brain Monitor — the second, searchable, member-facing read of every message

**Files:**
- `apps/memory-api/app/routes/brain.py`
- `apps/memory-api/app/repos/brain.py`
- `apps/memory-api/app/routes/admin_brain.py`

**Action:** This is the surface most likely to be forgotten: `v_brain_events` exposes `LEFT(tm.content, 200) AS preview` for every team message, `_build_list_query` (routes/brain.py:264) serves it to any non-blocked member via `get_team_scope` (deps.py:386), and it supports `AND preview ILIKE :q_pattern` (:313) — a teammate can grep everyone's notes. Give `_build_list_query` a required `viewer_user_id: UUID` and append `AND (private_to_user_id IS NULL OR private_to_user_id = :viewer)`. Both callers pass it: `list_brain_events` (:179, call at :235) must add a `get_current_principal` dependency (the route has session + team_scope only today) and pass the caller's id; `events_drilldown` (admin_brain.py:98, call at :167) passes the superadmin's own id, so private rows stay hidden cross-team too. Give `fetch_event_row` (repos/brain.py:75, SQL at :98-104) the same predicate and viewer argument, so PATCH (:427), DELETE (:493) and restore (:558) return 404 to a non-owner BEFORE `assert_can_edit_brain_event` (deps.py:512) would hand a team admin the row and its preview. Do NOT add the column to `BrainEventOut` (schemas/brain.py:26) — it is `extra="ignore"`, so the view column is dropped at serialization and the owner id never reaches the wire.

**Done:** As member B: `/v1/brain/events?entity_type=team_message` omits A's private row, and `?q=<a rare token from its content>` returns empty. `PATCH /v1/brain/events/team_message/{id}` as a team admin who is not the author returns 404 with no preview in the body.

### Task 9 — Name the paths that must stay unfiltered, and pin them with a test

**Files:**
- `apps/memory-api/app/services/brain_ingest.py`
- `apps/memory-api/app/services/team_context_cache.py`
- `apps/memory-api/app/repos/brain_metrics.py`
- `apps/brain-janitor/app/pg_purger.py`
- `apps/memory-api/app/routes/admin_wipe.py`
- `apps/memory-api/app/repos/merge.py`

**Action:** No code change — a comment on each, and one test that fails if someone later 'helpfully' adds a filter. The memory layer stays open by the locked decision: `brain_ingest.ingest_team_message` (:67, MemoryItem at :116-127) writes the note's full text as a `memory_item` at `visibility=Visibility.TEAM`, and `team_context_cache.get_team_memory_bundle` (:160-175) feeds it to every member's agent turn. The three that must not be filtered for correctness reasons: `repos/brain_metrics.py` (:37, :95-96, :190, :243) are superadmin COUNT aggregates — filtering makes the dashboard disagree with the database; `apps/brain-janitor/app/pg_purger.py:36-43` PURGE_TABLES and `routes/admin_wipe.py:96,:118` are retention machinery — a private row must still be purged at 30 days and wiped with its team; `repos/merge.py:35` documents team_messages as left as-is during identity merge.

**Done:** A test asserts member B CAN find member A's private-lane note through `/v1/brain/events?entity_type=memory_item` and through the agent's memory bundle. It is the locked decision, written down as an executable claim.

### Task 10 — The server-side isolation test — one file, one control case

**Files:**
- `apps/memory-api/tests/test_private_lane_isolation.py`

**Action:** New integration test against real Postgres, modelled on tests/test_message_star_gate.py (`_install_principal` helper, `pytest.mark.integration`). Two members of ONE team plus a superadmin. Monkeypatch `centrifugo_client.publish` to record every (channel, data) pair, `web_push.send_to_user_bg`, and `team_chat_agent._stream_via_fallback_provider` to capture the prompt. Full assertion list in the isolation_test field. The control case is not optional: the same message sent WITHOUT `private: true` must be visible to member B on every path and must reach `team:<team_id>` on the wire — without it, all the isolation assertions pass on a build that silently drops the message.

**Done:** `cd apps/memory-api && pytest tests/test_private_lane_isolation.py -v` is green under Docker, and reverting any single line from tasks 2-8 turns exactly one named assertion red.

### Task 11 — Client: teach the user channel to render a message, before anything can send one

**Files:**
- `packages/chat-core/publication.js`
- `packages/chat-core/render.js`
- `packages/chat-core/brain_tag.js`
- `app-site/app/chat.js`
- `chrome-extension/popup.js`
- `chrome-extension/tests/test_chat_core_realtime.mjs`

**Action:** Today a `message` frame on `user:` renders NOWHERE: the PWA returns on anything but `open_url` (app-site/app/chat.js:1117-1118) and the extension on anything but catchup/open_url (chrome-extension/popup.js:836). Both `handleUserPublication` functions must delegate `message`, `agent_stream_start/chunk/end/error` to the SAME `createPublicationRouter` instance the team channel uses (wired at popup.js:323-324 and chat.js:1213-1214) — not a second renderer, or the two drift. GUARD FIRST: drop any delegated frame whose `team_id` is not the open team. `user:` is cross-team by construction and `route()` (realtime.js:332-341) does not filter it; `_serialize_message` carries `team_id` (team_chat.py:178) and task 4 adds it to the agent frames. In render.js, `buildBubbleNode` (:344) gets a 'not in the chat' marker beside `.xb-msg-provenance` (:405-411), driven by the boolean `msg.private`. New `packages/chat-core/brain_tag.js` holds the label strings, following the exported-constants precedent in message_menu.js:53-72. EDIT ONLY packages/chat-core — chrome-extension/chat_core/ and app-site/app/chat_core/ are generated byte-identical copies; run `make sync-chat-core` and `make check-chat-core` (Makefile:50-56). Add realtime tests in the shape of the two existing isolation tests at test_chat_core_realtime.mjs:604-632: a `message` on `user:<sub>` reaches onUserPublication and never onTeamPublication; a private frame carrying another team's `team_id` renders nothing.

**Done:** `make check-client` passes; a private message published on the author's channel renders as a marked bubble on their second device; the same frame carrying a foreign team_id renders nothing.

### Task 12 — Client: the tag, and the two contract tests it breaks by design

**Files:**
- `chrome-extension/popup.html`
- `chrome-extension/popup.js`
- `chrome-extension/popup.css`
- `app-site/app/index.html`
- `app-site/app/chat.js`
- `app-site/app/app.css`
- `chrome-extension/tests/test_popup_contract.mjs`
- `chrome-extension/tests/test_pwa_chat.mjs`

**Action:** Add `#btn-brain` as the last child of `.xb-composer-pill` (after `#btn-send`) in both composers — chrome-extension/popup.html:190-222 and app-site/app/index.html:184-216 — copying `#btn-agent`'s exact shape: `type="button"`, `class="xb-icon-btn"`, `aria-pressed="false"`, `data-state="off"`, inline 15px SVG, no emoji. Move `#btn-agent` to the far left. `setBrainArmed()` mirrors `setAgentArmed()` (popup.js:1228-1234, chat.js:979-985): writes BOTH `dataset.state` and `aria-pressed`, in-memory only. The two toggles are INDEPENDENT and may be armed together — that combination is exactly 'ask the brain a question without cluttering the chat', and it needs no new mechanism because the agent tag rewrites text (chat_stream.js:264-270) while the brain tag sets a field. `sendMessage` sends `{ content, private: state.brainArmed }` at popup.js:1251 and chat.js:1010; disarm on success only, like the agent toggle. Disarm the brain tag whenever a file is picked: `uploadFile` posts its own message and the blob stays reachable by item_id through `/v1/media/{id}/raw` (routes/media.py:298-310) whatever the message row says, so v1 does not apply the tag to a media send. REWRITE the two tests that hard-pin `ids.slice(-2) === ["btn-agent","btn-send"]` (test_popup_contract.mjs:833-846 and test_pwa_chat.mjs:474-501) to pin the new order — `ids.slice(-2) === ["btn-send","btn-brain"]` and `ids[0] === "btn-agent"`. They exist to catch a control silently dropped between two others; rewrite them, never delete them. The 'no parallel flag' tests (test_popup_contract.mjs:888-904) ban the literals summon/to_agent/toAgent/is_agent/mention_agent — `private` is none of those and is not a second summon authority, so their stated principle holds.

**Done:** `make check-client` passes with the rewritten order assertions; arming the tag and sending posts `private: true`; picking a file disarms it.

### Task 13 — The wording, its first-use sheet, and the agent's own product knowledge

**Files:**
- `packages/chat-core/brain_tag.js`
- `packages/chat-core/platform.js`
- `chrome-extension/popup.html`
- `app-site/app/index.html`
- `apps/memory-api/app/knowledge/xbrain_product_kb.md`

**Action:** Exact strings in the wording field. They live as exported constants in packages/chat-core/brain_tag.js so both surfaces read one copy — the precedent is the delete-confirm block at message_menu.js:53-72, whose header states this exact principle (copy that oversells makes people avoid what they need; copy that undersells lets them believe they removed something they had not). The sheet shows once, on first arming. NO seen-once mechanism exists today: every dismissal in this codebase is in-memory and per-visit (popup.js:91-97, chat_stream.js:507). The flag must persist through the storage shim in packages/chat-core/platform.js:16-42 — chat-core is forbidden from touching localStorage or chrome.storage directly. Also update apps/memory-api/app/knowledge/xbrain_product_kb.md:164-184, which today describes the composer's + and @agent and knows nothing of a brain tag: without it, someone asks the agent 'can the team see this?' and gets the pre-tag answer in the product's own voice.

**Done:** The tag's title, the sheet, and the KB all say the same thing: the team's brain still learns it and teammates can still ask about it. Neither the word 'private' nor the word 'secret' appears in any user-facing string.

### Task 14 — Deploy in the only order that works

**Files:**
- `apps/memory-api/app/routes/team_chat.py`
- `chrome-extension/manifest.json`

**Action:** memory-api plus migration 0034 to the VM FIRST (alembic auto-applies at boot). Then the PWA to Firebase. Then the extension, which needs a manual reload by each user and ships as version 1.3.1 with a fixed unpacked-install key (manifest.json:2-5). `PostMessageBody` is `extra="forbid"` (team_chat.py:275), so a client that reaches a server without the field 422s on EVERY send — a total send outage, not a degraded feature. Bump the extension version. Old installed clients keep working unchanged: they never send `private`, so they never receive a private frame either.

**Done:** The VM reports the new schema before either frontend ships; a pre-update extension still sends and receives ordinary team messages normally.


## The isolation test

One file, `apps/memory-api/tests/test_private_lane_isolation.py`, real route + real Postgres (the shape of tests/test_message_star_gate.py, `pytest.mark.integration`). Setup: members A and B in ONE team (B is a team admin, so the admin-privilege paths are exercised), plus a superadmin. `centrifugo_client.publish` is monkeypatched to record every (channel, data); so are `web_push.send_to_user_bg` and `team_chat_agent._stream_via_fallback_provider` (the latter captures `chat_history_block`). A posts one message with `private: true` containing a rare token and an `@B` mention and an @agent mention, so the question, the reply, the push and the prompt are all produced by one act.

ON THE WIRE — the assertion that matters most, because the `team` namespace keeps 100 frames for 7 days with force_recovery (infrastructure/centrifugo/config.json:12-21), so a leak here is not transient:
1. No recorded channel equals `team:<team_id>` for the question frame or for any of the five agent frames.
2. Every one of those six channels equals exactly `user:<A.source_user_id>`.
3. All five agent frames carry `team_id` (without it the client cannot tell team A's private answer from team B's).

THROUGH EVERY READ PATH — each is a separate surface; passing one is not passing another:
4. `GET /v1/teams/{id}/messages` as B: neither the question id nor the agent reply id appears. As A: both do.
5. `GET /v1/brain/events?entity_type=team_message` as B: absent. `?q=<rare token>` as B: empty (this is the ILIKE preview search at routes/brain.py:313 — the surface that lets a teammate grep everyone's notes). As A: present.
6. `GET /v1/admin/brain/events?team_slug=...` as superadmin: absent.
7. `PATCH /v1/brain/events/team_message/{id}` as B (team admin): 404, and no `preview` anywhere in the response body — B must not reach the first 200 characters through the drill-down.
8. `PUT /v1/teams/{id}/messages/{mid}/star` as B: 404, and zero new recorded frames on any channel (no existence oracle, no side effect on A's linked brain items).
9. `DELETE /v1/teams/{id}/messages/{mid}` as B (team admin): 404.
10. `GET /v1/teams/{id}/unread-summary` as B: `count` is byte-identical before and after A's private send.
11. `POST /v1/teams/{id}/catch-me-up` as B with A's private note as the only thing in the window: returns `{"status": "nothing_to_summarize"}`, and `team_chat_agent.catch_me_up` is never scheduled.
12. AGENT PROMPT: B summons @agent afterwards; the captured `chat_history_block` does not contain A's rare token. This is the leak where the model quotes the note into the public thread on its own.
13. `GET /v1/teams/{id}/agent-context-bundle?acting_user_sub=<B>` with a bridge JWT: A's row absent from `last_messages`.
14. PUSH: `web_push.send_to_user_bg` was called zero times, despite the `@B` in the content — this is a write-path read of `body.content` (team_chat.py:385-417) that no repo filter can reach.

THE LOCKED DECISION, asserted so a later "fix" breaks loudly:
15. B CAN retrieve A's note through `/v1/brain/events?entity_type=memory_item` and it IS present in the agent's memory bundle. The note is in the team's brain by design.

THE CONTROL — without it every assertion above passes on a build that silently drops the message:
16. The identical message sent WITHOUT `private: true` is visible to B on 4, 5 and 10, publishes on `team:<team_id>`, and does fire the push.

## Wording

BUTTON (#btn-brain, identical in chrome-extension/popup.html and app-site/app/index.html):
  title="Keep this out of the team chat — the team's brain still learns it"
  aria-label="Keep this out of the team chat"

FIRST-USE SHEET (shown once, the first time the tag is armed; strings exported from packages/chat-core/brain_tag.js):
  Title:  "Out of the chat. Not out of the brain."
  Body:   "Your teammates won't see this message, or the answer, in the chat.
           It still goes into the team's brain: anyone on the team can find it by
           searching, and the agent can quote it when they ask.
           This keeps the chat clear. It does not keep anything secret."
  Confirm: "Send it"
  Cancel:  "Cancel"
  Secondary: "Don't show this again"

BUBBLE MARKER (render.js, beside .xb-msg-provenance): "not in the chat"

WHY THIS WORDING. Three deliberate choices. First, the word "private" never appears — the note is team-retrievable and a label that says private is the product causing the mistake the backlog predicts (a password, a salary, an HR note behind that tag). Second, the second sentence is specific about HOW a teammate reaches it — searching, and the agent quoting it — because "the brain still learns it" is abstract enough that people nod at it without picturing a colleague reading the text back. Third, the last line names what the feature IS before what it is not, so the person still knows why they would use it. Existing copy already blurs this: popup.html:185 says "ask the team brain", render.js:436 labels agent replies "agent · from your brain", and index.html:193 offers "Talk to the team, or @agent" — "your brain" and "the team brain" are used interchangeably today, which is exactly the confusion this sheet has to end.

## Owner's decisions — 2026-08-06, locked

These answer the open questions below. Where they contradict the plan text
further down, these win.

**1. A superadmin CAN read brain-tag rows.** The plan proposed hiding them; the
owner ruled the other way. This is the better fit for the code that already
exists: `/v1/admin/brain/events` writes a synchronous audit row BEFORE the read
(`admin_brain.py:107-150`), precisely so superadmin content access is
accountable rather than invisible. So task 8 filters for ordinary members and
for team admins, and does NOT filter for a superadmin.

**2. Attachments are allowed with the tag.** The plan proposed "no for v1"
because `/v1/media/{item_id}/raw` authorises on Bearer + X-Team-Scope alone
(`media.py:298-310`) and the image token is minted from (item_id, team_scope)
with no message binding (`media_helpers.py:56`) — so hiding the message hides
the bubble and not the blob.

Under the locked decision that is not a hole, it is consistency. The tag never
promised secrecy: the note itself stays team-retrievable by design. A file that
is likewise reachable by the team makes the same promise as the text it came
with. Nothing extra to build — but it raises the stakes on the wording, which
must not imply the attachment is hidden from anyone either.

**3. The tag sits immediately to the RIGHT of the agent button**, not at the far
right of the composer. No text — a **closed-eye icon**. This supersedes the
backlog's "agent far-left / brain far-right" layout and dissolves the open
question about where `#btn-clip` goes: nothing else moves.

The icon carries the whole message on its own, so the tooltip and the first-use
sheet do more work than they would beside a word. A closed eye reads as "hidden"
— and hidden from the CHAT is true, while hidden from the TEAM is not. The
wording has to close that gap rather than lean on the icon.

**4. Every recalled fact must say who introduced it.** New requirement, wider
than this feature — it applies to recall generally, not only to brain-tag rows.

State of play, verified 2026-08-06:
- The data already exists. `brain_ingest` writes `metadata.author_sub` and
  `source = "team-chat:<author_sub>"` (`brain_ingest.py:105-127`).
- It already reaches the model. `_format_item` renders
  `- [LEVEL] (source) content` (`team_context_cache.py:119-130`), and the query
  already selects `source` (`:164`).
- What is missing is presentation, not plumbing: the source is a raw sub
  (`team-chat:github:mrboups`), not a readable name; nothing tells the model
  that the parenthetical means "who introduced this"; and for non-chat items
  (Drive, Granola, GitHub) `source` is the CONNECTOR, not a person — so the
  honest rendering is "who or what introduced it", and it must not invent a
  person where there is only an integration.

Sizing: small, but it touches the cached bundle, so any name resolution has to
stay deterministic or it busts the prompt cache on every rebuild. Its own task.


## Open questions for the owner

1. Where does the '+' attach button (#btn-clip) go once #btn-agent takes the far-left slot? It holds that position in both composers (popup.html:192-194, index.html:186-188) and the product KB calls it 'the control on every surface' (xbrain_product_kb.md:168-169). The backlog assigns agent-left and brain-right and says nothing about the third control.

2. Confirm the single-column design before the migration lands — it is the hardest thing to change afterwards. The backlog says the tagging contract's `visibility` field carries the distinction, but `team_messages` carries no contract field except `truth_level`, `visibility='private'` names no owner, and an agent reply row has `author_user_id = NULL` by construction (repos/team_messages.py:59). The plan uses one column, `private_to_user_id`.

3. Should a superadmin read private-lane rows through /v1/admin/brain/events? The plan hides them (viewer = the superadmin's own id). The synchronous audit-write-before-read at admin_brain.py:107-150 exists precisely so superadmin content access is accountable, which is an argument for the opposite call.

4. Should the tag work with an attachment? The plan says no for v1: /v1/media/{item_id}/raw authorizes on Bearer + X-Team-Scope alone (media.py:298-310) and the img token is minted from (item_id, team_scope) with no message binding (media_helpers.py:56), so filtering the message row hides the bubble and not the blob.

5. Where does the private answer render? The extension has an ephemeral author-only panel (#catchup-summary, popup.html:173-179); app-site/app/index.html has no catchup markup at all. The plan assumes a normal bubble marked 'not in the chat', because that is the only option the PWA has today without building the panel twice.

6. The strings below have not been through the `wr` + `verify-copy` pipeline this project requires for audience-facing text. Treat them as the specification of what must be said, not as final copy.
