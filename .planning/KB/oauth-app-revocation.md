# OAuth App `xbrain` revocation runbook

**Status:** Pending (run 24h after Phase 12 deploy if no regressions)
**OAuth App to revoke:** `xbrain` — Client ID `Ov23liy7tZekl0uEztoj`
**OAuth App to KEEP:** `xbrain LibreChat` — Client ID `Ov23li0XHV3NL8Git7Dk` (separate concern)
**Operator:** mrboups (App owner)

## When to revoke

After ALL of these are confirmed:

1. Phase 12 deploy has been live ≥ 24 hours.
2. mrboups has successfully signed in via the new GitHub App from BOTH the
   web app AND the Chrome extension.
3. `verify-phase12.sh` PASS rate ≥ 16/18 (the manual `LOCKDOWN_TEST` step
   counts as 1; REVISION 2 added 1 SC-5 assertion).
4. No errors in memory-api logs matching `auth.github` or `github_app`
   over a 6-hour observation window.

If any of these are not met, DO NOT REVOKE. Diagnose first.

## Revocation steps

1. Visit https://github.com/settings/applications.
2. Find the OAuth App named `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`).
   The legacy management URL is https://github.com/settings/applications/3585830
   (the numeric App registration ID, distinct from the public Client ID).
3. Click "Edit" → scroll to bottom → "Delete application".
4. GitHub prompts to confirm by typing the app name. Type `xbrain` exactly.
5. Confirm. The App is now removed.

## Verification post-revocation

1. Sign out of example.com.
2. Visit https://example.com/account/teams/. Click "Sign in with GitHub".
3. Consent screen MUST show the new GitHub App (NOT the OAuth App). Click Authorize.
4. Land on the teams page with the same teams visible as before.
5. Verify in DevTools: the OAuth `authorize` URL points to the GitHub App's
   `client_id` (`Iv23liVnZvIN0Lo6isof` matching `GITHUB_APP_CLIENT_ID` in
   `.env` on the VM), NOT the old `Ov23liy7tZekl0uEztoj`.

## Rollback

If revocation breaks something unexpected:

- The OAuth App can be re-created with the same name + client_id IS NOT possible
  (GitHub issues a new client_id on re-creation). However, the chrome ext +
  app-site no longer reference the old client_id (Phase 12 swapped both in
  Plans 12-08 and 12-09), so there is no production code path that needs the
  old App back.
- Rollback path: revert commits from Phase 12 Plan 12-09 + 12-08 + 12-06 in
  the frontend repos. memory-api can stay on Phase 12 — the legacy
  `GITHUB_CLIENT_ID` env (LibreChat OAuth App, NOT the revoked one) is still
  valid and is consumed exclusively by `app/routes/me_github.py`.

## Environment cleanup (after revocation)

The deprecation comment in `.env.example` (added by Plan 12-04) references
`GITHUB_API_PAT` and `GITHUB_ORG_PAT`. Those are unrelated to OAuth App
`xbrain` and were already removed from code by Plan 12-04 — the env values
on the VM can be safely deleted alongside this revocation, but it's optional.

## Why the LibreChat OAuth App stays

`xbrain LibreChat` (Client ID `Ov23li0XHV3NL8Git7Dk`) is a separate OAuth
App owned by mrboups, used by LibreChat for its own GitHub social login +
`/api/xbrain/github-repos` proxy. It does NOT participate in xbrain's auth
or org-membership flow and is explicitly out of scope per `12-CONTEXT.md`.
DO NOT delete this App.

| App                  | Client ID              | Use                                                  | Action      |
| -------------------- | ---------------------- | ---------------------------------------------------- | ----------- |
| `xbrain` (OAuth App) | `Ov23liy7tZekl0uEztoj` | Legacy xbrain web/ext auth (replaced by GitHub App)  | **REVOKE**  |
| `xbrain LibreChat`   | `Ov23li0XHV3NL8Git7Dk` | LibreChat social login + github-repos proxy          | **KEEP**    |
| `xbrain` (GitHub App)| `Iv23liVnZvIN0Lo6isof` | Phase 12 xbrain web/ext auth + org install flow      | **KEEP**    |

## References

- `12-CONTEXT.md` (locked decisions)
- `12-RESEARCH.md` §Q7 (migration sequencing)
- Plans 12-08 + 12-09 (chrome ext + app-site `client_id` swap)
- Phase 10 commit `f57f458` (the deploy that introduced the OAuth App with
  the current callback URL).
- This runbook is gated on Plan 12-11 ship-pass success.
