---
phase: 22-push-a-link
verified: 2026-07-19T05:38:14Z
status: human_needed
score: 4/4 must-haves verified (NUDGE-01 satisfied)
overrides_applied: 0
---

# Phase 22: Push-a-Link (NUDGE-01) — Verification Report

**Phase Goal:** From the team chat, a member sends a URL to another team member; the target's extension shows a native OS notification (sender + full destination URL) and opens the URL as a new tab ONLY on the target's explicit click. Consent-gated, same-team-only, URL-validated, rate-limited, recipient opt-out.
**Verified:** 2026-07-19T05:38:14Z
**Status:** human_needed (all 4 Success Criteria mechanically VERIFIED with real-path evidence, re-executed live; one residual — the actual OS-notification/click/tab-open browser interaction — flagged for human confirmation, non-blocking)
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria from ROADMAP.md)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | Same-team POST publishes `open_url` to target `user:<sub>`; non-member/cross-team target → 403, no publish — proven against real Postgres, membership NOT mocked | VERIFIED | Re-ran `apps/memory-api/tests/test_nudge_open_gate.py` live (real Postgres testcontainer): **1 passed in 26.53s**. Read the test source: only `app.services.centrifugo_client.publish` is monkeypatched with a recorder (`monkeypatch.setattr("app.services.centrifugo_client.publish", _recorder)`); `_resolve_team_and_check_membership`, `teams_repo.get_membership`, `users_repo.get_user_by_id` all run for real against the real DB. Asserts: alice→carol (same team-a) → `202` + exactly one publish to `user:carol-sub` carrying `{type:'open_url', url, from:{sub,display_name}, team_id, team_slug}`; alice→bob (team-b member) → `403`, publish count unchanged; alice→random UUID → `403`, publish count unchanged. |
| SC2 | URL safety: http/https only, `javascript:`/`file:`/`data:` → 422 no publish; recipient sees the real URL; per-sender rate limit → 429 | VERIFIED | Same live gate run: `javascript:alert(1)` and `file:///etc/passwd` → `422`, publish count unchanged. Rate-limit block: `rate_limit._storage.reset()` + `NUDGE_RATE_LIMIT="1/minute"` → first nudge `202` (publish count 2), second `429` (publish count still 2). `apps/memory-api/tests/test_url_safety.py` re-ran live: **24 passed** (accept/bad-scheme/malformed/non-str/too-long/boundary table). SSRF ban independently grep-confirmed: `app/services/url_safety.py` imports only `urllib.parse.urlsplit` — no `requests`/`httpx`/`socket`/`urlopen`/DNS of any kind. Rate bucket is keyed on `sender.source_user_id` via `rate_limit.check_rate(settings.NUDGE_RATE_LIMIT, "nudge", sender.source_user_id)` — confirmed NOT `enforce_rate_limit` (which keys on client IP). |
| SC3 | Extension opens a tab ONLY on the notification-click gesture, never on event receipt | VERIFIED | `chrome-extension/nudge_open.js` (the receive handler) contains ZERO occurrences of the substring `"tabs"` (independently grepped) and imports nothing from `chrome.*` — it only calls injected `deps.notify` / `deps.persistPending`. `chrome.tabs.create` appears in exactly two places in the whole extension: `background.js:1333` inside the `chrome.notifications.onClicked` listener (guarded by a `nudge_<id>` session-storage key lookup, so it only fires for genuine pending nudges) and `popup.js:146` (the pre-existing, unrelated "open LibreChat" header button — not part of the nudge path). `popup.js` routes `open_url` frames to `nudge_open.handleOpenUrl` (`popup.js:572`) and contains no direct tab-open in that code path. Node proof re-run live: `test_nudge_open.mjs` **7/7 passed**, including the structural "source contains NO 'tabs' capability" assertion. |
| SC3b | Popup contract test asserts EVERY referenced id exists in popup.html (proven to go RED on a mismatch) | VERIFIED | Re-ran the full node suite from a copy in the scratchpad (OUTSIDE `.claude/`, since its `package.json` forces commonjs): `test_popup_contract.mjs` → **129/129 passed** (90 from the original frozen-id/class/token/a11y contract + 39 new "referenced id exists in popup.html" assertions covering every id `popup.js` binds, incl. the Plan-22-03 send-link ids). Independently falsified the RED-on-mismatch claim: renamed `id="sendlink-status"` to `id="sendlink-status-RENAMED"` in a scratch copy of `popup.html` and re-ran — result: `128 passed, 1 failed` with `FAIL: referenced id exists in popup.html: #sendlink-status`, confirming the gate is live, not decorative. Restored the file after the check (scratch copy only, no repo file touched). |
| SC4 | Recipient opt-out (`allowOpenLinkRequests` default ON) suppresses the notification when OFF; offline/closed-browser delivery documented as residual, not promised | VERIFIED | `chrome-extension/settings.js`: `DEFAULT_SETTINGS.allowOpenLinkRequests: true`, `_SCHEMA.allowOpenLinkRequests: ["boolean"]`. `nudge_open.js::handleOpenUrl` returns `null` (no notification built) when `settings.allowOpenLinkRequests === false`, checked BEFORE the URL-scheme check. `options.html`/`options.js` wire `#opt-allow-open-link` to `chrome.storage.sync` with the English label "Allow open-link requests". Re-ran `test_settings.mjs` live: **11/11 passed**, incl. "allowOpenLinkRequests defaults ON (D-22-04)" and the opt-out suppression case in `test_nudge_open.mjs`. `docs/push-a-link.md` §"Known residual — offline / closed-browser delivery (D-22-06)" explicitly states delivery is live-only via Centrifugo and does NOT promise closed-browser delivery — matches D-22-06 exactly, not overstated. |

**Score:** 4/4 truths verified (all backed by re-executed, real-Postgres/real-node evidence, not SUMMARY claims alone)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/memory-api/app/services/url_safety.py` | Pure `is_safe_nudge_url(url, max_len)` guard, no network | VERIFIED | 63 lines; imports only `urllib.parse.urlsplit`; grep for `requests\|httpx\|socket\|urlopen\|resolve` → no matches. |
| `apps/memory-api/app/routes/team_chat.py` | `POST /v1/teams/{team_id}/nudge-open` + `PostNudgeBody` | VERIFIED / WIRED | Route at line 287; guard-chain ordering (sender-403 → target-403 → url-422 → rate-429 → publish-202) confirmed by reading the code and exercised by the live gate test. |
| `apps/memory-api/app/config.py` | `NUDGE_RATE_LIMIT`, `NUDGE_MAX_URL_LENGTH` | VERIFIED | Lines 261-262: `NUDGE_RATE_LIMIT: str = "10/minute"`, `NUDGE_MAX_URL_LENGTH: int = 2048`. Read at request time (`settings.NUDGE_RATE_LIMIT`/`settings.NUDGE_MAX_URL_LENGTH`), confirmed by the monkeypatch-takes-effect assertion in the gate test. |
| `apps/memory-api/tests/test_nudge_open_gate.py` | Real-Postgres gate, publish captured not mocked | VERIFIED | Re-ran live: 1 passed / 26.53s. Membership/validation confirmed NOT mocked by source read. |
| `apps/memory-api/tests/test_url_safety.py` | Unit table | VERIFIED | Re-ran live: 24 passed. |
| `chrome-extension/nudge_open.js` | Pure `isSafeHttpUrl` + `handleOpenUrl`, no tab capability | VERIFIED | No `chrome.*` import, no "tabs" substring (grep-confirmed), consent + opt-out + scheme gates all present. |
| `chrome-extension/tests/test_nudge_open.mjs` | 7-case node proof incl. structural no-tabs assertion | VERIFIED | Re-ran live (outside `.claude/`): 7/7 passed. |
| `chrome-extension/settings.js` + `options.html`/`options.js` | `allowOpenLinkRequests` opt-out, default ON | VERIFIED / WIRED | Confirmed default true, schema-guarded boolean, checkbox wired to `chrome.storage.sync`. |
| `chrome-extension/popup.js` | `user:<sub>` subscription + `open_url` routing + send-link affordance | VERIFIED / WIRED | `subscribeUserChannel()` (idempotent via `state.userSubscription`), `handleUserPublication` routes to `handleOpenUrl` with chrome-backed deps; `wireSendLink`/`submitSendLink` POST the nudge-open endpoint with a client `isSafeHttpUrl` pre-check (UX only, server is the boundary). |
| `chrome-extension/background.js` | `chrome.notifications.onClicked` → `chrome.tabs.create` (the ONLY nudge tab-open) | VERIFIED / WIRED | Lines 1317-1339: guarded by presence checks + a `nudge_<id>` session-storage key lookup; removes the key and clears the notification immediately after opening. |
| `chrome-extension/tests/test_popup_contract.mjs` | Full referenced-id existence gate (129 assertions) | VERIFIED | Re-ran live: 129/129 passed; independently falsified to confirm it goes RED on a missing id. |
| `docs/push-a-link.md` | Feature doc incl. D-22-06 offline residual | VERIFIED | Present, documents flow, security posture, and explicitly non-promised offline delivery; deferred items listed match CONTEXT.md's `<deferred>` section. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `team_chat.py::nudge_open` | `app.services.centrifugo_client.publish` | `asyncio.create_task(centrifugo_client.publish(channel=f"user:{target.source_user_id}", ...))` | WIRED | Confirmed by the live gate test capturing exactly one recorded publish per accepted request and zero for every rejected request. |
| `team_chat.py::nudge_open` | `app.repos.teams.get_membership` (target) | Single membership check covering non-member AND cross-team | WIRED | Confirmed live: cross-team (bob) and unknown UUID both → 403, zero publishes. |
| `popup.js::handleUserPublication` | `nudge_open.js::handleOpenUrl` | Centrifugo `user:<sub>` subscription → `open_url` frame → `handleOpenUrl(data, deps)` | WIRED | Confirmed by source read; `deps.notify`/`deps.persistPending` map to real `chrome.notifications.create`/`chrome.storage.session.set`. |
| `background.js::notifications.onClicked` | `chrome.tabs.create` | `chrome.storage.session["nudge_<id>"]` lookup → tab open → key cleanup | WIRED | Confirmed by source read; this is the sole tab-open path for nudges (the only other `chrome.tabs.create` call in the codebase is the unrelated "open LibreChat" header button). |
| `popup.js::submitSendLink` | `POST /v1/teams/{id}/nudge-open` | Raw `fetch` (not `fetchJson`) so 202/403/422/429 map to distinct English status text | WIRED | Confirmed by source read; `mapNudgeError` covers 403/422/429/404 with plain English messages. |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|--------------|--------|----------|
| NUDGE-01 | 22-01, 22-02, 22-03 | Member sends URL to same-team member; native notification (sender+URL); tab opens only on explicit click; consent-gated, same-team-only, URL-validated, rate-limited, recipient opt-out | SATISFIED | All 4 roadmap Success Criteria independently re-verified above with live-executed evidence (server gate, url-safety unit table, node consent-gate proof, popup contract gate). No orphaned Phase-22 requirement IDs found in REQUIREMENTS.md beyond NUDGE-01. |

### Anti-Patterns Found

None. Grepped all phase-22-touched files (`url_safety.py`, `team_chat.py`, `nudge_open.js`, `popup.js`, `background.js`, `settings.js`, `options.js`, `options.html`) for `TODO|FIXME|XXX|HACK|PLACEHOLDER|not yet implemented|coming soon` — zero matches. No stub returns, no hardcoded empty payloads on the render path, no console.log-only handlers.

### English-Only / Shadcn Contract

- All new/changed user-facing strings (send-link overlay labels, status messages, options checkbox label + help text, notification title/message construction) are English-only — confirmed by reading `popup.html`, `options.html`, `nudge_open.js`, and by `test_popup_contract.mjs`'s "english-only: no accented Latin chars" assertion (re-ran live, passed).
- Phase-20 shadcn Neutral contract unbroken: the original 90-assertion subset (frozen ids/classes, token contract incl. radius-0/Geist/dark-light resolution, a11y focus-visible, reduced-motion, CSP-safe fonts, XSS guard, no-fabricated-provenance) all still pass inside the same 129/129 run — no drift introduced by the Phase-22 additions.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Real-Postgres nudge gate (happy/403/422/429, publish captured) | `python -m pytest tests/test_nudge_open_gate.py -v` | `1 passed in 26.53s` | PASS |
| URL-safety unit table | `python -m pytest tests/test_url_safety.py -q` | `24 passed` | PASS |
| SSRF-ban grep | `grep -n "requests\|httpx\|socket\|urlopen\|resolve" app/services/url_safety.py` | no matches | PASS |
| Extension consent-gate node proof | `node tests/test_nudge_open.mjs` (outside `.claude/`) | `7 passed, 0 failed` | PASS |
| Popup id-contract full gate | `node tests/test_popup_contract.mjs` (outside `.claude/`) | `129 passed, 0 failed` | PASS |
| Popup id-contract RED-on-mismatch falsification | rename an id in a scratch copy, re-run | `128 passed, 1 failed` (expected failure fired) | PASS |
| Full extension suite | `node tests/run_tests.mjs` (outside `.claude/`) | `12/12 test files passed` | PASS |
| Settings opt-out default | `node tests/test_settings.mjs` | `11 passed, 0 failed` | PASS |

### Human Verification Required

### 1. Live browser confirmation of the notification → consent-click → tab-open flow

**Test:** With the extension loaded (reloaded, unpacked) and two real accounts on the same team (sender + recipient, both signed in, popup open or side panel active on the recipient's side): from the sender's "send link" control, pick the recipient and submit a `https://` URL. On the recipient's machine, confirm a native OS notification appears showing the sender's name and the full literal URL, that NO tab opens automatically, and that clicking the notification opens exactly that URL in a new tab. Then toggle "Allow open-link requests" OFF in Options and repeat — confirm no notification appears at all.

**Expected:** Notification title = sender's display name + "wants to open a link"; message = the literal URL; no tab opens until the click; after the click the tab opens and the notification clears; with the toggle OFF, nothing appears.

**Why human:** This is a real-time, cross-device OS-notification + user-gesture + browser-tab interaction. There is no `jsdom` (confirmed: `test_popup_contract.mjs` reports "jsdom not installed") and no Chrome-extension browser-automation harness in this repo, so the actual native-notification rendering, the click gesture wiring to `chrome.notifications.onClicked`, and the resulting `chrome.tabs.create` call cannot be executed in this verification pass. All of the logic this flow depends on IS mechanically proven: the server-side publish path (real-Postgres gate), the consent-gate/opt-out logic (node tests), and the structural absence of any auto-open capability (grep-proven). This is a residual visual/interaction smoke-check, consistent with how this project has historically tracked such items (cf. Phase 21's Settings-field browser smoke-check) — not a sign of missing implementation.

### 2. Live "send link" overlay UI confirmation

**Test:** Open the popup, click "send link" in the header, confirm the member picker lists same-team members (excluding yourself and blocked members), pick a member, enter an invalid URL (e.g. `ftp://x`), submit, and confirm the inline English error text appears without a page reload. Then submit a cross-team/invalid target scenario is not reachable from the UI (picker only lists real teammates) — confirm the picker never lets you select a non-member.

**Expected:** Overlay opens/closes without navigation; member list populates from the real `/v1/teams/{id}/members` endpoint; invalid URL is rejected client-side with clear English text; a valid submission shows "Sent ✓".

**Why human:** DOM rendering + click-interaction + visual confirmation in a live Chrome extension popup, same class of residual as item 1 — no DOM-execution harness available in this repo's test suite.

### Gaps Summary

No gaps. All 4 Success Criteria for Phase 22 (and NUDGE-01) are backed by real, re-executed evidence:
- The security-critical decision (same-team-only publish) is proven against a real Postgres testcontainer with the actual `team_chat.py::nudge_open` route, with ONLY the terminal `centrifugo_client.publish` network call captured by a recorder — membership resolution, target lookup, URL validation, and rate limiting all run for real (the "gate lesson" honored).
- URL safety is provably pure/lexical — zero network-capable imports in `url_safety.py`, independently grepped.
- The client-side consent gate is structurally incapable of opening a tab (no `chrome.*` import, no "tabs" substring in the source) — proven by a node test that itself was exercised live, not just read.
- The full referenced-id popup contract (129 assertions) was independently falsified to confirm it actually fails on a broken id, not merely present in the repo.
- The recipient opt-out defaults ON and is enforced before any notification is built; the offline/closed-browser residual is documented accurately (matches D-22-06, not overstated as guaranteed delivery).

Two items are routed to human verification (the live notification/click/tab-open flow, and the send-link overlay's DOM interaction) because no browser-automation or `jsdom` harness exists in this repo to mechanically execute them — these are visual/interaction confirmations, not code gaps. All of the logic those flows invoke is independently and mechanically proven above.

**Note (non-blocking, documentation only):** `.planning/ROADMAP.md` still shows the three 22-0x plan checkboxes as `- [ ]` and `.planning/STATE.md` still reads "Executing Phase 22" — this is expected per each SUMMARY.md's explicit "STATE.md / ROADMAP.md deliberately NOT updated (parallel-executor rule)" note, and is a bookkeeping step for phase completion, not a code gap.

---

*Verified: 2026-07-19T05:38:14Z*
*Verifier: Claude (gsd-verifier)*
