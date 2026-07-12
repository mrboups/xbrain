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
