---
plan_id: 09-05
phase: 9
plan: 05
status: complete
subsystem: librechat-config + chrome-extension-ui
tags: [librechat, chrome-extension, popup, ui, phase-9, wave-2]
requirements: [SESSION-04]

dependency_graph:
  requires:
    - "09-01 (session-bridge HTTP/WS, register-upsert path) — runtime endpoint at https://bridge.grooveos.app/v1"
    - "09-03 (extension background.js ws_status_query listener) — sibling Wave 2, contract documented"
    - "09-04 (memory-api GET/DELETE /v1/me/external-sessions) — popup fetches both"
  provides:
    - "LibreChat custom endpoint 'Claude (mon abonnement)' → user-pasted xbt_ Bearer routes through session-bridge"
    - "Popup Sessions section (#sessions-list, #ws-dot, #claude-email, #claude-last-seen) with refresh + disconnect actions"
    - "popup.css (new file) — status-dot + session-row styles; linked from popup.html"
  affects:
    - "09-06 verify-phase9.sh UAT SC-1 (LibreChat dropdown has the new option) + SC-4 (popup renders metadata.email_logged)"

tech-stack:
  added: []
  patterns:
    - "user_provided apiKey pattern — LibreChat passes user-pasted token directly as Authorization: Bearer (Phase 4 BYOK precedent reused)"
    - "chrome.runtime.sendMessage popup → service-worker query/response (T-09-05-01 mitigation; no window.postMessage)"
    - "fail-soft renderSessions on DOMContentLoaded — 🔴 idle dot if background.js doesn't respond (Wave 2 sibling not shipped yet)"
    - "Relative time formatting (s/m/h ago) inline in popup.js — no date library needed"

key-files:
  created:
    - chrome-extension/popup.css
    - .planning/phases/09-session-bridge-pro-max-routing-via-chrome-extension/09-05-SUMMARY.md
  modified:
    - infrastructure/librechat/librechat.yaml  (+15 lines — one new custom endpoint, 4 preserved)
    - chrome-extension/popup.html              (+24 lines — Sessions <section> + popup.css <link>)
    - chrome-extension/popup.js                (+130 lines — Sessions handlers appended)

key-decisions:
  - "popup.css created as a new file (not inline). The existing Web Clipper styles stay inline in popup.html <style> to avoid churning unrelated rules. popup.css is linked first in <head>; inline rules take precedence per cascade order, so there is no risk of breaking existing UI."
  - "Sessions section appended AFTER #status (the Web Clipper status message div). Placing it inside the same <body>, separated by a top-border, keeps the popup width invariant (320px) and the existing Clipper UI untouched."
  - "renderSessions() is fail-soft on every leg: missing #ws-dot → return; chrome.runtime.sendMessage throws → catch → 🔴 idle dot; no xbt_token → 'non connecté'; fetch fails → 'erreur réseau'. The popup will render even if 09-03 has not yet wired background.js (which is exactly the Wave 2 ordering this plan was built to tolerate)."
  - "DELETE confirmation uses confirm() (single popup dialog). Adequate for the popup UX surface — anything richer would require additional DOM nodes that would clash with the existing Web Clipper visual hierarchy."
  - "err.message NOT logged in renderClaudeSessionInfo's catch — a hostile proxy could inject a probe string. The user-visible 'erreur réseau' is sufficient."

metrics:
  files-created: 2
  files-modified: 3
  loc-added: 169       # 15 yaml + 24 html + 87 css + 130 js = 256 raw, minus blank/comment lines net ~169 functional
  commits: 3
  duration: ~15 min
  completed: 2026-05-12
---

# Phase 9 Plan 05: LibreChat "Claude (mon abonnement)" Endpoint + Popup Sessions Section Summary

**One-liner:** LibreChat dropdown now exposes "Claude (Pro/Max)" routing through session-bridge with user-pasted xbt_ Bearer; the Chrome extension popup gains a Sessions section that shows WS readyState (🟢/🔴), the email logged on claude.ai, last-seen relative time, and refresh + disconnect actions wired to `memory-api`.

## What shipped

### `infrastructure/librechat/librechat.yaml`

A new custom endpoint appended after `Claude Reasoning`:

```yaml
- name: "Claude (mon abonnement)"
  apiKey: "user_provided"
  baseURL: "https://bridge.grooveos.app/v1"
  models:
    default: ["claude-opus-4-7", "claude-sonnet-4-6"]
    fetch: false
  titleConvo: true
  titleModel: "claude-sonnet-4-6"
  modelDisplayLabel: "Claude (Pro/Max)"
```

LibreChat will surface this in the endpoint dropdown after a config reload. The user pastes their `xbt_` token as the API key; LibreChat forwards it as `Authorization: Bearer xbt_...` to `bridge.grooveos.app/v1/chat/completions`, where session-bridge (09-01) validates against memory-api and relays the request to the user's extension.

The 4 pre-existing entries (`Anthropic`, `OpenAI`, `xAI`, `Claude Reasoning`) are byte-identical to before — programmatic yaml round-trip confirmed.

### `chrome-extension/popup.html` + `chrome-extension/popup.css`

A new `<section class="xb-sessions">` was appended after the existing `#status` div. It renders one row for Claude:

- `<span id="ws-dot" class="status-dot">` — colored by JS (10px circle)
- `<small id="claude-email">` — `metadata.email_logged` from the row
- `<small id="claude-last-seen">` — relative time
- `<button id="btn-refresh-claude">` — re-runs `renderSessions()`
- `<button id="btn-disconnect-claude">` — DELETE + re-render after `confirm()`
- `<p class="xb-hint">` — onboarding hint for users without the extension active

`popup.css` is a new file (the existing Web Clipper styles stay inline in `popup.html`). The link tag is added in `<head>` before the inline `<style>` so inline rules take cascade precedence — no risk of regressing the Clipper UI.

### `chrome-extension/popup.js`

Appended ~130 lines under the existing module:

- `renderSessions()` runs on DOMContentLoaded and on every refresh / disconnect cycle.
- `renderWsStatus()` calls `chrome.runtime.sendMessage({kind: "ws_status_query"})` — the contract from plan 09-03 — and maps `readyState === 1` → `dot.active` (🟢), anything else → `dot.idle` (🔴). Failures are caught and surfaced as `dot.idle` with a tooltip; the popup never errors out if 09-03 hasn't shipped the listener yet.
- `renderClaudeSessionInfo()` reads `xbt_token` from `chrome.storage.session` (Phase 5/8 convention; will be populated by 09-03 when it wires the WS handshake). If absent → `non connecté` label. If present, fetches `GET https://api.grooveos.app/v1/me/external-sessions`, finds the `provider === "claude"` row, and renders `metadata.email_logged` + relative `last_seen_at`.
- `disconnectClaude()` calls `DELETE /v1/me/external-sessions/claude` after a `confirm()` dialog, then re-renders.
- `formatRelative()` returns `Ns ago`, `Nm ago`, `Nh ago`, or `Locale date` — no date library.

## Tasks completed

| # | Task | Commit |
|---|------|--------|
| 1 | Append 'Claude (mon abonnement)' to librechat.yaml | `9851bee` |
| 2 | Add Sessions section to popup.html + create popup.css | `18c2618` |
| 3 | Wire popup.js Sessions handlers (ws_status_query + external-sessions GET/DELETE) | `f4d2a76` |

## Dependency contracts confirmed

### With 09-01 (session-bridge HTTP/WS — already shipped, commits ed244e2/b894c1b/848989b)

The LibreChat custom endpoint's `baseURL: https://bridge.grooveos.app/v1` matches the `routes_chat.py` mount point exposed by `apps/session-bridge`. The `apiKey: user_provided` flow puts the user's `xbt_` token into the `Authorization` header, which `auth.validate_xbt_token` in 09-01 already validates via memory-api `/v1/me`. End-to-end the contract holds — confirmed against 09-01 SUMMARY's "Success criteria from plan" checklist.

### With 09-03 (extension background.js WS — Wave 2 sibling, not yet shipped)

09-03 is on the same wave as this plan and has not landed yet. Per plan instructions ("code the popup against the documented contract anyway — they're both shipping in Wave 2"), the popup uses the must_haves contract verbatim:

- `chrome.runtime.sendMessage({kind: "ws_status_query"})` → `{readyState: number}` where `readyState` ∈ {0, 1, 2, 3, -1}.

If 09-03 hasn't installed the listener at popup-render time, `chrome.runtime.sendMessage` resolves to `undefined` (no listener responded), `renderWsStatus` catches the missing field, the dot turns 🔴 idle, and the popup remains functional. No exception bubbles up.

Additionally, `xbt_token` is read from `chrome.storage.session` — that key will be set by 09-03 when the WS handshake completes. Until then, the popup gracefully renders "non connecté".

### With 09-04 (memory-api endpoints — already shipped, commits ccc258c/f4f047b/40ff769)

- `GET /v1/me/external-sessions` returns `[{id, provider, extension_id, last_seen_at, metadata}]` — popup reads `metadata.email_logged` and `last_seen_at`.
- `DELETE /v1/me/external-sessions/claude` returns 204 (hit) or 404 (miss) — popup treats both as success and re-renders.

Contract verified against 09-04 SUMMARY's response-shape table.

## Verification (per plan `<verification>`)

- [x] `python -c "import yaml; yaml.safe_load(...)"` — passed; `Claude (mon abonnement)` present with correct apiKey, baseURL, models; 4 pre-existing custom endpoints retained.
- [x] `node --check chrome-extension/popup.js` — exits 0.
- [x] popup.html contains `xb-sessions`, `sessions-list`, `ws-dot`, `btn-disconnect-claude` (4 grep matches).
- [x] popup.css contains `status-dot.active`, `status-dot.idle` (2 grep matches).
- [x] popup.js contains `ws_status_query`, `/v1/me/external-sessions`, `method: "DELETE"` (7 grep matches collectively).
- [x] 0 uses of `window.postMessage` outside comments — sole hit is the `// CRITICAL: use chrome.runtime.sendMessage, NOT window.postMessage` annotation.
- [ ] LibreChat dropdown shows the new endpoint after restart — deferred to 09-06 UAT (requires deploying the updated config to the VM).
- [ ] Popup Sessions section renders with at least one row when loaded — deferred to 09-06 UAT (requires loading the unpacked extension in Chrome).

## Success criteria from plan

- [x] LibreChat config exposes "Claude (mon abonnement)" — yaml-validated.
- [x] Popup HTML/CSS structure ready for the bridge data — id selectors present.
- [x] Popup JS handlers wired (refresh + disconnect + DOMContentLoaded init) — verified via grep + node --check.
- [ ] End-to-end smoke (live LibreChat dropdown + live popup render with real WS) — 09-06 UAT.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking issue] popup.css linkage strategy**
- **Found during:** Task 2
- **Issue:** The plan said "If there is no popup.css, create one. If popup.html doesn't already link a stylesheet for popup.css, add `<link rel=\"stylesheet\" href=\"popup.css\">` in its `<head>`." The existing popup.html had ALL styles inline in a `<style>` block (no external stylesheet at all). Linking popup.css naively could either (a) override existing rules if I'd duplicated them, or (b) leave the existing Clipper UI to depend on inline rules and the new section to depend on the external file — split sources.
- **Fix:** Decided to keep existing Web Clipper rules inline (zero risk of regression) and put ONLY the new `.xb-sessions` + `.status-dot` + `.session-row` etc. rules in popup.css. Linked the new file FIRST in `<head>`, so the inline `<style>` block that follows takes cascade precedence for any hypothetical clash. Documented this choice in popup.css header comment and in key-decisions above.
- **Files modified:** `chrome-extension/popup.html`, `chrome-extension/popup.css`
- **Commit:** `18c2618`

**2. [Rule 2 — Missing critical functionality] Hostile error message redaction**
- **Found during:** Task 3
- **Issue:** The plan's reference popup.js had `email.textContent = \`erreur réseau: ${e.message}\`` in the fetch catch. A network-level adversary (compromised proxy injecting a 200 with attacker-crafted JSON, or DNS hijack returning attacker text in error responses) could inject arbitrary strings into `e.message`, which would then surface verbatim in the user's popup. Low impact (no JS execution since `textContent` not `innerHTML`), but still leaks attacker-controlled text into the trusted xbrain UI surface.
- **Fix:** Truncated to a static `"erreur réseau"` string; the error detail is deliberately dropped. The chrome.runtime.lastError surface for the WS query DOES include the message (tooltip only — sensible for debugging), but the network-fetch surface does not.
- **Files modified:** `chrome-extension/popup.js`
- **Commit:** `f4d2a76`

### Architectural Decisions Made (no checkpoint needed — within plan scope)

None.

## Authentication gates

None during execution. The popup's runtime auth gate is the missing `xbt_token` in `chrome.storage.session` — handled gracefully with a "non connecté" label, no UI break. 09-03 will populate that key when it wires the extension WS handshake.

## Known stubs

- The Sessions section will render a 🔴 idle dot and `non connecté` label until **both** 09-03 (extension background.js wiring `xbt_token` + ws_status_query listener) and 09-04 (already shipped) are running together with a logged-in user. This is intentional fail-soft behavior, not a stub. Documented in the popup's `<p class="xb-hint">` paragraph as user-facing guidance.

- The Web Clipper part of the popup (top of the UI) is completely untouched — same behavior as before this plan.

## Threat Flags

None — no new attack surface beyond what the plan's threat register enumerated. The three mitigations are in place:

- **T-09-05-01** (Spoofing on popup↔background channel): only `chrome.runtime.sendMessage` is used; `window.postMessage` does not appear in popup.js source outside a comment annotation.
- **T-09-05-02** (Token visibility): the `xbt_` token in the `Authorization` header is the user's own token, used to call memory-api over HTTPS — same disclosure surface as the existing `loadUserTeams` call, no new exposure.
- **T-09-05-03** (Tampering with existing librechat.yaml endpoints): yaml round-trip programmatically confirmed all 4 pre-existing custom endpoints retained verbatim.

## What's needed next from Wave 2 / Wave 3

- **Plan 09-03 (extension v1.1.0 — pending Wave 2 sibling):** must store the validated `xbt_` token at `chrome.storage.session.xbt_token` after the WS register-handshake completes, AND install the `chrome.runtime.onMessage` listener that responds to `{kind: "ws_status_query"}` with `{readyState: number}`. When that lands, this plan's popup section will light up 🟢.
- **Plan 09-06 (UAT — Wave 3):** SC-1 walks the LibreChat dropdown manually; SC-4 walks the popup with a logged-in claude.ai tab. Both depend on this plan's artifacts being deployed to the VM (librechat container reload + extension repack).

## Self-Check

- [x] `infrastructure/librechat/librechat.yaml` modified — FOUND: 5 custom endpoints including `Claude (mon abonnement)`
- [x] `chrome-extension/popup.css` created — FOUND: 87 lines, contains `.status-dot.active`, `.status-dot.idle`, `.session-row`
- [x] `chrome-extension/popup.html` modified — FOUND: Sessions section appended, popup.css linked in `<head>`
- [x] `chrome-extension/popup.js` modified — FOUND: renderSessions, renderWsStatus, renderClaudeSessionInfo, disconnectClaude, formatRelative all present
- [x] Commit `9851bee` exists in `git log`
- [x] Commit `18c2618` exists in `git log`
- [x] Commit `f4d2a76` exists in `git log`

## Self-Check: PASSED
