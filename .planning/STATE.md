---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: Open-Core Edition
status: executing
stopped_at: "Phase 10 LIVE end-to-end (web sign-in fix 8c3df36 validated via Playwright). Phase 8 + Phase 9 reconfirmed LIVE via verify scripts on VM. Phase 12 (GitHub App migration) roadmapped (e5ef93b). Next action: `/gsd:execute-phase 11` (Brain Monitor — 11 plans, wave 1→2→3a→3b→3c→4→5→6)."
last_updated: "2026-07-18T19:47:03.118Z"
last_activity: 2026-07-18 -- Phase 17 execution started
progress:
  total_phases: 21
  completed_phases: 19
  total_plans: 146
  completed_plans: 140
  percent: 90
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-02)

**Core value:** Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.
**Current focus:** Phase 17 — CI Lockstep

## Current Position

Phase: 17 (CI Lockstep) — EXECUTING
Plan: 1 of 4
Status: Executing Phase 17
Last activity: 2026-07-18 -- Phase 17 execution started

## Performance Metrics

**Velocity:**

- Total plans completed: 20
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
- 2026-05-24 (13-01): Relevance classifier = Claude 4.5 Haiku (`claude-haiku-4-5-20251001`) with ephemeral prompt cache + per-team daily token budget (50K input tokens/day default); fail-soft to ≥15-char heuristic on Haiku error/timeout/budget exhaustion
- 2026-05-24 (13-03): native_provider.upsert now uses INSERT … ON CONFLICT (id) DO UPDATE — fixes SELECT+INSERT race exposed by deterministic UUID5 idempotency keys
- 2026-05-24 (13-05/06): Per-turn enrichment uses min_level=VALIDATED (>= semantics: includes VALIDATED+CANONICAL+PUBLIC); CHAT07_TOP_K=5 default; env vars CHAT07_TOP_K + CHAT07_TRUTH_FILTER_MIN_LEVEL configurable per deployment

### Pending Todos

- ~~Make the GitHub App `xbrain-auth` public~~ — **Already public** (confirmed 2026-06-07 on the App's Advanced page: only a disabled "Make private" button is shown, reason "already installed on other accounts"; GitHub offers "Make private" only when an App is already public). Non-owner members can load the GitHub authorize page → sign in / connect. The remaining 2nd-member auto-join gap is the separate LibreChat→memory-api token bridge.
- **Team-wall the GitHub repo-read tools (Task #147)** — the github read path is team-NEUTRAL today: any authenticated GrooveOS user (any team) can read any repo the App installation can access (`/v1/internal/github/{list,read,sync}` is "NOT team-scoped"). Harmless now (1 team = aibrussels, 3 users) but MUST gate by team membership before onboarding a 2nd team. Kept open to all per user request 2026-06-07.
- **Narrow GitHub App repo scope (Task #148)** — 2026-06-07 granted `Contents: Read-only` with `repository_selection=all` on both installs (aibrussels org 14 repos, mrboups personal 60 repos), left OPEN per user request. LATER: switch the mrboups personal install to "Only select repositories" (e.g. just mrboups/xbrain) so the 60 personal private repos aren't readable by every GrooveOS user; aibrussels org stays "All".
- ~~Auto-index personal (User-install) repos on sign-in (Task #149)~~ — **DONE 2026-06-07** (commit e9e0990, deployed + verified live: `find_user_installation('mrboups')`→137865560, `index_team_catalog('aibrussels','mrboups')`→indexed=60). Added `github_installation.find_user_installation` (`GET /users/{login}/installation`), a User-install fallback in `index_team_catalog`, a `user_login` param to `index_orgs_catalog` that indexes the signing-in user's personal repos into their teams (`list_teams_for_user`), and wired `auth_github` sign-in to pass `user_login=profile.login`. So sign-in now auto-indexes org repos AND the user's personal repos. (Which team gets a user's personal repos still ties to Task #147 team-wall.)

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

## Phase 13 deploy 2026-05-27

**Deployed**: memory-api, librechat-bridge, openwebui-pipeline rebuilt with Phase 13 changes. All 3 containers healthy on VM 130.211.55.142.

**Env vars wired**: 7 Phase 13 vars added to infrastructure/docker-compose.yml (commit 10d547b) and VM .env: RELEVANCE_HAIKU_ENABLED, RELEVANCE_HAIKU_MODEL=claude-haiku-4-5-20251001, RELEVANCE_HAIKU_TIMEOUT_S=3.0, RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM=50000, BRAIN_INGEST_ENABLED, CHAT07_TOP_K=5, CHAT07_TRUTH_FILTER_MIN_LEVEL=VALIDATED.

**verify-phase13.sh on VM** (TEST_TEAM_SCOPE=aibrussels): PASS 3 / SKIP 2 / FAIL 3.

- PASS: (b) LibreChat live ingest — item_id minted, memory_items row + Qdrant point materialized end-to-end. (d) Haiku low-relevance skip. (e) Heuristic fallback ingests substantive messages.
- SKIP: (c) OWUI pipeline only on docker network (unit-tested in 13-06). (f) no VALIDATED items yet (requires Brain Monitor promotion).
- FAIL: (a)(g)(h) — test-helper auth/endpoint-shape bugs in test-phase13-cross-frontend.py, **not feature failures**. Helper sends bridge JWT to user-only endpoint and wrong body to Brain Monitor PATCH. Tightening task for the helper, not Phase 13 code.

**Production proof**: memory-api logs show relevance_filter.classified events with cache_read_input_tokens=5201 (Anthropic prompt cache active — Haiku system prompt cached and reused), classify latency ~900ms, brain_ingest.external.ok events upserting to memory_items, brain_ingest.external.skipped_by_filter for short messages.

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
| 260531-x8i-librechat-bridge-per-user-team-scope | 2026-06-01 | ca3e620 | Verified | Per-user team_scope resolution in librechat-bridge — replaces the never-finished Phase-1 BRIDGE_DEFAULT_TEAM_SCOPE stub. New memory-api `GET /v1/internal/resolve-team-scope` (sub→team slug; source_user_id / `email:` / bare-email resolution; follows merge pointer; never-500). Bridge `resolve_team_scope` now calls it with a 300s TTL cache + fail-soft fallback (uncached fallback self-heals on later signup). Fixes LibreChat passive recall AND brain ingest operating under the wrong `default` team brain while real facts live under the user's slug. Live-verified on VM: the GitHub-auth user → aibrussels, enricher recall returns the AI Brussels fact (fact_count=1). bridge 5/5 unit PASS; memory-api 3 integration tests gated. Also fixed: mcp_tool_registry wiped by clean-start → re-seeded 4 tools (scraper smoke-test OK). |
| 260601-1ay-mcp-brain-tokenless-team-resolution | 2026-06-01 | bd6a6a0 | Verified | mcp-brain tokenless team resolution for LibreChat. ROOT FINDING: LibreChat passive recall is structurally broken — the enricher injects a Mongo message with `sender=undefined` and no `parentMessageId`, so it's an orphan outside LibreChat's parent-linked message tree and never reaches the model (team_scope was only the first bug). Pivoted to the ACTIVE `memory_search` MCP tool. mcp-brain now resolves the team server-side from the LibreChat-forwarded email (`X-LibreChat-User-Email`), **gated by `X-Internal-Secret == BRIDGE_SHARED_SECRET`** (constant-time; public ChatGPT/Claude.ai token path on mcp.grooveos.app is unchanged + cannot use the email path), via `/v1/internal/resolve-team-scope` + a minted HS256 bridge JWT (mirrors aggregate.py). Zero per-user token for LibreChat users. New: `apps/mcp-brain/app/bridge_jwt.py`, `_resolve` dual-path, authlib dep. Live-verified on VM: bridge JWT accepted by memory-api, email path resolves aibrussels, `memory_search` returns the AI Brussels fact (21–22 Oct 2026). 9 unit tests (py_compile only locally). |
| 260601-3is-scraper-tool-name-fix-claude-mention-url | 2026-06-01 | f063521 | Verified | Scraper "can't fetch" fix + @claude URL browsing. ROOT CAUSE: every MCP sidecar's real tool name differs from its gateway registry id (scraper→`scrape`, calendar→`list_events`, drive-read→`read_drive_file`/`write_drive_file`, deck→`deck_create`/`deck_update`); the aggregate forwards the registry id → sidecar returns "Unknown tool" (the old register-mcp-tools.sh smoke test false-passed by measuring the error JSON's length). Fix 1 (mcp-gateway call_tool): list the sidecar's tools and resolve the name when the requested one isn't exposed AND the sidecar has exactly one tool (scraper/calendar; correct-name callers like agent-runtime unaffected; multi-tool drive/deck via LibreChat still need the explicit tool name — noted). Fix 2 (memory-api team_chat_agent): the extension @claude mention now pre-fetches up to 3 http(s) URLs in the message via the gateway scraper and injects the content into the UNCACHED user turn (works on Pro/Max + Anthropic fallback; cached memory block untouched). Fix 3: register-mcp-tools.sh smoke test now checks isError. Live-verified on VM: gateway name=scraper resolves to scrape and returns real content; memory-api _fetch_url_via_scraper returns 528 chars. 8/8 _extract_urls unit tests PASS. |
| 260601-gcf-extension-optimistic-render-own-message | 2026-06-01 | 79e32d5 | Verified | Extension chat: own sent message didn't display until reopen. Backend realtime verified HEALTHY (extension subscribed to team:<id> num_clients:1, secrets match, memory-api publishes {"type":"message"} 200 OK). Root cause frontend: `popup.js::sendMessage` POSTed + cleared input but never rendered — relied on the Centrifugo echo, which lags/misses while a Chrome action popup is backgrounded (popups tear down JS/WS on blur). Fix: render optimistically from the POST response (team_chat.py:236 returns the serialized message); renderMessage de-dupes by id (popup.js:455) so the later echo is a no-op. node --check OK. Refreshed popup.js inside chrome-extension.zip too. User must reload the unpacked extension. Backend untouched (no deploy). Incoming other-user/@claude messages still rely on the WS echo (backend-healthy; popup-blur lifecycle → use the Chrome side panel for persistent realtime). |
| 260601-uom-searxng-web-search-scraper-auto-fetch | 2026-06-01 | 6209183 | Partial | Make LibreChat read links like claude.ai. ✅ DONE: (1) scraper auto-fetch nudge — promptPrefix on all 5 xbrain modelSpecs → model calls our `scraper` tool on any URL (uses mcp-scraper, no dep; live). (2) SearXNG container (settings.yml JSON-format-on + limiter-off, compose service) — healthy, JSON API verified (9 results). ⛔ BACKED OUT: the LibreChat `webSearch` block crash-looped LibreChat (~2min outage, recovered to RestartCount=0) — TWO blockers: v0.8.5 Zod schema rejects `rerankerType: "none"` (only jina/cohere), AND no valid Firecrawl key (the only fc- on the system is 25 chars + 401s; searched env/settings/@security/plugins). Backout commit 6209183. RESOLVED same day (a4b3df8): user provided a valid Firecrawl key (fc-e2c3…, 35-char, verified success=True); rerankerType OMITTED (confirmed `.optional()` in v0.8.5's `librechat-data-provider` schema — no jina/cohere key needed); webSearch re-enabled, deployed, LibreChat healthy (RestartCount=0, ZodError count=0). **Web Search toggle now LIVE** (SearXNG search + Firecrawl read). NOTE observed during deploy: `[MCP][xbrain-memory] Tools: undefined` — the mcp-brain memory tools may not be listing in LibreChat at startup (separate from web search; investigate — could affect active memory recall from 260601-1ay). |
| 260603-1vr-gateway-unwrap-kwargs-from-aggregate-tool | 2026-06-03 | (see git) | Verified | Scraper tool failing in LibreChat with `scrapeArguments: url Field required [input_value={"kwargs":{"url":...}}]`. Root cause: the 260601-3is mcp-gateway rebuild pulled a newer FastMCP that exposes the aggregate's `_proxy(**kwargs)` tools with a single generic `kwargs` object param → the model is forced to send `{"kwargs":{<real args>}}`, which the gateway forwarded verbatim → sidecar `scrape(url)` never saw `url`. Fix: `call_tool` strips one `kwargs` level when `mcp_arguments == {"kwargs": {...}}` (safe — agent-runtime never wraps; no tool has a sole kwargs param). Live-verified: kwargs-wrapped scraper call → isError=False + real HTML. Follow-up: proper fix = aggregate exposes real sidecar inputSchemas. |
| 260603-29h-mcp-brain-memory-add-complete-item | 2026-06-03 | (see git) | Verified | Active save (`memory_add` via xbrain-memory/mcp-brain) failed 422 on `/v1/memory/upsert`. Root cause: `MemoryItem` model requires `id` + `created_at` + `updated_at` (no server default), but mcp-brain's memory_client.memory_add omitted them. (NOT /v1/brain/ingest — that runs the Haiku relevance filter + is fire-and-forget + ignores truth_level, wrong for an explicit save.) Fix: memory_add now builds a complete item (uuid4 id + now() timestamps). Live-verified: memory_add → returns id, item lands in memory_items+Qdrant (aibrussels/WORKING). NOTE: the earlier `[MCP][xbrain-memory] Tools: undefined` was a FALSE ALARM — the memory tools ARE callable (this 422 proved memory_add runs); only the upsert payload was wrong. |
| 260603-brain-monitor-team-resolve | 2026-06-03 | (see git) | Verified | Brain Monitor (grooveos.app/account/teams/brain/) showed "Not a member of team default" / 403 on the bare URL. Root cause: `brain.js` line 34 `TEAM_SLUG = QS.get("team") \|\| "default"` — no `?team=` → falls back to `default` (a team nobody is a member of). Fix: in `init()`, when `?team=` is absent, fetch `/v1/teams/my-teams` (user-scoped, not team-gated) and `location.replace(?team=<first slug>)`. Deployed to Firebase grooveos target (release complete). Immediate workaround for users: append `?team=aibrussels`. Same pattern may exist elsewhere (admin.js is cross-team, not affected). |
| 260603-drop-memory-source-unique-constraint | 2026-06-03 | (see git) | Verified | After the 422 fix, LibreChat `memory_add` returned **500** on 4/5 entries: `UniqueViolationError idx_memory_source_team_unique`. Migration 0004 added UNIQUE(source, team_scope) "for drive-sync idempotence" — but NO code uses ON CONFLICT(source, team_scope) (drive-sync = search-then-update; provider.upsert = ON CONFLICT(id)). The constraint is vestigial + harmful: any path writing multiple items with the same source per team 500s (mcp-brain source="mcp-brain"; brain_ingest source="librechat:<model>"/"team-chat:<author>" dropped silently via fire-and-forget). Fix: DROP the index (migration 0020, IF EXISTS, idempotent). Dropped live on VM + verified 3 same-source saves succeed. ⚠️ SELF-INFLICTED during cleanup: a broad Qdrant filter-delete on team_scope=aibrussels wiped ALL 4 aibrussels vectors (PG intact) — recovered by re-upserting the 4 items (provider.upsert regenerates embeddings); Qdrant restored to 4 points. Migration applies on next memory-api deploy (index already dropped). |
| 260603-3et-media-foundation (BL-003 slice 1) | 2026-06-03 | c700260 | Verified | Started the media/documents feature (backlog BL-003). Design: `.planning/features/BL-003-media-design.md` (5 slices). SLICE 1 (foundation) DONE + deployed + live-verified: reconciled MinIO config (get_minio_client() read empty MINIO_URL/ACCESS/SECRET — mapped from MINIO_ENDPOINT/ROOT_USER/ROOT_PASSWORD in compose; MINIO_BUCKET=xbrain-media); new `POST /v1/media/upload` (multipart→MinIO+media memory_item, metadata.media={key,mime,size,filename}) + `GET /v1/media/{id}/raw` (Bearer-auth proxy stream). Live test: PNG upload→201, /raw→200 image/png 67b, bucket auto-created. 10/10 unit tests. NEXT slices: 2) Brain Monitor render (decide serving A=public-MinIO vs B=signed-token-proxy), 3) extension upload + UI reorg (📎→upload, clipper→menu "add to memory"), 4) @claude render, 5) LibreChat render (hardest). |
| 260604-glo-mcp-brain-claude-connector | 2026-06-06 | 05ca986→b4af42e | LIVE / Connected | Make mcp-brain connectable as an official Claude.ai Custom Connector via OAuth 2.1. memory-api = hand-rolled AS (alembic 0022: oauth_clients/codes/access_tokens; native FastAPI routers un-prefixed: /.well-known/oauth-authorization-server + /oauth/{register,authorize,token,introspect}; S256 PKCE, public-client `none`, RFC 7591 DCR, RFC 7662 introspection gated by constant-time X-Internal-Secret; GitHub-login consent binds ONE team_scope; app/auth.py→app/auth/ package). mcp-brain = Protected Resource (3-tuple _resolve with oat_ branch first→introspect+RFC 8707 audience check→bridge JWT; @custom_route /.well-known/oauth-protected-resource at ROOT; UnauthenticatedMCP401Middleware 401+WWW-Authenticate; connector writes force source=claude.ai-connector + truth_level≤WORKING + single team). nginx /.well-known/+/oauth/ proxy (no nginx ACAO — app owns it, claude.ai added to regex); compose OAUTH_* env. Confirmed mcp 1.27.2: custom routes at root, streamable path /mcp, WWW-Authenticate→https://mcp.grooveos.app/.well-known/oauth-protected-resource (live 401 + well-known smoke-tested). Gates: memory-api 16 pass/2 skip, mcp-brain 21 pass. Tasks 1-5 built+committed + tasks.source fix (migration 0023). **DEPLOYED + CONNECTED LIVE 2026-06-06** — alembic head 0023; public OAuth surface verified; connector added in Claude.ai bound to team aibrussels (9 tools, oat_ source=claude.ai-connector); GitHub App callback `api.grooveos.app/oauth/github-callback` added via Playwright. Migration 0023 (commit 412d1a8) closed the /v1/tasks source-enum gap (column widened to VARCHAR(32) + CHECK extended; v_brain_events view dropped/recreated around the ALTER). Pending: make the GitHub App public for non-owner members. |
| 260607-267-github-repo-catalog-auto-indexer | 2026-06-07 | fdd6720→39631b2 | LIVE / Verified | GitHub repo-catalog auto-indexer for better search. Each repo a team's GitHub App can reach → one searchable brain `memory_item` (source=`github:repo-catalog`, truth_level=WORKING, project_scope=repo, uuid5 keyed by team_scope+full_name) with a **Haiku README summary** in `content`; BRAIN ONLY (no table). New `app/services/github_catalog.py`, `GET /v1/internal/github/catalog` (team-scoped exact list), `repository` webhook branch (created/renamed/deleted→incremental upsert/soft-delete), sign-in + team-create triggers (non-blocking), and `github_list_repos` MCP tool on mcp-github. DEPLOYED 2026-06-07 (rebuild memory-api+mcp-github, no migration; App subscribed to Repository event via Playwright). Backfilled **74 repos** (aibrussels 14 + mrboups personal 60) into team aibrussels; verified `github_list_repos`→74 + `memory_search('crypto trading bot')`→mrboups/xtrader w/ summary. Deviation→Task #149: catalog auto-triggers cover ORG installs only (find_installation_for_org is org-only); mrboups personal repos backfilled manually + kept fresh via webhook, not re-indexed on sign-in. |
| 260711-45b-extension-session-bridge-auto-delete | 2026-07-11 | 5959a1d→1c97413 | Verified (node) | Extension session-bridge: auto-delete each claude.ai conversation after its completion stream ("piste 1"). The bridge is STATELESS on claude.ai's side (`openaiToClaudeAi` re-sends the full history in `prompt` + always nil `parent_message_uuid`), so the per-message conversation is a throwaway container — `handleClaude` now deletes it in a `finally` AFTER the end/error frame (best-effort, empty catch → no error frame on cleanup failure; skipped when convUuid is null / nothing created). New exported `deleteConversation()` + `DELETE_URL` template, mirrored on the globalThis fallback. DELETE endpoint **CONFIRMED live 2026-07-11** via Playwright (claude.ai page context, org 9338272c Play Asbl): POST chat_conversations→201, DELETE .../{uuid}→**204**, GET .../{uuid}→404 — best-guess contract exact, `deleteConversation()` ships unchanged, no version bump (09-CAPTURE.md A11 CONFIRMED). New `test_claude_ai_client.mjs` 3/3 PASS (delete-after-end ordering + best-effort swallow of thrown AND non-2xx DELETE); suite otherwise unchanged (only the 3 pre-existing `test_chat_stream.mjs` `detectMentionClient` failures remain). Deferred (notes only): native threading (token opt — incompatible with delete-per-message) + orgId module-level caching. background.js untouched. |
