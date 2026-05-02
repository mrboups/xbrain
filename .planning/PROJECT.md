# xbrain — AI Cognitive OS

## What This Is

Plateforme open-source de **mémoire collective persistante** pour humains + agents, organisée par équipe et par projet. Toute donnée (chats, faits extraits, documents, sorties d'outils internes) traverse une couche unique — `memory-api` — qui applique un contrat de tagging strict : team-scope, truth-level, provenance, validation. Frontends multiples (LibreChat / Open WebUI / ChatGPT API / Claude Code) et agents LangGraph lisent et écrivent **la même mémoire**.

Ce n'est pas un workspace de chatbot. Le différenciateur est la couche mémoire + truth-level + team-scope, pas l'interface.

## Core Value

**Toute donnée produite (humain ou agent, peu importe le frontend) atterrit dans une mémoire commune, taguée par équipe et par niveau de vérité, et reste réutilisable de façon scopée par n'importe quel membre, agent ou outil.** Si tout le reste plante, ce contrat doit tenir.

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
- [ ] Couche mémoire structurée : Remembra (long terme + provenance + entity graph), Memstate (versioning + conflits), Memori (extraction structurée auto)
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
- **Compte GCP de déploiement** : `team@grooveos.app` (cible Phase 1 pour la VM Ubuntu 24.04).

## Constraints

- **Tech stack** : LibreChat + Open WebUI + Remembra + Memstate + Memori + LangGraph + Qdrant + Neo4j + PostgreSQL + MinIO + Langfuse — **Pourquoi :** stack tranchée au kickoff, 100 % open-source, auto-hébergeable, couvre toutes les fonctions attendues. Substitutions à challenger explicitement avant adoption.
- **Déploiement** : GCP VM Ubuntu 24.04 (e2-medium baseline ~25€/mois) ou Railway, via Docker Compose — **Pourquoi :** budget contraint, ops simple, pas d'expertise Kubernetes requise, portable entre les deux cibles.
- **Budget infra** : ~25€/mois en baseline (e2-medium) — **Pourquoi :** projet interne, pas de revenu direct, doit rester soutenable. Sizing peut monter (e2-standard-2) si Phase 2/3 le requièrent.
- **Open-source uniquement** : aucun service managé propriétaire dans le chemin critique — **Pourquoi :** auto-hébergeable, pas de lock-in, contrôle complet de la donnée (sensibilité multi-team).
- **Multi-frontend invariant** : LibreChat + Open WebUI + ChatGPT (API) + Claude Code lisent/écrivent la même mémoire — **Pourquoi :** l'équipe utilise déjà ces outils en pratique. Imposer un frontend unique ferait échouer l'adoption.
- **Contrat de tagging obligatoire** : 7 champs minimum sur chaque donnée — **Pourquoi :** invariant qui rend possibles l'isolation team, la promotion truth-level, l'audit, le retrieval scopé. C'est le différenciateur.
- **Multi-modèle** : Claude (coding/archi), GPT (reasoning/summary), Grok (second avis) — **Pourquoi :** chaque modèle a un rôle distinct. La plateforme doit pouvoir en ajouter (futur Mistral, Gemini, etc.) sans refactor.
- **Performance** : pas de SLA strict en v1, mais l'expérience LibreChat doit rester fluide (< 2s pour une réponse simple, retrieval mémoire < 500ms en P95) — **Pourquoi :** UX d'équipe.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Plateforme open-source self-hosted, pas SaaS managé | Sensibilité multi-team + contrôle données + budget contraint | — Pending |
| Stack : LibreChat + Open WebUI + Remembra/Memstate/Memori + LangGraph + Qdrant/Neo4j/PostgreSQL + MinIO + Langfuse | Couvre toutes les fonctions attendues, 100 % OSS, déployable en Docker Compose | — Pending |
| `memory-api` comme couche centrale, frontends pluggables | Invariant fondateur — empêche la fragmentation par frontend | — Pending |
| Contrat de tagging à 7 champs sur chaque donnée | Permet isolation team, promotion truth-level, audit, retrieval scopé | — Pending |
| Truth-levels : EPHEMERAL → WORKING → VALIDATED → CANONICAL → PUBLIC | Permet de marquer une info "super valid" / "public" comparée au reste du brain | — Pending |
| Hiérarchie Org → Team → Projects/Agents/Memory/Assets | Isolation par défaut, partage par promotion explicite | — Pending |
| Outils internes en API services ou MCP servers, pas plugins frontend | Réutilisables depuis tous les frontends, agents et clients | — Pending |
| Phasing 1 (socle infra + frontends) → 2 (mémoire + agents) → 3 (graphe + extraction + intégrations) | Permet de chatter en multi-modèle dès Phase 1, puis empile les couches mémoire | — Pending |
| Granularité de phase : Coarse | Stack complexe mais bien définie ; phases larges réduisent le coût d'orchestration GSD | — Pending |
| Plans en parallèle | Composants Docker Compose largement indépendants | — Pending |
| Profil de modèles GSD : Balanced (Sonnet) | Bon ratio qualité/coût pour les agents de planning | — Pending |

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
*Last updated: 2026-05-02 after initialization*
