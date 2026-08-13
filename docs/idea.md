# xbrain — AI Cognitive OS

> **SUPERSEDED — kickoff brief du 2026-05-02, conservé comme archive (annoté 2026-08-13).**
> C'est le document d'origine qui a servi à `/gsd:new-project --auto @docs/idea.md`,
> pas une description du produit actuel. Il décrit une intention, pas ce qui existe :
> 27 phases ont été livrées depuis. **Ne rien planifier à partir d'ici.**
>
> Pour l'état réel : `CLAUDE.md` (statut, stack, conventions, architecture),
> `.planning/PROJECT.md` + `REQUIREMENTS.md` + `ROADMAP.md` (périmètre et
> traçabilité), `README.md` (vue externe), `docs/INSTALL.md` (installation).
>
> Ce qu'il garde d'utile : le problème d'origine et le raisonnement derrière les
> invariants qui, eux, n'ont pas bougé — une seule couche `memory-api`, le contrat de
> tagging, l'isolation par équipe, le multi-frontend.

## En une ligne

Construire un **système de mémoire collective persistante** pour humains + agents, organisé par équipe et par projet — pas un workspace de chatbot.

## Le problème qu'on résout

On gère plusieurs équipes qui travaillent sur des projets distincts, avec des données distinctes par équipe. Aujourd'hui :

- La connaissance produite (chats, infos, validations) reste enfermée dans des silos par outil (Claude.ai, ChatGPT, Slack, Notion, Drive).
- Les outils internes développés par l'équipe (scrapers, calendriers, éditeurs de pitch deck, pipelines d'ingestion) ne partagent pas leurs sorties dans une couche commune accessible à tous.
- Aucune notion de **niveau de validité** : on ne peut pas dire "cette info est canonique" vs "c'est encore une hypothèse".
- Aucun cloisonnement clair par équipe / projet quand il faut qu'il y en ait, ni élévation contrôlée vers le partage public quand c'est utile.
- L'équipe utilise des modèles différents (Claude Code en navigateur pour certains, ChatGPT pour d'autres, Grok pour les seconds avis). Personne ne tape la même mémoire.

## La vision cible

Une couche unique — **memory-api** — sur laquelle tous les frontends, agents et outils internes lisent/écrivent. Chaque donnée porte un contrat de tagging qui permet l'isolation par équipe, la promotion entre niveaux de vérité, et l'audit complet.

Le différenciateur **n'est pas le frontend** (LibreChat ou Open WebUI sont remplaçables). Le différenciateur est **la couche mémoire + truth-level + team-scope**.

## Utilisateurs

- **Membres d'équipes internes** qui produisent et consomment de la connaissance partagée (chats, documents, données scrappées, decks, calendriers).
- **Agents** (LangGraph) qui travaillent en arrière-plan : ingestion, extraction de faits, validation de cohérence, automation.
- **Admins** qui gèrent la promotion entre truth levels, l'attribution des permissions, et le monitoring.

## Nature du système (capacités, pas une liste fermée)

xbrain est une **plateforme**, pas un produit à features fixes. Toute capacité décrite ici est **illustrative et non limitante** — la roadmap doit prévoir l'ajout continu de nouveaux outils, agents, intégrations et workflows sans changer l'architecture. Ce qui est verrouillé, c'est le contrat (tagging, truth levels, team-scope, multi-frontend, OSS) — pas la liste des choses qu'on peut faire.

### Familles de capacités attendues (exemples non exhaustifs)

- **Conversations** — multi-modèle, multi-frontend, indexées comme mémoire d'équipe.
- **Outils internes** — n'importe quel outil interne (existant ou futur) peut être branché en MCP server ou API service et publier/lire dans la mémoire de l'équipe avec le contrat de tagging respecté. Exemples actuels : scraper de données, calendrier, éditeur de pitch deck collaboratif, sync Google Drive, pipelines d'ingestion. Tout outil futur (générateur de rapports, monitoring de campagnes, qualification de leads, automation de workflows métier, etc.) suit le même schéma — c'est conçu pour qu'on en ajoute en permanence.
- **Agents** — agents persistants LangGraph qui ingèrent, valident, extraient, automatisent. Le runtime accueille de nouveaux agents sans modification de l'infra.
- **Promotion de connaissance** — workflow de promotion entre truth levels (`EPHEMERAL` → `WORKING` → `VALIDATED` → `CANONICAL` → `PUBLIC`) avec rôles, approvals, audit.
- **Second avis modèles** — n'importe quel modèle disponible (Grok, GPT, Claude, ou autre ajouté plus tard) peut être appelé en parallèle pour critique / contradiction / vérification.
- **Intégrations externes** — Google Drive en premier, mais l'architecture doit permettre l'ajout de Notion, Slack, Linear, GitHub, Gmail, calendriers tiers, etc.
- **Recherche et RAG** — sémantique (Qdrant), graphe (Neo4j), événementiel (Postgres), avec scoping par team/project/truth_level.
- **Observabilité** — toutes les actions (humains + agents) tracées dans Langfuse pour debug, optimisation, audit.

L'objectif de la roadmap n'est pas de "couvrir 6 cas d'usage". L'objectif est de **livrer une plateforme extensible** où ajouter une 50ème capacité a un coût marginal, parce que le contrat de données est respecté partout.

## Contraintes structurantes

- **Open-source et auto-hébergeable uniquement.** Aucun service managé propriétaire qui verrouille le déploiement.
- **Déploiement** : VM GCP Ubuntu 24.04 (e2-medium baseline ~25€/mois) ou Railway, via Docker Compose.
- **Multi-frontend** : LibreChat + Open WebUI + ChatGPT (API) + Grok doivent tous lire/écrire la même couche mémoire. Toute logique qui enferme la donnée dans un frontend est à proscrire.
- **Contrat de tagging obligatoire sur toute donnée** : `team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level`, `source`, `validation_status`.
- **Truth levels** (échelle de promotion) : `EPHEMERAL` → `WORKING` → `VALIDATED` → `CANONICAL` → `PUBLIC`.
- **Hiérarchie organisationnelle** : Organization → Team → (Projects, Agents, Memory, Assets). Chaque équipe possède sa mémoire, ses embeddings, ses documents, ses agents, ses permissions.

## Architecture cible (déjà tranchée)

### Frontends / Workspaces

- **LibreChat** — interface principale équipe, conversations multi-modèle (Claude / GPT / Grok), collaboration humaine.
- **Open WebUI** — backend admin / tooling : RAG, tests d'agents, monitoring IA, workflows internes.

### Couche mémoire (cœur du système)

- **Remembra** — mémoire long terme, persistante, temporelle, avec provenance et entity graph.
- **Memstate** — versioning de faits, gestion des conflits, "semantic git" pour la connaissance.
- **Memori** — extraction structurée automatique : facts, tasks, entities, rules, preferences.

### Bases de données

- **Qdrant** — vector store, retrieval sémantique, mémoire des agents, search.
- **Neo4j** — graphe relationnel, lineage, dépendances, validation graph.
- **PostgreSQL** — event store, audit logs, truth levels, permissions, workflows.

### Runtime agents

- **LangGraph** — agents persistants, workflows, approvals, multi-agent orchestration.

### Outils internes (tous exposés en API services ou MCP servers)

- Scraper de données
- Calendrier
- Éditeur de pitch deck collaboratif
- Sync Google Drive
- Pipelines d'ingestion

### Stockage assets

- **MinIO** — PDFs, images, decks, fichiers générés, datasets.

### Observabilité

- **Langfuse** — traces, prompts, failures, tool calls, lineage des agents.

### Modèles (rôles)

- **Claude** — coding, architecture, agents principaux.
- **GPT** — reasoning général, summarization (typiquement via API ChatGPT pour les utilisateurs qui restent sur ChatGPT).
- **Grok** — second avis, critique, contradiction.

### Intégrations externes

- **Google Drive** — sync documents + édition collaborative + ingestion automatique + indexation mémoire.

## Structure repo prévue (monorepo)

```
/apps
  /librechat
  /openwebui
  /memory-api
  /agent-runtime
  /mcp-gateway

/services
  /scraper
  /calendar
  /drive-sync
  /deck-service

/packages
  /schemas
  /shared-types
  /memory-models
  /agent-tools

/infrastructure
  docker-compose.yml
  nginx
  monitoring
```

## Phasing

### Phase 1 — Socle infra et frontends

- VM GCP Ubuntu 24.04 (ou Railway pour démarrer plus vite)
- Docker Compose
- LibreChat
- Open WebUI
- PostgreSQL
- Qdrant

**Objectif :** une équipe peut chatter en multi-modèle, les conversations sont stockées, on peut faire du RAG basique sur des docs.

### Phase 2 — Mémoire intelligente et agents

- Remembra (mémoire long terme)
- Memstate (versioning / validation)
- LangGraph (runtime agents)

**Objectif :** la mémoire devient structurée, versionnée, avec gestion de conflits. Les agents tournent en arrière-plan.

### Phase 3 — Graphe, extraction, intégrations

- Neo4j (graph relationnel)
- Memori (extraction structurée auto)
- Google Drive sync
- Premiers MCP tools (scraper, calendar, deck-service)

**Objectif :** le brain est complet — tout outil interne peut publier dans la mémoire commune avec le contrat de tagging respecté.

## Workflow de développement

- **Claude Code** développe les services, écrit le Docker, les APIs, les migrations, les MCP tools.
- **LibreChat / Open WebUI** servent à tester les agents, valider humainement, collaborer en équipe.
- Tout passe par GSD : spec-driven, sub-plans par phase, commits atomiques par tâche.

## Points encore à trancher (non bloquants pour démarrer Phase 1)

1. **Auth & identité** : SSO Google (cohérent avec Drive) ou auth locale LibreChat ?
2. **Sizing VM** : `e2-medium` (4 Go RAM) sera serré dès Phase 2 avec Neo4j + Qdrant + MinIO. Peut-être démarrer `e2-standard-2` ou commencer Railway puis migrer.
3. **Isolation team** : une instance LibreChat multi-team avec isolation logique par `team_scope`, ou une instance par team ?
4. **Mémoire inter-team** : qu'est-ce qui traverse les frontières ? Seulement `PUBLIC` ? Ou aussi `CANONICAL` au niveau organisation ?
5. **Maturité Remembra / Memstate / Memori** : ces trois projets sont à des niveaux variables. Prévoir un POC d'1 jour avant de les graver dans la stack en Phase 2.

## Objectif final

Construire un **système de mémoire collective persistant pour humains + agents**, et **non** un simple chatbot workspace.
