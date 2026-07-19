# Phase 23: Catch Me Up (summary since last visit) — Context

**Gathered:** 2026-07-19 (autonomous — backlog feature #3).
**Source:** BACKLOG "Catch me up — summary since your last visit on entering a busy chat".

<domain>
## Phase Boundary

When a member opens the team chat and a lot happened since they were last here, offer an **opt-in** "Catch me up" that produces a brain-grounded summary of the messages since their last visit. Half the machinery exists (the agent summarizer + brain ingest); the net-new is a read cursor, a `since` query, an opt-in trigger, and the summarize invocation.

**IN scope:** a `last_read_at` cursor on `team_members` (migration 0026) + `POST /teams/{id}/mark-read` (client calls on focus / scroll-to-bottom); an `after_created_at` param on `list_messages` + a since-count endpoint; a `POST /teams/{id}/catch-me-up` that summarizes messages since the caller's `last_read_at` via the existing streaming agent path (brain-grounded, truth-level source chips like other agent replies); an opt-in, threshold-gated "Catch me up" affordance in the extension (only shown when volume is meaningful), never auto-run, rate-limited; the summary is ephemeral (a dismissible in-thread reply/banner), NOT a persisted message everyone sees.

**OUT of scope:** auto-running the summary (LLM cost — opt-in click only); a persisted per-everyone summary message; changing the agent's streaming machinery (reuse it); read receipts / "seen by" UI (this is a private per-member cursor, not a social read-receipt feature).
</domain>

<the_hard_facts_from_live_code>
1. **No read cursor exists.** `apps/memory-api/app/models/team.py` `TeamMember` has `team_id, user_id, role, joined_at, blocked_at, blocked_by` — NO `last_read_at`. (The only `last_seen_at` is on `user_external_sessions`, a 90s presence heartbeat — NOT a chat read cursor.) Add `last_read_at: Mapped[datetime | None]` nullable. Alembic head is `0025_team_agent_aliases` → new `0026`.
2. **`list_messages` only paginates backward.** `apps/memory-api/app/repos/team_messages.py:74` `list_messages(..., before_created_at=None, limit)` — has `before_created_at` (older-than) but no `after_created_at`. Add `after_created_at` (mirrors it: `created_at > after_created_at`) to fetch "since last_read_at" + a count query.
3. **The summarizer already exists.** `team_chat_agent.py:91 handle_claude_mention` builds a context bundle + calls Claude via `_stream_via_promax` (:496) / `_stream_via_anthropic_api` (:571), streaming frames over Centrifugo. `team_context_cache.get_team_memory_bundle` (:83) assembles the brain context. Catch-me-up is a specialized invocation of this path with a "summarize these N messages since <ts>" prompt — NOT new agent infra.
4. **Every message is already brain-ingested** (`team_chat.py` ingest_team_message, Phase 21-aware). So the summary can prioritize importance (decisions, validated facts, @-mentions to the returning user) from the brain, not just recency.
5. **Membership + rate-limit + team-resolution helpers** exist (`team_chat.py:_resolve_team_and_check_membership`, `rate_limit.check_rate`) — reuse for mark-read, since-count, and catch-me-up.
</the_hard_facts>

<decisions>
## Implementation Decisions (locked)

### D-23-01 — Read cursor: `team_members.last_read_at` (migration 0026), per-member private.
Nullable timestamptz. `POST /v1/teams/{team_id}/mark-read` sets it to now() for the caller's membership (idempotent; the client calls it on chat focus / scroll-to-bottom). NULL = never read (a first-time visitor). Forward-only additive column, edition-agnostic (Phase-17 pattern). This is a PRIVATE per-member cursor — not a "seen by" social feature, no cross-member exposure.

### D-23-02 — `since` query + unread count.
Add `after_created_at` to `tm_repo.list_messages` (symmetric to `before_created_at`: `created_at > after_created_at`, chronological). Add a cheap count of messages since the caller's `last_read_at` (exclude the caller's OWN messages and agent frames from the "unread that matters" count — you don't need to catch up on what you sent). Surface it via a small endpoint (e.g. `GET /v1/teams/{id}/unread-summary` → `{count, since}`) the client polls on open.

### D-23-03 — Opt-in, threshold-gated, never auto-run.
The "Catch me up" affordance appears ONLY when the unread count since `last_read_at` is meaningful (default threshold, e.g. ≥10 new messages that aren't the caller's own; tunable). Never fires automatically (LLM cost). The recipient clicks to run it. Rate-limit per caller. Don't nag: if dismissed, don't re-show for the same unread window.

### D-23-04 — Summarize via the existing streaming agent path, brain-grounded, ephemeral.
`POST /v1/teams/{team_id}/catch-me-up` gathers messages since the caller's `last_read_at` (capped to a sane max, e.g. last N or a token budget), builds a "summarize what happened, highlight decisions / questions directed at me / validated facts" prompt, and drives the SAME agent streaming path used by `@agent` (reuse `_stream_via_*` + the brain bundle) — brain-grounded, with truth-level source chips. The result is EPHEMERAL: streamed to the caller (their own `user:<sub>` channel or a direct response), rendered as a dismissible banner/in-thread reply — NOT inserted as a `team_messages` row everyone sees. Reuses the Phase-21 team-aware pieces where relevant.

### D-23-05 — Guardrails.
Team-scoped (no new privacy surface — the chat is already team-shared per the 2026-07-19 product decision). The summary must not leak another team's content (team_scope on the brain bundle). Rate-limit the catch-me-up call. English-only strings. If there is nothing to summarize (0 unread), the affordance doesn't show.

### Claude's Discretion
- Whether catch-me-up streams over Centrifugo (like `@agent`) to the caller's `user:<sub>` channel, or returns a synchronous/streamed HTTP response — pick what reuses the existing agent path most cleanly and keeps the summary ephemeral.
- The exact unread threshold + whether it's also time-based (first visit in >X hours).
- Exact endpoint shapes (a combined `unread-summary` vs separate) — keep it minimal and consistent with existing team routes.
- Where the "Catch me up" affordance sits in the popup (a dismissible banner at the top of the thread is the natural spot) — shadcn Neutral, no navigation.
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `apps/memory-api/app/models/team.py` (TeamMember — add `last_read_at`) + `apps/memory-api/app/repos/teams.py` (membership helpers + a set_last_read).
- `apps/memory-api/alembic/versions/0025_team_agent_aliases.py` — the head to base migration 0026 on (down_revision = 0025).
- `apps/memory-api/app/repos/team_messages.py:74` — `list_messages` (add `after_created_at`) + a count query.
- `apps/memory-api/app/routes/team_chat.py` — `_resolve_team_and_check_membership`, the message routes, where mark-read / unread-summary / catch-me-up endpoints go.
- `apps/memory-api/app/services/team_chat_agent.py` (handle_claude_mention :91, `_stream_via_promax` :496, `_stream_via_anthropic_api` :571) + `team_context_cache.py:83` (the brain bundle) — the summarizer path to reuse.
- `apps/memory-api/app/services/rate_limit.py` — per-caller rate limit for catch-me-up.
- `apps/memory-api/tests/conftest.py` + a prior real-Postgres gate (test_agent_aliases_gate.py / test_nudge_open_gate.py) — the testcontainer pattern to mirror.
- `chrome-extension/popup.js` — where mark-read is called (focus/scroll) + where the "Catch me up" banner renders (shadcn, contract test stays green).
- CLAUDE.md — English-only.
</canonical_refs>

<specifics>
## The gate lesson applies
A "catch-me-up works" claim proves nothing until it traverses the real path. Verification MUST, against a REAL Postgres testcontainer: seed a team + messages; a member marks read at T0; new messages arrive after T0; assert the unread count since `last_read_at` counts only messages after T0 and excludes the caller's own; assert mark-read updates the cursor; assert catch-me-up gathers exactly the since-window (with the agent streaming stubbed to a recorder — the message-GATHERING and cursor logic are NOT mocked); assert a non-member → 403; assert team_scope isolation (a different team's messages never appear). Migration 0026 forward-only under oss AND saas. SKIP=FAIL. Dev arm64 / prod amd64 — no cross-arch deploy.
</specifics>

<deferred>
- Auto-running the summary — never (opt-in only).
- A persisted per-everyone summary message — out (ephemeral only).
- Read-receipts / "seen by" social UI — out (private per-member cursor only).
</deferred>

---
*Phase: 23-catch-me-up*
*Context gathered: 2026-07-19 (autonomous)*
