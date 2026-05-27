# Phase 13 Helper Bug Fix Notes

**Commit:** 6af6f48  
**Files patched:** `infrastructure/scripts/test-phase13-cross-frontend.py`, `infrastructure/scripts/verify-phase13.sh`

## Summary

Fixed 3 test failures in the Phase 13 verify/helper scripts that caused tests (a), (g), and (h) to FAIL on the VM with `TEST_TEAM_SCOPE=aibrussels`.

All 3 failures shared the same root category: the scripts sent requests to endpoints whose auth contracts were incompatible with the bridge JWT they minted.

---

## Bug 1 — (a) team_chat_ingest: bridge JWT rejected by user-only endpoint

**File:** `test-phase13-cross-frontend.py` — `test_a_team_chat_ingest()`

**Root cause:** The test POSTed to `/v1/teams/{team_id}/messages` using a bridge JWT (`kind=bridge`). The endpoint's `_require_user_principal()` guard explicitly rejects anything that is not `kind=user` or `kind=user_api_token`, returning `HTTP 403 "user-only endpoint"`. This was always going to return 403 on the bridge credential — the test was structurally broken from the start.

**Fix (Option A from brief):** Replaced the team-chat POST with `POST /v1/brain/ingest` using `source="team-chat:test-phase13-a"`. The real team-chat endpoint calls `brain_ingest.ingest_team_message()` internally anyway — both paths land identical `memory_items` rows with `source='team-chat:*'`. The fix uses a deterministic `idempotency_key="verify-phase13-test-a"` (UUID5 deterministic) so repeated runs are idempotent and cleanup is reliable.

**Assertions preserved:** The test still checks that a `memory_items` row with the expected content and `source='team-chat:test-phase13-a'` materialises in Postgres within 10 seconds.

---

## Bug 2 — (g) cross_frontend PATCH: missing X-Team-Scope header returns 422

**File:** `test-phase13-cross-frontend.py` — `test_g_cross_frontend()` and `_api_patch()`

**Root cause:** `_api_patch()` sent `Authorization` and `Content-Type` headers but NOT `X-Team-Scope`. The `PATCH /v1/brain/events/{entity_type}/{entity_id}` endpoint has `team_scope: str = Depends(get_team_scope)` in its signature. `get_team_scope` reads the `X-Team-Scope` request header and returns a 422 validation error when it is absent — FastAPI's dependency resolution treats the missing header as a missing required parameter.

**Fix:** Added `team_scope: str = ""` parameter to `_api_patch()`. When non-empty, it is forwarded as the `X-Team-Scope` header. The `test_g_cross_frontend()` call site now passes `team_scope=team_scope`.

**Body shape:** The `TruthLevelPatchBody` schema (`extra="forbid"`, single field `truth_level: str`) was already correct in the test — no body change needed.

---

## Bug 3 — (h) verify-phase13.sh: bridge JWT rejected by user-only endpoint

**File:** `verify-phase13.sh` — `test_08_h_chat_send_failsoft()`

**Root cause:** Same mismatch as (a). The bash script minted a bridge JWT then POSTed to `/v1/teams/{team_id}/messages`, receiving `HTTP 403 "user-only endpoint"`.

**Fix:** Replaced the POST target with `POST /v1/brain/ingest` (202 fire-and-forget). The request body includes `source="verify-phase13-h"` and `idempotency_key="verify-phase13-test-h"`. Added cleanup logic after the test that computes the expected UUID5 item ID and deletes it from `memory_items`.

**Semantics preserved:** The test still proves the fail-soft design: the ingest endpoint returns 2xx immediately without blocking, and downstream async processing (classification, DB write, Qdrant upsert) runs in a fire-and-forget task. The primary assertion is the HTTP 202 status code; log scanning for `brain_ingest.*` events is kept as secondary evidence.

---

## Self-Check

- [x] `infrastructure/scripts/test-phase13-cross-frontend.py` modified and committed
- [x] `infrastructure/scripts/verify-phase13.sh` modified and committed  
- [x] Commit `6af6f48` exists in git log
- [x] No files accidentally deleted (only 2 files modified)
- [x] All 3 test functions still exercise the brain ingest pipeline end-to-end

## Self-Check: PASSED
