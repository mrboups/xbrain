# xbrain — AI Cognitive OS

## What This Is

Plateforme open-source de **mémoire collective persistante** pour humains + agents, organisée par équipe et par projet. Toute donnée (chats, faits extraits, documents, sorties d'outils internes) traverse une couche unique — `memory-api` — qui applique un contrat de tagging strict : team-scope, truth-level, provenance, validation. Frontends multiples (LibreChat / Open WebUI / ChatGPT API / Claude Code / Chrome extension / app-site) et agents LangGraph lisent et écrivent **la même mémoire**.

Ce n'est pas un workspace de chatbot. Le différenciateur est la couche mémoire + truth-level + team-scope, pas l'interface.

**Milestone v1.0 status :** SHIPPED (12 phases livrées 2026-05-03 → 2026-05-17). Le scope initial v1 (73 REQ-IDs sur 3 phases) a été étendu en cours de route avec 9 phases additionnelles (3.5 → 12) couvrant la plateforme projets, le marketing site, le CRM/Granola/tasks, le Pro/Max routing, l'auth GitHub-primary, le Brain Monitor universel et la migration GitHub App. Voir `ROADMAP.md` pour le détail des 12 phases et `REQUIREMENTS.md` pour les capacités post-v1.

## Core Value

**Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.** Si tout le reste plante, ce contrat doit tenir.

## Current capabilities (as of 2026-05-17)

Capacités livrées et opérationnelles en production (https://example.com + 30 containers sur VM GCP `e2-standard-2`) :

- **5-truth-level promotion workflow** (Phase 2) — état machine `EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC` enforced par memory-api ; promotion review + approval + audit log immuable
- **Multi-frontend confirmé** (Phases 1, 4, 5, 7, 8, 9, 10) — LibreChat (`chat.example.com`), Open WebUI (`adm.example.com`), Chrome extension MV3 (side panel + clip + chat), app-site Firebase (`example.com/account/teams/`), agents LangGraph (`agent-runtime`), MCP gateway clients — tous lisent/écrivent via `memory-api`
- **Memory layer hybride** (Phases 1-3) — `mem0` (Apache 2.0) sous interface `MemoryProvider` + `memory-api` natif FastAPI qui enforce le contrat de tagging à 7 champs et la state machine truth-level
- **Graphe + extraction temporelle** (Phases 3, 5) — Neo4j Community (relations, lineage) + extraction Claude NER (`/v1/graph/*`) + Graphiti pour extraction temporelle continue
- **CRM + Tasks + Granola pipeline** (Phases 7-8) — contacts auto-extraits depuis chats/agents/meetings, tasks auto-générées sur action items (regex + Claude detection), notifications email aiosmtplib, Granola API key per-user Fernet-chiffrée, agent `meeting-recap` seedé
- **Pro/Max routing via Chrome extension** (Phase 9) — microservice `session-bridge` (port 8105, OpenAI-compat ↔ WebSocket router) + extension WebSocket persistante + fetch credentialed claude.ai → les users consomment leur propre quota Claude Pro/Max au lieu de la clé API team
- **GitHub-primary auth + org-driven team membership** (Phase 10) — sign-in via GitHub OAuth, auto-grant team membership sur match `teams.github_org`, block/pre-block admin, auto-merge des user rows orphelines (Google ↔ GitHub)
- **Brain Monitor — universal truth-level inspector + soft delete** (Phase 11) — colonnes `truth_level` + `deleted_at` + `deleted_by` sur les 6 tables d'entités (memory_items, conversations, messages, team_messages, tasks, contacts) + vue `v_brain_events`, UI `/account/teams/brain/` avec filtres + edit inline + soft delete + 30-day retention, container `brain-janitor` cron quotidien pour purge Postgres + Qdrant + Neo4j
- **Superadmin dashboard cross-team** (Phase 11) — UI `/account/admin/` 4 sections (Brain Overview / Storage / Activity / Top Sources) avec drill-down par team, audit log sur chaque accès cross-team (`action='superadmin_brain_access'`), gated par `ADMIN_USER_SUBS` env
- **GitHub App authentication** (Phase 12) — migration OAuth App → GitHub App "xbrain" (Client ID `Iv23liVnZvIN0Lo6isof`) avec multi-callback URLs natif (web + Chrome extension), JWT RS256 signing, installation tokens cachés (1h TTL), refresh token flow user-to-server (8h ghu_ + 6mo ghr_), table `installations` populée par webhook `installation` events, `GITHUB_API_PAT` retiré du runtime — ready for public deployment

## Current Milestone: v2.0 — Open-Core Edition

**Goal:** Rendre xbrain maintenable en 2+ éditions depuis UN seul codebase (OSS self-host / SaaS hébergé / self-host payant "pro"), pour qu'un update se propage à toutes les éditions automatiquement — jamais de fork.

**Target features:**
- **OSS light self-host** — une team installe chat + brain complet (analyse de doc, ingest, retrieval, truth-levels, connecteur ChatGPT-web via `mcp-brain`, clip) sur ~10 containers.
- **Édition par config, pas par branche** — Docker Compose `profiles:` (untagged = cœur OSS, `integrations`/`pro`/`saas`/`ops`) + flag `EDITION` (oss|saas|pro) qui gate le montage des routers dans memory-api, le cœur brain/chat/retrieval/truth-level TOUJOURS actif.
- **Tier self-host payant "pro"** — clé de licence Ed25519 vérifiée offline débloque le profil `pro` (graphe Neo4j/Graphiti, observabilité Langfuse).
- **Fondation de portabilité** — dé-câbler ~28 `example.com` / 15 `your-team` / 15 `default` team_scope vers la config ; `.env.example` OSS mince et remplissable.
- **UI web chat autonome** — extraire le chat du popup de l'extension Chrome vers une web app hébergée (mutualisée avec la future PWA).
- **CI lockstep** — une pipeline par commit build 1×, teste le sous-ensemble OSS ET le profil full, publie la release OSS ET déploie le SaaS.

**Design source :** `.planning/features/open-core-edition-design.md`.

**Revisite délibérément des frontières v1 "Out of Scope" :** "SaaS multi-tenant pour clients externes" et "pas de frontend custom à maintenir" — v2.0 introduit volontairement le split open-core et une UI web chat autonome. Les morceaux SaaS-only (multi-tenant, pont Pro/Max) restent derrière le profil/flag `saas`.

**Hors scope v2.0 (tracks séparés) :** feature Email (envoi + lecture/recherche/ingest Gmail — absente aujourd'hui) et le fallback Grok clé-API + cap trial par message (trial SaaS).

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Hypotheses until shipped and validated. -->

- [ ] Plateforme déployable en open-source sur GCP VM Ubuntu 24.04 (ou Railway), via Docker Compose, budget de fonctionnement cible ~25€/mois
- [ ] `memory-api` centralisée que tous les frontends et agents consomment — aucune logique de donnée enfermée dans un frontend
- [ ] Contrat de tagging obligatoire sur chaque donnée stockée : `team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level`, `source`, `validation_status`
- [ ] Échelle de truth-levels avec workflow de promotion entre niveaux : `EPHEMERAL` → `WORKING` → `VALIDATED` → `CANONICAL` → `PUBLIC`
- [ ] Hiérarchie organisationnelle Organization → Team → (Projects, Agents, Memory, Assets) avec isolation effective des données par équipe
- [ ] LibreChat installé et configuré comme frontend principal de conversation multi-modèle (Claude / GPT / Grok), branché sur `memory-api`
- [ ] Open WebUI installé comme backend admin / RAG / monitoring agents, branché sur `memory-api`
- [ ] PostgreSQL pour event store, audit logs, truth levels, permissions, workflows
- [ ] Qdrant pour retrieval sémantique, mémoire vectorielle des agents, recherche
- [ ] Neo4j pour graphe relationnel, lineage, dépendances, validation graph
- [ ] Couche mémoire = **`mem0` (Apache 2.0, 40k★) + `memory-api`**. mem0 fournit storage / embeddings / graph / extraction. memory-api enforce truth-level state machine, team-scoping, promotion workflow, audit. mem0 est encapsulé derrière une interface `MemoryProvider` pour rester remplaçable
- [ ] LangGraph comme runtime des agents persistants (workflows, approvals, multi-agent)
- [ ] MinIO comme stockage assets (PDFs, images, decks, fichiers générés, datasets)
- [ ] Langfuse pour observabilité (traces, prompts, failures, tool calls, lineage agents)
- [ ] Outils internes exposés en API services ou MCP servers, publiant/consommant la mémoire avec le contrat de tagging respecté (premiers : scraper, calendar, deck-service, drive-sync, ingestion pipelines)
- [ ] Sync Google Drive : sync documents, édition collaborative, ingestion automatique, indexation mémoire
- [ ] Architecture extensible — ajouter un nouvel outil interne / agent / intégration externe ne doit pas modifier l'infra centrale, juste ajouter un service qui respecte le contrat

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- **Services managés propriétaires (cloud-only) — Pinecone, OpenAI Assistants persistance, Notion API comme source de vérité, etc.** — incompatibles avec contrainte "open-source + auto-hébergeable". Verrouillent le déploiement.
- **Logique métier ou stockage enfermé dans un frontend (LibreChat plugin spécifique, Open WebUI extension propriétaire, etc.)** — viole l'invariant multi-frontend. Toute capacité passe par `memory-api`.
- **Nouveaux schémas de données sans le contrat de tagging complet** — viole l'invariant fondateur de la plateforme.
- **Couplage à un seul modèle (Claude-only ou GPT-only)** — l'utilisateur exige le multi-modèle (Claude / GPT / Grok minimum) et veut pouvoir en ajouter.
- **Mobile-first, app native, ou frontend "maison"** — les frontends sont LibreChat + Open WebUI + ChatGPT API + Claude Code. Pas de frontend custom à maintenir.
- **SaaS multi-tenant pour clients externes (v1)** — la plateforme est interne à l'organisation. Multi-tenant cross-org peut être considéré plus tard, hors scope v1.

## Context

- **Pré-implémentation totale.** Aucun code à date — uniquement la chaîne GSD installée sous `.claude/`. Le repo démarre à zéro.
- **Multi-team réel.** L'organisation gère plusieurs équipes travaillant sur des projets distincts. La donnée doit être cloisonnée par défaut, partagée explicitement par promotion de truth-level.
- **Tooling utilisateur hétérogène.** Certains membres travaillent dans Claude Code (session navigateur), d'autres dans ChatGPT (utilisable via API), Grok est appelé pour les seconds avis / contradictions. La plateforme doit absorber cette hétérogénéité, pas la contraindre.
- **Beaucoup d'outils internes existants ou prévus** : scrapers de données, calendriers, éditeurs de pitch deck collaboratifs, pipelines d'ingestion. Le brief liste ces exemples comme **non exhaustifs** — la roadmap doit prévoir l'ajout continu de nouveaux outils sans changer l'infra.
- **Workflow de dev imposé.** Claude Code écrit les services / Docker / APIs / migrations / MCP tools. LibreChat + Open WebUI servent à tester les agents, valider humainement, collaborer en équipe. Tout passe par GSD : spec-driven, sub-plans par phase, commits atomiques par tâche.
- **L'utilisateur travaille en français.** Les artefacts de planning et la communication restent en français sauf contenu purement technique.
- **Repo GitHub** : `https://github.com/mrboups/xbrain` (origin du dépôt local).
- **Compte GCP de déploiement** : `team@example.com` (cible Phase 1 pour la VM Ubuntu 24.04).

## Constraints

- **Tech stack** (révisée après research + extensions Phases 5-12) : LibreChat + Open WebUI + **mem0** + LangGraph + Qdrant + Neo4j + PostgreSQL + MinIO (image Chainguard) + Langfuse + Graphiti + Centrifugo (team chat realtime) + Chrome extension MV3 + Firebase Hosting (app-site, marketing site, projects dashboard) + `PyJWT[crypto]>=2.10` (GitHub App JWT signing, Phase 12) — **Pourquoi :** stack 100 % OSS auto-hébergeable. Memstate.ai (SaaS fermé), Remembra (13★ + SQLite, immature) et Memori (Alpha) ont été retirés au profit de mem0 + memory-api natif après vérification : voir Key Decisions ci-dessous.
- **Déploiement** : GCP VM Ubuntu 24.04, Docker Compose — **Pourquoi :** budget contraint, ops simple, pas d'expertise Kubernetes requise. Stratégie de sizing échelonnée :
  - **Phase 1** : `e2-medium` (4 GB, ~25€/mo) — LibreChat + Open WebUI + Postgres + Qdrant + memory-api stub. Tolérance fine — surveiller OOM, pas de service ajouté en plus sans couper autre chose.
  - **Phase 2** : upgrade vers `e2-standard-2` (8 GB, ~38-49€/mo) en début de phase, **avant** d'ajouter mem0 + LangGraph + agent runtime.
  - **Phase 3** : `e2-standard-4` (16 GB, ~98€/mo) **OU** Langfuse sur VM séparée (~62€/mo total) — décision en début de Phase 3 selon charge observée.
  - GCP project cible : compte `team@example.com`, projet à créer (`xbrain-prod` proposé) sans toucher aux projets existants.
- **Open-source uniquement** : aucun service managé propriétaire dans le chemin critique — **Pourquoi :** auto-hébergeable, pas de lock-in, contrôle complet de la donnée (sensibilité multi-team).
- **Multi-frontend invariant** : LibreChat + Open WebUI + ChatGPT (API) + Claude Code lisent/écrivent la même mémoire — **Pourquoi :** l'équipe utilise déjà ces outils en pratique. Imposer un frontend unique ferait échouer l'adoption.
- **Contrat de tagging obligatoire** : 7 champs minimum sur chaque donnée — **Pourquoi :** invariant qui rend possibles l'isolation team, la promotion truth-level, l'audit, le retrieval scopé. C'est le différenciateur.
- **Multi-modèle** : Claude (coding/archi), GPT (reasoning/summary), Grok (second avis) — **Pourquoi :** chaque modèle a un rôle distinct. La plateforme doit pouvoir en ajouter (futur Mistral, Gemini, etc.) sans refactor.
- **Performance** : pas de SLA strict en v1, mais l'expérience LibreChat doit rester fluide (< 2s pour une réponse simple, retrieval mémoire < 500ms en P95) — **Pourquoi :** UX d'équipe.
- **Soft delete universel + 30-day retention** (Phase 11) : toute entité brainable (memory_items, conversations, messages, team_messages, tasks, contacts) porte `deleted_at` + `deleted_by` ; container `brain-janitor` cron quotidien (03:00 UTC) hard-delete Postgres + Qdrant + Neo4j après 30 jours — **Pourquoi :** restauration possible pendant 30j (UX), purge réelle ensuite (RGPD + storage hygiene).
- **GitHub App authentication public-deployment-ready** (Phase 12) : pas d'OAuth App long-lived PAT en runtime, tokens courts (App JWT 10min / installation token 1h / user token ghu_ 8h / refresh ghr_ 6mo) — **Pourquoi :** multi-callback natif (web + extension + futurs frontends), rate limit per-installation, support webhooks installation, retirable proprement de l'org GitHub.
- **Logging caps Docker** (2026-05-17) : tous les ≥29 services xbrain-* ont `max-size 100m, max-file 3` via YAML anchor — **Pourquoi :** post-incident disque VM 100% (clickhouse log 17GB), prévient récurrence.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Plateforme open-source self-hosted, pas SaaS managé | Sensibilité multi-team + contrôle données + budget contraint | — Pending |
| Stack : LibreChat + Open WebUI + mem0 + LangGraph + Qdrant + Neo4j + PostgreSQL + MinIO (Chainguard) + Langfuse | 100 % OSS, déployable en Docker Compose. Remembra/Memstate/Memori retirés (cf. décision suivante). | ✓ Validé après research |
| Couche mémoire = mem0 + memory-api natif (au lieu de Memstate + Remembra + Memori) | Memstate.ai = SaaS fermé (pas d'OSS officiel). Remembra immature (13★, SQLite). Memori en Alpha. mem0 (40k★, prod-ready) couvre storage + embeddings + graph + extraction. Le state machine truth-level + team-scoping + audit reste dans memory-api (notre vrai différenciateur). | ✓ Validé 2026-05-02 |
| `MemoryProvider` interface dans `/packages/memory-models` avant intégration mem0 | Garde mem0 remplaçable si besoin futur. Mitigation bus-factor / lock-in. | — Pending |
| VM échelonnée : e2-medium Phase 1 → e2-standard-2 Phase 2 → e2-standard-4 (ou split) Phase 3 | Aligne sur budget initial ~25€/mo, scaling progressif. Risque OOM Phase 1 à mitiger. | ✓ Validé 2026-05-02 |
| Open WebUI v0.6.6+ : licence custom non-OSI mais OK pour usage interne ≤50 users avec branding préservé | Acceptable dans notre périmètre interne. À re-évaluer si l'équipe dépasse 50 users. | ✓ Validé 2026-05-02 |
| MinIO via image Chainguard (`cgr.dev/chainguard/minio:latest`) | Images officielles Docker Hub discontinuées oct 2025. Chainguard est le standard de fait, déjà utilisé par Langfuse. | ✓ Validé 2026-05-02 |
| `memory-api` comme couche centrale, frontends pluggables | Invariant fondateur — empêche la fragmentation par frontend | — Pending |
| Contrat de tagging à 7 champs sur chaque donnée | Permet isolation team, promotion truth-level, audit, retrieval scopé | — Pending |
| Truth-levels : EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC | Permet de marquer une info "super valid" / "public" comparée au reste du brain | — Pending |
| Hiérarchie Org → Team → Projects/Agents/Memory/Assets | Isolation par défaut, partage par promotion explicite | — Pending |
| Outils internes en API services ou MCP servers, pas plugins frontend | Réutilisables depuis tous les frontends, agents et clients | — Pending |
| Phasing 1 (socle infra + frontends) → 2 (mémoire + agents) → 3 (graphe + extraction + intégrations) | Permet de chatter en multi-modèle dès Phase 1, puis empile les couches mémoire | — Pending |
| Granularité de phase : Coarse | Stack complexe mais bien définie ; phases larges réduisent le coût d'orchestration GSD | — Pending |
| Plans en parallèle | Composants Docker Compose largement indépendants | — Pending |
| Profil de modèles GSD : Balanced (Sonnet) | Bon ratio qualité/coût pour les agents de planning | — Pending |
| Phase 11 ordering : Brain Monitor before GitHub App migration | Concrétise le différenciateur truth-level sur toutes les entités avant le clean break auth. Phase 12 peut alors s'appuyer sur l'audit log universel pour tracer les events superadmin. | ✓ Validé 2026-05-17 |
| Phase 12 strategy : clean break OAuth App → GitHub App (no dual-auth) | Un seul user existant (mrboups), re-authorize 1x acceptable. Dual-auth ajouterait 2 chemins à maintenir indéfiniment. | ✓ Validé 2026-05-17 |
| Phase 12 GitHub App owner : `mrboups` personal account + minimal permissions (`read:user`, `user:email`, `read:org`) | Pas de GitHub org dédié pour le moment ; perms minimales pour limiter blast radius. | ✓ Validé 2026-05-17 |
| Phase 12 Chrome extension : deterministic ID via fixed `key` in manifest.json | chromiumapp.org callback URL doit être stable pour figurer dans la liste multi-callback GitHub App. Derived ID: `anigikcnmldoklcmogffmgcojdhhficb`. | ✓ Validé 2026-05-17 |
| Soft delete universel via `deleted_at` + cron `brain-janitor` 30j | Restauration UX-friendly + purge réelle Postgres + Qdrant + Neo4j garantie. Évite la prolifération de "trash forever". | ✓ Validé 2026-05-17 |
| Superadmin cross-team via `ADMIN_USER_SUBS` env (lockdown par défaut) | Pas de table superadmin en DB, env-only allowlist — réduit le blast radius d'une compromission DB. Audit log obligatoire sur chaque accès. | ✓ Validé 2026-05-17 |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-05-17 — sync to 12-phase shipped reality (Phase 11 Brain Monitor + Superadmin + Phase 12 GitHub App migration LIVE). v1 73-REQ scope frozen; post-v1 capabilities documented in REQUIREMENTS.md.*
*Previous update: 2026-05-02 after research synthesis (memory layer revised: mem0 + memory-api natif au lieu de Memstate/Remembra/Memori, VM strategy confirmée e2-medium → e2-standard-2 → e2-standard-4, Open WebUI license + MinIO Chainguard documentés).*
