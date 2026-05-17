# Phase 12 — Deferred items

Out-of-scope discoveries logged during execution. Not blockers for Phase 12.


## From Plan 12-08 execution (2026-05-17)

- **chrome-extension/tests/test_translate_sse.mjs** — fails with pre-existing
  bug in `translate_sse.js` (Phase 9 module). Unrelated to GitHub App migration.
  Triage: separate quick task.
- **chrome-extension/tests/test_ws_keepalive.mjs** — fails because
  `ws_keepalive.js` exports CommonJS while the test imports as ESM
  (Named export 'MAX_ATTEMPT' not found). Pre-existing Phase 9 mismatch.
  Triage: separate quick task. NOTE: background.js imports the same module
  via ESM (line 30-34) and runs fine in Chrome — only Node ESM is strict
  about this; Chrome's MV3 module loader is more permissive. So the
  extension itself works at runtime; only the Node test is broken.
