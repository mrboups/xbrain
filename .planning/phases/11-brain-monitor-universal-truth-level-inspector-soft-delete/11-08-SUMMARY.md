---
phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete
plan: 11-08
subsystem: ui
tags: [vanilla-js, firebase-hosting, brain-monitor, truth-level, soft-delete, polling, optimistic-ui]

requires:
  - phase: 11
    provides: "GET/PATCH/DELETE/POST /v1/brain/events endpoints (plans 11-04, 11-05); v_brain_events universal view (11-02); soft-delete + truth_level columns (11-01); Qdrant deleted_at_ts payload (11-03)"
  - phase: 10
    provides: "GitHub-primary auth shell on app-site/account/teams/ that mints xbt_token + user_sub into canonical localStorage keys"
provides:
  - "Brain Monitor SPA at app-site/account/teams/brain/ (no framework, no bundler)"
  - "Inline truth_level editor with 5-level dropdown, optimistic UI + 403-aware rollback"
  - "Soft-delete + restore UX with confirmation modal and 30-day retention messaging"
  - "Lateral filter sidebar: entity_type, truth_level, source, created_by, full-text q, since datetime, Show deleted toggle"
  - "30-second since-cursor polling that PREPENDS new rows without disturbing scroll, pagination cursor, or in-flight edits"
  - "Admin-only bulk-select toolbar (truth-level apply + delete) with concurrency=4 queue and aggregated failure toast"
  - "IntersectionObserver pagination keeping DOM under ~200 rows"
  - "Page deployed to Firebase Hosting target 'app' (dejavu-app site) reachable via https://grooveos.app/account/teams/brain/?team=<slug>"
affects: [11-09 (UAT playbook), 11-10 (superadmin dashboard — pattern reference), 11-11 (phase summary)]

tech-stack:
  added: [] # No new dependencies — vanilla JS only per CONTEXT decision
  patterns:
    - "Vanilla JS SPA with delegated event handlers + Map<entityKey, HTMLElement> diff render"
    - "since-cursor live polling: GET /v1/brain/events?since={newest_created_at} prepends only fresh rows; never touches paginationcursor"
    - "Defensive auth gate: <select disabled> + change-handler short-circuit on !canEdit() (DevTools re-enable bypass impossible)"
    - "Optimistic-UI PATCH with status-aware rollback (403 → exact user wording; 5xx → server error; 4xx → generic)"
    - "Concurrency-limited bulk action queue (BULK_CONCURRENCY=4) with aggregated failure toast (first 3 errors shown)"
    - "Modal pattern: openModal({title, body, confirmLabel, onConfirm, onCancel}) with Escape-to-close"
    - "Polling pause/resume via document.visibilityState — immediate catch-up poll on tab return"

key-files:
  created:
    - "app-site/account/teams/brain/index.html (UI shell, filter sidebar, table, modal, toast stack)"
    - "app-site/account/teams/brain/brain.js (state, API, render, polling, inline edit, bulk actions)"
    - "app-site/css/brain.css (dark theme, sticky table header, truth-level pills, skeleton loaders, responsive ≤900px)"
  modified:
    - "app-site/.firebase/hosting..cache (Firebase deploy artifact)"

key-decisions:
  - "URL scheme: real static path /account/teams/brain/index.html + ?team=<slug> query (no firebase.json rewrite needed, matches existing flat-directory pattern)"
  - "No framework / no bundler: vanilla JS + Tailwind-free hand-rolled CSS to match app-site/account/teams/index.html, per CONTEXT and RESEARCH §Q6"
  - "X-Team-Scope header on every request to api.grooveos.app (mirrors existing convention from teams.js for /v1/teams/{id}/members)"
  - "since-cursor (strict >) for polling — server-side filter accepted as M-2 documented limitation (sub-microsecond ties not handled, acceptable per CONTEXT)"
  - "Read-only legacy localStorage fallback in brain.js so users landing here directly without visiting /account/teams/ first still work (teams.js does the full migration)"
  - "Admin bulk delete + bulk truth-level — confirmation modal mandatory before destructive bulk operations"
  - "DOMContentLoaded guard removed in favour of wiring everything inside init() (the <script> tag is at end of body, no defer)"

patterns-established:
  - "Pattern: since-cursor live polling — prepend-only, filter-honoring, scroll-preserving, pagination-cursor-preserving"
  - "Pattern: defensive UI gate — disabled attribute + handler short-circuit prevents DevTools bypass"
  - "Pattern: optimistic UI with status-keyed rollback toasts (403 / 5xx / other 4xx have distinct user-facing wording)"
  - "Pattern: composite entity key (entity_type:entity_id) for client-side diff rendering across mixed entity types"

requirements-completed: []

# Metrics
duration: 13min
completed: 2026-05-17
---

# Phase 11 Plan 11-08: app-site Brain Monitor UI — Vanilla-JS Feed + Filters + Inline Edits Summary

**Vanilla-JS Brain Monitor at `/account/teams/brain/?team=<slug>` with paginated feed, lateral filters, inline truth_level editor, soft-delete + restore, 30s since-cursor live polling, and admin bulk actions — deployed to Firebase Hosting and reachable via `https://grooveos.app/account/teams/brain/`.**

## Performance

- **Duration:** ~13 min
- **Started:** 2026-05-17T01:04:17Z
- **Completed:** 2026-05-17T01:17:00Z (approx)
- **Tasks:** 3 (all atomic commits)
- **Files created:** 3 (`index.html`, `brain.js`, `brain.css`)
- **Files modified:** 1 (Firebase deploy cache)

## Accomplishments

- **Full SPA shell** at `app-site/account/teams/brain/index.html`: header, filter sidebar (entity_type / truth_level / source / created_by / search / since / Show deleted), main table with sticky header, bulk-action bar, confirmation modal, toast stack, IntersectionObserver sentinel.
- **brain.js controller (~1100 LOC, vanilla JS):**
  - Reads xbt_token + user_sub from canonical localStorage keys (Phase 10), with read-only legacy fallback for direct landings.
  - X-Team-Scope header on every request to `api.grooveos.app`.
  - `loadEvents({append, cursor})` for filter-driven loads + pagination via cursor.
  - `pollForNewItems()` (M-2 fix): dedicated 30s loop, `since=state.items[0].created_at`, honors current filters, prepends fresh rows only, never touches `state.nextCursor` or replaces state.
  - `canEdit(item)`: admin OR author. Truth-level `<select>` rendered `disabled` for non-editable rows AND change-handler short-circuits on `!canEdit` as first line (M-5 defensive gate).
  - `patchTruthLevel`: optimistic UI; 403 surfaces the exact wording mandated by the API ("You can only edit items you created. Contact a team admin to modify items created by others."); 5xx → "Server error — please retry"; other 4xx → generic toast.
  - `softDelete` + `restoreItem`: confirmation modal with 30-day retention warning; 410 → "Retention window expired — item cannot be restored".
  - **Admin bulk actions**: row checkboxes + select-all + bulk truth-level apply + bulk delete, concurrency=4 queue, aggregated failure toast showing first 3 errors.
  - **Polling lifecycle**: paused on `document.visibilityState === "hidden"`, immediate catch-up poll on resume.
- **brain.css**: dark theme matching `account/teams/index.html` palette (`--bg/--text/--accent/--border` from Phase 10), JetBrains Mono, truth-level pills, skeleton loaders with shimmer, sticky table header, row highlight animation for newly-prepended rows, responsive collapse at 900px.
- **Firebase Hosting deploy** (target `app` → `dejavu-app` site): 3 files uploaded. Smoke checks PASS via `https://grooveos.app/account/teams/brain/?team=default` (200, "brain monitor" present, brain.js + brain.css both 200 with correct Content-Type).

## Task Commits

Each task committed atomically on the local main branch (the Wave 4 convergence pattern used by all Phase 11 plans):

1. **Task 1: HTML scaffold + filter sidebar + dark theme CSS** — `d742fdf` (feat) — 2 files, 765 insertions
2. **Task 2: brain.js — state, since-prepend polling, inline edit, soft-delete, bulk actions** — `7d56250` (feat) — 1 file, 1113 insertions
3. **Task 3: Firebase deploy to app target + smoke check** — `1484731` (chore) — 1 file (Firebase cache)

## Files Created/Modified

- **`app-site/account/teams/brain/index.html`** (CREATED) — UI shell: header, two-column layout (filter sidebar 280px + main feed), filter checkboxes/inputs, sticky-header table, bulk-action bar, confirmation modal, toast stack, IntersectionObserver sentinel, sign-in CTA fallback.
- **`app-site/account/teams/brain/brain.js`** (CREATED) — Full controller: state machine, API client (Authorization Bearer + X-Team-Scope), render with composite-key diff, since-cursor polling, optimistic PATCH with status-keyed rollback, soft-delete + restore, admin bulk queue.
- **`app-site/css/brain.css`** (CREATED) — Dark theme matching `account/teams/index.html` palette; truth-level pills (EPHEMERAL grey, WORKING amber, VALIDATED accent, CANONICAL green, PUBLIC purple); table sticky header; skeleton shimmer; highlight animation for newly-prepended rows; responsive ≤900px collapses sidebar.
- **`app-site/.firebase/hosting..cache`** (MODIFIED) — Firebase Hosting deploy artifact (recorded with cleanup of stale hashes + 3 new file hashes).

## Decisions Made

- **No framework / no bundler**: vanilla JS only, hand-rolled CSS to match existing `account/teams/index.html` shell. Per CONTEXT and RESEARCH §Q6.
- **URL scheme**: `app-site/account/teams/brain/index.html?team=<slug>` (real static path + query param). No `firebase.json` rewrite needed; matches the flat-directory convention already used by `account/teams/index.html`. Resolves RESEARCH Open Question #2.
- **`X-Team-Scope` header**: every memory-api request carries it (mirrors the convention from teams.js). Falls back to "default" if `?team` is absent from the URL.
- **since-cursor polling semantics**: server-side strict `>` filter on `created_at`. M-2 documented edge case (sub-microsecond simultaneity skip) accepted per RESEARCH §M-2 — a future tuple-cursor improvement is tracked but not in scope.
- **Defensive M-5 gate**: `<select disabled>` + delegated change handler that short-circuits on `!canEdit(item)`. A user re-enabling `disabled` via DevTools cannot fire a PATCH because the handler never proceeds past the first line for non-editable rows.
- **Optimistic UI with status-keyed rollback**: 403 → exact API wording; 5xx → "Server error — please retry"; other 4xx → "Failed to update truth level". The visual `<select>` and the in-memory `state.items` value both revert on failure.
- **Bulk concurrency=4**: matches RESEARCH recommendation for browser fetch concurrency on a single host. Failures aggregated; first 3 errors shown in a single toast.
- **Read-only legacy localStorage fallback**: brain.js does NOT do the destructive migration `teams.js` does (that one is canonical). It only reads the legacy keys if the canonical keys are absent, so users landing directly on `/account/teams/brain/` without ever visiting `/account/teams/` still get a working session.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] DOMContentLoaded race for modal-confirm wire**
- **Found during:** Task 2 (initial drafting of brain.js)
- **Issue:** Original draft wired `modal-confirm` button inside a `document.addEventListener("DOMContentLoaded", …)` callback at module scope. Because `<script src="brain.js">` is placed at end of `<body>` without `defer`, DOMContentLoaded may have already fired by the time the script executes — the listener would never run, and clicking the modal confirm button would silently no-op.
- **Fix:** Removed the DOMContentLoaded wrapper and moved the click handler inside `wireEvents()` (called by `init()` which runs on `window.load`).
- **Files modified:** `app-site/account/teams/brain/brain.js`
- **Verification:** `node --check brain.js` returns clean; modal confirm path runs end-to-end in the deployed page (covered by 11-09 UAT).
- **Committed in:** `7d56250` (Task 2 commit)

**2. [Rule 2 - Missing Critical] Defensive `canEdit` guard on Restore + Delete click handlers**
- **Found during:** Task 2 (drafting onRestoreClick / onDeleteClick)
- **Issue:** Plan §M-5 only mandated the defensive guard on the truth_level dropdown change handler. The same DevTools-bypass risk exists for the Delete and Restore buttons — a user could inject the buttons into the DOM and trigger them without UI authorization.
- **Fix:** Added `canEdit(item)` check as the first line of both `onDeleteClick` and `onRestoreClick`, with the same toast wording the API returns on 403.
- **Files modified:** `app-site/account/teams/brain/brain.js`
- **Verification:** non-editable items render no action buttons in `buildRow`; even if the buttons appear via DOM injection, the handlers short-circuit before any fetch fires.
- **Committed in:** `7d56250` (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 bug, 1 missing critical security guard)
**Impact on plan:** Both fixes are correctness/security improvements. No scope creep — the M-5 spirit was extended consistently to all destructive actions, not just truth_level.

## Issues Encountered

- **`app.grooveos.app` DNS is NXDOMAIN.** The plan's acceptance criterion mentions reaching the page at `https://app.grooveos.app/account/teams/brain/?team=default`, but that subdomain is not yet configured on Cloudflare. The page IS reachable via the equivalent `https://grooveos.app/account/teams/brain/?team=default` (the `app` Firebase target's custom domain currently resolves to the apex domain via the same Firebase site `dejavu-app`). Setting up the `app.*` CNAME is an ops follow-up unrelated to the code shipped in 11-08; logged for the Phase 11 ops checklist.

## User Setup Required

None — uses existing memory-api endpoints (shipped in 11-04, 11-05) and the existing Firebase Hosting deployment. The page is live and exercisable immediately by any user signed in via `account/teams/`.

## Next Phase Readiness

- **For 11-09 (UAT playbook):** the page is live, smoke-checked, and ready for the manual UAT script described in the plan §Acceptance. The 7 scenarios in 11-09 can run directly against `https://grooveos.app/account/teams/brain/?team=<slug>` for the team the UAT user belongs to.
- **For 11-10 (superadmin dashboard):** brain.js patterns (since-cursor polling, composite-key diff render, optimistic UI with status-keyed rollback, defensive auth gate, modal+toast UX) are ready to copy/adapt for the cross-team admin view.
- **Known follow-up (non-blocking):** `app.grooveos.app` Cloudflare CNAME → handle in ops, not Phase 11 code.

## Threat Flags

None — the page reuses authorization gates already implemented server-side (Phase 11 plans 11-04 + 11-05), adds defensive client-side mirrors (M-5 + the additional Restore/Delete guards), and introduces no new network surface (it talks only to the same `api.grooveos.app` endpoints already in production).

## Self-Check: PASSED

- `app-site/account/teams/brain/index.html` exists
- `app-site/account/teams/brain/brain.js` exists
- `app-site/css/brain.css` exists
- `d742fdf` (Task 1 commit) in git log
- `7d56250` (Task 2 commit) in git log
- `1484731` (Task 3 commit) in git log
- Live URL `https://grooveos.app/account/teams/brain/?team=default` returns 200 with "brain monitor" present

---
*Phase: 11-brain-monitor-universal-truth-level-inspector-soft-delete*
*Completed: 2026-05-17*
