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
