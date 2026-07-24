---
phase: 26-collaborative-board
reviewed: 2026-07-24T09:43:13Z
depth: deep
files_reviewed: 44
files_reviewed_list:
  - apps/board-web/.dockerignore
  - apps/board-web/.gitignore
  - apps/board-web/Dockerfile
  - apps/board-web/index.html
  - apps/board-web/nginx.conf
  - apps/board-web/package.json
  - apps/board-web/src/Board.tsx
  - apps/board-web/src/main.tsx
  - apps/board-web/src/session.ts
  - apps/board-web/src/styles.css
  - apps/board-web/src/vite-env.d.ts
  - apps/board-web/src/yjs-binding/DECISION.md
  - apps/board-web/src/yjs-binding/LICENSE
  - apps/board-web/src/yjs-binding/diff.ts
  - apps/board-web/src/yjs-binding/helpers.ts
  - apps/board-web/src/yjs-binding/index.ts
  - apps/board-web/tsconfig.json
  - apps/board-web/vite.config.ts
  - apps/hocuspocus/.gitignore
  - apps/hocuspocus/Dockerfile
  - apps/hocuspocus/package.json
  - apps/hocuspocus/src/auth.mjs
  - apps/hocuspocus/src/bridge.mjs
  - apps/hocuspocus/src/persistence.mjs
  - apps/hocuspocus/src/server.mjs
  - apps/hocuspocus/tests/gate_client.mjs
  - apps/hocuspocus/tests/run_tests.mjs
  - apps/hocuspocus/tests/test_auth.mjs
  - apps/memory-api/alembic/versions/0028_boards.py
  - apps/memory-api/app/config.py
  - apps/memory-api/app/main.py
  - apps/memory-api/app/models/board.py
  - apps/memory-api/app/repos/boards.py
  - apps/memory-api/app/routes/board_helpers.py
  - apps/memory-api/app/routes/boards.py
  - apps/memory-api/tests/test_board_gate.py
  - apps/memory-api/tests/test_board_token.py
  - chrome-extension/popup.html
  - chrome-extension/popup.js
  - chrome-extension/tests/test_popup_contract.mjs
  - infrastructure/docker-compose.ci-images.yml
  - infrastructure/docker-compose.yml
  - infrastructure/nginx/templates/70-board.conf.template
  - infrastructure/scripts/verify-phase16.sh
  - infrastructure/scripts/verify-phase17-full.sh
  - infrastructure/scripts/verify-phase26.sh
findings:
  blocker: 1
  high: 1
  medium: 2
  low: 3
  total: 7
status: issues_found
---

# Phase 26: Code Review Report — Collaborative Board (Excalidraw + Yjs)

**Reviewed:** 2026-07-24T09:43:13Z
**Depth:** deep (cross-file, including the callee `team_chat.py`/`deps.py` helpers boards.py reuses)
**Files Reviewed:** 44 (diff `4f9721e..HEAD`)
**Status:** issues_found

## Summary

The three-gate team-scope design (memory-api membership checks → token mint →
`onAuthenticate` claim-vs-`documentName` match) is real and well tested on the
Node side: `auth.mjs` pins `algorithms: ["HS256"]`, checks `scope`, `board_id
=== documentName`, and `team_scope` presence, returns one generic `DENY`
message for every rejection (no oracle), and `test_auth.mjs` includes a
genuine alg:none forged-token case. The fragment handoff (`session.ts`) reads
and strips the token before any network call and never a query parameter. The
bridge-only doc endpoints correctly reject non-bridge principals, store/return
bytes verbatim (no base64/JSON coercion), and the nginx board vhost cleanly
isolates `/collab` + `/` with no `/v1/internal` exposure. The vendored
`y-excalidraw` binding preserves its MIT license and consistently applies the
`CaptureUpdateAction.NEVER` patch at all four `updateScene()` call sites.

That said, this review found one confirmed authorization gap that
contradicts the phase's own stated security invariant, one confirmed
functional bug that undermines the doc-size DoS mitigation the phase relies
on, and several smaller issues. Findings below.

## Blocker Issues

### BL-01: Blocked (revoked) team members can still create/list boards and mint fresh board tokens

**File:** `apps/memory-api/app/routes/boards.py:110-136` (`create_board`, gate 1)
and `apps/memory-api/app/routes/boards.py:159-206` (`issue_board_token`,
gate 2), both calling into `apps/memory-api/app/routes/team_chat.py:67-81`
(`_resolve_team_and_check_membership`) → `apps/memory-api/app/repos/teams.py:141-153`
(`get_membership`)

**Issue:** `boards.py`'s docstring states this as the load-bearing design:
"the token mint RE-CHECKS membership against the BOARD's own team_id at mint
time... which is what makes a revoked member lose access within one token
TTL." But `_resolve_team_and_check_membership` only checks `membership is
None` — it never inspects `TeamMember.blocked_at`. `get_membership()` itself
returns blocked rows unfiltered (no `blocked_at IS NULL` predicate).

This codebase already has a canonical, deliberate block-enforcement point:
`deps.get_team_scope()` explicitly checks `membership.blocked_at is not None`
for both the `user` and `xbt_` branches, commented "Phase 10 GHA-03 — block
enforcement... Without the xbt_-side check, a user blocked AFTER minting a
scoped token could keep using that pre-minted token to bypass enforcement."
`_resolve_team_and_check_membership` is a *different*, parallel membership
check used by `team_chat.py` (and now reused verbatim by the new board
routes) that never received the same fix — even the pre-existing nudge
endpoint in the same file has to special-case blocked-target checking by
hand (`team_chat.py:455-459`) precisely because the shared helper doesn't do
it for you.

Concretely: a team admin blocks a member via `POST
/teams/{id}/members/{user_id}/block` (sets `blocked_at`, keeps the
`team_members` row). That member's existing session/OAuth token is
unaffected. They can still call `POST /v1/teams/{team_id}/boards` (gets the
existing board + a fresh 1-hour token) and `POST /v1/boards/{board_id}/token`
(mints another fresh token any time), and keep editing the team's live board
indefinitely — the exact scenario Phase-10 GHA-03 was written to close for
every other team-scoped surface.

**Fix:** Add the same `blocked_at` gate `deps.get_team_scope` uses, either in
`_resolve_team_and_check_membership` itself (fixes every caller, including
the pre-existing message-send path) or explicitly in `boards.py` right after
resolving membership:

```python
team = await _resolve_team_and_check_membership(session, user.id, board.team_id)
membership = await teams_repo.get_membership(session, user_id=user.id, team_slug=team.slug)
if membership is not None and membership.blocked_at is not None:
    raise HTTPException(404, _BOARD_NOT_FOUND)  # same no-oracle answer as "not found"
```

Add a regression test mirroring the existing block-enforcement tests for
`deps.get_team_scope`, scoped to `create_board` and `issue_board_token`.

## High Issues

### HI-01: Vendored asset-delta computation uses `for...in` on a `Set` — deleted images are never pruned from the shared doc

**File:** `apps/board-web/src/yjs-binding/diff.ts:191-195`

**Issue:**
```ts
for (let fileId in lastKnownFileIds) {
  if (!files.hasOwnProperty(fileId)) {
    operations.push({type: "delete", id: fileId})
  }
}
```
`lastKnownFileIds` is a `Set<string>` (see the call site in `index.ts:60`,
and the type in `diff.ts:177`). `for...in` enumerates an object's own
enumerable *string-keyed properties* — a `Set`'s elements are not exposed
that way, so this loop body never executes and `operations` never contains a
`delete` op for an asset. Verified directly (`for (let k in new
Set(['a','b','c'])) { ... }` yields nothing in Node).

Impact: this is the code path that is supposed to remove a pasted image's
base64 payload from the Y.Doc once the corresponding element is deleted from
the canvas. Because it silently no-ops, deleted images accumulate forever in
`yAssets`. The phase's own documented DoS mitigation is
`BOARD_MAX_DOC_BYTES` (16 MiB default) on `PUT
/v1/internal/boards/{id}/doc`, justified in `boards.py:262-263` as "the only
thing standing between a big paste and the memory limit" — but with asset
GC dead, the compacted doc can only grow, never shrink. Once it crosses the
cap, `store()` gets a 413 on every subsequent debounced write and the whole
team loses the ability to persist further edits to that board (existing
content survives, but nothing new saves) — a silent, cumulative denial of
service triggered by ordinary use (paste an image, delete it, repeat).

**Fix:**
```ts
for (const fileId of lastKnownFileIds) {
  if (!files.hasOwnProperty(fileId)) {
    operations.push({type: "delete", id: fileId})
  }
}
```
Add a test that pastes → deletes an image and asserts the resulting Y.Doc
size decreases (or at least that a `delete` AssetOperation is produced).

## Medium Issues

### ME-01: Board token verification does not pin the JWT algorithm to HS256 (Python side)

**File:** `apps/memory-api/app/routes/board_helpers.py:104` (also present in
the pre-existing `apps/memory-api/app/auth/__init__.py:117`
`verify_bridge_jwt`, which this file's docstring claims to mirror)

**Issue:** `verify_board_token` calls `authlib_jwt.decode(token,
settings.BRIDGE_SHARED_SECRET)` where `authlib_jwt` is `from authlib.jose
import jwt` — the library's default singleton, constructed internally as
`JsonWebToken(["HS256","HS384","HS512","RS256","RS384","RS512","ES256",
"ES256K","ES384","ES512", ...])` (confirmed by inspecting
`authlib.jose.__init__`). No `algorithms=` restriction is applied at the call
site. This means a token whose header says `alg: RS256` (or any of the other
listed algorithms) is *accepted for signature-verification attempt* — the
only reason it currently fails is that `RSAAlgorithm.prepare_key()` tries to
parse `BRIDGE_SHARED_SECRET` as a PEM/JWK RSA key and (implicitly, not by
design) fails because the secret is a plain random string.

Contrast with the Node verifier (`apps/hocuspocus/src/auth.mjs:48`), which
explicitly pins `{ algorithms: ["HS256"] }` and is unit-tested for exactly
this class of attack (`tests/test_auth.mjs`, case i, "alg:none token is
rejected (algorithm confusion)"). The Python side has no equivalent test —
`test_board_token.py` never varies the header `alg`. The module's own
docstring claims "Never hand-roll HMAC: authlib is the repo's existing JOSE
primitive (ASVS V6)" and "Mirrors verify_media_token exactly," implying
parity with the pinned Node side that does not actually exist.

`alg: none` specifically is still blocked (authlib marks it `deprecated` and
rejects deprecated algorithms whenever `algorithms` isn't explicitly
restricted), so the classic none-alg bypass does not apply here. This is
about the broader confusion surface (HS/RS/ES swap) being open by omission
rather than by design.

**Fix:** Construct a scoped verifier instead of importing the default
singleton:
```python
from authlib.jose import JsonWebToken
_board_jwt = JsonWebToken(["HS256"])
...
claims = _board_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
```
(or migrate to `joserfc`, which the repo already depends on transitively, and
pass `algorithms=["HS256"]` explicitly). Apply the same fix to
`verify_bridge_jwt` and `verify_media_token` for consistency, and add a
Python test mirroring `test_auth.mjs`'s alg-confusion case.

### ME-02: `PUT /v1/internal/boards/{id}/doc` buffers the entire body before enforcing `BOARD_MAX_DOC_BYTES`

**File:** `apps/memory-api/app/routes/boards.py:243-273`

**Issue:**
```python
body = await request.body()
if len(body) > settings.BOARD_MAX_DOC_BYTES:
    ...
    raise HTTPException(413, "board document too large")
```
The full request body is read into memory *before* the size check runs, so
the cap bounds what gets *persisted*, not what gets *buffered*. The
docstring for this endpoint (and the sibling comment in `persistence.mjs`)
frames `BOARD_MAX_DOC_BYTES` as a DoS ceiling ("Reject loudly rather than let
one pasted screenshot OOM the board container"), but as implemented a
request just under, say, several hundred MB is fully read into memory first
and only rejected afterward. This is meaningfully mitigated today because
`_require_bridge_principal` runs as a FastAPI dependency (resolved from the
`Authorization` header) before the handler body executes, so an
unauthenticated caller never reaches `request.body()` — exploitation would
require already holding a valid `scope=bridge` JWT, i.e., already having
`BRIDGE_SHARED_SECRET`. Still, it doesn't deliver the guarantee the comments
describe, and a legitimate-but-compromised or buggy Hocuspocus instance
could self-inflict this.

**Fix:** Check `request.headers.get("content-length")` against
`BOARD_MAX_DOC_BYTES` before calling `request.body()`, and/or stream the body
with a running byte counter that aborts early once the cap is exceeded.

## Low Issues

### LO-01: `verify_board_token` is dead code — no production route calls it

**File:** `apps/memory-api/app/routes/board_helpers.py:83-115`

**Issue:** `mint_board_token` is used by both `boards.py` endpoints, but
`verify_board_token` is referenced nowhere in `app/` outside its own
definition — only `tests/test_board_token.py` exercises it. Production
verification of a board token happens exclusively in
`apps/hocuspocus/src/auth.mjs`. The docstring's framing ("the third and
final team-scope gate... re-verified by the Hocuspocus server") reads as
though this function is itself part of that live gate, which it currently
is not. Not a security issue (the real gate does run, in Node), but it's
either an intended-but-unwired symmetric verifier (e.g., for a future
"validate before redirect" SPA pre-flight) or leftover code that should be
removed — as written it's misleading about what actually executes in
production.

**Fix:** Either wire it into a real call site (e.g., an optional
pre-flight/validate endpoint) or delete it and correct the docstring to say
the Python side only *mints*.

### LO-02: `onAuthenticate`'s read-only flag fails open, not closed, contrary to its own comment

**File:** `apps/hocuspocus/src/server.mjs:58-63`

**Issue:**
```js
// Guard for shape so a future upstream rename fails closed rather than
// throwing post-verification.
if (connectionConfig) connectionConfig.readOnly = claims.read_only === true;
```
If `connectionConfig` were ever falsy (the exact "future upstream rename"
scenario the comment anticipates), the branch silently skips setting
`readOnly` — authentication still **succeeds** and the socket becomes a full
read-write connection regardless of `claims.read_only`. That is fail-*open*
for the read-only enforcement, not fail-*closed* as the comment claims (the
team-scope boundary itself is unaffected — only the optional read-only
restriction would be silently dropped). Currently dormant: no code path
mints a token with `read_only=True` (`mint_board_token`'s default is
`False` and no caller overrides it — confirmed via grep), so this can't
manifest today, but the comment's stated guarantee doesn't match the code.

**Fix:** If `connectionConfig` is missing/falsy, `throw` (matching the
"fails closed" claim) rather than silently proceeding, once/if a read-only
minting path is added. Until then, correct the comment to describe the
actual (fail-open-for-this-one-flag) behavior.

### LO-03: Vendored binding dereferences `this.awareness!` unconditionally in the constructor despite `awareness` being typed optional

**File:** `apps/board-web/src/yjs-binding/index.ts:194-210`

**Issue:** `awareness?: awarenessProtocol.Awareness` is optional, and the
constructor correctly guards it everywhere else (`if (this.awareness) {
... }`), but the "init collaborators" block unconditionally does
`this.awareness!.getStates()` and `this.awareness!.clientID`. If
`ExcalidrawBinding` is ever constructed without an awareness instance (in
this codebase, `Board.tsx:91` always passes `provider.awareness ??
undefined`, and `HocuspocusProvider` always instantiates `.awareness` by
default, so this isn't reachable today), this throws a `TypeError` at
construction time instead of degrading gracefully.

**Fix:** Wrap the init-collaborators block in the same `if (this.awareness)`
guard used elsewhere, or default to an empty collaborators map when
awareness is absent.

---

_Reviewed: 2026-07-24T09:43:13Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
