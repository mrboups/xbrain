# Phase 21: Configurable Agent Mention Aliases — Context

**Gathered:** 2026-07-19 (autonomous — user directive "remove @claude … @agent @chad or @a or name set in settings" + "then you can ship")
**Source:** BACKLOG "Agent mention alias — settable from the Settings UI" + "Client/server agent-mention desync" (merged into one feature by the user).

<domain>
## Phase Boundary

The team's agent is summoned by a **configurable, per-team alias list** — `@agent` always works, plus defaults (`chad`, `a`), plus a **custom name a team admin sets in Settings** — and `@claude` is removed. The Chrome-extension client and the memory-api server share **one source of truth** for that list, so they can never diverge again.

**IN scope:** a nullable `agent_aliases` column on `teams` (migration 0025); the `mention_detector` becomes team-aware; `team_chat.py` resolves the team's effective alias list before detecting; a GET endpoint the client reads the effective list from + a PATCH (admin) to set the team's custom alias(es); the extension client (`chat_stream.js`) building its MENTION_RE from the server list instead of the stale hardcoded `@(grooveos|groove|gr|g)`; a Settings UI field (extension) to set the team's agent name; removing every `@claude` and the grooveos-era vocabulary.

**OUT of scope:** LibreChat/Open WebUI mention paths (they don't use `team_chat.detect`); a global (per-install) name (this is per-team — see D-21-01); one-to-one chat.
</domain>

<the_hard_facts_from_live_code>
1. **Server regex is built ONCE at import, globally.** `apps/memory-api/app/services/mention_detector.py:49` — `_MENTION_RE = _build_mention_regex(settings.AGENT_MENTION_ALIASES)` at module level. `detect(content)` (line ~57) uses that global regex. Per-team requires `detect` to accept the team's alias list (and cache the compiled regex per alias-set to avoid recompiling every message).
2. **The server ALREADY escapes aliases + sorts longest-first + falls back to `agent` on empty.** `_build_mention_regex` does `re.escape(a)` per alias and `aliases.sort(key=len, reverse=True)`. So regex-injection is already handled SERVER-side; the CLIENT regex build must do the same (JS regex-escape each alias).
3. **Config default:** `apps/memory-api/app/config.py:243` — `AGENT_MENTION_ALIASES: str = "agent"` (deployed `agent,chad`). This stays the bootstrap/env default; the per-team value overrides/extends it.
4. **`teams` has no alias column.** `apps/memory-api/app/models/team.py` — id/slug/display_name/description/visibility/github_org/created_at. Add `agent_aliases: Mapped[str | None]` (nullable Text, comma-separated). Alembic head is `0024_local_credentials` → new migration `0025`.
5. **The summon happens server-side** at `apps/memory-api/app/routes/team_chat.py:243` — `mention = mention_detector.detect(body.content)`, and this call has `team_id` in scope. The client's regex only drives OPTIMISTIC UI ("agent will reply"); the server detection is what actually summons — so both must agree, but the server is authoritative.
6. **Client stale vocabulary:** `chrome-extension/chat_stream.js:22` — `MENTION_RE = /@(grooveos|groove|gr|g)/i` (grooveos-era). `popup.html` was already corrected to `@agent` in Phase 20. Extension settings surfaces exist: `options.html`/`options.js`/`settings.js`.
</the_hard_facts_from_live_code>

<decisions>
## Implementation Decisions (locked)

### D-21-01 — Per-team, additive, `@agent` always works. (aliases model)
Effective alias list for a team = the env/`AGENT_MENTION_ALIASES` defaults ∪ the team's `agent_aliases` (custom) — with **`agent` guaranteed present** no matter what (a universal default that never breaks, per the prior additive-alias decision). Env default expands to **`agent,chad,a`** (adds the short `a` the user asked for). `@claude` is removed everywhere. A team admin's custom name is ADDED, not a replacement (so `@agent` and the defaults keep working alongside the custom name).

### D-21-02 — ONE source of truth: the client derives its regex from the server list.
New `GET /v1/teams/{id}/agent-aliases` (member-readable) returns the team's **effective** alias list (defaults ∪ custom, `agent` included). `chat_stream.js` fetches it (cached, refreshed on team switch / storage change) and builds its MENTION_RE from it — JS-escaping each alias (mirror the server's `re.escape`) and longest-first — instead of hardcoding. Delete the `@(grooveos|groove|gr|g)` literal. This closes the desync class permanently: client and server read the same list.

### D-21-03 — Persistence: nullable `teams.agent_aliases` (migration 0025).
Comma-separated custom aliases; `NULL`/empty → env default only. Down-migration drops the column (but forward-only per Phase 17 — a plain additive nullable column, no data backfill).

### D-21-04 — `mention_detector` becomes team-aware, cached, backward-compatible.
`detect(content, aliases_csv: str | None = None)` — when `aliases_csv` is given, use a **per-alias-set cached** compiled regex (a small `dict[str, re.Pattern]` keyed by the normalized alias string, so we don't recompile every message); when omitted, use today's module-level default (unchanged callers keep working). `team_chat.py:243` resolves the team's effective aliases and passes them. Reuse the existing `_build_mention_regex` (already escapes + sorts).

### D-21-05 — Settings UI: a team-admin field, effect without restart.
An "Agent name" field in the extension Settings (options/settings surface), scoped to the active team, editable by a **team admin**, that PATCHes `teams.agent_aliases`. Detection reads the DB per message (via team_chat), and the client re-fetches its list on team switch / after a successful save — so a changed name takes effect with NO restart. (Web team-admin surface may mirror it; the extension is the primary product surface.)

### D-21-06 — Validation (admin PATCH). (security/robustness)
Server validates each submitted alias: charset `[A-Za-z0-9_-]` only (NO regex metacharacters, NO leading `@` — strip it), 1–32 chars, deduped, capped (e.g. ≤ 8 custom aliases), non-empty after trim. The regex build already `re.escape`s (defense in depth), but reject bad input at the edge with a clear 422. Only team admins may PATCH; members may GET. The client MUST also escape each alias when building its JS regex.

### Claude's Discretion
- Exact endpoint shape (a dedicated `/agent-aliases` vs folding into an existing team-settings PATCH) — keep it small + consistent with existing team routes.
- Whether the effective-list GET is its own route or part of an existing `/v1/teams/{id}` payload the client already fetches.
- The precise Settings UI placement (options.html vs a section in the popup settings).
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `apps/memory-api/app/services/mention_detector.py` — `_build_mention_regex` (already escapes/sorts) + `detect` (make team-aware + cache).
- `apps/memory-api/app/routes/team_chat.py` (~L243) — the summon site; has `team_id`; resolve + pass the team's aliases.
- `apps/memory-api/app/config.py:243` — `AGENT_MENTION_ALIASES` default (bootstrap; expand to `agent,chad,a`).
- `apps/memory-api/app/models/team.py` + `apps/memory-api/app/repos/teams.py` — add `agent_aliases`; the admin-check pattern for the PATCH.
- `apps/memory-api/alembic/versions/0024_local_credentials.py` — the head to base migration 0025 on (down_revision = 0024).
- `apps/memory-api/tests/test_mention_detector.py` — extend for per-team + the `@claude`-gone + `a` cases.
- `chrome-extension/chat_stream.js` (MENTION_RE), `chrome-extension/options.{html,js}` / `settings.js`, `chrome-extension/popup.js` (how it calls detectMentionClient + team switch), `chrome-extension/background.js` (auth/team context) — the client side.
- CLAUDE.md — product strings English-only; the extension is a product surface.
- Memory `project_brand_product_structure` — the additive-alias rule (@agent must always work; rebrand target teamchad.ai → @chad).
</canonical_refs>

<specifics>
## The gate lesson applies
A per-team alias that "should" work proves nothing until a message with that alias actually summons the agent through the real path. Verification MUST, against a REAL Postgres (testcontainer): set a team's custom alias → POST a message mentioning it to `team_chat` → assert `detect` fires for THAT team AND does NOT fire for a different team that didn't set it → assert `@claude` no longer triggers → assert `@agent` still triggers for every team. And a client check: `chat_stream.js`'s regex, built from a given server list, matches the same aliases the server would (and rejects `@claude`). SKIP=FAIL. Migration applies forward-only under both editions (Phase-17 pattern). Dev arm64 / prod amd64 — no cross-arch deploy.
</specifics>

<deferred>
- LibreChat/Open WebUI mention paths — not this feature (they don't use team_chat.detect).
- A global (per-install) name — not chosen; per-team is the model.
- The over-restrictive `kind=="user"` 403 sweep (me.py/audit.py/promotions.py) — separate backlog item.
</deferred>

---
*Phase: 21-configurable-agent-aliases*
*Context gathered: 2026-07-19 (autonomous)*
