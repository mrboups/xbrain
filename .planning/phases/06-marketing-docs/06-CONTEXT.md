---
phase: 6
title: Marketing Site + Documentation
created: 2026-05-06
---

# Phase 6 Context: Marketing Site + Documentation

## Goal

Livrer un site marketing statique en anglais et une documentation complète pour xbrain, puis les déployer en ligne via Firebase Hosting. Le tout sans interaction — décisions prises ici font foi.

## Locked Decisions

### D1 — Tech stack: HTML/CSS/JS vanilla + TailwindCSS CDN
- Zéro build step, déploiement direct `firebase deploy`
- TailwindCSS via CDN (pas de Node build)
- Une seule page marketing (`index.html`) + pages de doc (`docs/`)
- Raison : rapidité de livraison, facilité de maintenance, pas de dépendance framework

### D2 — Hosting: Firebase Hosting (projet xbrain-495115)
- Projet Firebase déjà initialisé : `xbrain-495115`
- Site existant : `xbrain-495115.web.app` (actuel : projects-dashboard)
- **Nouveau site Firebase** : créer un 2e site dans le même projet (Firebase supporte multi-site hosting)
- Site ID cible : `xbrain-marketing` → URL publique `xbrain-marketing.web.app`
- Ou déployer dans le même site si projects-dashboard pas utilisé

### D3 — Domaine
- Pas de custom domain pour l'instant (déploiement rapide)
- URL finale : `xbrain-495115.web.app` ou `xbrain-marketing.web.app`
- DNS personnalisé déféré (hors scope Phase 6)

### D4 — Structure du site
```
marketing-site/
  index.html          ← landing page marketing
  docs/
    index.html        ← docs home / getting started
    architecture.html ← vue d'ensemble architecture
    memory.html       ← système de mémoire (truth levels, tagging)
    teams.html        ← gestion des équipes et scopes
    chat.html         ← interfaces chat (LibreChat, Open WebUI)
    mcp-tools.html    ← outils MCP (scraper, drive-read, calendar, deck)
    drive-sync.html   ← Google Drive sync + webhooks
    chrome-ext.html   ← extension Chrome web clipper
    github-auth.html  ← authentification GitHub + Org membership
    agents.html       ← agents LangGraph + agent-runtime
    graphiti.html     ← service Graphiti (fact extraction temporelle)
    api-reference.html ← memory-api endpoints complets
    deployment.html   ← guide de déploiement Docker Compose
    configuration.html ← référence variables d'environnement
  assets/
    style.css         ← styles communs (nav, layout, typography)
    docs.css          ← styles spécifiques docs (sidebar, code blocks)
```

### D5 — Design landing page
- Fond blanc (#FFFFFF), texte gris foncé (#111827)
- Accent couleur : violet (#7C3AED) — cohérent avec l'identité AI/OS
- Style : propre, moderne, professionnel, pas de stock photos
- Sections :
  1. Hero — "xbrain: AI Memory OS for Teams" + sous-titre + CTA
  2. Problem — Le problème du savoir dispersé dans les équipes
  3. Solution — Comment xbrain centralise la mémoire d'équipe
  4. Features — 6 features clés (cards)
  5. How it works — 3 étapes simples
  6. Technical overview — pour les équipes tech
  7. Footer — links, GitHub, contact
- Pas de prix, pas de signup, pas de SaaS (produit open-source self-hosted)

### D6 — Cible audience
- Startup teams (5-50 personnes) qui veulent implémenter l'AI dans leur workflow
- Tech-savvy mais pas forcément DevOps experts
- Veulent : mémoire persistante partagée, multi-model AI, self-hosted, open-source
- Douleur : informations perdues dans Slack/Notion, chaque dev qui réinvente la roue avec ses propres prompts

### D7 — Langue
- 100% anglais (marketing + docs)
- Ton : professionnel mais accessible, pas corporate
- Pas de jargon superflu, mais précis sur les concepts techniques

### D8 — Documentation
- Complète : TOUTES les features Phase 1-5 documentées
- Structurée : navigation sidebar, breadcrumbs, sections claires
- Détaillée : exemples concrets, screenshots conceptuels (ASCII art ou descriptifs), code snippets
- Orientée "getting started first" → "advanced config"
- Chaque page : introduction, why it matters, how it works, configuration, examples

### D9 — Déploiement
- `firebase deploy --only hosting` depuis `marketing-site/`
- `firebase.json` déjà dans `projects-dashboard/` comme référence
- Créer `marketing-site/firebase.json` adapté
- Pas de GitHub Actions pour l'instant (déploiement manuel une fois)

### D10 — Agents sonnet
- Utiliser des subagents claude-sonnet-4-6 pour générer le contenu
- Chaque page docs = 1 agent
- Landing page = 1 agent dédié
- Firebase deploy = 1 agent

## Non-Decisions (déférés)

- Custom domain (xbrain.io, xbrain.dev, etc.) — déféré
- Recherche/indexation (Algolia DocSearch) — déféré
- Versioning de la doc — déféré
- Dark mode — déféré
- i18n (français) — déféré
- Analytics (Plausible, etc.) — déféré
- Blog / changelog — déféré

## Files to Create

```
marketing-site/
  firebase.json
  .firebaserc
  index.html
  assets/style.css
  assets/docs.css
  docs/index.html
  docs/architecture.html
  docs/memory.html
  docs/teams.html
  docs/chat.html
  docs/mcp-tools.html
  docs/drive-sync.html
  docs/chrome-ext.html
  docs/github-auth.html
  docs/agents.html
  docs/graphiti.html
  docs/api-reference.html
  docs/deployment.html
  docs/configuration.html
```

## Source of Truth for Content

Pour générer le contenu, les agents doivent lire :
- `.planning/ROADMAP.md` — vue d'ensemble des phases
- `.planning/PROJECT.md` — vision et architecture
- `.planning/phases/*/CONTEXT.md` — décisions techniques par phase
- `apps/memory-api/` — endpoints API, modèles
- `infrastructure/docker-compose.yml` — stack complet
- `.env` — variables de configuration
- `chrome-extension/` — extension Chrome
- `docs/brain-yaml-schema.md` — brain.yaml format

## Success Criteria

- `index.html` accessible en ligne via Firebase Hosting URL
- Toutes les pages docs (`docs/*.html`) accessibles
- Navigation fonctionnelle entre toutes les pages
- Design cohérent (landing + docs)
- Contenu complet et précis pour chaque feature
