# Deferred Items — Phase 20 (extension-chat-shadcn)

Out-of-scope discoveries logged during execution. Do NOT fix inside the plan
that found them.

## From Plan 20-01

- **Pre-existing test failure in `chrome-extension/tests/test_chat_stream.mjs`**
  (3 assertions fail: `detectMentionClient: @claude matched`, `@c and @cl short
  aliases`, `case insensitive`). Present at the Plan 20-01 base commit
  (`9a376f4`) before any 20-01 change. Unrelated to the shadcn token / theme /
  contract work (it concerns `@claude` mention detection in `chat_stream.js`).
  Effect: `node chrome-extension/tests/run_tests.mjs` exits non-zero overall
  (8/9 files pass) independent of Plan 20-01. Left untouched per the executor
  scope boundary. Should be triaged separately.

## From Plan 20-03

- **Root cause found for the 3 pre-existing `detectMentionClient` failures**
  (same item as above; re-confirmed failing at the 20-03 base commit `3a68285`
  with no 20-03 change applied). `MENTION_RE` in `chat_stream.js` was rebranded
  to `@(grooveos|groove|gr|g)` while `test_chat_stream.mjs` still asserts the
  old `@claude` / `@c` / `@cl` aliases. The tests are stale, not the regex.
  Fix = update the three assertions to the current alias list (and decide
  whether `@agent`/`@chad` aliases belong in the client-side hint regex).
  Left untouched per the executor scope boundary.

- **Agent `metadata.sources` is never emitted by the backend.** Plan 20-03
  ships the `<details>` source rows + truth-level chips design-complete and
  renders them only when `metadata.sources` actually exists (it does not today),
  so no source rows appear in production yet. Populating per-source rows with
  real truth levels requires a `team_chat_agent.py` change (persist the retrieved
  bundle items alongside `memory_items`). Documented backend follow-up —
  threat register T-20-03-03 (disposition: accept).

- **Live agent replies never show their sources.** `agent_stream_start` renders
  the bubble by `message_id`, and the later persisted `message` frame carrying
  `metadata.memory_items` is dropped by the `data-msg-id` de-dupe in
  `renderMessage`. So the "N sources from the brain" disclosure only appears
  after a history reload, not on the live stream. Pre-existing behavior (the
  stream frames have never carried metadata); fixing it means merging metadata
  into an existing row instead of skipping the duplicate. Out of scope here.
