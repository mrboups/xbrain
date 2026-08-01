# Backlog — ideas captured, not yet planned

Items here are seeds for future milestones/phases. Promote to ROADMAP when ready.

---

## BL-001 — Cold-start: brain proactively interviews the team when it has no info

**Captured:** 2026-06-03 (user request)

**Idea:** When the team brain has **no (or almost no) information** about what the team
works on, the assistant should **proactively ask for project context in the chat** —
*before* the user even asks a question — so the brain learns:
- what the team is working on (projects, domain, goals),
- which kinds of information are worth capturing/curating going forward,
- who the people / key entities are.

**Why:** the differentiator is the curated team brain. A brand-new team with an empty brain
has nothing to recall, and the user doesn't know what to feed it. A guided onboarding
"interview" bootstraps the brain and teaches it the team's selection criteria (what to keep,
at what truth-level).

**Rough shape (to refine when planned):**
- Trigger: on a new team / first conversation where `memory_items` count for the team is ~0
  (detectable via team_context / a brain-size check), the assistant opens with a short,
  structured set of questions (project? domain? what should I remember? who's involved?).
- Capture: answers are written to the brain as WORKING facts (tagged source=onboarding),
  optionally proposed for VALIDATED via the promotion flow.
- Surfaces: LibreChat (system-prompt-driven opener) + the extension team chat (@claude).
- Guardrail: only fires once (don't nag); skippable; respects the empty-brain detection so it
  doesn't trigger for established teams.

**Open questions:** where the "opener" is injected (modelSpecs promptPrefix vs librechat-bridge
vs team_chat_agent); how to detect "empty brain" cheaply per team; how aggressive (one opener
vs a multi-turn wizard); whether to also drive truth-level selection criteria.

---

## BL-002 — "Connect Google" for a GitHub-primary user → Drive/Calendar/Granola

**Captured:** 2026-06-03 (user request)

**Idea:** Today the extension + web sign you in via GitHub OR Google, both minting the universal
`xbt_token`, and identity-merge unifies linked accounts. But a **GitHub-signed-in user (no Google)
cannot reach Google-only features** (Drive sync, Calendar, Granola) from the UI — there's no
"connect Google" button, and the linking UI that exists only goes Google→+GitHub (web
`/account/teams/` "Link GitHub" CTA; the extension `#github-link-row` is even dead — never shown).

**Scope (this is a small feature, not a quick fix):**
- A **Google OAuth flow with Drive/Calendar scopes** (not just login scopes) initiated from a
  "Connect Google" button (extension + web `/account`).
- **Store the Google token per-user**, encrypted (Fernet — same pattern as the GitHub tokens on
  `users`, and the Granola key on `granola_user_connections`).
- **Link it to the existing GitHub user** (same `user.id`) — do NOT create a second Google identity.
  Reuse the Phase-10 merge machinery (`find_user_by_github_id` / `follow_merge_pointer`) in reverse,
  or match by verified email.
- **UI button** in the extension (and surface the dead `#github-link-row` properly — see BL-004).

**Related (separate, smaller):** BL-004 — revive the extension's "Link GitHub" affordance
(`#github-link-row` is hidden inside the connection card which disappears post-sign-in; move it to
the chat header / a banner, shown when `state.me.github_username` is null). User OK'd doing this one
sooner; it's ~1 popup.html + popup.js edit + extension reload.

---

## BL-003 — Media + documents: store, display in chat, upload from the extension

**Captured:** 2026-06-03 (user request) · ✅ **SHIPPED 2026-06-03** — all 5 slices live (see `.planning/features/BL-003-media-design.md`). Storage (MinIO) + upload/serve endpoints + Brain Monitor render + extension upload/UI-reorg/render + LibreChat recall render. Browser-level LibreChat end-to-end is the user's final check.

**Problem today:** images/files sent to the brain are only stored as **text references** (e.g. a
`file:///C:/.../poster.jpg` local path) — nothing is actually stored or displayable. LibreChat /
extension `@claude` "can't show the image", and the Brain Monitor shows only text rows. The user
wants real media handling end-to-end.

**Asks (multi-part feature):**
1. **Store the blob, not just a path.** When an image or document (pdf/doc/md/…) is sent via
   LibreChat, the extension chat, or the clipper → upload the binary to **MinIO** (already deployed,
   S3-compatible — `xbrain-langfuse-minio`, or a dedicated bucket) and store a memory_item that
   references the object (key/URL + mime + dimensions) instead of a local path.
2. **Display it.** LibreChat + extension `@claude` should render the **image inline** (served from
   MinIO via a signed/proxied URL), and documents as a **clickable file link**.
3. **Brain Monitor** (`/account/teams/brain/?team=…`): show **images as thumbnails/inline** and
   **documents as clickable file chips**, not just text.
4. **Extension upload UX reorg:**
   - The current 📎 spot becomes the **"send a photo / document"** button (direct upload into the
     extension chat).
   - The **"launch clipper"** action moves to the **menu bar** (next to the team dropdown) as a
     text button **"add to memory"**.

**Notes / building blocks:** MinIO is already running (used by Langfuse) + memory-api already has
`MINIO_*` env + boto3 (used by the deck/wipe paths) → object storage is available. Need: an upload
endpoint (memory-api) that puts to MinIO + returns a key, a served/proxied URL with team-scoped
auth, mime/type handling, the tagging contract on the media memory_item, and the three render
surfaces (LibreChat, extension, Brain Monitor). Sizeable — plan as its own phase.

---

## Agent mention alias — settable from the Settings UI, not just `.env`

**Requested:** 2026-07-12 (mid-Phase-14). **Not in Phase 14 scope** — logged here rather than
improvised into a plan-checked, half-executed phase.

**Context / decided naming:** the chat agent is summoned with `@chad`, and the product rebrand target
is `teamchad.ai` (replacing the grooveos.app naming). `@agent` MUST keep working as well — the alias
list is additive, not a replacement.

**What Phase 14 already gives us (the substrate — do not redo):**
- `AGENT_MENTION_ALIASES` (added by 14-01, decision D-08) — comma-separated, no leading `@`,
  code default `"agent"`. `apps/memory-api/app/config.py`. The mention detector reads it; nothing is
  hardcoded any more.
- So the deployed value becomes `agent,chad` — both `@agent` and `@chad` resolve. Setting it is a
  `.env` change today, with no code change and no redeploy of source.
- Likewise `XBRAIN_BASE_DOMAIN` (14-03a) makes `teamchad.ai` a config value, not a hardcode.

**What is still missing (this backlog item):** an in-app option so the alias can be changed from the
Settings UI instead of editing `.env` on the VM and restarting. Shape to design:
- Per-team or global? Per-team is consistent with the rest of the product (team_scope everywhere), but
  the mention detector currently resolves aliases process-wide — this is the real design question.
- Needs a persisted override (Postgres) that takes precedence over the env default, a settings surface
  (likely alongside the existing team admin / Brain Monitor UIs), and cache invalidation so a changed
  alias takes effect without a restart.
- Keep the env var as the fallback/bootstrap default so a fresh self-hosted install still works with
  zero configuration.

**Sizing:** small-to-medium. Candidate for Phase 15 (Edition Mechanics) or a dedicated slice.

---

## Telegram bridge — chat in your team chat from Telegram

**Requested:** 2026-07-12. Feasibility checked against the live code, not assumed.

**Verdict: feasible, and a clean plug-in — not a rewrite.** The team chat is a real backend in
memory-api, not something buried in the web frontend. The whole surface is a handful of endpoints
(`apps/memory-api/app/routes/team_chat.py`):
- `POST /v1/teams/{team_id}/messages` — inserts the message, publishes to Centrifugo, and enqueues the
  agent task if a mention is detected
- `GET /v1/teams/{team_id}/messages` — history
- `POST /v1/me/centrifugo-token` — realtime subscription token

The web team chat is a thin client over exactly these. So a Telegram bridge is **another frontend**,
consistent with the project's multi-frontend invariant. Three in-repo precedents to copy the shape
from: `apps/librechat-bridge`, `apps/session-bridge`, `apps/openwebui-pipeline`.

**Shape:**
- Inbound: Telegram Bot API webhook → `telegram-bridge` adapter → `POST /v1/teams/{id}/messages`
- Outbound: adapter subscribes to the team's Centrifugo channel → `sendMessage` back to the Telegram chat
- `@chad` works for free: `mention_detector.detect(body.content)` runs **server-side** on the message
  content, so mentioning the agent from Telegram summons it exactly as in the web chat. No agent work.

**Where the actual work is (NOT the transport):**
1. **Identity linking — the bulk of it.** The routes require a `principal` from a GitHub-backed JWT.
   A Telegram user only has a `telegram_user_id`. Needs a link table + a pairing flow (bot `/link` →
   one-time code → user confirms in the web app while signed in) and stored per-user tokens. Without
   this, messages cannot be attributed and the tagging contract (`source`, author) breaks. There is
   precedent for a service-principal path (`routes/internal.py` + `BRIDGE_SHARED_SECRET`).
2. **Team mapping.** Admin-set binding `telegram_chat_id → team_id`.
3. Telegram specifics: Markdown flavor, message length caps, no real threading (forum topics only).

**OPEN DECISION — must be settled BEFORE this is planned. Do not start without it:**
A Telegram group is outside xbrain's access control. Once team-brain content flows into a Telegram
group, anyone later added to that group sees it — the `team_scope` isolation that is *the* product
differentiator ends at the Telegram boundary. Two candidate positions:
- **(a) Chat-only.** Messages relay both ways, but memory recall/brain content never egresses to
  Telegram. Safe, less useful.
- **(b) Full member.** The bound Telegram group is treated as a full team surface, brain content
  included. Useful, but the operator owns the leak risk and it must be explicit and consented.

Sizing: modest phase, dominated by the identity-pairing work — but blocked on the decision above.

---

## ~~Cross-user conversation leak — `conversations.py:92`~~ — NOT A BUG (resolved 2026-07-19)

**Resolution (2026-07-19, user decision):** this was a **false alarm**. xbrain is a **per-team group chat**; every team member is *supposed* to see the team's conversations — there is no per-user privacy within a team today (one-to-one is a possible future feature, not current). See memory `project_xbrain_team_shared_no_1to1`.

**What was actually fixed:** an *inconsistency* in the other direction. `list_conversations` filtered `kind=="user"` principals to their own rows but let `user_api_token` principals (every extension user post-onboarding) see all — so two members of the same team saw DIFFERENT scopes depending on auth kind. Unified to **team-shared** (`owner_filter = None`) + corrected the misleading "users see only their own" comment (commit on 2026-07-19). `team_scope` isolation (team A ≠ team B) is unaffected and remains the real invariant.

**Future:** when one-to-one / private conversations are built, per-conversation owner scoping returns as an **opt-in per conversation**, not as an auth-kind side effect.

**Adjacent (still worth a look, separate concern):** several routes strictly gate `principal["kind"] == "user"` and 403 a valid `user_api_token` (`me.py:76-83`, `audit.py:34`, `promotions.py:58-63`). That's over-restrictive (a real extension user gets 403 on their own data), NOT a privacy issue — worth a small sweep so `user_api_token` principals aren't wrongly rejected. Low priority.

---

## test_phase10_auth.py — 6 pre-existing failures from stale Phase-12 fixtures

**Found:** 2026-07-13, by the Phase 18 code review + gate build. NOT Phase 18's — logged for a dedicated pass.

The Phase 18 fix already cleared ONE of the original 7 failures (the real `merge.py`
`memory_promotions`→`promotions` table-name bug, which WAS a live production 500 on GitHub-signin
convergence — fixed in commit d2b6621 because Phase 18's local auth made it reachable). The remaining
**6** failures in `apps/memory-api/tests/test_phase10_auth.py` are test-fixture staleness from the
Phase 12 GitHub App migration, not product bugs:
- the fixture sets `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` (the pre-Phase-12 OAuth App vars) instead
  of the post-Phase-12 `GITHUB_APP_CLIENT_ID`/`GITHUB_APP_CLIENT_SECRET`;
- that same fixture's mocked token-exchange response body omits the `refresh_token` field the Phase 12
  rewrite now requires.

Both are in Phase-10/12-vintage test files this session's phases must not touch (scope boundary).
Fix = update the fixture to the Phase-12 var names + add the refresh_token to the mock. Small, but its
own pass. They have been part of the "57 failed" pre-existing baseline all along.

---

## ~~Team join-by-code (Slack/Discord-style invite link)~~ — SHIPPED 2026-07-31 (Phase 25)

**Resolved:** invite codes are hashed at rest (sha256) and redeemed through a conditional
UPDATE (`redeem_atomic`) so a race cannot over-consume a single-use code. The one-click
landing page is `app-site/join/`, live at https://grooveos.app/join/, and it accepts both
Google sign-in and email/password. The code travels in the URL **fragment** (`#c=`), never
the query string, and is stripped from history on arrival.

### Original

**What the user wants:** on creating a team, generate a code; anyone who submits that code joins the
team chat, without an individual invite. The current model has NO such concept — only:
- `POST /teams/{id}/join` (direct self-join, `open` teams only — gate is knowing team_id, not a secret)
- `POST /teams/{id}/join-request` (closed teams, admin-approved)
- `POST /teams/{id}/invite` (invite-by-email an ALREADY-existing xbrain user)
`teams` has `visibility ('open'|'closed')` and no code/token column.

**Shape (small, plugs into existing `add_member`):**
- New table `team_invite_codes(id, code UNIQUE, team_id FK, role, expires_at, max_uses/uses_remaining,
  created_by, revoked_at)`. A table, not a column — supports multiple codes, expiry, revocation, per-code role.
- `POST /teams/{id}/invite-codes` (admin) → mint a random code, return once.
- `POST /teams/join-by-code {code}` → resolve → `add_member(caller)`. No individual invite needed.
- `DELETE /teams/{id}/invite-codes/{code_id}` → revoke.
- UI: a "create invite link" action in the team-admin surface (extension Settings + app-site/account/teams).

**SECURITY — must be built in, not bolted on:** a join-code is a BEARER SECRET — whoever holds it gets
inside the team brain (team-scoped memory, the product's sensitive core). So the code MUST be
random/unguessable, REVOCABLE, and EXPIRING (and ideally max-uses-limited). A leaked permanent code is a
standing open door — the same team_scope-leak class flagged for the Telegram bridge. The redeemer still
needs an xbrain account (sign in once) — frictionless now that Phase 18 ships email/password.

Sizing: small phase / slice. Blocked on nothing; schedule after v2.0 (16, 17) or as a standalone.

---

## ~~Push-a-link — nudge a specific member to open a page in their browser~~ — SHIPPED 2026-07-31 (Phase 22)

**Resolved:** `POST /v1/teams/{id}/nudge-open` with a single `target_user_id`, delivered over
the existing Centrifugo `user:<sub>` channel. Per the owner's ruling the nudge **notifies and
invites** — it never auto-opens unless the recipient has opted in via the
`autoOpenLinkRequests` setting (default off). Reachable from the people overlay and from a
click on a member in the chat.

**Still open:** the team-wide send is a CLIENT fan-out — see "Team-wide nudge belongs
server-side" below.

### Original

**What the user wants:** from the chat, target a specific member with a URL. That member gets a
notification ("someone wants to open a page"), and on their confirmation it opens as a new tab in
their browser.

**Shape (small — 1 endpoint + 1 targeted event + 1 extension handler + a consent UI):**
- `POST /v1/teams/{id}/nudge-open` (or `/v1/users/{id}/nudge-open`) → validates sender is a team
  member and target is a team member → publishes a **targeted Centrifugo event** to the target's
  personal channel (`user:#<id>`): `{ type:'open_url', url, from, team_id }`. Reuse the existing
  Centrifugo publish path from `team_chat.py`.
- Extension (already subscribed to the user channel) receives the event → **native OS notification**
  via `chrome.notifications.create` showing the **sender + full destination URL**.
- On the user clicking **Open**, the extension calls `chrome.tabs.create({ url })`. Tab opens.

**SECURITY — build in, do not bolt on (this is the whole risk of the feature):**
- **Consent-gated, never silent auto-open.** Opening a tab on someone's machine from another user's
  message is a phishing/abuse vector. Always show sender + the real, un-shortened URL and require an
  explicit click. (Browsers also block programmatic tab-open without a user gesture — the consent
  click doubles as that gesture.)
- Restrict to team members; rate-limit per sender; show the true destination (expand/redirect-resolve
  shortened URLs, or reject them); a recipient-side setting to disable "allow open-link requests".
- Same `team_scope`-boundary discipline as the Telegram bridge and join-by-code items.

**Offline delivery:** if the target's extension is closed, Centrifugo won't deliver live. Options:
persist a "pending nudge" fetched on reconnect, OR Web Push via the extension service worker (fires
with the popup closed). Delivery when the browser is fully closed is limited — document, don't promise.

**Sizing:** small. Blocked on nothing.

---

## ~~Catch me up — "summary since your last visit" on entering a busy chat~~ — SHIPPED 2026-07-31 (Phase 23)

**Resolved:** the read cursor this item said was missing now exists (`team_members.last_read_at`),
with `/unread-summary` behind it — the same endpoint that feeds the unread badges on the team
rail. The banner is non-intrusive and opt-in, per the original note.

**Gate lesson recorded:** the first implementation silently swallowed its own banner —
`scrollToBottom` fires a native scroll event, whose handler marked the chat read *before* the
banner was captured. Fixed with a `readyForAutoMarkRead` flag; the ordering is now a test.

### Original

**What the user wants:** when a member opens the team chat and a lot has happened since they were
last here, offer them a summary of the important things since their last visit.

**What already exists (do not rebuild):**
- **Summarization is basically free.** `team_chat_agent.py:handle_claude_mention` already builds a
  context bundle and calls Claude; `get_agent_context_bundle` (`team_chat.py:265`) already assembles
  recent messages for the agent. A "catch me up" is a specialized agent invocation scoped to
  "messages since last visit."
- **Brain-grounded importance (the differentiator).** Every message is already ingested as a WORKING
  memory_item + vector (`team_chat.py:221`). So the summary can prioritize what's *important*
  (decisions, validated facts, questions/@-mentions directed at the returning user) via the brain —
  not just replay the last N messages by recency.

**What is missing (the net-new work):**
1. **A read cursor — there is none today.** `TeamMember` (`models/team.py:34`) has `joined_at`,
   `blocked_at`, `role` but **no `last_read_at`**. (The only `last_seen_at` in the codebase is on
   `user_external_sessions` — a 90s presence heartbeat used at `team_chat_agent.py:343-359`, NOT a
   chat read cursor.) Add `team_members.last_read_at` (simplest — one row per membership) + a
   `POST /teams/{id}/mark-read` the client calls on focus / scroll-to-bottom.
2. **A `since` query — list only supports `before`.** `list_team_messages` (`team_chat.py:149`) does
   `before` (older-than) pagination but not `after`. Add `after_created_at` to `tm_repo.list_messages`
   (mirrors the existing `before_created_at`) to fetch "messages since last_read_at" + a count.
3. **Trigger + UX.** On open, compute unread count since `last_read_at`; only surface a **non-intrusive
   opt-in** "Catch me up" affordance when volume is meaningful (e.g. ≥10 new messages, or first visit
   in >X hours). On click → the agent summarizes messages since `last_read_at`, brain-grounded, with
   the same truth-level source chips as other agent replies.

**Guardrails:** never auto-run (LLM cost) — opt-in click only, rate-limited. Don't nag (threshold-gated).
Team-scoped, so no new privacy surface (the team chat is already shared among members). Keep the summary
ephemeral (a reply in-thread or a dismissible banner), not a persisted message everyone sees.

**Sizing:** small-to-medium. Blocked on nothing. Natural companion to the in-chat agent work.

---

## ~~Document body extraction on upload — `media.py` embeds only the caption, not the file content~~ — SHIPPED 2026-07-31 (Phase 24)

**Resolved:** uploads now extract the document body (pdf/docx/md/txt), chunk it and embed the
chunks, so a file's *contents* are recallable and not just its caption.

**Two gate lessons recorded:** (1) the sync pypdf/python-docx parse ran on the event loop with
`UVICORN_WORKERS=1`, freezing the whole API for the duration of a large PDF — now wrapped in
`asyncio.to_thread`; (2) the `no_text_layer` patch was built from a closure snapshot while
`provider.update()` replaces metadata wholesale, so it silently dropped concurrent writes — now
a fresh `provider.get` then merge.

### Original

**The gap:** `apps/memory-api/app/routes/media.py:111` builds the embedded text from `caption or filename`.
The uploaded **bytes go to MinIO and are never extracted or embedded**. So "upload a document and have it
semantically retrievable" only works to the extent the user typed a caption — the document's actual body
(PDF/DOCX/MD text) is NOT in the brain and cannot be retrieved semantically.

**Why it matters:** it partially hollows out the Phase-16 SC#3 promise ("uploads/analyzes a document, has it
ingested and semantically retrieved"). The 16-04 gate had to assert against the caption to be truthful — the
executor flagged this rather than writing a check that silently proved nothing.

**Shape (its own plan):**
- A text-extraction step on the media-upload path: PDF (pypdf/pdfminer), DOCX, plain text/markdown → text.
- Chunk + embed the extracted body (the local keyless embedder from Phase 19 already handles the vectors),
  carrying the full 7-field tagging contract, linked back to the MinIO object.
- Size/type guards (skip or truncate huge files; skip binaries with no text layer) + a clear "no text layer"
  outcome for scanned PDFs rather than a silent empty embed.
- Update the 16-04 gate check to assert on the extracted BODY once this lands.

**Sizing:** medium (a real ingestion feature, not a fix). Blocked on nothing.

---

## Client/server agent-mention desync — the extension detects `@grooveos`, the server answers `@agent`/`@chad`

**Found:** 2026-07-18, by the Phase 20 restyle (20-03) while root-causing 3 long-standing "pre-existing" test failures.

**The desync — three different vocabularies for the same feature:**
- **Client:** `chrome-extension/chat_stream.js:22` — `MENTION_RE = /@(grooveos|groove|gr|g)/i` (stale grooveos-era branding).
- **Server:** `AGENT_MENTION_ALIASES` (`.env.example:122`, default `agent`; deployed `agent,chad`) — `mention_detector.detect()` runs SERVER-side on the message content and is what actually summons the agent.
- **Tests:** `chrome-extension/tests/test_chat_stream.mjs` still asserts `@claude` — which matches neither.

**Impact (real, not cosmetic):** the server is the source of truth, so `@agent`/`@chad` DO summon the agent — but any client-side affordance driven by `detectMentionClient` (optimistic "the agent will reply" UI) does **not** fire for the aliases users are actually told to use, and *does* fire for `@g`/`@gr`, which the server ignores. The 3 failing `detectMentionClient` tests are the symptom that kept getting waved off as "stale fixtures".

**Fix shape:** make the client stop hardcoding aliases — have it read the alias list the server already exposes (or ship it via the existing config/`/v1/me`-style surface) so one source of truth drives both, then update `test_chat_stream.mjs` to the real aliases. Relates to the "Agent mention alias — settable from the Settings UI" backlog item above: both want the alias list to stop being hardcoded in two places.

**Sizing:** small. Blocked on nothing.

## Board websocket: enforce token lifetime on the LIVE connection (codex P1, 2026-07-19)

**Found:** 2026-07-19 by a codex adversarial review of Phase 26a (on top of the passing
gsd-code-review + live gate). The cross-team boundary itself HOLDS — this is a lifetime gap.

**The gap:** Hocuspocus `onAuthenticate` (`apps/hocuspocus/src/auth.mjs`) validates the board
token's `exp` and the `board_id === documentName` match ONCE, at the websocket handshake.
An ESTABLISHED connection is never revalidated (`server.mjs`), so a client that connected
before its token expired — or before an admin blocked them / revoked membership — keeps
read/write access for as long as the socket stays open. The stated contract ("a revoked
member loses access within one token TTL") is only true for NEW connections.

**Why it needs its own slice (not a quick patch):** the fix is a design addition —
enforce a per-connection max lifetime = the token's remaining TTL (force-close on expiry
via a server-side timer keyed off `exp`), and ideally a revocation signal so a block takes
effect faster than one TTL. It touches the live auth path, so it MUST re-run the 26-07
live gate (two-client convergence + the cross-language rejection matrix + a NEW "socket
closes at exp" assertion) — patching it without that boot gate would repeat the exact
"check that never traverses the real path" trap that the connectionConfig bug fell into.

**Sizing:** small-medium, security hardening. Pairs naturally with 26b (the next board slice).
Bounded default: cap connection lifetime at BOARD_TOKEN_TTL_S so the worst-case stale-access
window equals one token TTL (1h), matching the documented contract.

**Also from the same review (already fixed 2026-07-19, for the record):** require `exp`
as an essential claim + a generic no-oracle message in the Python `verify_board_token`;
try/finally the SPA fragment-strip so a malformed token can't linger. The P1 above is the
only item deferred.

## Team-wide nudge belongs server-side (client fan-out today) — 2026-08-01

**Shipped as a deliberate stopgap, agreed with the owner.** The people overlay's "Send to
everyone" loops over `GET /v1/teams/{id}/members` and fires ONE
`POST /v1/teams/{id}/nudge-open` per member from the extension, because that endpoint takes a
single `target_user_id`.

**Why it needs replacing:** it burns the caller's per-user rate-limit budget (N sends count as N),
it is not atomic (a failure halfway leaves some members notified and some not, with no way to
retry just the rest), and it scales linearly with team size from a client that may be closed
mid-loop.

**Shape:** `POST /v1/teams/{id}/nudge-open-all {url}` — one membership read, one URL validation,
one publish per member server-side, one rate-limit charge. Same guards as the single-target
version (sender membership, blocked-member exclusion, URL safety, self-skip). The client then
makes one call and reports one result.

**Sizing:** small. Blocked on nothing.

---

## ~~Send a FILE to a teammate~~ — SHIPPED 2026-08-01 (commit d331e0c + client)

**Resolved:** `POST /v1/media/upload` now returns `signed_url`; the people overlay uploads then
nudges that URL. Save-to-device (`chrome.downloads` + the manifest permission) is STILL open.
Original note below.

### Original

The people overlay can send a LINK today (Phase 22 nudge). Sending a FILE cannot work yet: the
upload response returns `raw_path` (`/v1/media/{id}/raw`), which requires `Authorization` +
`X-Team-Scope` headers — a browser opening a nudged URL sends neither. The signed variant
`GET /media/{id}/img?t=<token>` exists but its token is minted server-side only
(`mint_media_token`, used by `/v1/brain/events` and `_serialize_message`), so the sender's client
has no way to produce one.

**Shape:** include a signed URL in the `POST /v1/media/upload` response (mint_media_token is
already imported in that module's neighbours), then "send file" = upload → nudge the signed URL.
Same TTL discipline as the existing media tokens.

**Also requested:** the recipient should be able to SAVE the file to their device (not Drive —
the owner corrected this explicitly on 2026-08-01). That is `chrome.downloads.download()`, which
needs the `downloads` permission added to manifest.json — a permission change users are prompted
about, so it should ship with the feature rather than ahead of it. NOTE: Drive-side saving would
need the `drive.file` WRITE scope; the project only requests `drive.readonly` today.

**Sizing:** small backend + small client. Blocked on nothing.

---

## ~~Click a member IN THE CHAT~~ — SHIPPED 2026-08-01

**Resolved:** clicking a teammate's name in the chat opens the people overlay with their row
highlighted. Original note below.

### Original

Requested alongside the people overlay: clicking a message author should offer the same
send-link / send-file actions the overlay provides, so you can act on the person you are reading
rather than reopening a list. Depends on the file item above for the file half; the link half
works today via the existing nudge.

**Sizing:** small (a popover anchored to the author element, reusing sendLinkToMember).

---
