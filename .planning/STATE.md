---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: complete
stopped_at: Phase 7 complete — all 9 plans executed (07-01..07-09). Milestone v1.0 DONE.
last_updated: "2026-05-07T04:00:00.000Z"
last_activity: 2026-05-07 -- Phase 7 execution complete (all 9 plans, all 7 phases)
progress:
  total_phases: 7
  completed_phases: 7
  total_plans: 59
  completed_plans: 59
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-05-02)

**Core value:** Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.
**Current focus:** Milestone v1.0 COMPLETE — Phases 1-7 done

## Current Position

Phase: 7 — COMPLETE
Plan: 9/9
Status: All 7 phases complete. Milestone v1.0 shipped.
Last activity: 2026-05-07 -- Phase 7 execution complete — CRM + Granola + Task Intelligence

Progress: [██████████] 100% — ALL PHASES COMPLETE

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

### Pending Todos

None yet.

### Blockers/Concerns

- **Phase 2 entry gate** : POC 1-jour mem0 vs native doit être réalisé AVANT le planning Phase 2 — résultat détermine le chemin `MemoryProvider` implémentation
- **Phase 3 entry gate** : POC Memori BYODB (Alpha) doit être réalisé avant planning Phase 3 — fallback = LangGraph + LLM structured output
- **OOM Risk Phase 1** : e2-medium (4 GB) est serré avec LibreChat + MongoDB + Qdrant + memory-api. Surveiller `docker stats` total. Ne pas ajouter Langfuse complet (ClickHouse) sans upgrade VM.
- **VM disk 99%** : La VM est à 99% (28G/29G). Le rebuild de drive-sync a échoué par manque d'espace. Nettoyer avant prochain plan avec rebuild de containers. `docker system prune` n'a pas libéré d'espace — probablement des snapshots containerd à nettoyer manuellement ou agrandir le disque.

## Deferred Items

| Category | Item | Status | Deferred At |
|----------|------|--------|-------------|
| *(none)* | | | |

## Session Continuity

Last session: 2026-05-07
Stopped at: Plan 07-09 complete — task_intent_detector.py (3628d38), mongo_watcher hook + config + deps (b866c99), docker-compose librechat-bridge env vars (01c26f9). Phase 7 Wave 7 COMPLETE.
Resume file: None
