---
phase: 12
plan: 12-08
subsystem: chrome-extension
tags: [github-app, chrome-extension, manifest-key, deterministic-id, auth]
dependency_graph:
  requires:
    - operator-prep: GitHub App "xbrain" registered + chrome-ext keypair generated (DONE 2026-05-17)
    - operator-prep: Manifest key value at /tmp/phase12-keys/chrome-ext-manifest-key.txt
    - operator-prep: GitHub App callback URL #2 = https://anigikcnmldoklcmogffmgcojdhhficb.chromiumapp.org/ registered
  provides:
    - chrome.runtime.id deterministic = anigikcnmldoklcmogffmgcojdhhficb across all unpacked installs
    - extension OAuth flow now points at the new GitHub App client_id (Iv23liVnZvIN0Lo6isof)
    - KB doc for future maintainers (rotation policy, generation procedure)
  affects:
    - GHAPP-07 (extension client_id constant) — DONE for extension surface
    - 12-11 UAT scenario 5 (Chrome extension sign-in) — unblocked
tech_stack:
  added:
    - node:crypto SHA-256 (already in Node stdlib; new use site = test_manifest_key.mjs)
  patterns:
    - "Manifest V3 'key' field for deterministic chrome.runtime.id (Chrome docs reference pattern)"
    - "Defense-in-depth gitignore (root *.pem + chrome-extension/.gitignore *.pem)"
key_files:
  created:
    - chrome-extension/.gitignore
    - chrome-extension/tests/test_manifest_key.mjs
    - .planning/KB/chrome-extension-key.md
    - .planning/phases/12-github-app-migration-public-deployment-ready-auth/deferred-items.md
  modified:
    - chrome-extension/manifest.json (add "key" field at top of object)
    - chrome-extension/background.js (replace OAuth App client_id with GitHub App client_id; update comment block)
decisions:
  - "Place 'key' as first property in manifest.json (visibility / convention; Chrome parser is order-agnostic)"
  - "Add chrome-extension/.gitignore even though root .gitignore already excludes *.pem (defense-in-depth — Plan 12-08 Section 2 explicit ask)"
  - "Add test_manifest_key.mjs (Rule 2 auto-add) — derives runtime.id from the committed key + asserts == anigikcnmldoklcmogffmgcojdhhficb. Belt-and-braces against silent key rotation that would break sign-in via chromiumapp.org callback mismatch."
  - "Use explicit hex→letter mapping table in derivation (not charCode shift) — '9'(0x39) and 'a'(0x61) are not ASCII-adjacent, so naive shift produces non-printable characters."
metrics:
  duration_minutes: 13
  completed_date: 2026-05-17
  tasks_completed: 2
  commits: 3
  files_created: 4
  files_modified: 2
---

# Phase 12 Plan 8: Chrome extension — manifest key + new GitHub App client_id Summary

**One-liner:** Embedded RSA-2048 public key in `manifest.json` (392-char base64 DER) so `chrome.runtime.id` is now deterministic = `anigikcnmldoklcmogffmgcojdhhficb` across all unpacked installs; replaced legacy OAuth App client_id (`Ov23liy7tZekl0uEztoj`) with the new xbrain GitHub App client_id (`Iv23liVnZvIN0Lo6isof`); added a Node test that re-derives the ID from the committed key to guard against silent rotation.

## What was done

### Task 1 — KB doc + gitignore (commit `37c7897`)

- **`chrome-extension/.gitignore`** (new): defense-in-depth `*.pem` / `*.key` exclusion, plus explicit `chrome_ext_private.pem` patterns. Root `.gitignore` already excludes `*.pem` but duplicating locally guards against a future move/copy that drops the parent gitignore.
- **`.planning/KB/chrome-extension-key.md`** (new): full runbook documenting (a) the openssl generation procedure for the keypair, (b) where each output lives (manifest, GitHub App settings UI, operator's secret store), (c) the verification path (automated test + manual smoke), (d) the rotation policy (NEVER while unpacked installs exist), and (e) the migration cost runbook if rotation is ever forced.

### Task 2 — manifest key + GITHUB_CLIENT_ID + derivation test (commit `d62d723`)

- **`chrome-extension/manifest.json`** (modified):
  - Inserted `"key": "MIIBIjAN...QIDAQAB"` (392-char base64-encoded DER public key) as the first property of the manifest object.
  - JSON validity verified (`JSON.parse` succeeds).
  - Key length = 392 (canonical for RSA-2048 DER pub key).
- **`chrome-extension/background.js`** (modified, line ~63):
  - Replaced `const GITHUB_CLIENT_ID = "Ov23liy7tZekl0uEztoj"` with `const GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof"`.
  - Updated the comment block above (lines 51-62) to reflect Phase 12 reality: "uses the xbrain GitHub App", multi-callback URL support, no per-frontend dispatch in memory-api. Removed the now-incorrect "Same OAuth app as LibreChat's GitHub sign-in" wording.
- **`chrome-extension/tests/test_manifest_key.mjs`** (new — Rule 2 add):
  - Reads `chrome-extension/manifest.json`, decodes the `key` field as base64 → DER bytes, SHA-256s them, takes the first 32 hex chars, and maps `[0-9a-f] → [a-p]` via an explicit table.
  - Asserts the derived ID equals `anigikcnmldoklcmogffmgcojdhhficb`.
  - 3/3 assertions pass when run via `node chrome-extension/tests/test_manifest_key.mjs`.

### Deferred items log (commit `284036d`)

- **`.planning/phases/12-github-app-migration-public-deployment-ready-auth/deferred-items.md`** (new): documents two pre-existing test failures discovered while running the chrome-extension test suite. Both are Phase 9 issues unrelated to Phase 12 — out of scope per the scope-boundary rule.

## Commits

| Commit    | Type | Description                                                       |
| --------- | ---- | ----------------------------------------------------------------- |
| `37c7897` | docs | KB article on stable-ID keypair + gitignore PEM                   |
| `d62d723` | feat | Add manifest key + switch to GitHub App client_id + test_manifest_key.mjs |
| `284036d` | chore | Log pre-existing Phase 9 test failures as deferred                |

## Verification

### Acceptance checks (Plan 12-08 Tasks 1 + 2)

```bash
# Task 1 acceptance
$ [ -f chrome-extension/.gitignore ] && grep -q "chrome_ext_private.pem" chrome-extension/.gitignore && echo OK
OK

$ [ -f .planning/KB/chrome-extension-key.md ] && grep -q "deterministic" .planning/KB/chrome-extension-key.md && echo OK
OK

# Task 2 acceptance
$ node -e "const m = require('./chrome-extension/manifest.json'); console.log('key field present, len=', m.key.length)"
key field present, len= 392

$ grep -E '^const GITHUB_CLIENT_ID' chrome-extension/background.js
const GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof";

$ node -e "JSON.parse(require('fs').readFileSync('chrome-extension/manifest.json','utf8')); console.log('manifest JSON OK')"
manifest JSON OK

# Rule 2 add — derivation test
$ node chrome-extension/tests/test_manifest_key.mjs
  PASS: manifest.json has "key" field
  PASS: key value is base64 DER (392 chars, base64 alphabet only)
  PASS: derived chrome.runtime.id == anigikcnmldoklcmogffmgcojdhhficb
3 passed, 0 failed

# Legacy client_id eradication
$ grep -n "Ov23liy7tZekl0uEztoj" chrome-extension/background.js
(no output — clean)
```

### Manual smoke (operator post-deploy)

1. Pull the latest extension code into the operator's local checkout.
2. Reload the unpacked extension in Chrome (`chrome://extensions` → reload).
3. Open the service worker DevTools console and run `chrome.runtime.id` — must equal `anigikcnmldoklcmogffmgcojdhhficb`.
4. Click "Sign in with GitHub" in the extension popup — GitHub consent screen MUST show the new App "xbrain" (not the old OAuth App `xbrain`).
5. After consent, user lands back in the popup with an `xbt_` token stored.

Once these 5 steps pass, GHAPP-07 (extension surface) is functionally signed off. Plan 12-11 UAT scenario 5 will exercise the same path end-to-end.

## Deviations from Plan

### Auto-added items (Rule 2 — missing critical functionality)

**1. [Rule 2 — Missing test] Added `chrome-extension/tests/test_manifest_key.mjs`**
- **Found during:** Task 2 (after writing the new test, the first run failed with a non-printable derived ID, revealing a bug in my own derivation code — see Rule 1 fix below).
- **Issue:** The user-prompt's success criteria explicitly required: "Verification: derive chrome.runtime.id from the manifest key in a test/script + assert == `anigikcnmldoklcmogffmgcojdhhficb`". The PLAN (12-08-PLAN.md) only specified manual verification via `chrome.runtime.id` console inspection — not an automated CI-grade test. Without the test, a future maintainer could silently rotate the manifest key (or introduce stray whitespace via editor auto-format) and only notice when end-to-end sign-in fails in production.
- **Fix:** Added `chrome-extension/tests/test_manifest_key.mjs` with three assertions (key field present, base64 alphabet + length, derived ID matches `anigikcnmldoklcmogffmgcojdhhficb`). Uses only Node stdlib (`crypto` + `fs`), no external deps.
- **Files added:** `chrome-extension/tests/test_manifest_key.mjs`
- **Commit:** `d62d723`

### Auto-fixed bugs (Rule 1)

**1. [Rule 1 — Bug] Fixed naive hex→letter shift in derivation test**
- **Found during:** First run of `test_manifest_key.mjs` — derived ID was `aigicdcgffgcjdhhficb` with embedded non-printable bytes instead of `anigikcnmldoklcmogffmgcojdhhficb`.
- **Issue:** Initial implementation used `String.fromCharCode(c.charCodeAt(0) + 'a'.charCodeAt(0) - '0'.charCodeAt(0))` assuming `[0-9]` and `[a-p]` were ASCII-adjacent. They are not: `'9'` is 0x39, `'a'` is 0x61 — the shift formula produces non-printable bytes for `'0'..'9'` and wrong letters for `'a'..'f'`.
- **Fix:** Replaced with an explicit `{ '0':'a', '1':'b', ..., 'f':'p' }` lookup table. Added a clarifying comment about the ASCII gap so a future maintainer doesn't try to "optimize" back to charCode arithmetic.
- **Files modified:** `chrome-extension/tests/test_manifest_key.mjs`
- **Commit:** `d62d723` (same atomic commit as Task 2 since the test was being introduced in that commit)

## Out of scope (deferred — Phase 9 issues)

Documented in `.planning/phases/12-github-app-migration-public-deployment-ready-auth/deferred-items.md` (commit `284036d`):

- `chrome-extension/tests/test_translate_sse.mjs` — pre-existing failure in Phase 9 SSE translator.
- `chrome-extension/tests/test_ws_keepalive.mjs` — pre-existing CommonJS-vs-ESM export mismatch in Phase 9. Note: the extension itself runs fine in Chrome (MV3 module loader is more permissive than Node ESM strict). Only the Node test is broken.

Both should be triaged as separate quick tasks. Per scope boundary rule, NOT auto-fixed in Plan 12-08.

## Risks observed (none material)

- **Risk: existing single user (mrboups) has the OLD random extension ID and breaks on update.** Mitigated by manifest `key` field: Chrome detects the mismatch on next reload and migrates `chrome.runtime.id` automatically. Acceptable v1 cost (only mrboups has the extension installed today — he reloads, and his old `xbt_` token in `chrome.storage.local` survives the ID change). Documented in PLAN-12-08 Section 4 risk register.
- **Risk: GitHub App callback URLs do NOT include the new chromiumapp.org URL.** Mitigated by operator prep on 2026-05-17 — callback URL #2 `https://anigikcnmldoklcmogffmgcojdhhficb.chromiumapp.org/` was registered before execution (per memory note `xbrain-phase12-operator-prep`).

## Known Stubs

None. All implementation is wired end-to-end. The KB doc and the test are CI-grade; nothing is mocked.

## Threat Flags

None new. The plan's threat-model surface (Chrome extension OAuth flow) was already mapped in Phase 9/10. Phase 12 narrows the trust boundary (single GitHub App instead of OAuth App) without introducing new network surface.

## Self-Check: PASSED

All claims in this SUMMARY verified against the filesystem and git log:

**Files (7/7 found):**
- FOUND: `chrome-extension/.gitignore`
- FOUND: `chrome-extension/tests/test_manifest_key.mjs`
- FOUND: `.planning/KB/chrome-extension-key.md`
- FOUND: `.planning/phases/12-github-app-migration-public-deployment-ready-auth/deferred-items.md`
- FOUND: `chrome-extension/manifest.json` (modified — `key` field present)
- FOUND: `chrome-extension/background.js` (modified — `GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof"`)
- FOUND: `.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-08-SUMMARY.md` (this file)

**Commits (3/3 found in git log):**
- FOUND: `37c7897` — `docs(12-08): KB article on stable-ID keypair + gitignore PEM`
- FOUND: `d62d723` — `feat(12-08): add manifest key + switch to GitHub App client_id`
- FOUND: `284036d` — `chore(12-08): log pre-existing Phase 9 test failures as deferred`

**Functional re-verification:**
- `node chrome-extension/tests/test_manifest_key.mjs` → 3/3 PASS (derived ID == `anigikcnmldoklcmogffmgcojdhhficb`)
