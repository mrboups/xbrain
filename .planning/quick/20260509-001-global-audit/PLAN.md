---
slug: global-audit
created: 2026-05-09
status: in-progress
---

# Quick Task: Global Features Audit + Docs/Specs Update

## Goal

Audit complet de toutes les fonctionnalités actives sur la VM (26 containers), puis mise à jour des fichiers de planning (.planning/STATE.md, .planning/ROADMAP.md) pour refléter l'état réel : Phase 7 complète, Phase 8 onboarding partiellement implémenté, migration domaine example.com terminée.

## Infrastructure Audit Result (completed)

**ALL 26 CONTAINERS HEALTHY** — verified via `sudo docker ps` on VM __VM_HOST__.

Endpoints validés :
- `chat.example.com` → 200 ✓ (LibreChat)
- `adm.example.com` → 200 ✓ (Open WebUI)
- `api.example.com` → 200 ✓ (memory-api)
- `lang.example.com` → 200 ✓ (Langfuse)
- `example.com` → 200 ✓ (Firebase Hosting / app-site)
- `projects.example.com` → 200 ✓ (Firebase / dashboard, partial=False)
- `POST /v1/teams/self-solo` → 422 (missing body = expected) ✓
- `GET /api/xbrain/github-orgs` → 401 (no JWT = expected) ✓

No regressions found.

## Tasks

- [x] Infrastructure audit — 26 containers healthy
- [x] Endpoint audit — all URLs responding
- [ ] Update STATE.md: Phase 7 complete, Phase 8 onboarding milestone, domain example.com, VM disk resolved, last_activity 2026-05-09
- [ ] Update ROADMAP.md: Phase 7 ✅ complete 2026-05-07, Phase 8 progress (onboarding shipped), deployment URLs → example.com
- [ ] Commit all updates
