---
phase: 11
plan: 09
type: uat
generated: 2026-05-17
maps_to: Phase 11 ROADMAP success criteria + REVISION 1 (M-2 polling, M-5 403 wording) + REVISION 2 (superadmin)
---

# Phase 11 UAT — Brain Monitor + Superadmin Dashboard

| Field         | Value                                                   |
| ------------- | ------------------------------------------------------- |
| Verifier      | _______________________                                 |
| Date          | _______________________                                 |
| VM IP         | __VM_HOST__                                          |
| App host      | https://app.example.com                                |
| API host      | https://api.example.com                                |
| Marketing host| https://example.com                                    |

This is the manual checklist for the things `verify-phase11.sh` cannot automate —
mostly UI interactions (inline edits, dropdowns, polling, drill-down banner,
DevTools network panel).

Walk it after `verify-phase11.sh` exits 0 (or with only `LOCKDOWN_TEST` SKIP).
SKIPPED never blocks; only FAIL == 0 is required.

---

## Pre-checks (must all be true before starting)

- [ ] `bash infrastructure/scripts/verify-phase11.sh` returned `PASS: N / N (SKIPPED: M)` with `FAIL == 0`
- [ ] You are signed in to https://app.example.com as a team member (admin of `default` ideally)
- [ ] Team `default` has at least 10 brain events visible (any mix of memory_items, tasks, conversations, contacts — top up via the app / API if dev DB is empty)
- [ ] Team `default` has at least one item where you are NOT the author (needed for step 7 — sign in as a second principal once or have an admin seed)
- [ ] **For step 8 (superadmin)** the VM has `ADMIN_USER_SUBS` set and includes your `sub` for the superadmin half, AND at least one OTHER team exists in the DB (seed via `POST /v1/admin/teams` if there's only `default`)
- [ ] `docker ps` shows `xbrain-memory-api` AND `xbrain-brain-janitor` running
- [ ] `docker exec xbrain-memory-api alembic current` returns `0018_brain_events_view` or later

---

## Acceptance items

### Step 1 — Load page (SC-3, BMO-09)

- [ ] Open https://app.example.com/account/teams/brain/?team=default in a fresh browser tab.
- [ ] Page loads in **under 2 seconds** (measure via DevTools → Network → DOMContentLoaded).
- [ ] The brain events table is rendered with at least 10 rows.
- [ ] The filter sidebar (left or top, depending on viewport) is visible and lists at least `entity_type` and `truth_level` filter groups.
- [ ] Each row shows: timestamp, entity_type badge, truth_level badge, content preview, author (if applicable), and an actions cell (truth_level dropdown + Delete button if you have permission).

### Step 2 — Filter by entity_type and truth_level

- [ ] In the sidebar, check `task` under entity_type AND `WORKING` under truth_level.
- [ ] The table re-fetches and now shows **only `task` rows with truth_level=WORKING**.
- [ ] The URL or sidebar state reflects the filters (you can refresh the page and still see the filters applied — locked behaviour from 11-08).
- [ ] Uncheck both filters → the full unfiltered list returns.

### Step 3 — Inline edit `truth_level`

- [ ] Pick a row YOU authored (so the dropdown is enabled — typically a `task` or `team_message`).
- [ ] Click the truth_level dropdown. Select `VALIDATED` (or any value different from the current one).
- [ ] A success toast appears within 1 s ("Updated to VALIDATED" or equivalent).
- [ ] The badge on the row updates immediately (no full table refresh).
- [ ] **DB confirmation** (on the VM):
      ```
      docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
        "SELECT truth_level FROM tasks WHERE id='<the-row-id-from-step-3>'"
      ```
      Result is `VALIDATED`.

### Step 4 — Soft-delete + reappear with toggle

- [ ] Pick a `memory_item` or `task` row you have permission to delete.
- [ ] Click the Delete button (icon or "Delete" link in the row).
- [ ] A confirmation modal appears mentioning the **30-day purge window**.
- [ ] Confirm. The row disappears from the table.
- [ ] **DB confirmation:**
      ```
      docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
        "SELECT deleted_at, deleted_by FROM tasks WHERE id='<id>'"
      ```
      `deleted_at` is non-NULL and recent (within last minute); `deleted_by` matches your user UUID.
- [ ] Toggle the "Show deleted" switch (or equivalent). The deleted row reappears, **faded**, with a Restore button instead of the Delete button.

### Step 5 — Restore the soft-deleted row

- [ ] Click Restore on the faded row from step 4.
- [ ] Success toast appears within 1 s.
- [ ] The row un-fades. Toggling "Show deleted" off and on again, the row appears in the default list (no longer faded).
- [ ] **DB confirmation:**
      ```
      docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
        "SELECT deleted_at IS NULL FROM tasks WHERE id='<id>'"
      ```
      Returns `t`.

### Step 6 — 30-second polling (locked semantics: prepend, no scroll-jump, no row re-render)

- [ ] Note the topmost row currently visible in the brain monitor table.
- [ ] In a **second browser tab** (or via curl on the VM with a valid xbt_), create a brand-new memory_item in team `default`:
      ```
      curl -X POST https://api.example.com/v1/memory/upsert \
        -H "Authorization: Bearer $TEST_XBT" \
        -H "X-Team-Scope: default" \
        -H "Content-Type: application/json" \
        -d '{"item":{"content":"UAT-step6 polling probe","team_scope":"default","project_scope":null,"visibility":"team","confidence":0.9,"truth_level":"EPHEMERAL","source":"uat:phase11","validation_status":"pending"}}'
      ```
- [ ] Return to the brain monitor tab. **Wait up to 30 s** — do not refresh.
- [ ] The new "UAT-step6 polling probe" row appears at the **top** of the list automatically.
- [ ] Rows below it have NOT been re-rendered (verify by, e.g., having a row's dropdown open at step start — it must still be open).
- [ ] Your scroll position is **preserved** — the page did not jump back to top.
- [ ] (Clean up: soft-delete the probe row via step 4 procedure.)

### Step 7 — 403 toast wording (locked verbatim)

- [ ] Sign in as a **second principal** who is a member of `default` but NOT an admin and is NOT the author of any row in the team. Easiest path: a freshly auto-joined org member, or temporarily downgrade your role.
- [ ] Find a row you did NOT author. The truth_level dropdown and Delete button on that row should be visibly **disabled** (greyed out).
- [ ] Open DevTools → Elements → re-enable the dropdown (remove the `disabled` attribute). Pick a new truth_level value.
- [ ] The PATCH fires and the server returns 403. The toast displays the **EXACT** text:
      > You can only edit items you created. Contact a team admin to modify items created by others.
- [ ] No state change in the row (badge does not flip).
- [ ] Sign back in as your normal admin account before step 8.

### Step 8 — Superadmin dashboard end-to-end (REVISION 2)

**Preamble:** confirm at least 2 teams exist in the DB:
```
docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
  "SELECT slug, name FROM teams ORDER BY slug LIMIT 10"
```
If there's only `default`, create another via:
```
curl -X POST https://api.example.com/v1/admin/teams \
  -H "Authorization: Bearer $SUPERADMIN_XBT" -H "Content-Type: application/json" \
  -d '{"slug":"uat-extra","name":"UAT Extra Team"}'
```

#### 8a — Superadmin view

- [ ] Sign in as a **superadmin** principal (`sub` listed in `ADMIN_USER_SUBS`).
- [ ] Open https://app.example.com/account/admin/ in a fresh tab.
- [ ] All **4 sections** render within ~2 s of page load:
      - Brain Overview matrix (per team × entity_type)
      - Storage table (per team rows / Qdrant points / MinIO bytes — N/A cells acceptable)
      - Activity sparklines (one inline SVG per team, 30-day window)
      - Top Sources table (top-5 + "other")
- [ ] Brain Overview lists **at least 2 teams**.
- [ ] DevTools Network panel shows:
      - 1× GET `/v1/admin/brain/overview` → 200
      - 1× GET `/v1/admin/brain/storage` → 200 (or 200 with N/A in cells)
      - 1× GET `/v1/admin/brain/activity` → 200
      - 1× GET `/v1/admin/brain/sources` → 200

#### 8b — Drill-down with banner + audit

- [ ] Click **Drill down →** on any team row in Brain Overview (any team works — `default` or the seeded extra).
- [ ] Browser navigates to `https://app.example.com/account/teams/brain/?team=<slug>&as_superadmin=1`.
- [ ] A **yellow banner** appears at the top of the page reading exactly:
      > Viewing as superadmin — this access is logged.
- [ ] The brain monitor table renders for the target team.
- [ ] Every Delete / Restore button is hidden, and every truth_level dropdown is disabled (read-only superadmin v1).
- [ ] **Audit row written.** On the VM:
      ```
      docker exec -i xbrain-postgres psql -U xbrain -d xbrain -c \
        "SELECT created_at, payload->>'target_team_slug', payload->>'actor_sub' \
         FROM audit_log WHERE action='superadmin_brain_access' \
         AND team_scope='<slug>' ORDER BY created_at DESC LIMIT 3"
      ```
      The most recent row is within the last minute and `target_team_slug` matches the slug you drilled into.
- [ ] **Polling preserves audit cadence.** Wait 30+ s on the drill-down page. Re-run the SQL above. Row count has increased (one new row per poll cycle).
- [ ] Browser-back to `/account/admin/`. The dashboard reappears (re-fetched or cached — either is acceptable).

#### 8c — Non-superadmin → 403 fallback

- [ ] Sign out, then sign in as a **non-superadmin** member of any team.
- [ ] Open `/account/admin/` directly.
- [ ] The page renders an **"Access denied" panel** (no dashboard sections, no team data).
- [ ] DevTools Network panel shows exactly **1 GET** to `/v1/admin/brain/overview` returning **403**, then **no further admin requests** fire.
- [ ] Refreshing the page reproduces the same 403 + fallback panel (no admin endpoint accidentally cached for the non-admin).

---

## Sign-off

- [ ] Steps 1 through 8 all passed (sub-steps 8a / 8b / 8c included)
- [ ] Any FAIL or SKIP recorded below: ______________________________________________
- [ ] Verifier signature: ______________________________________________
- [ ] Date: ______________________________________________

When everything is ticked, reply `uat-pass` to the orchestrator. On a FAIL,
reply `uat-fail: step-N` with a one-line description and a gap-closure plan
will follow.
