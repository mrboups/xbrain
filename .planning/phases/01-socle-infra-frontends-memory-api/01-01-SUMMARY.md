---
phase: 01-socle-infra-frontends-memory-api
plan: 01
subsystem: infra
tags: [monorepo, makefile, docker-compose, gitignore, scaffold]

requires:
  - phase: roadmap
    provides: ROADMAP.md + REQUIREMENTS.md + RESEARCH.md
provides:
  - Structure monorepo (apps/, services/, packages/, infrastructure/)
  - Makefile avec targets help/build/up/down/logs/test/lint/fmt/sync/deploy/vm-logs/vm-ps/vm-down/ssh/backup/restore-test/env-check
  - .env.example exhaustif (17 placeholders __FILL pour secrets/credentials)
  - docker-compose.yml squelette (nginx placeholder + 8 named volumes + xbrain_net bridge network)
  - nginx/conf.d/00-health.conf placeholder (répond /nginx-health, retourne 503 sur les autres paths jusqu'à ce que 01-05 configure le routing)
  - README.md quickstart
affects: [01-02-PLAN, 01-03-PLAN, 01-04-PLAN, 01-05-PLAN, 01-06-PLAN]

tech-stack:
  added: [GNU Make, Docker Compose v2, nginx]
  patterns: [monorepo layout, env template avec placeholders, atomic Makefile targets]

key-files:
  created:
    - .gitignore (étendu avec secrets/, *.log, backups/, *.tar.gz, .pytest_cache, .ruff_cache, docker-compose.override.yml)
    - Makefile (16 targets nommés)
    - .env.example (17 placeholders, 0 secret réel)
    - infrastructure/docker-compose.yml (squelette)
    - infrastructure/nginx/conf.d/00-health.conf
    - README.md (quickstart Phase 1, Configuration Google OAuth, Structure du repo)
    - packages/schemas/README.md (note placeholder Phase 2)
    - apps/.gitkeep, services/.gitkeep, packages/.gitkeep, infrastructure/.gitkeep, infrastructure/nginx/conf.d/.gitkeep
  modified:
    - .gitignore (déjà présent, étendu avec ~10 entrées Phase 1)

key-decisions:
  - "Garder le .gitignore existant et l'étendre, plutôt que de le remplacer (préserve la config GSD : .planning/sessions/, .planning/spikes/, .planning/sketches/)"
  - "Volumes Docker nommés (pas de bind mounts) pour Postgres/Qdrant/MongoDB/MeiliSearch — simplifie backup/restore et migration de VM"
  - "Single bridge network xbrain_net (pas de segmentation frontends-net/data-net en Phase 1) — simplicité d'abord, segmentation à reconsidérer en Phase 2"
  - "Makefile cible la VM via SSH par défaut (VM_HOST/VM_USER/SSH_KEY) — pas de différence entre 'local' et 'remote' du point de vue user"
  - "nginx/conf.d/00-health.conf retourne 503 catch-all jusqu'au plan 01-05 — évite les 404 nginx confusants pendant le bootstrap"

patterns-established:
  - "Wave-based plan numbering (01-NN-PLAN.md / 01-NN-SUMMARY.md) — wave numéro dans frontmatter pour /gsd:execute-phase"
  - "Convention placeholder __FILL__ / __FILL_RANDOM_*__ dans .env.example — env-check make target les détecte"
  - "Volume mounts read-only par défaut (`:ro` dans docker-compose) sauf data dirs"

requirements-completed: []

duration: ~12 min (inline orchestrator, pas de subagent spawned)
completed: 2026-05-03
---

# Plan 01-01 — Scaffold monorepo

**Le squelette repo est prêt à recevoir les 5 plans suivants. Tout le tooling (Makefile, .env.example, docker-compose skeleton) est en place pour que les plans 01-02 → 01-06 puissent être exécutés.**

## Performance

- **Duration:** ~12 min
- **Started:** 2026-05-03 (inline)
- **Completed:** 2026-05-03
- **Tasks:** 5/5 completed
- **Files created:** 11 (incl. 5 .gitkeep placeholders)
- **Files modified:** 1 (.gitignore étendu)

## Accomplishments

- Structure monorepo cohérente : `/apps`, `/services`, `/packages`, `/infrastructure`
- Makefile = entrée unique pour dev local + deploy VM + backup
- `.env.example` exhaustif (17 secrets/credentials placeholders) — empêche oubli de var
- `docker-compose.yml` squelette validé (YAML parseable, 8 volumes nommés, xbrain_net bridge)
- nginx healthcheck endpoint déjà en place (les containers nginx passeront healthy avant que 01-05 configure le routing complet)
- README quickstart actionnable (génération secrets, config OAuth Google, deploy)

## Task Commits

Cette exécution étant inline (pas de subagent), un seul commit groupé en fin de plan plutôt que des commits per-task.

## Files Created/Modified

- `.gitignore` — étendu avec `secrets/`, `*.log`, `backups/`, `*.tar.gz`, `.pytest_cache/`, `.ruff_cache/`, `docker-compose.override.yml`, `*.swp/swo`
- `Makefile` — 16 PHONY targets (help, build, up, down, logs, ps, test, lint, fmt, sync, deploy, vm-logs, vm-ps, vm-down, ssh, backup, restore-test, env-check)
- `.env.example` — template complet Phase 1
- `README.md` — quickstart + structure repo
- `packages/schemas/README.md` — note "Phase 1 = Pydantic in memory-api canonical"
- `infrastructure/docker-compose.yml` — squelette avec nginx placeholder + 8 volumes nommés
- `infrastructure/nginx/conf.d/00-health.conf` — `/nginx-health` + 503 catch-all
- `apps/.gitkeep`, `services/.gitkeep`, `packages/.gitkeep`, `infrastructure/.gitkeep`, `infrastructure/nginx/conf.d/.gitkeep`
- Dirs créés : `apps/`, `services/`, `packages/schemas/`, `infrastructure/{nginx/conf.d, scripts, librechat, backup}/`

## Verification

- ✅ `docker-compose.yml` parse via PyYAML (8 volumes, 1 service, networks OK)
- ✅ `.env.example` contient 17 placeholders `__FILL*` (≥ 10 requis)
- ✅ Aucun fichier `.env` réel committé (`ls .env` retourne "No such file")
- ✅ `infrastructure/nginx/conf.d/00-health.conf` contient `/nginx-health`
- ⏭ `make help` non testable localement (make pas installé sur Windows). Sera testé sur la VM en wave 3.
- ⏭ `docker compose config` non testable localement (Docker Desktop pas installé). Sera testé sur la VM en wave 3.

## Notes

- Make et Docker ne sont pas installés sur la machine de dev (Windows). Toutes les commandes opérationnelles (`make deploy`, `docker compose up`) tournent sur la VM via SSH. Pour utiliser `make` localement, installer Git for Windows (qui fournit `make`) ou WSL.
- Le `.env.example` avait 5 valeurs DOMAIN_URL hardcodées en `http://__VM_HOST__` — c'est l'IP éphémère actuelle de la VM, à promouvoir en static IP quand on attache un domaine (Phase 1.5 ou Phase 2).
- nginx 503 catch-all sera supprimé/remplacé par le routing complet (LibreChat / Open WebUI / memory-api) dans le plan 01-05.
