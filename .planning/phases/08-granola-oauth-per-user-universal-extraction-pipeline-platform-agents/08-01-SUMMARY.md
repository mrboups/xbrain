---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: "01"
subsystem: database
tags: [alembic, postgresql, migration, granola, agent-definitions, fernet, uuid]

requires:
  - phase: 07-crm-granola-tasks
    provides: "Migration chain 0009-0011, granola_integrations pattern, tasks.created_by FK SET NULL pattern"
  - phase: 05-plateforme-projets-equipe
    provides: "users table (FK target), Fernet encryption pattern"
provides:
  - "Table granola_user_connections (per-user Granola API key, Fernet encrypted, FK users.id CASCADE, UNIQUE user_id)"
  - "Table agent_definitions (platform agent registry, UNIQUE name, tools_json JSONB nullable, created_by FK SET NULL)"
  - "Seed row meeting-recap in agent_definitions (auto_trigger=true, system_prompt FR, idempotent ON CONFLICT)"
  - "Alembic migration 0012 — prochaine étape migration chain après 0011"
affects:
  - 08-02-granola-user-connections-api
  - 08-03-granola-sync-per-user
  - 08-04-agent-definitions-crud
  - 08-05-agent-invoke
  - 08-06-extraction-pipeline
  - 08-07-meeting-recap-auto-trigger
  - 08-08-verify

tech-stack:
  added: []
  patterns:
    - "Migration seed via op.execute(sa.text(...).bindparams(...)) + ON CONFLICT (name) DO NOTHING — idempotent seeding"
    - "UNIQUE user-scoped FK: UniqueConstraint('user_id') pour au plus une clé Granola par user"
    - "tools_json JSONB nullable reserved field — stocké sans être exécuté (réservé Phase 9+)"

key-files:
  created:
    - apps/memory-api/alembic/versions/0012_granola_user_agents.py
    - .planning/phases/08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents/.MIGRATE_0012_REQUIRED
  modified: []

key-decisions:
  - "Migration 0012 créée avec down_revision=0011 — chaîne strictement consécutive"
  - "FK granola_user_connections.user_id ON DELETE CASCADE — suppression user = suppression clé Granola (RGPD-friendly)"
  - "FK agent_definitions.created_by ON DELETE SET NULL — cohérent avec tasks.created_by (migration 0010)"
  - "UNIQUE(user_id) sur granola_user_connections — un user a au plus une clé Granola active"
  - "Seed via bindparams + ON CONFLICT (name) DO NOTHING — idempotent, safe pour re-run migration"
  - "system_prompt meeting-recap en français — langue produit de l'équipe"
  - "Marker file .MIGRATE_0012_REQUIRED créé — agent Windows sans SSH, migration VM requise manuellement"

patterns-established:
  - "Seed migration: op.execute(sa.text(SQL).bindparams(...)) pour typage sûr + idempotence ON CONFLICT"
  - "tools_json JSONB nullable: champ réservé phases futures, présent dès la migration de base"

requirements-completed: []

duration: 15min
completed: 2026-05-09
---

# Phase 08 Plan 01: Alembic migration 0012 — granola_user_connections + agent_definitions + seed meeting-recap

**Migration Alembic 0012 créant deux tables fondatrices Phase 8 : granola_user_connections (clé Granola per-user chiffrée Fernet, FK CASCADE, UNIQUE user) + agent_definitions (registry agents plateforme, UNIQUE name, tools_json JSONB) + seed idempotent de l'agent meeting-recap**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-05-09T15:00:00Z
- **Completed:** 2026-05-09T15:12:45Z
- **Tasks:** 2/2
- **Files modified:** 2 créés

## Accomplishments

- Migration 0012 fichier créé, valide syntaxiquement (ast.parse OK), conforme aux schémas D1+D4+D5+D6
- Table `granola_user_connections` : per-user Granola API key, FK users.id CASCADE, UNIQUE user_id, colonne `enabled`, timestamps
- Table `agent_definitions` : registry platform agents, UNIQUE name, tools_json JSONB nullable (réservé Phase 9+), auto_trigger, FK created_by SET NULL
- Seed `meeting-recap` avec system_prompt structuré FR, auto_trigger=true, ON CONFLICT (name) DO NOTHING (idempotent)
- Fichier marker `.MIGRATE_0012_REQUIRED` créé avec commandes SSH exactes pour application sur VM

## Task Commits

1. **Task 1: Migration Alembic 0012** - `819b242` (feat)
2. **Task 2: Marker VM migration** - `4f509da` (chore)

**Plan metadata:** (ci-dessous — commit final docs)

## Files Created/Modified

- `apps/memory-api/alembic/versions/0012_granola_user_agents.py` — Migration Alembic 0012 : granola_user_connections + agent_definitions + seed meeting-recap
- `.planning/phases/08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents/.MIGRATE_0012_REQUIRED` — Marker + commandes SSH exactes pour appliquer la migration sur la VM __VM_HOST__

## Decisions Made

- **FK CASCADE vs SET NULL** : `granola_user_connections.user_id` CASCADE (suppression user = suppression clé, RGPD) ; `agent_definitions.created_by` SET NULL (agent survive au user, cohérent avec tasks 0010)
- **UNIQUE user_id** sur `granola_user_connections` : un user a au plus une clé Granola active (résout Open Question 1 RESEARCH.md)
- **Seed via bindparams + ON CONFLICT** : pattern typé + safe idempotent, pas d'interpolation string (évite injection SQL)
- **Marker file Task 2** : agent Windows sans accès SSH → marker créé avec commandes exactes, migration manuelle requise sur VM

## Deviations from Plan

None — plan exécuté exactement comme écrit. Task 2 a suivi le fallback documenté dans le plan (marker file au lieu d'exécution SSH directe).

## Issues Encountered

None — fichier migration Python parse sans erreur. Tous les critères d'acceptance vérifiés programmatiquement (10/10 PASS).

## User Setup Required

**Action manuelle requise avant de lancer les plans 08-02..08-08.**

Voir `.planning/phases/08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents/.MIGRATE_0012_REQUIRED` pour les commandes exactes.

Résumé des étapes :

```bash
ssh ubuntu@__VM_HOST__
cd ~/xbrain/infrastructure
git pull origin main
docker compose exec memory-api alembic upgrade head
# Vérifier : SELECT version_num FROM alembic_version → doit retourner 0012
```

## Next Phase Readiness

- Tables `granola_user_connections` et `agent_definitions` disponibles après application de la migration 0012 sur la VM
- Plan 08-02 (granola-user-connections API endpoints) peut commencer dès que `alembic_version=0012` confirmé
- Plans 08-03 à 08-08 tous bloqués sur cette migration — ne pas les lancer avant confirmation

**Blocker :** Migration 0012 non encore appliquée sur la VM (accès SSH non disponible depuis l'agent Windows). L'orchestrateur doit exécuter les commandes du marker `.MIGRATE_0012_REQUIRED` avant de lancer les plans suivants.

---

## Known Stubs

None — ce plan crée uniquement une migration DDL + un fichier marker. Aucun composant UI ou API avec données mock.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: schema_change | apps/memory-api/alembic/versions/0012_granola_user_agents.py | Nouvelle table granola_user_connections stocke api_key_enc (ciphertext Fernet) — conforme T-08-01-02 (aucune route ne SELECT api_key_enc en clair en Phase 8) |

---
*Phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents*
*Completed: 2026-05-09*
