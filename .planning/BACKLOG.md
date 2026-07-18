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

## Cross-user conversation leak — `conversations.py:92` (LIVE prod bug)

**Found:** 2026-07-13, by the Phase 18 auth-architecture audit. Surfaced to the user. NOT folded into Phase 18 (kept auth-focused); logged here for a dedicated fix.

**The bug:** `apps/memory-api/app/routes/conversations.py:92` —
`owner_filter = principal["user"].id if principal["kind"] == "user" else None`.
For a `kind="user_api_token"` principal — which is **every extension user after onboarding**, since both Google and GitHub sign-in end by minting an `xbt_` token — `owner_filter` becomes `None`, and `list_conversations` treats `None` as "no filter". So **every team member sees every other member's conversations**, contradicting the code's own comment ("Users see only their own conversations within the team").

**Severity:** real cross-user privacy leak, affecting production now, not a future risk. Team-scope isolation still holds (you only see *your team's* data) — but within a team, per-user conversation privacy is gone for `xbt_` sessions.

**Fix:** one line — gate on `principal.get("user")` being truthy rather than strict `kind == "user"`, so `user_api_token` (which has a fully-populated `user`) also gets its `owner_filter`. Then add a regression test: two members of the same team, each `xbt_`-authenticated, must NOT see each other's conversations.

**Adjacent (same class, from the same audit):** several routes strictly gate `principal["kind"] == "user"` and 403 a valid `user_api_token` (`me.py:76-83`, `audit.py:34`, `promotions.py:58-63`). Lower severity (over-restrictive, not a leak), but worth a sweep in the same pass.

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

## Team join-by-code (Slack/Discord-style invite link)

**Requested:** 2026-07-13. Checked against live code — genuinely net-new (no code/token concept exists on teams).

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

## Push-a-link — nudge a specific member to open a page in their browser

**Requested:** 2026-07-18. Fits the existing architecture (Centrifugo + the extension) — no new infra.

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

## Catch me up — "summary since your last visit" on entering a busy chat

**Requested:** 2026-07-18. Checked against live code — half the machinery already exists.

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
