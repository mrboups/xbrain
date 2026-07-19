---
phase: 22-push-a-link
reviewed: 2026-07-19T00:00:00Z
depth: deep
files_reviewed: 12
files_reviewed_list:
  - apps/memory-api/app/routes/team_chat.py
  - apps/memory-api/app/services/url_safety.py
  - apps/memory-api/app/config.py
  - chrome-extension/nudge_open.js
  - chrome-extension/popup.js
  - chrome-extension/background.js
  - chrome-extension/settings.js
  - chrome-extension/options.html
  - chrome-extension/options.js
  - chrome-extension/popup.html
  - chrome-extension/popup.css
  - docs/push-a-link.md
findings:
  critical: 0
  warning: 3
  info: 2
  total: 5
status: issues_found
---

# Phase 22: Code Review Report

**Reviewed:** 2026-07-19T00:00:00Z
**Depth:** deep
**Files Reviewed:** 12
**Status:** issues_found

## Summary

The core AuthZ invariant the phase is built on holds up under adversarial tracing:
the sender-membership check (`_resolve_team_and_check_membership`, pre-existing)
and the new target-membership check in `nudge_open()` both run server-side, both
return the *same* generic 403 for "non-member" and "unknown user id" (no
enumeration oracle), and the publish channel is built exclusively from the
DB-resolved `target.source_user_id` — the request body's `target_user_id` UUID
never reaches the Centrifugo channel string directly. The real-Postgres gate
(`test_nudge_open_gate.py`) actually exercises the cross-team/non-member/bad-url/
rate-limit paths against a captured publish recorder rather than mocking them
away, and its assertions match the code. The consent model is also structurally
sound: `nudge_open.js` has no tab-opening capability (enforced by both hand-review
and a grep-based node test), and `background.js`'s `chrome.notifications.onClicked`
listener is the only call site that invokes `chrome.tabs.create`, gated on an
explicit user click. `is_safe_nudge_url` is genuinely pure — no imports of any
network client, confirmed by reading the whole module.

That said, three real gaps survive a closer look, all in the "URL is safe" /
"nudge reaches only the intended, still-valid audience" territory the plan's own
threat model calls out:

1. **Both** the server's `is_safe_nudge_url` and the client's `isSafeHttpUrl`
   accept URLs carrying embedded userinfo (`https://accounts.google.com@evil.tld/...`),
   which defeats the stated anti-spoofing purpose of "show the recipient the full,
   un-shortened URL so they see exactly where they'd go" — a one-line payload
   hides the true host behind what looks like a trusted domain.
2. The single tab-opening call site (`background.js`'s `onClicked` handler) trusts
   the URL it reads back out of `chrome.storage.session` without re-checking its
   scheme, breaking the "defense in depth" pattern the rest of this phase follows
   (both layers re-validate everywhere else).
3. The new target-membership check reuses `teams_repo.get_membership()` without
   filtering `blocked_at`, so a blocked team member can still be selected as a
   nudge target (inconsistent with the block feature's "denies all scoped API
   access" elsewhere in this codebase, e.g. `deps.py`'s `X-Team-Scope` path and
   `get_team_admins_emails`'s `blocked_at IS NULL` filter).

No BLOCKER-grade defect (no cross-team leak, no auto-open-without-click path, no
secret/credential exposure) was found. The three findings below are WARNING-level;
two additional INFO-level polish items are also listed.

## Warnings

### WR-01: `is_safe_nudge_url` / `isSafeHttpUrl` accept URLs with embedded userinfo (spoofing bypass)

**File:** `apps/memory-api/app/services/url_safety.py:58-63`, mirrored in `chrome-extension/nudge_open.js:34-43`

**Issue:** Both the server guard and the client pre-check only verify `scheme in
{http, https}` and `netloc` non-empty. `urlsplit()`/`URL()` both parse
`user:pass@host` (or just `trusted-looking-name@host`) as a valid, non-empty
netloc, so a payload like:

```
https://accounts.google.com@evil.example/steal
```

passes `is_safe_nudge_url()` server-side, passes `isSafeHttpUrl()` client-side,
and is shown to the recipient verbatim in the notification (`message: data.url`,
`nudge_open.js:82`) — the entire point of showing the literal, un-shortened URL
(doc: "so the recipient sees exactly where they would go", T-22-10) is to let the
recipient visually verify the destination. Embedded userinfo defeats that: the
"trusted" hostname sits first in the string and the real destination
(`evil.example`) can be pushed past the visible/scannable portion of a
notification bubble. This is CWE-1021-class UI-redressing/spoofing, reachable by
any authenticated team member against any teammate — no other checks stand in
the way (confirmed with a local repro: `urlsplit("https://accounts.google.com@evil.example/x").netloc == "accounts.google.com@evil.example"`, non-empty, scheme `https`).

**Fix:**
```python
# apps/memory-api/app/services/url_safety.py
    if not parts.netloc:  # host must be present (rejects "http://", scheme-relative)
        return False
    if parts.username is not None or parts.password is not None:
        # Reject embedded userinfo — "user@host" / "user:pass@host" lets a sender
        # hide the real destination behind a trusted-looking prefix (CWE-1021).
        return False
```
```js
// chrome-extension/nudge_open.js
export function isSafeHttpUrl(url) {
  if (typeof url !== "string" || url.trim() === "") return false;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.username || parsed.password) return false; // reject embedded creds
  return parsed.protocol === "http:" || parsed.protocol === "https:";
}
```
Add a test row for `"https://accounts.google.com@evil.example/x"` (and
`"https://user:pass@evil.example/"`) to both `test_url_safety.py` and
`test_nudge_open.mjs`'s bad-URL table.

### WR-02: `chrome.tabs.create` fires on a stored URL that is never re-validated

**File:** `chrome-extension/background.js:1317-1343`

**Issue:** The `chrome.notifications.onClicked` listener — the *only* place in
the extension that can open a tab from a nudge — reads the URL straight back out
of `chrome.storage.session["nudge_" + notificationId]` and passes it directly to
`chrome.tabs.create({ url })` (line 1333) with no scheme check. Every other layer
in this phase deliberately re-validates even though a prior layer already did
(the server re-validates lexically even though the client pre-checked; the client
`nudge_open.js` re-checks the scheme even though the server already validated).
This call site breaks that pattern: it implicitly trusts that only
`handleOpenUrl()` (which does validate before calling `persistPending`) ever
writes a `nudge_*` key. That's true today (confirmed — `nudge_*` keys are written
in exactly one place, `popup.js:577`), but it is an unenforced invariant sitting
right before the single site with real navigation power, and any future writer of
a `nudge_*` key (or a bug that reorders the check/persist calls in
`handleOpenUrl`) silently regains the ability to open an unsafe scheme with no
compiler/test signal pointing at this file.

**Fix:**
```js
// chrome-extension/background.js
import { isSafeHttpUrl } from "./nudge_open.js";
// ...
      const key = "nudge_" + notificationId;
      const got = await chrome.storage.session.get(key);
      const url = got && got[key];
      if (!url || !isSafeHttpUrl(url)) return; // re-validate before navigating
      chrome.tabs.create({ url });
      await chrome.storage.session.remove(key);
```
(Importing `isSafeHttpUrl` here does not violate the `nudge_open.js`-has-no-`tabs`
structural test — that test greps `nudge_open.js` itself, not its importers.)

### WR-03: nudge target-membership check doesn't exclude blocked members

**File:** `apps/memory-api/app/routes/team_chat.py:320-324`

**Issue:**
```python
target = await users_repo.get_user_by_id(session, body.target_user_id)
if target is None or await teams_repo.get_membership(
    session, user_id=target.id, team_slug=team.slug
) is None:
    raise HTTPException(403, "target is not a member of this team")
```
`teams_repo.get_membership()` (`apps/memory-api/app/repos/teams.py:141-153`)
returns the `TeamMember` row regardless of `blocked_at` — it does not filter
`blocked_at IS NULL` the way `deps.py`'s `X-Team-Scope` resolution and
`get_team_admins_emails()` do elsewhere in this codebase. A blocked team member
therefore still passes the "target must be a member of the SAME team" check and
can be nudged. The client-side send-link picker (`popup.js:207`) filters
`!m.blocked_at` out of the dropdown, but that's UX-only — a modified/scripted
client can still POST a blocked member's `user_id` directly and the server will
accept it, which is inconsistent with the block feature's documented intent
("Block this member — keeps the row, denies all scoped API access", per
`options.html:336`). Impact is limited (a blocked member gains no capability,
just still receives a notification), but it's a real inconsistency in a place
that is explicitly a security/moderation boundary elsewhere in the codebase, and
no test in `test_nudge_open_gate.py` covers it.

**Fix:**
```python
target = await users_repo.get_user_by_id(session, body.target_user_id)
target_membership = (
    await teams_repo.get_membership(session, user_id=target.id, team_slug=team.slug)
    if target is not None
    else None
)
if (
    target is None
    or target_membership is None
    or target_membership.blocked_at is not None
):
    raise HTTPException(403, "target is not a member of this team")
```

## Info

### IN-01: Self-nudge is blocked only in the UI, not on the server

**File:** `apps/memory-api/app/routes/team_chat.py:279-359` (no `target_user_id == sender.id` guard); UI-side filter at `chrome-extension/popup.js:205-208`

**Issue:** `populateSendLinkMembers()` excludes the caller's own `user_id` from the
picker ("you cannot nudge yourself"), but `nudge_open()` has no equivalent
server-side check — a direct POST with `target_user_id` equal to the sender's own
id passes membership validation and publishes normally. Harmless (a user just
notifies themselves), but the client comment implies a rule the server doesn't
actually enforce, which will surprise the next person extending this endpoint.

**Fix:** Either drop the implication in the UI comment, or add a cheap explicit
check for symmetry: `if body.target_user_id == sender.id: raise HTTPException(422, "cannot nudge yourself")`.

### IN-02: Rapid double-click on a nudge notification can open two tabs

**File:** `chrome-extension/background.js:1329-1334`

**Issue:**
```js
const got = await chrome.storage.session.get(key);
const url = got && got[key];
if (!url) return;
chrome.tabs.create({ url });
await chrome.storage.session.remove(key);
```
The pending URL is only removed *after* `chrome.tabs.create` is issued. If
`onClicked` fires twice in quick succession for the same notification (double
click, or a screen-reader activating it twice) before the first `remove()`
resolves, both invocations read the same still-present key and both call
`tabs.create`, opening two tabs. Low impact (no security implication — the URL
was already validated once), but easy to close.

**Fix:** Remove the key before opening the tab so a concurrent read sees it gone:
```js
if (!url) return;
await chrome.storage.session.remove(key);
chrome.tabs.create({ url });
```

---

_Reviewed: 2026-07-19T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
