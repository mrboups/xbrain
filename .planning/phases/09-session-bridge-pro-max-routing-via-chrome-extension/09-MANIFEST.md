---
phase: 9
name: Session Bridge — Pro/Max Routing via Chrome Extension
plans: 6
waves: 3
generated: 2026-05-11
---

# Phase 9 Plan Manifest

## Wave Structure

| Wave | Plans | Rationale |
|------|-------|-----------|
| 1 | 09-01, 09-02, 09-04 | No cross-plan dependencies — server scaffold, extension claude.ai client, and infrastructure (DNS/nginx/migration/external-sessions endpoints) build in parallel. 09-01 calls 09-04's POST upsert at runtime, but the contract is fixed in both plans so no build-time order is required |
| 2 | 09-03, 09-05 | 09-03 needs 09-01 (WS endpoint + register frame contract) + 09-02 (handleClaude/getOrgId). 09-05 needs 09-01 + 09-03 (ws_status_query message) + 09-04 (GET/DELETE endpoints) |
| 3 | 09-06 | Verification gate — depends on every other plan being shipped |

## Plans

| # | File | Wave | Type | Autonomous | Depends on | Key Output |
|---|------|------|------|------------|-----------|-----------|
| 09-01 | `09-01-PLAN.md` | 1 | execute | yes | — | `apps/session-bridge` FastAPI service skeleton (HTTP 401/503/WS pool) + register-handshake → memory-api upsert + docker-compose entry + unit tests |
| 09-02 | `09-02-PLAN.md` | 1 | execute | no (live DevTools capture) | — | `chrome-extension/claude_ai_client.js` + `translate_sse.js` + `09-CAPTURE.md` validating assumptions A1–A10 |
| 09-03 | `09-03-PLAN.md` | 2 | execute | yes | 09-01, 09-02 | Extension v1.1.0 manifest + background.js WS + chrome.alarms watchdog + register-on-open + handleClaude dispatcher |
| 09-04 | `09-04-PLAN.md` | 1 | execute | no (Cloudflare DNS) | — | nginx 50-bridge.conf + Alembic 0014 + memory-api `/v1/me/external-sessions` GET / POST (upsert) / DELETE |
| 09-05 | `09-05-PLAN.md` | 2 | execute | yes | 09-01, 09-03, 09-04 | librechat.yaml "Claude (mon abonnement)" endpoint + popup Sessions section |
| 09-06 | `09-06-PLAN.md` | 3 | execute | no (UAT) | 09-01..05 | verify-phase9.sh (8 tests, SKIP-aware counter) + `.env.example` + docs/sessions.html + 09-UAT.md |

## Roadmap Success Criteria Mapping

| ROADMAP SC | Addressed by Plan(s) | Verification |
|------------|----------------------|--------------|
| 1. E2E LibreChat → claude.ai with Pro/Max quota | 09-01, 09-02, 09-03, 09-04, 09-05 | Manual UAT SC-1 |
| 2. Explicit error when extension absent | 09-01 (503 no_session), 09-05 (popup 🔴 + hint) | verify-phase9.sh test 5 (SKIPPED tolerated), UAT SC-2 |
| 3. session-bridge container + nginx vhost reachable | 09-01 (container), 09-04 (vhost) | verify-phase9.sh tests 1, 2, 3, 4 |
| 4. Popup status + user_external_sessions table populated | 09-01 (bridge → memory-api upsert on register frame), 09-03 (extension sends register on WS open), 09-04 (POST upsert endpoint + table), 09-05 (popup renders metadata.email_logged) | verify-phase9.sh test 6 (table exists) + UAT SC-4 (popup shows email_logged) |
| 5. claude.ai SSE → OpenAI SSE translation | 09-02 (translator + node tests) | verify-phase9.sh test 8 + UAT SC-5 |
| 6. verify-phase9.sh PASS | 09-06 | `PASS: N / N (SKIPPED: M)` with FAIL == 0; exit code 0 (SKIPPED never blocks) |

## Files Created / Modified Summary

### New files (35)
- `apps/session-bridge/` — 19 files (Dockerfile, pyproject.toml, README.md, 9 app modules including memory_api_client.py, 6 tests including test_register_upsert.py)
- `chrome-extension/claude_ai_client.js`
- `chrome-extension/translate_sse.js`
- `chrome-extension/ws_keepalive.js`
- `chrome-extension/popup.css` (if not existing)
- `chrome-extension/tests/run_tests.mjs`
- `chrome-extension/tests/test_translate_sse.mjs`
- `chrome-extension/tests/test_openai_to_claudeai.mjs`
- `chrome-extension/tests/test_ws_keepalive.mjs`
- `infrastructure/nginx/conf.d/50-bridge.conf`
- `apps/memory-api/alembic/versions/0014_external_sessions.py`
- `apps/memory-api/app/routes/external_sessions.py`
- `apps/memory-api/tests/test_external_sessions.py`
- `infrastructure/scripts/verify-phase9.sh`
- `docs/sessions.html`
- `docs/cloudflare-bridge-dns.md`
- `.planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-CAPTURE.md`
- `.planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-UAT.md`

### Modified files (8)
- `chrome-extension/manifest.json` (v1.0.0 → v1.1.0)
- `chrome-extension/background.js` (WS layer + register-on-open appended)
- `chrome-extension/popup.html` (Sessions section appended)
- `chrome-extension/popup.js` (Sessions handlers appended)
- `infrastructure/docker-compose.yml` (session-bridge service added with BRIDGE_SHARED_SECRET)
- `infrastructure/librechat/librechat.yaml` (custom endpoint appended)
- `apps/memory-api/app/main.py` (include_router external_sessions)
- `infrastructure/.env.example` (Phase 9 section appended)

## Anti-Scope (explicitly OUT)

- ChatGPT Plus routing → Phase 10
- Opt-in extraction routing via extension → deferred
- Multi-tab WebSocket coordination (last-write-wins is acceptable for v1)
- Auth refactor — reuses existing xbt_ tokens from Phase 8
- Redis cross-instance USER_SOCKETS — single-instance bridge per CONTEXT.md

## Acceptance

Phase 9 complete when:
- All 6 plans executed and committed
- `verify-phase9.sh` prints `PASS: N / N (SKIPPED: M)` with FAIL == 0 on the VM (M ≥ 0 is acceptable — VERIFY_XBT_TOKEN unset is not a failure)
- `09-UAT.md` walked with all 6 SC ticked (including SC-4: popup email_logged renders from user_external_sessions row populated by session-bridge register flow)
- ROADMAP `Phase 9` line marked `[x]` with completion date
- STATE.md updated to mark Phase 9 SHIPPED
