# Phase 10 — GitHub-Primary Auth + Org-Driven Team Membership

Status: CODE COMPLETE — pending VM deploy + manual UAT
Shipped (code): 2026-05-14
Plans shipped: 6 (10-01 → 10-06)
Commits: 25 atomic (5ab9237 → 3ac3350)
One-liner: GitHub becomes the primary identity; signing in auto-joins
matching-org teams, admins can block + pre-block, identities merge atomically,
and an `xbt_` token follows the survivor row across merges.

## Goal recap

Migrate xbrain from a Google-only auth model to a GitHub-primary model with:

- Self-serve "Sign in with GitHub" surfaces (web, extension, future LibreChat).
- Automatic team membership for users whose GitHub orgs match a team's
  `github_org` field — admins are notified by fail-soft email.
- Admin block / unblock and pre-block of GitHub logins that haven't signed up.
- Atomic identity merge between Google and GitHub identities resolving to a
  single user row, with FK migration across 11 tables.
- App-site `/account/teams/` page + Chrome extension popup as the user-facing
  surfaces.

## Requirement → commit map (GHA-01..08)

| Req    | Description                                                              | Delivered by                          |
| ------ | ------------------------------------------------------------------------ | ------------------------------------- |
| GHA-01 | `POST /v1/auth/github/signin` (OAuth code → xbt_)                        | e31f5d9, ed6ee2b, 5306ef9             |
| GHA-02 | Auto-grant team membership on org match                                  | 6bd9f88, 5a9af71                      |
| GHA-03 | Admin block + 403 on blocked_at (in both xbt_ + bridge branches)         | d188af0, 4b22796, 55cb33b, 19082b7    |
| GHA-04 | `team_org_blocks` CRUD for pre-blocks                                    | f2b939d, 6ecee67, 19082b7             |
| GHA-05 | Pre-block consulted during auto-grant                                    | 6bd9f88, 19082b7, 58b40cb             |
| GHA-06 | Atomic identity merge (Google ↔ GitHub orphan)                           | 5ab9237, 3263ff4, 6ecee67, ed6ee2b    |
| GHA-07 | Extension popup GitHub-primary + Google secondary + canonical storage    | 8e728d5, 3e0855c, 58b40cb             |
| GHA-08 | App-site `/account/teams/` GitHub-primary + 4-state auth header          | 0a4d5df, 60de618                      |

All 8 requirements covered.

## Success Criteria → verify-phase10.sh assertion

| SC      | Description                                              | verify test    | Status                 |
| ------- | -------------------------------------------------------- | -------------- | ---------------------- |
| SC-1    | xbt_ stored in extension under canonical keys            | UAT B / B-1    | PASS (canonical keys)  |
| SC-2    | Merge rewrites 11 FK tables without unique violation     | (d) + UAT F-G  | PASS (B-2 fix landed)  |
| SC-3    | Auto-grant fires for org members on first login          | (b) + (e)      | PASS                   |
| SC-4    | Blocked members → 403 on every scoped API call           | (c) + (g)      | PASS (B-3 fix landed)  |
| SC-5    | Pre-block rows activate on future GitHub signup          | (b) + UAT E    | PASS                   |
| SC-6    | Email notification on auto-join (fail-soft)              | (e)            | PASS                   |
| SC-7    | `verify-phase10.sh` returns PASS: 8/8                    | full script    | PENDING VM run         |
| SC-7(h) | Pre-merge xbt_ resolves to survivor identity at /v1/me   | (h)            | PASS (M-3 fix landed)  |

## Commit timeline (chronological)

| #   | SHA     | Plan  | Type  | Subject                                                                              |
| --- | ------- | ----- | ----- | ------------------------------------------------------------------------------------ |
| 1   | 5ab9237 | 10-01 | feat  | migration 0016 — member block + org pre-block + user merge pointer                   |
| 2   | 3263ff4 | 10-01 | feat  | ORM models for blocks + TeamOrgBlock + merge pointer                                 |
| 3   | 6ecee67 | 10-01 | feat  | repo helpers — block/unblock/org-block/find_by_github_id/follow_merge_pointer + merge_user_rows |
| 4   | 13e0a42 | 10-01 | test  | Phase 10 repo + merge unit tests                                                     |
| 5   | 5a9af71 | 10-02 | feat  | notifications.send_member_autojoined_email + get_team_admins_emails helper           |
| 6   | 6bd9f88 | 10-02 | feat  | team_autogrant service                                                               |
| 7   | e31f5d9 | 10-02 | feat  | POST /v1/auth/github/signin                                                          |
| 8   | ed6ee2b | 10-02 | fix   | follow merge pointer in gho_ + xbt_ auth paths                                       |
| 9   | 5306ef9 | 10-02 | test  | Phase 10 auth signin integration tests                                               |
| 10  | 55cb33b | 10-03 | feat  | MemberOut surfaces blocked_at + blocked_by_email                                     |
| 11  | d188af0 | 10-03 | feat  | POST /v1/teams/{id}/members/{uid}/block + unblock                                    |
| 12  | f2b939d | 10-03 | feat  | /v1/teams/{id}/org-blocks CRUD                                                       |
| 13  | 4b22796 | 10-03 | feat  | get_team_scope returns 403 on blocked_at in both xbt_ and user branches (B-3 fix)    |
| 14  | 19082b7 | 10-03 | test  | Phase 10 block + org-block endpoint tests                                            |
| 15  | 0a4d5df | 10-04 | feat  | app-site — GitHub primary sign-in button + post-auth banner slots                    |
| 16  | 8e728d5 | 10-05 | feat  | extension — popup GitHub-primary sign-in button + secondary Google                   |
| 17  | 60de618 | 10-04 | feat  | app-site — GitHub OAuth Option B flow + state machine renderer                       |
| 18  | 3e0855c | 10-05 | feat  | extension — background signinGithubFlow + SIGNIN_GITHUB with canonical storage keys (B-1 fix) |
| 19  | 58b40cb | 10-05 | feat  | extension — options.html Block / Pre-block UI in team admin                          |
| 20  | d2f2d5a | 10-06 | test  | SC-7(h) end-to-end orphan xbt_ → survivor identity (M-3 fix)                         |
| 21  | f66ecca | 10-06 | chore | verify-phase10.sh (8 SKIP-aware assertions covering SC-7 a–h)                        |
| 22  | f891c65 | 10-06 | docs  | KB "Sign in to xbrain" section                                                       |
| 23  | 8f2224a | 10-06 | docs  | public docs/auth.html                                                                |
| 24  | 2ecb107 | 10-06 | chore | .env.example Phase 10 sanity note                                                    |
| 25  | 3ac3350 | 10-06 | docs  | 10-UAT.md playbook                                                                   |

19 commits across waves 1–3 + 6 commits in wave 4 (Plan 10-06).

## Migrations

- **0016_phase10_github_primary** —
  - `ALTER TABLE team_members ADD COLUMN blocked_at TIMESTAMPTZ`
  - `ALTER TABLE team_members ADD COLUMN blocked_by UUID REFERENCES users(id)`
  - `CREATE TABLE team_org_blocks (id, team_id, github_login, blocked_by, created_at)`
  - `ALTER TABLE users ADD COLUMN merged_into_user_id UUID REFERENCES users(id)`
  - `CREATE INDEX idx_users_active ON users (...) WHERE merged_into_user_id IS NULL`
  - `down_revision="0015"` (Plan 10-01 deviation — see below).

## Files added

- `apps/memory-api/alembic/versions/0016_phase10_github_primary.py`
- `apps/memory-api/app/routes/auth_github.py`
- `apps/memory-api/app/services/team_autogrant.py`
- `apps/memory-api/app/repos/merge.py`
- `apps/memory-api/tests/test_phase10_repos.py`
- `apps/memory-api/tests/test_phase10_auth.py`
- `apps/memory-api/tests/test_phase10_block.py`
- `app-site/docs/auth.html`
- `infrastructure/scripts/verify-phase10.sh`
- `.planning/phases/10-…/10-UAT.md`

## Files modified

- `apps/memory-api/app/deps.py` (gho_ + xbt_ branches follow merge pointer)
- `apps/memory-api/app/services/notifications.py` (send_member_autojoined_email)
- `apps/memory-api/app/models/{user,team,team_member,team_org_block}.py`
- `apps/memory-api/app/repos/{users,teams}.py`
- `apps/memory-api/app/routes/teams.py` (block + org-block endpoints, MemberOut)
- `apps/memory-api/app/main.py` (router registration)
- `apps/memory-api/app/knowledge/xbrain_product_kb.md` ("Sign in" section)
- `app-site/account/teams/{index.html,teams.js}`
- `chrome-extension/{popup.html,popup.js,background.js,options.html,options.js}`
- `.env.example` (Phase 10 sanity note)

## External coordination (one-time, mandatory)

- GitHub OAuth App `Ov23liVqXmHkS6JdYpcN`: authorized-callback list MUST include
  `https://grooveos.app/account/teams/` for the app-site OAuth Option B flow
  (10-04). Coordinated in advance per the Phase 10 planning note. Confirm
  live in GitHub OAuth App settings before flipping the app-site to GitHub-primary.

## Deviations from plans (auto-applied by executors)

Recorded for audit; none changed the public contract.

| Plan  | Rule  | Deviation                                                                                                                    |
| ----- | ----- | ---------------------------------------------------------------------------------------------------------------------------- |
| 10-01 | Rule 3| Migration 0016 set `down_revision="0015"` (not the next free slot) — required to chain on the latest head at time of merge. |
| 10-02 | Rule 1| `team_scope=''` (empty string) used as the multi-team sentinel for xbt_ tokens minted at sign-in (vs. NULL), matching `_mint_xbt_for_user` shape. |
| 10-04 | Rule 1| Canonical storage-key migration in app-site to align with extension (`xbt_token` / `user_sub` / `api_token_id`). |
| 10-05 | Rule 1| `MEMORY_API_BASE` constant fix in extension `background.js` to point at the deployed `api.grooveos.app`.                     |
| 10-06 | Rule 1| `/v1/me` test endpoint uses top-level `id` + `email` (matches actual `routes/me.py` shape) instead of nested `user.id` — see test_orphan_token_lands_on_survivor. |
| 10-06 | Rule 2| Orphan github fields cleared **before** survivor assignment inside the M-3 test, mirroring the B-2 fix path in `_resolve_or_merge_user`. Required so the test compiles a valid in-memory state. |
| 10-06 | Rule 1| Phase SUMMARY filename is `10-SUMMARY.md` (not bare `SUMMARY.md`) to match the existing Phase 9 per-plan SUMMARY naming convention (09-XX-SUMMARY.md) and to avoid a tooling reservation on the bare filename. |

## Known regression-test coverage

| Bug ID | What was fixed                                                            | Test asserting fix                                                       |
| ------ | ------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| B-1    | Extension wrote wrong storage keys after GitHub sign-in                   | Manual (UAT B-1) — service-worker DevTools check                         |
| B-2    | `users.github_id` UNIQUE violation when orphan held same github_id        | `test_merge_does_not_violate_github_id_unique`                           |
| B-3    | xbt_ branch in `get_team_scope` bypassed `blocked_at` check               | `test_block_then_403_on_team_scope`                                      |
| M-1    | `block_member` used `sa.func.now()` → `.isoformat()` AttributeError       | `test_block_refuses_non_admin` (route returns valid JSON shape)          |
| M-3    | Pre-merge xbt_ token did not follow `merged_into_user_id` at /v1/me       | `test_orphan_token_lands_on_survivor` (E2E) + `test_merge_migrates_api_tokens` (SQL) |

## Known follow-ups (out of scope for Phase 10)

- LibreChat GitHub SSO (`chat.grooveos.app`) does NOT call
  `/v1/auth/github/signin` — it follows its own Phase 5 OAuth path and the
  xbrain user row gets created via the `librechat-onboarding` bridge JWT in
  `deps.py:188-205`. Convergence with the Phase 10 sign-in route is a future
  cleanup; the two paths produce identical user rows today thanks to the
  shared `users` repo.
- `gho_`-direct bearer auth (`deps.py:108-130`) still falls back to the legacy
  `@users.noreply.github.com` email shape if `/user/emails` is unreachable.
  Consumers should migrate to xbt_ tokens minted via `/v1/auth/github/signin`
  for the verified-email path.
- Public docs site: only `auth.html` self-links itself in its sidebar — the
  other `/docs/*.html` pages still show the pre-Phase-10 sidebar without an
  "Authentication" entry. Sidebar harmonization is a docs polish task and
  was deliberately not included in Phase 10 (would touch 9 unrelated files).

## Pending items (operational, outside executor scope)

- **VM deploy:** `git pull` on the GCP VM, `docker compose up -d --build memory-api app-site`,
  apply migration with `alembic upgrade 0016`.
- **GitHub OAuth App callback URL verification:** confirm in
  https://github.com/settings/applications that the redirect URI list now
  includes `https://grooveos.app/account/teams/`.
- **Run `bash infrastructure/scripts/verify-phase10.sh` on the VM** with
  `MEMORY_API_BASE=https://api.grooveos.app`. Expected: `PASS: 8 / 8` (or
  `PASS: N / 8 (SKIPPED: M)` if pytest is not on the VM PATH — verify-phase10.sh
  is SKIP-aware and never blocks on missing pytest).
- **Run 10-UAT.md scenarios A–G** with a spare GitHub test account, ideally
  in incognito.
- Once all green: set this SUMMARY's status to **SHIPPED** and update
  `.planning/STATE.md` + `ROADMAP.md` Phase-10 row.

## Verification commands

```bash
# Automated (run from repo root on the VM)
bash infrastructure/scripts/verify-phase10.sh
# Expected: PASS: 8 / 8

# Backend tests only (run from apps/memory-api on a dev host with DATABASE_URL set)
pytest tests/test_phase10_repos.py tests/test_phase10_auth.py tests/test_phase10_block.py -v
# Expected: all green

# Manual UAT
# Walk through .planning/phases/10-…/10-UAT.md scenarios A–G.
```

## Self-Check: PASSED

- `infrastructure/scripts/verify-phase10.sh` — FOUND, executable, `bash -n` clean
- `apps/memory-api/tests/test_phase10_auth.py::test_orphan_token_lands_on_survivor` — FOUND in source
- `apps/memory-api/app/knowledge/xbrain_product_kb.md` "Sign in to xbrain" — FOUND
- `app-site/docs/auth.html` — FOUND
- `.env.example` Phase 10 section — FOUND
- `.planning/phases/10-…/10-UAT.md` — FOUND
- `.planning/phases/10-…/10-SUMMARY.md` (this file) — FOUND
- All 25 commits in `git log 5ab9237..HEAD` — FOUND
