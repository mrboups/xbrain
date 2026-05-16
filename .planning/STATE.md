---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: shipped
stopped_at: "Phase 10 LIVE + Phase 8/9 verified live + OAuth web sign-in fixed end-to-end. Phase 11 PLANNED ready for /gsd:execute-phase 11. Phase 12 ROADMAPPED (GitHub App migration, planning pending until Phase 11 ships)."
last_updated: "2026-05-17T23:30:00.000Z"
last_activity: "2026-05-17 -- Phase 10 OAuth client_id+callback URL bug fixed (commit 8c3df36), end-to-end web sign-in validated. Phase 8/9 confirmed LIVE on VM (verify scripts PASS). Phase 12 roadmapped (e5ef93b). mcp-brain healthcheck fix staged. Logging caps staged."
progress:
  total_phases: 12
  completed_phases: 10
  total_plans: 90
  completed_plans: 79
  percent: 83
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-02)

**Core value:** Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.
**Current focus:** Phase 11 — Brain Monitor (Universal Truth-Level Inspector + Soft Delete + Superadmin Dashboard) — ready for `/gsd:execute-phase 11`. Phase 12 (GitHub App migration) roadmapped, planning pending until Phase 11 ships.

## Current Position

Phase: 10 (GitHub-Primary Auth + Org-Driven Team Membership) — **LIVE 2026-05-14** (web sign-in fix 2026-05-17, commit 8c3df36)
Plan: 6 of 6 (all plans shipped + verify-phase10.sh PASS + Playwright smoke PASS); OAuth web sign-in end-to-end validated 2026-05-17
Status: Phase 10 LIVE end-to-end. Phase 8 + Phase 9 reconfirmed LIVE on VM today (verify-phase8.sh PASS 7/7 + verify-phase9.sh PASS 6/6 with 2 acceptable SKIP). Next: `/gsd:execute-phase 11` (Brain Monitor — 11 plans, wave 1→2→3a→3b→3c→4→5→6).
Last activity: 2026-05-17 -- Phase 10 OAuth client_id+callback URL bug fixed (commit 8c3df36). Real OAuth App ID `Ov23liy7tZekl0uEztoj` replaces placeholder `Ov23liVqXmHkS6JdYpcN` in app-site/teams.js + chrome-extension/background.js; callback URL on OAuth App `xbrain` updated from `chat.grooveos.app/oauth/github/callback` → `grooveos.app/account/teams/` via Playwright. End-to-end web sign-in validated via Playwright (JWT received, Dejavudev team listed). Phase 12 roadmapped (commit e5ef93b). mcp-brain unhealthy fix staged (nc missing → Python socket probe). Logging caps added to all 29 services via YAML anchor.

Progress: [████████░░] 83% — 10 of 12 phases COMPLETE (Phase 11 PLANNED, Phase 12 ROADMAPPED)

## Performance Metrics

**Velocity:**

- Total plans completed: 0
- Average duration: —
- Total execution time: —

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| — | — | — | — |

**Recent Trend:**

- Last 5 plans: —
- Trend: —

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- 2026-05-02: Couche mémoire = mem0 + memory-api natif (Memstate/Remembra/Memori retirés comme dépendances directes)
- 2026-05-02: VM strategy confirmée : e2-medium (P1) → e2-standard-2 (P2 entry gate) → e2-standard-4 ou split Langfuse (P3 entry gate)
- 2026-05-02: MinIO via Chainguard image (`cgr.dev/chainguard/minio:latest`) — images Docker Hub discontinuées oct 2025
- 2026-05-02: `MemoryProvider` interface dans `/packages/memory-models` obligatoire avant toute intégration mem0 (Phase 2)
- 2026-05-02: Langfuse sur e2-medium Phase 1 : déployé en config légère sans ClickHouse complet — surveiller RAM ; si OOM, migrer Langfuse à Phase 2 start post-VM-upgrade
- 2026-05-05 (D-06): OAuth state param changé de team_scope → mapping_id UUID pour supporter N folders/team (T-04-06-SEC-02 accepted)
- 2026-05-05 (04-03): mcpSettings.allowedDomains requis pour débloquer les hosts Docker internes (SSRF protection LibreChat v0.8.5 bloque par défaut)
- 2026-05-06 (05-01): graphiti_client initialisé dans lifespan() uniquement — piège event loop graphiti-core
- 2026-05-06 (05-01): OPENAI_API_KEY obligatoire pour graphiti-service même avec Anthropic LLM (embeddings text-embedding-3-small ne supportent pas Anthropic)
- 2026-05-06 (05-01): SEMAPHORE_LIMIT=3 défaut pour respecter rate limit Anthropic Haiku Tier 1
- 2026-05-06 (05-02): GitHub token détecté par préfixe gho_ dans deps.py — simple, sans appel API supplémentaire
- 2026-05-06 (05-02): github_is_org_member=None (pas False) pour Google users — permet `if ... is False` sans bloquer Google users (D7)
- 2026-05-06 (05-02): source_user_id GitHub = github:{login} — robuste car email GitHub peut être null
- 2026-05-06 (05-03): Stockage projets dans memory_items (source='admin:project') — pas de migration Alembic 0008
- 2026-05-06 (05-03): brain-index.sh fail-soft (exit 0) — T-05-03-04 accepted, brain indexing optionnel
- 2026-05-06 (05-03): Workload Identity Federation recommandé (sans JSON key file) pour Cloud Run deploys
- 2026-05-06 (05-04): launchWebAuthFlow response_type=id_token (Solution A) — ID token JWT compatible verify_google_id_token sans modifier auth.py
- 2026-05-06 (05-04): chrome-extension://* wildcard CORS — acceptable, auth Bearer token est le vrai contrôle (T-05-04-03 accepted)
- 2026-05-06 (06-01): Firebase multi-site — site ID xbrain-marketing dans firebase.json, targets dans .firebaserc, projet xbrain-495115
- 2026-05-06 (06-01): Two-CSS architecture — style.css (global, chargé par toutes les pages) + docs.css (docs-only, chargé uniquement par docs/*.html)
- 2026-05-06 (06-01): public: "." dans firebase.json — firebase.json est à la racine de marketing-site/, pas de build step
- 2026-05-07 (07-01): tasks.created_by FK ON DELETE SET NULL (pas RESTRICT) — permet suppression user, tasks conservées avec attribution NULL
- 2026-05-07 (07-01): contacts table porte les 7 champs du tagging contract complets (visibility + validation_status inclus malgré optionalité v1)
- 2026-05-07 (07-01): granola_integrations.api_key_enc = Text brut DB — chiffrement Fernet couche applicative (Plan 07-04)
- 2026-05-07 (07-03): Bridge JWTs rejected 401 at POST /v1/tasks — created_by NOT NULL invariant preserved
- 2026-05-07 (07-03): PATCH audit differentiates task.status_changed (from/to) vs task.updated
- 2026-05-07 (07-03): _validate_assignee runs SELECT before INSERT/UPDATE — cross-team assignee returns 422
- 2026-05-07 (07-04): FERNET_KEY uses OAUTH_CREDENTIALS_ENCRYPTION_KEY as fallback — single key source for all Fernet encryption
- 2026-05-07 (07-04): created_by = NULL for system-generated tasks (migration 0010 nullable) — distinguishes auto-generation from user creates
- 2026-05-07 (07-04): _is_admin moved from admin_drive.py to deps.py — DRY shared helper, imported by both admin_drive.py and granola_integration.py
- 2026-05-07 (07-08): UPDATE last_polled_at BEFORE _fetch_notes — at-most-once delivery + note-level dedup in 07-04 = exactly-once-effective
- 2026-05-07 (07-08): FERNET_KEY fallback to OAUTH_CREDENTIALS_ENCRYPTION_KEY in granola-sync compose env — single Fernet key source
- 2026-05-07 (07-08): 401/403 Granola = log.warning only (plan insuffisant fail-soft), not log.error
- 2026-05-07 (07-07): ANTHROPIC_API_KEY not duplicated in .env.example — comment reference to existing var at line 20
- 2026-05-07 (07-07): Nginx Phase 7 blocks placed in x.dejavu.cat server block (port 80) alongside existing /memapi/ routes
- 2026-05-07 (07-07): location /v1/tasks (no trailing slash) — nginx prefix match captures both /v1/tasks and /v1/tasks/{id}
- 2026-05-07 (07-07): verify-phase7.sh uses set -uo pipefail (not -e) — all 8 tests run independently
- 2026-05-07 (07-09): Bridge sets metadata.contains_action=true rather than calling /v1/tasks directly — 07-03 rejects bridge JWT
- 2026-05-07 (07-09): TASK_INTENT_DETECTION=false default — opt-in kill-switch for D5 trigger 3
- 2026-05-07 (07-09): Lazy anthropic import in _get_client() — module loads without package installed
- 2026-05-07: Domain migration dejavu.cat → grooveos.app. Subdomains: x→chat, ai→adm. Canonical URLs: chat.grooveos.app (LibreChat), adm.grooveos.app (Open WebUI), api.grooveos.app (memory-api), lang.grooveos.app (Langfuse), grooveos.app (app-site Firebase), projects.grooveos.app (dashboard Firebase).
- 2026-05-09 (08-partial): GitHub OAuth read:org scope added (githubStrategy.js patch) — required for /api/xbrain/github-orgs
- 2026-05-09 (08-partial): github_access_token stored in MongoDB users collection at OAuth login (socialLogin.js) — all 3 login paths (same provider, linked, new user)
- 2026-05-09 (08-partial): /api/xbrain/github-orgs endpoint (xbrain-routes.js) — reads github_access_token from Mongo, calls GitHub /user/orgs, returns [] for Google-only users
- 2026-05-09 (08-partial): POST /v1/teams/self-solo (teams.py) — idempotent solo workspace creation; GET /v1/teams/my-team — 204 if no team
- 2026-05-09 (08-partial): onboarding.js boot() — checks orgs → if [] → createSoloTeam() → renderSoloWelcome(); else → renderPicker()
- 2026-05-09 (08-partial): Bridge JWT requires scope="bridge" field — authlib HS256, iss=librechat-onboarding; missing scope raises ValueError
- 2026-05-09 (08-partial): XBRAIN_BRIDGE_JWT secret + XBRAIN_TEAM_SCOPE=dejavudev set in GitHub Actions — dashboard now partial=False
- 2026-05-09 (08-partial): generate_dashboard.py uses requests.Session() with User-Agent to bypass Cloudflare bot detection
- 2026-05-09 (08-01): Migration 0012 — granola_user_connections FK CASCADE (RGPD-friendly) + agent_definitions FK SET NULL (cohérent 0010 tasks)
- 2026-05-09 (08-01): UNIQUE(user_id) sur granola_user_connections — un user = au plus une clé Granola active
- 2026-05-09 (08-01): Seed meeting-recap via bindparams + ON CONFLICT (name) DO NOTHING — idempotent, safe re-run
- 2026-05-17 (audit): Phase 8 + Phase 9 reconfirmed LIVE on VM (verify-phase8.sh PASS 7/7 + verify-phase9.sh PASS 6/6 with 2 acceptable SKIP — VERIFY_XBT_TOKEN missing for [5/8] WS E2E test, node missing on prod VM for [8/8] translator test)
- 2026-05-17 (phase11/12 ordering): Phase 11 executes before Phase 12 (Brain Monitor first, GitHub App migration after public-deployment readiness)
- 2026-05-17 (phase12 strategy): Clean break OAuth → GitHub App (no dual-auth) — only 1 existing user (mrboups), acceptable to re-authorize once
- 2026-05-17 (oauth fix): GitHub OAuth web sign-in had double bug — placeholder client_id `Ov23liVqXmHkS6JdYpcN` (didn't exist on GitHub) + callback URL on OAuth App "xbrain" pointing to legacy `chat.grooveos.app/oauth/github/callback`. Fixed by updating callback URL via Playwright + replacing client_id with real `Ov23liy7tZekl0uEztoj` in `app-site/teams.js` + `chrome-extension/background.js` (commit 8c3df36)
- 2026-05-17 (extension auth): Chrome extension auth flow remains broken (`chromiumapp.org` callback doesn't match OAuth App single-callback constraint). Resolved naturally by Phase 12 (GitHub App multi-callback support). Deferred.

### Pending Todos

None yet.

### Blockers/Concerns

- **Phase 8 onboarding E2E walk pending** : verify-phase8.sh PASS 7/7 (endpoints répondent correctement, reconfirmed 2026-05-17 on VM), mais le flux user-facing E2E (Google → github-orgs → solo-team → Granola key step) n'a pas encore été walké manuellement avec une vraie clé Granola.
- **Chrome extension GitHub flow broken until Phase 12 ships** : single-callback OAuth App constraint — `chromiumapp.org` callback doesn't match the single web callback URL. Resolved naturally by Phase 12 (GitHub App multi-callback support). Web sign-in (app-site) works fine since fix 2026-05-17.
- **GH_API_PAT mrboups repos** : GitHub Actions dashboard montre 0 repos pour l'utilisateur `mrboups` — le PAT semble ne pas avoir le scope `repo` ou est expiré. Vérifier `gh secret list` et regénérer si besoin. (Will be replaced by GitHub App installation token in Phase 12.)

**Resolved (archivé):**

- ~~Phase 2 entry gate POC~~ — Phases 2-7 complètes, mem0 + native memory-api retenu
- ~~Phase 3 entry gate POC Memori~~ — Phase 3 complète, fallback LangGraph utilisé
- ~~OOM Risk Phase 1~~ — VM upgradée e2-standard-2 (8 GB) depuis Phase 2
- ~~chat.grooveos.app 502~~ — Race condition nginx/librechat au démarrage, auto-résolu
- ~~VM disk~~ — Était à 99% le 2026-05-07 et de nouveau 100% pendant deploy Phase 10 (2026-05-14, clickhouse log 17GB). Résolu via truncate + agrandissement disque. Logging caps (max-size 100m, max-file 3) ajoutés à tous les 29 services 2026-05-17 pour prévenir récurrence.
- ~~Phase 10 OAuth web sign-in broken~~ — placeholder client_id + callback URL mismatch on OAuth App "xbrain". Fixed 2026-05-17 (commit 8c3df36), validated end-to-end via Playwright.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-05-17
Stopped at: Phase 10 LIVE end-to-end (web sign-in fix 8c3df36 validated via Playwright). Phase 8 + Phase 9 reconfirmed LIVE via verify scripts on VM. Phase 12 (GitHub App migration) roadmapped (e5ef93b). Next action: `/gsd:execute-phase 11` (Brain Monitor — 11 plans, wave 1→2→3a→3b→3c→4→5→6).
Resume file: None

### Quick Tasks Completed

| Slug | Date | Commit | Status | Description |
|------|------|--------|--------|-------------|
| global-audit | 2026-05-09 | — | — | Global features audit + STATE.md/ROADMAP.md docs update |
| 260509-a1b-mcp-brain-remote-server | 2026-05-09 | 9f21d52 | Verified | mcp-brain remote MCP server for Claude.ai + ChatGPT web access to team brain |
| 260511-0jb-lot-2-quick-wins-llm-stack | 2026-05-11 | d8fcb69 | Verified | LibreChat grok-3 + Claude Reasoning endpoint + second-opinion 3-way (Sonnet+Opus 4.7+Grok-3) + Anthropic prompt caching on 6 extraction sites |
| 260512-eo1-extension-onboarding | 2026-05-12 | 5809d8b | Verified | Chrome extension single-click Connect/Disconnect (mints xbt_, persists to chrome.storage.local, revokes on disconnect) + popup/LibreChat FR→EN migration ("Claude Pro/Max"). 8 new tests PASS, phase-9 verify 6/6 PASS on VM. |
| 260512-spx-extension-sidepanel-libchat-autofill | 2026-05-12 | 7179d61 | Verified | Extension v1.2.0 — Chrome side panel mode (Chrome 114+) + LibreChat API key auto-fill on chat.grooveos.app, both toggleable via new Options page (`chrome.storage.sync`). 13 new tests PASS; suite at 6 test files / 53 assertions. |
| 260512-zca-extension-zero-click-auth | 2026-05-12 | a4dae12 | Verified | Zero-click onboarding — memory-api accepts Google OAuth access tokens (auth.py verify_google_access_token + deps.py auto-detect); extension uses chrome.identity.getAuthToken (silent post-consent); popup auto-mints on first open. memory-api 10/10 auth unit tests PASS; extension suite 6/6 still PASS. |
| 260512-cmu-extension-context-menu-silent-ux | 2026-05-12 | 2e3ca29 | Verified | Silent-only auto-mint (no consent popup at open without user click); in-flight promise dedup for concurrent getGoogleIdToken (kills double-popup); right-click "Add selection to xbrain" context menu opens side panel with text pre-filled. Suite still 6/6 PASS. |
| 260512-csm-context-submenu-teams | 2026-05-12 | f2fa3a3 | Verified | Context menu upgraded — "CortX OS" parent with one child per team the user belongs to. Click a team child → side panel opens with text + team_scope pre-selected. 24h cached team list, auto-refresh on storage.onChanged.local.xbt_token. Suite still 6/6 PASS. |
| 260512-glk-link-github-account | 2026-05-12 | dbf6ad8 | Verified | Link GitHub account from extension. /v1/me exposes github_username/id; /v1/teams/my-teams merges GitHub-org teams when linked; new POST /v1/me/link-github-with-code does server-side code exchange. Extension "Link" button + GitHub OAuth via launchWebAuthFlow, post-link cache invalidation refreshes CortX OS submenus. memory-api 10/10 PASS, ext 6/6 PASS. |
| 260512-tcr-team-chat-realtime | 2026-05-12 | d7716c1 | Verified | Team chat realtime: Centrifugo v6 broker (centrifugo.grooveos.app, MIT, ~50MB RAM), alembic 0015 team_messages, 4 new endpoints (POST/GET messages, /me/centrifugo-token, /teams/X/agent-context-bundle), session-bridge accepts acting_user_sub JWT, inline Claude handler with Pro/Max routing + Anthropic fallback, 5min team memory cache for Anthropic prompt cache hits, full extension UI redesign (chat-first, clip overlay, 📎 button, ⚙️ + 💬 header icons). 24 chat_stream tests + 23 mention_detector PASS; ext suite 7/7. |
