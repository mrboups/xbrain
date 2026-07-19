---
phase: 21-configurable-agent-aliases
verified: 2026-07-19T02:12:18Z
status: human_needed
score: 4/4 must-haves verified (ALIAS-01 satisfied)
overrides_applied: 0
---

# Phase 21: Configurable Agent Mention Aliases — Verification Report

**Phase Goal:** Per-team, additive agent aliases (`@agent` always + defaults `chad`/`a` + a custom name set in Settings), `@claude` removed, client and server share ONE source of truth.
**Verified:** 2026-07-19T02:12:18Z
**Status:** human_needed (all 4 Success Criteria mechanically VERIFIED with real-path evidence; one residual browser-UI smoke-check flagged, non-blocking — see Human Verification section)
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria from ROADMAP.md)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | A team's custom `@name` summons the agent for THAT team only; a different team does NOT fire on it — proven through the real `team_chat` → `mention_detector` path against real Postgres | VERIFIED | `tests/test_agent_aliases_gate.py::test_summon_per_team_gate` — re-ran live: `3 passed in 32.74s` (full file), `1 passed` isolated. Case A/B assert team-a's `@wizard` summons team-a (`summoned == [team_a.id]`) and does NOT summon team-b (`summoned == []`). The mention DECISION (`effective_aliases` + `detect` at `team_chat.py:246-247`) is NEVER mocked — only 3 downstream fire-and-forget network callers (`handle_claude_mention`, `centrifugo_client.publish`, `brain_ingest.ingest_team_message`) are stubbed, confirmed by reading the test source. |
| SC2 | `@agent` summons on EVERY team; `@claude` summons NOTHING (removed client + server; rejected as reserved word in PATCH) | VERIFIED | Same gate test, Case C/D: `@agent` fires for both team-a and team-b (incl. the team with no custom alias); `@claude` fires for neither. Server: `_RESERVED = frozenset({"claude"})` filters it in `effective_aliases()`; `_validate_aliases()` in `teams.py` additionally rejects `"claude"` (case-insensitive) with 422 at the PATCH edge (double defense). Client: `buildMentionRegex()` filters `"claude"` case-insensitively; `chat_stream.js` test asserts `@claude` → `null` even when "smuggled" into the input list. Grep of the whole diff surface for `@claude` in EXECUTABLE code (not comments/docstrings) → zero matches in `mention_detector.py`, `team_chat.py`, `teams.py`, `repos/teams.py`, `chat_stream.js`, `popup.js`, `options.js`. (A handful of STALE prose comments/docstrings referencing "@claude" remain in unrelated pre-existing files — see Anti-Patterns.) |
| SC3 | The client builds its regex from the server's effective list (JS-escaped, longest-first); the hardcoded `@(grooveos\|groove\|gr\|g)` is GONE | VERIFIED | `chat_stream.js` — `grep 'grooveos|groove|gr|g' chat_stream.js` → no match (re-confirmed). `buildMentionRegex(aliases)` JS-escapes each alias (`escapeAlias`), sorts longest-first, filters `claude`, falls back to `["agent"]` on empty — mirrors server `_build_mention_regex`. `popup.js` fetches `GET /v1/teams/{id}/agent-aliases` and rebuilds `state.mentionRe` from the returned list (`refreshAgentAliases()`), never hardcoding a vocabulary. |
| SC4 | PATCH admin-only (403 non-admin); aliases validated (charset/len/count, strip @, empty→422, `claude` reserved→422); GET member-readable; Settings field takes effect with NO restart | VERIFIED (server, mechanically) / see Human Verification for client-UI smoke-check | Real-Postgres `tests/test_agent_aliases_api.py` (5/5 passed, re-ran live): member GET works for admin+plain member; non-admin member (403) and non-member admin (403) both rejected on PATCH; cross-team isolation (team-a's `wizard` never leaks into team-b's list); 422 on bad charset/`>32` chars/empty/`claude`/`>8` items; leading `@` stripped on accept. `_require_team_admin` runs BEFORE `_validate_aliases` (403 precedes 422, confirmed in `teams.py:648-649`). No-restart: `team_chat.py:246` resolves `team.agent_aliases` fresh from the DB on every message (no caching of the team's custom value); `options.js`/`popup.js` re-fetch after PATCH/on team switch. |

**Score:** 4/4 truths verified (all backed by re-executed, real-Postgres/real-JS evidence, not SUMMARY claims alone)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/memory-api/alembic/versions/0025_team_agent_aliases.py` | Nullable `teams.agent_aliases` TEXT, forward-only, `down_revision=0024_local_credentials` | VERIFIED | Confirmed file content: additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, no `EDITION` token (grep empty), `alembic heads` resolves to single clean head `0025_team_agent_aliases` — no branch. |
| `apps/memory-api/app/models/team.py` | `Team.agent_aliases: Mapped[str \| None]` | VERIFIED | Present, nullable `Text` column, correctly documented. |
| `apps/memory-api/app/config.py` | `AGENT_MENTION_ALIASES` default `agent,chad,a` | VERIFIED | Line 245: `AGENT_MENTION_ALIASES: str = "agent,chad,a"`. |
| `apps/memory-api/app/services/mention_detector.py` | `effective_aliases()` resolver + team-aware cached `detect()` | VERIFIED | Both present, logic matches SUMMARY exactly (agent-always, claude-never, dedup, per-alias-set regex cache). |
| `apps/memory-api/app/routes/team_chat.py` (~L246) | Summon site resolves `effective_aliases(team.agent_aliases)` before `detect()` | VERIFIED / WIRED | Confirmed at lines 246-247, team-scoped. |
| `apps/memory-api/app/routes/teams.py` | GET (member) / PATCH (admin) `/agent-aliases` + `_validate_aliases` | VERIFIED / WIRED | Both routes present (lines 598, 626); validation logic matches D-21-06 exactly. |
| `apps/memory-api/app/repos/teams.py` | `set_agent_aliases()` persistence helper | VERIFIED / WIRED | Present, used by PATCH route. |
| `apps/memory-api/app/services/brain_ingest.py` | Skip-prefixes derived from `effective_aliases(None)`, no hardcoded `@claude` | VERIFIED | `_AGENT_COMMAND_PREFIXES = tuple(f"@{alias}" for alias in effective_aliases(None))` — confirmed at line 48. |
| `chrome-extension/chat_stream.js` | `buildMentionRegex`/`detectMentionClient` build-from-list, no hardcoded vocabulary | VERIFIED | Confirmed; `@(grooveos\|groove\|gr\|g)` literal fully deleted. |
| `chrome-extension/popup.js` | `refreshAgentAliases()` fetch/cache/rebuild on team switch + storage change | VERIFIED / WIRED | Called at boot (line 404) and on `xbt_token` storage change (line ~1384); fail-soft (keeps prior regex on fetch error). |
| `chrome-extension/options.js` | Admin-only "Agent name" field, GET-prefill + PATCH + re-fetch | VERIFIED / WIRED | Field is nested inside `if (isAdmin) { ... }` (lines 350-448) — non-admins never render the DOM elements; `loadAgentAliases`/`saveAgentAliases` call the real endpoints. |
| `apps/memory-api/tests/test_mention_detector.py` | 36 unit tests incl. 15 new Phase-21 cases | VERIFIED | Re-ran live: `36 passed, 1 warning in 0.49s`. |
| `apps/memory-api/tests/test_agent_aliases_api.py` | 5 real-Postgres endpoint tests | VERIFIED | Re-ran live: `5 passed in 22.29s`. |
| `apps/memory-api/tests/test_agent_aliases_gate.py` | Real-Postgres summon gate + 2-edition migration proof | VERIFIED | Re-ran live: `3 passed in 36.55s` (1 summon + 2 migration `[oss]`/`[saas]`). |
| `chrome-extension/tests/test_chat_stream.mjs` | Client regex parity + `@claude`-rejected + escape-parity tests | VERIFIED | Re-ran live: `36 passed, 0 failed`. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `team_chat.py` POST handler | `mention_detector.effective_aliases` + `detect` | direct call at L246-247 | WIRED | Team-scoped; confirmed by reading source AND by the real-Postgres gate test exercising the actual POST path. |
| `teams.py` GET/PATCH `/agent-aliases` | `mention_detector.effective_aliases` / `teams_repo.set_agent_aliases` | direct call | WIRED | GET returns `effective_aliases(team.agent_aliases)`; PATCH calls `set_agent_aliases()` then re-derives the effective view for the response. |
| `chat_stream.js` `buildMentionRegex` | `popup.js` `state.mentionRe` | import + `refreshAgentAliases()` fetch → rebuild | WIRED | `popup.js` imports `buildMentionRegex`, fetches the server list, and assigns `state.mentionRe = buildMentionRegex(aliases)`. |
| `options.js` Save button | PATCH `/v1/teams/{id}/agent-aliases` | `saveAgentAliases()` → `_xbtFetch(..., {method:"PATCH"})` | WIRED | Confirmed at line 526; re-fetches effective list into the input on success. |
| `brain_ingest.py` skip-prefix filter | `mention_detector.effective_aliases(None)` | module-level derivation | WIRED | `_AGENT_COMMAND_PREFIXES` computed from the resolver at import — no independent hardcoded vocabulary. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `GET /v1/teams/{id}/agent-aliases` | `team.agent_aliases` (DB column) | `teams_repo.get_team_by_id` → real Postgres row | Yes | FLOWING — confirmed by `test_cross_team_isolation` (team-a's stored `wizard` never appears in team-b's response) and `test_admin_patch_round_trip_no_restart` (a fresh GET after PATCH reflects the new value with no caching layer). |
| `popup.js state.mentionRe` | `data.aliases` from the GET response | live `fetch` to memory-api | Yes | FLOWING — `refreshAgentAliases()` only overwrites `state.mentionRe` when `aliases.length` is truthy from the actual response; on fetch failure the prior regex is kept (fail-soft, not silently emptied). |
| `team_chat.py:246` summon decision | `team.agent_aliases` | same ORM row loaded earlier in the same request | Yes | FLOWING — no caching between the DB and the detect call; re-read on every POST (proves the "no restart" requirement mechanically, not just by claim). |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Server unit suite (mention_detector) | `python -m pytest tests/test_mention_detector.py -q` | `36 passed, 1 warning in 0.49s` | PASS |
| Server API suite (real Postgres) | `python -m pytest tests/test_agent_aliases_api.py -q` | `5 passed in 22.29s` | PASS |
| Server summon+migration gate (real Postgres, 2 editions) | `python -m pytest tests/test_agent_aliases_gate.py -q` | `3 passed in 36.55s` | PASS |
| Combined phase-21 backend suite | `python -m pytest tests/test_mention_detector.py tests/test_agent_aliases_api.py tests/test_agent_aliases_gate.py -q` | `44 passed in 49.05s` | PASS |
| Client JS mention/regex suite | `node tests/test_chat_stream.mjs` | `36 passed, 0 failed` | PASS |
| Full client JS test suite (regression, incl. Phase-20 popup contract) | `node tests/run_tests.mjs` | `11/11 test files passed` (incl. `test_popup_contract.mjs` 90/90) | PASS |
| Alembic head resolution | `ScriptDirectory.get_heads()` | `['0025_team_agent_aliases']` | PASS (single clean head, no branch) |
| `node --check` on the 3 modified extension files | `node --check chat_stream.js popup.js options.js` | exit 0, no output | PASS |

All numbers above were **re-executed live during this verification** (not copied from SUMMARY.md) and match the SUMMARYs' claimed figures exactly.

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|-------------|--------------|--------|----------|
| ALIAS-01 | 21-01, 21-02, 21-03, 21-04 | Per-team additive alias list, `@agent` always, `@claude` removed, one source of truth client+server | SATISFIED | All 4 SC verified above with re-executed real-path tests. No orphaned requirements found for Phase 21 in REQUIREMENTS.md (ALIAS-01 is the only mapped ID). |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `apps/memory-api/app/services/team_chat_agent.py` | 1, 3 | Docstring says "Team chat @claude mention handler" / "`@claude` mention" | INFO | Pre-existing file (Wave 2.5), not modified by Phase 21. Purely descriptive prose, no executable logic reads a hardcoded `@claude` string. Does not affect SC2. |
| `apps/memory-api/app/config.py` | 124 | Comment: "POSTed on @claude mentions" | INFO | Same — stale comment on `AGENT_RUNTIME_INTERNAL_URL`, unrelated to the alias logic, not part of Phase 21's file set. |
| `apps/memory-api/app/services/relevance_filter.py` | 59, 445, 511 | LLM prompt text / comment mentioning `@claude/@c/@cl` as an example category | INFO | This is prompt text for a Haiku relevance classifier (example categories) and a stale comment; the actual filtering (`is_brain_relevant` in `brain_ingest.py`) correctly derives from `effective_aliases(None)`, confirmed by reading the code. No functional impact. |
| `apps/memory-api/tests/test_soft_delete_regression.py` | — | 5 additional pre-existing test failures beyond the 1 logged in `deferred-items.md` (`IntegrityError: memory_items_validation_check` — test seeds `validation_status='unverified'`, but the Phase-1-era CHECK constraint only allows `pending/validated/rejected/n/a`) | INFO | Confirmed via live re-run: 6 failures in this file (deferred-items.md logged only 1). Root cause traced to a pre-existing bug in `_seed_memory_item`'s hardcoded `'unverified'` literal vs. the `memory_items_validation_check` constraint from migration `0002` (ancient, unrelated to `agent_aliases`). Not caused by, or related to, Phase 21's changes — none of Phase 21's plans touch this test file, its fixtures, or the memory_items table. `deferred-items.md` undercounts the pre-existing breakage (6 actual vs. 1 logged for this file) — worth a note-only correction, not a Phase-21 gap. |

None of the above rise to BLOCKER or WARNING — they are pre-existing, out-of-scope, and non-functional (prose/comments or an unrelated pre-existing test bug).

### Human Verification Required

### 1. Extension Settings — "Agent name" admin field, live browser check

**Test:** In a loaded (reloaded) unpacked Chrome extension: open Settings/options for a team where you are `admin`. Confirm the "Agent name" field is visible and prefilled with the team's current effective alias list. Type a custom name (e.g. `wizard`), click Save. Then open the same Settings for a team where you are a plain `member` (not admin) and confirm the field is NOT rendered. Finally, in the team chat composer, type `@wizard` and confirm the optimistic "Will summon @wizard" hint appears without reloading the extension.

**Expected:** Field renders only for admins; Save round-trips and shows the confirmation message; the composer hint reflects the new alias immediately (via `refreshAgentAliases()` re-fetch), no browser/extension restart needed.

**Why human:** This is DOM rendering + click-interaction + visual confirmation in a live Chrome extension popup. There is no `jsdom` installed in this project's JS test harness (confirmed: `test_popup_contract.mjs` prints "NOTE: jsdom not installed — optional DOM smoke unavailable"), so the actual field-visibility-by-role and click→fetch→re-render flow is verified only by static code review (confirmed structurally correct, mirrors the already-shipped "Invite by email" admin field pattern one-for-one) and by the underlying network-layer tests (GET/PATCH endpoints, buildMentionRegex), not by an executed DOM interaction. All of the server-side and pure-function client logic this UI calls into IS mechanically verified (see SC4 evidence above) — this item is a residual visual/UX smoke-check, consistent with how this project has historically tracked such items (cf. Phase 20's PKG-02 "live-backend UAT residual" in ROADMAP.md), not a sign of missing implementation.

### Gaps Summary

No gaps. All 4 Success Criteria for Phase 21 are backed by real, re-executed evidence:
- The mention **decision** (the part the "gate lesson" cares most about) is proven against real Postgres with the actual `team_chat` → `effective_aliases` → `detect` path, with only fire-and-forget network side-effects (Anthropic call, Centrifugo publish, brain ingest) stubbed — never the detection itself.
- `@claude` is provably dead as a trigger, both server-side (reserved-token filter + 422 validation) and client-side (regex-level filter), confirmed via passing negative-assertion tests, not absence-of-evidence.
- The stale `@(grooveos|groove|gr|g)` hardcoded client vocabulary is deleted; the client demonstrably builds its regex from the server list.
- Migration 0025 is a clean single head, forward-only, additive, nullable, and edition-agnostic (proven under both `oss` and `saas` against fresh Postgres containers).

One item is routed to human verification (browser DOM smoke-check of the new Settings field) because no `jsdom`-based DOM test exists in this repo to mechanically prove render-by-role and click-driven network calls — this is a visual/interaction confirmation, not a code gap. All of the logic that field invokes is independently and mechanically proven.

---

_Verified: 2026-07-19T02:12:18Z_
_Verifier: Claude (gsd-verifier)_
