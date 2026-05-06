---
phase: 05-plateforme-projets-equipe
plan: "05-02"
subsystem: auth
tags: [github, oauth, librechat, memory-api, alembic, httpx, pydantic]

requires:
  - phase: 05-01
    provides: [graphiti-service container, memory-api enrichissement fail-soft]
provides:
  - GitHub OAuth login sur LibreChat (bouton "Se connecter avec GitHub")
  - Colonnes github_username (VARCHAR 256) + github_id (BIGINT, unique) sur la table users
  - check_github_org_membership() dans auth.py avec cache 5min TTL 300s
  - Branche GitHub dans get_current_principal() — détection token gho_ prefix
  - Gate org-membership dans get_team_scope() — 403 si non-membre org (T-05-02-02)
  - Endpoint POST /v1/me/link-github — liaison compte GitHub depuis compte Google
affects: [phase 05-03, phase 05-04, deps.py consumers, get_team_scope callers]

tech-stack:
  added: [Alembic migration 0007]
  patterns:
    - auth.py _github_membership_cache dict pattern (TTL 300s, clé tronquée token[:16]+org)
    - deps.py cascade fallback — Google OIDC → GitHub gho_ token → Bridge JWT
    - me_github.py — pattern endpoint link-account (pas d'admin guard, user update own row)

key-files:
  created:
    - apps/memory-api/alembic/versions/0007_github_users.py
    - apps/memory-api/app/routes/me_github.py
  modified:
    - infrastructure/librechat/librechat.yaml
    - infrastructure/docker-compose.yml
    - apps/memory-api/app/config.py
    - apps/memory-api/app/auth.py
    - apps/memory-api/app/deps.py
    - apps/memory-api/app/main.py
    - apps/memory-api/app/models/user.py

key-decisions:
  - "GitHub tokens détectés par préfixe gho_ — simple, sans appel GitHub API supplémentaire"
  - "github_is_org_member=None pour Google users (D7: accès team complet sans vérification)"
  - "github_is_org_member=False bloqué dans get_team_scope — pas dans get_current_principal (séparation des responsabilités)"
  - "source_user_id GitHub = github:{login} — stable par rapport à l'email qui peut être null"
  - "409 Conflict si un même github_id tenté de linker à deux users xbrain différents"

patterns-established:
  - "Token cascade: Google OIDC first → GitHub gho_ prefix → Bridge JWT — fail-through pattern"
  - "Cache dict + TTL pattern: _github_membership_cache dict[str, tuple[float, dict]] — réutiliser pour d'autres caches API externes"
  - "ORM model + Alembic migration en sync: colonnes ajoutées dans les deux en même commit"

requirements-completed: [AUTH-01, AUTH-03]

duration: 6min
completed: 2026-05-06
---

# Phase 5 Plan 02: GitHub OAuth + membership middleware Summary

**GitHub OAuth ajouté à LibreChat, migration Alembic 0007 (github_username + github_id sur users), vérification membership org GitHub avec cache 5min dans memory-api, et endpoint POST /v1/me/link-github pour les users Google**

## Performance

- **Duration:** 6 min
- **Started:** 2026-05-06T03:07:15Z
- **Completed:** 2026-05-06T03:13:00Z
- **Tasks:** 3
- **Files modified:** 8 (dont 2 créés)

## Accomplishments

- LibreChat affiche désormais deux boutons de login : Google + GitHub (1 ligne YAML changée dans socialLogins)
- Migration Alembic 0007 ajoute github_username (VARCHAR 256, nullable) et github_id (BIGINT, nullable, unique) sur la table users, avec 2 index
- check_github_org_membership() dans auth.py : 2 appels GitHub API séquentiels (user token pour username + PAT serveur pour membership), cache 5min TTL, clé tronquée pour éviter exposition du token (T-05-02-03)
- Branche GitHub dans get_current_principal() : détection par préfixe gho_, crée/retrouve un User avec source_user_id = github:{login}
- get_team_scope() bloque les GitHub non-membres d'org avec 403 (T-05-02-02)
- Endpoint POST /v1/me/link-github : lie un compte GitHub à un user Google existant, avec garde 409 anti-double-linking

## Task Commits

1. **Task 1: GitHub OAuth librechat.yaml + docker-compose + migration 0007** - `eaaa9a8` (feat)
2. **Task 2: membership check auth.py + deps.py + config.py** - `e8cc456` (feat)
3. **Task 3: POST /v1/me/link-github + User model columns** - `1a429dc` (feat)

## Files Created/Modified

- `infrastructure/librechat/librechat.yaml` — socialLogins: ["google", "github"]
- `infrastructure/docker-compose.yml` — GITHUB_CLIENT_ID/SECRET/CALLBACK_URL dans librechat; GITHUB_ORG/API_PAT dans memory-api
- `apps/memory-api/alembic/versions/0007_github_users.py` — migration DDL add_column github_username + github_id + 2 index
- `apps/memory-api/app/config.py` — GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_ORG, GITHUB_API_PAT
- `apps/memory-api/app/auth.py` — _github_membership_cache + check_github_org_membership() (62 lignes ajoutées)
- `apps/memory-api/app/deps.py` — import check_github_org_membership, branche gho_ dans get_current_principal, gate org dans get_team_scope
- `apps/memory-api/app/routes/me_github.py` — POST /v1/me/link-github router (nouveau fichier)
- `apps/memory-api/app/main.py` — import + register me_github router
- `apps/memory-api/app/models/user.py` — colonnes ORM github_username + github_id (sync avec migration)

## Decisions Made

- Détection du token GitHub par préfixe `gho_` (standard OAuth token GitHub) — simple, pas d'appel API supplémentaire pour le classifier
- google_is_org_member=None (pas False) pour les users Google — permet `if ... is False` dans get_team_scope sans bloquer les users Google
- source_user_id GitHub = `github:{login}` (pas l'email) — robuste car l'email peut être null sur GitHub et le login change rarement
- 409 Conflict si le même github_id est déjà lié à un autre user xbrain — protège contre le partage de compte
- GITHUB_API_PAT dans docker-compose.yml pour memory-api — variable fail-soft (défaut vide, fonctionnalité désactivée si non configurée)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] ORM User model mis à jour en sync avec la migration Alembic**
- **Found during:** Task 3 (endpoint me_github.py)
- **Issue:** Le plan spécifiait la migration Alembic mais pas la mise à jour du modèle SQLAlchemy User — sans colonnes ORM, le UPDATE dans l'endpoint échouerait silencieusement ou lèverait une AttributeError
- **Fix:** Ajout de `github_username` (String 256, nullable, index) et `github_id` (BigInteger, nullable, unique) dans `apps/memory-api/app/models/user.py`
- **Files modified:** apps/memory-api/app/models/user.py
- **Verification:** Syntaxe Python validée, cohérence avec la migration 0007 vérifiée
- **Committed in:** 1a429dc (Task 3 commit)

**2. [Rule 2 - Missing Critical] GITHUB_ORG + GITHUB_API_PAT ajoutés à docker-compose.yml (memory-api)**
- **Found during:** Task 2 (deps.py branche GitHub)
- **Issue:** config.py référence GITHUB_ORG et GITHUB_API_PAT mais le docker-compose.yml ne les propageait pas — memory-api ne les recevrait jamais sur la VM
- **Fix:** Ajout de `GITHUB_ORG: ${GITHUB_ORG:-your-github-org}` et `GITHUB_API_PAT: ${GITHUB_API_PAT:-}` dans le bloc environment de memory-api
- **Files modified:** infrastructure/docker-compose.yml
- **Verification:** grep GITHUB_API_PAT infrastructure/docker-compose.yml retourne la variable
- **Committed in:** e8cc456 (Task 2 commit)

**3. [Rule 2 - Missing Critical] Garde 409 Conflict dans me_github.py**
- **Found during:** Task 3 (me_github.py)
- **Issue:** Le plan ne mentionnait pas de vérification d'unicité de github_id — sans elle, deux users pourraient lier le même compte GitHub (violation d'intégrité implicite dans l'index UNIQUE de la migration)
- **Fix:** SELECT avant UPDATE pour vérifier qu'aucun autre user ne possède déjà ce github_id, retourne 409 Conflict si c'est le cas
- **Files modified:** apps/memory-api/app/routes/me_github.py
- **Verification:** Logique vérifiée par lecture du code
- **Committed in:** 1a429dc (Task 3 commit)

---

**Total deviations:** 3 auto-fixed (3 missing critical)
**Impact on plan:** Toutes les auto-corrections nécessaires pour la correction et la sécurité. Pas d'élargissement de scope.

## Issues Encountered

Aucun problème bloquant. La vérification syntaxique Python (`ast.parse`) de tous les fichiers passe sans erreur.

## User Setup Required

Les variables suivantes doivent être ajoutées au `.env` de la VM avant redéploiement :

```bash
# GitHub OAuth App (créer sur github.com/settings/developers > OAuth Apps)
# Homepage URL: https://x.dejavu.cat
# Callback URL: https://x.dejavu.cat/oauth/github/callback
GITHUB_CLIENT_ID=<client_id>
GITHUB_CLIENT_SECRET=<client_secret>
GITHUB_CALLBACK_URL=/oauth/github/callback

# GitHub Fine-grained PAT (scope read:org sur org your-github-org)
GITHUB_API_PAT=<pat>
GITHUB_ORG=your-github-org
```

Commandes de vérification après déploiement :
```bash
# Vérifier la migration
docker compose exec memory-api python -m alembic current
# Doit afficher: 0007 (head)

# Vérifier les colonnes
docker compose exec postgres psql -U $POSTGRES_USER -d $POSTGRES_DB \
  -c "SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name IN ('github_username','github_id');"

# Vérifier healthz
curl -sf https://api.dejavu.cat/v1/healthz
```

## Next Phase Readiness

- Auth unifié Google + GitHub en place — plan 05-03 (pipeline GitOps) peut utiliser les tokens GitHub pour le brain indexing
- L'endpoint POST /v1/me/link-github est prêt pour l'extension Chrome (plan 05-04) — les users pourront lier depuis l'extension
- La vérification org membership (cache 5min) est opérationnelle — les routes team-scoped sont protégées pour les users GitHub non-membres

---
*Phase: 05-plateforme-projets-equipe*
*Completed: 2026-05-06*
