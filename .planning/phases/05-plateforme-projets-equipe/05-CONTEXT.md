---
phase: 5
phase_name: Plateforme Projets Équipe
phase_slug: plateforme-projets-equipe
date: 2026-05-06
goal_from_roadmap: |
  Transformer xbrain en plateforme complète pour les équipes : pipeline de déploiement
  GitOps (GitHub Actions → Cloud Run + Firebase), extraction intelligente temporelle
  (Graphiti), extension Chrome pour validation truth-level, modèle d'authentification
  unifié (GitHub Org + Google + account linking), et dashboard des projets déployés.
requirements_in_scope:
  - TEAM-01  # création d'équipes et invitation membres
  - TEAM-03  # projets multiples par team
  - AUTH-01  # Google SSO
  - AUTH-03  # tokens JWT pour services
  - MEM-06   # mémoire long-terme entités (Graphiti)
  - MEM-07   # fact versioning temporel (Graphiti bi-temporal)
  - MEM-08   # conflict detection automatique (Graphiti)
  - CHAT-08  # endpoint OpenAI-compatible (GitHub auth)
---

# Phase 5 — Plateforme Projets Équipe — CONTEXT

## Goal en une phrase

**Phase 4 a fermé la boucle MCP frontends. Phase 5 ouvre la plateforme vers l'extérieur : n'importe quel membre peut déployer un projet depuis son terminal, le rendre accessible à l'équipe, et son contenu enrichit automatiquement le brain avec une extraction temporelle intelligente.**

<domain>
## Phase Boundary

Phase 5 livre 5 composants indépendants mais complémentaires :

1. **Pipeline déploiement GitOps** — GitHub Actions → Cloud Run (services) + Firebase Hosting (statique). Chaque repo GitHub avec un `brain.yaml` se déploie automatiquement au push et indexe son contenu dans xbrain.

2. **Graphiti** — Remplacement partiel de mem0 pour l'extraction de faits. Graphiti apporte le bi-temporel (4 timestamps par relation), la détection de contradictions automatique via LLM, et l'invalidation des anciennes infos. Tourne dans un container séparé (`graphiti-service`) branché sur Neo4j existant.

3. **Extension Chrome** — Web clipper + sélecteur truth-level. Un membre peut marquer n'importe quelle page web ou contenu de projet comme CANONICAL depuis son browser. Appelle `api.dejavu.cat` directement.

4. **Auth unifié — GitHub Org + Google + linking** — GitHub OAuth ajouté à LibreChat en parallèle de Google OAuth. Membres GitHub Org → accès team automatique. Outside collaborators → accès project_scope spécifique. Google users peuvent lier leur compte GitHub dans leur profil.

5. **Dashboard projets déployés** — `projects.dejavu.cat` listant tous les projets live, filtrables par member/projet, avec URL, statut et membres. Généré via GitHub Org API + `brain.yaml` de chaque repo.

**Hors périmètre Phase 5 :**
- RBAC par projet pour Google users (déféré YAGNI — Google users ont accès team complet pour l'instant)
- Éditeur collaboratif temps-réel (→ git + GitHub web UI)
- Mobile PWA
- Multi-org (xbrain reste mono-org en v1)
</domain>

<decisions>
## Implementation Decisions

### D1 — Pipeline déploiement : Cloud Run + Firebase Hosting

**Décision :** Deux targets selon le type de projet :
- **Firebase Hosting** pour tout ce qui est statique (Reveal.js decks, sites, SPAs)
- **Cloud Run** pour tout ce qui a un process (scrapers, APIs, dashboards avec backend)

**Pas de VM xbrain pour héberger les projets** — Cloud Run et Firebase sont dans le même projet GCP `xbrain-495115`, billing unifié, zero maintenance.

**brain.yaml** dans chaque repo (racine) définit le comportement au deploy :
```yaml
name: deck-fundraising
type: static          # static | service
slug: fundraising
team_scope: acme
project_scope: fundraising
deploy:
  target: firebase    # firebase | cloudrun
  region: europe-west1
brain:
  enrich: true
  trigger: on_deploy  # on_deploy | daily | on_output
  content: README.md  # fichier(s) à indexer
  truth_level: EPHEMERAL
```

**GitHub Actions workflow standard** (`.github/workflows/deploy.yml`) généré automatiquement par Claude Code pour chaque nouveau projet. Inclut : build → deploy → brain indexing.

### D2 — Graphiti : container isolé, wrappé par memory-api

**Décision :** Graphiti tourne dans son propre container `graphiti-service` (port 8300) avec une REST API maison (FastAPI ~50 lignes). `memory-api` l'appelle via HTTP — aucun conflit event loop possible.

**Backend :** Neo4j existant (déjà dans le stack, port 7687).

**Rôle dans le pipeline :** Graphiti remplace mem0 pour l'extraction de faits depuis les outputs de projets et les conversations. Mem0 reste pour la mémoire conversationnelle utilisateur (ils coexistent).

**LLM pour extraction :** Claude Haiku (coût faible, suffisant pour NER/extraction).

**Multi-tenancy :** `group_id = team_scope` pour l'isolation entre teams.

### D3 — Extension Chrome : Manifest V3, auth Google OAuth

**Décision :** Extension Chrome Manifest V3.
- Auth : Google OAuth (même provider que LibreChat — user déjà connecté → token réutilisable)
- Popup : sélecteur team_scope + project_scope + truth_level + bouton "Envoyer au brain"
- Web clipper : extrait le contenu de la page courante (titre + URL + texte sélectionné)
- Validation rapide : depuis `projects.dejavu.cat`, peut marquer un projet/fait comme CANONICAL en 1 clic
- Appelle directement `https://api.dejavu.cat/v1/memory`

### D4 — Auth : GitHub OAuth en parallèle de Google

**Décision :** LibreChat supporte GitHub OAuth nativement (1 ligne de config). Ajouter GitHub OAuth comme second provider — les users peuvent se connecter avec Google OU GitHub.

**Source de vérité pour accès projets :** GitHub API
- Membre GitHub Org → accès team complet (équivalent `role: member`)
- Outside collaborator sur un repo → accès uniquement au `project_scope` correspondant
- Google-only user → accès team complet (pas de restriction projet, déféré YAGNI)

**Account linking :** Un user Google peut lier son GitHub dans son profil xbrain. memory-api stocke `github_username` sur la table `users`. Une fois lié, il bénéficie des accès GitHub en plus.

**Org GitHub :** `your-github-org` (ou équivalent, à créer si pas existante). Tous les membres de l'équipe y sont invités avec leur compte GitHub perso existant.

**Vérification membership :** À chaque appel memory-api avec `X-Team-Scope`, le middleware vérifie l'appartenance via GitHub API (avec cache 5min pour éviter le rate limit).

### D5 — Dashboard : projects.dejavu.cat, généré via GitHub Actions

**Décision :** Site statique généré automatiquement à chaque deploy de n'importe quel projet.

**Données sources :**
- Liste des repos de l'org → GitHub Org API
- Membres par projet → GitHub collaborators API
- URL deployée + type → `brain.yaml` de chaque repo
- Statut live/down → Cloud Run health check API ou Firebase status

**Hébergement :** Firebase Hosting (même pattern que les projets statiques).

**Accès privé :** Cloudflare Access protège `projects.dejavu.cat` — seuls les emails whitelistés (team) peuvent accéder. Zero code, config Cloudflare uniquement.

**Filtres :** par member (GitHub username) et par projet (slug). UI simple, pas de framework lourd.

### D6 — MCP tools : viennent des repos GitHub, pas préconfigurés

**Décision :** Les MCP tools Phase 3/4 (scraper, drive-read, calendar, deck) restent. Mais les nouveaux tools viendront des repos GitHub déployés sur Cloud Run — ils s'enregistrent automatiquement dans le gateway via `brain.yaml` si `type: mcp_tool`.

Pas de tools préconfigurés supplémentaires dans xbrain core.

### D7 — Google users : accès team complet, RBAC projet déféré

**Décision :** Un user qui rejoint via Google OAuth a accès à toute la team (tous les project_scopes). C'est suffisant pour la v1.

Le RBAC par projet pour Google users (équivalent outside collaborator GitHub) est explicitement déféré à v2. L'architecture PostgreSQL est déjà prête (il suffira d'ajouter `project_members` table + endpoints).

### Claude's Discretion
- Design exact du dashboard (UI, framework CSS)
- Exact format du `brain.yaml` (peut évoluer pendant le planning)
- Stratégie de cache pour les appels GitHub API (5min suggéré, à ajuster)
- Choix du modèle Claude pour Graphiti extraction (Haiku suggéré)
- Gestion des erreurs GitHub API rate limit

</decisions>

<specifics>
## Specific Ideas

- **brain.yaml** : simple, pas de YAML complexe — le dev doit pouvoir le créer en 30 secondes
- **Deploy pipeline** : doit fonctionner sans que le dev connaisse GCP — `gcloud run deploy --source .` suffit, GitHub Actions fait le reste
- **Extension Chrome** : popup minimaliste, pas de page options compliquée — juste les 4 champs essentiels + bouton
- **Dashboard** : "comme la page Projects d'une GitHub Org" — liste propre avec statut, URL, membres
- **Graphiti** : transparent pour les users — ils ne savent pas que Graphiti tourne derrière, ils voient juste que le brain est plus intelligent sur les contradictions
- **Account linking** : optionnel, pas bloquant — un user Google sans GitHub fonctionne normalement

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Infrastructure existante
- `.planning/phases/04-consolidation-mcp-frontends-et-integrations/04-CONTEXT.md` — Architecture Phase 4, patterns MCP gateway, docker-compose
- `.planning/phases/03-graphe-extraction-integrations/03-CONTEXT.md` — Pattern Neo4j, drive-sync, mcp-gateway
- `infrastructure/docker-compose.yml` — Services existants, patterns volumes/networks
- `infrastructure/nginx/conf.d/` — Patterns vhosts nginx existants (10-xbrain.conf, 20-api.conf)

### Auth et identité
- `apps/memory-api/app/auth.py` — verify_google_id_token, verify_bridge_jwt, make_bridge_jwt
- `apps/memory-api/app/deps.py` — get_current_principal, get_team_scope, get_or_create_user
- `infrastructure/librechat/librechat.yaml` — Config OAuth existante (Google), mcpServers

### Memory et extraction
- `apps/memory-api/app/routes/` — Endpoints existants (messages, facts, memory, admin)
- `apps/agent-runtime/app/tools/mcp_gateway_client.py` — Pattern Bridge JWT pour tools

### Graphiti
- GitHub : https://github.com/getzep/graphiti (Apache 2.0, v0.29.0)
- Architecture : container séparé, Neo4j backend, REST API wrapper FastAPI

### GCP
- Projet : `xbrain-495115` (compte team@grooveos.app)
- VM existante : `__VM_HOST__` (e2-standard-2, Docker Compose)
- Cloud Run, Firebase Hosting, Cloud Scheduler — même projet GCP
- Cloudflare : domaine `dejavu.cat` (DNS + Access)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `apps/mcp-scraper/` — Pattern FastMCP sidecar à dupliquer pour graphiti-service (FastAPI wrapper)
- `apps/memory-api/app/auth.py:make_bridge_jwt` — Bridge JWT pour appels inter-services
- `apps/memory-api/app/routes/admin_drive.py` — Pattern Fernet + OAuth flow (réutilisable pour GitHub OAuth)
- `infrastructure/scripts/register-mcp-tools.sh` — Pattern enregistrement tool dans gateway
- `infrastructure/nginx/conf.d/20-api.conf` — Pattern vhost nginx à dupliquer pour projects.dejavu.cat
- `apps/agent-runtime/app/tools/mcp_gateway_client.py` — Pattern discovery dynamique de tools

### Established Patterns
- **Container séparé pour isoler async** : même pattern que mcp-gateway aggregate subprocess → graphiti-service est un container dédié
- **Brain enrichment** : POST /v1/memory avec Bridge JWT — pattern établi, à documenter comme snippet standard pour les repos GitHub
- **Neo4j outbox** : `apps/memory-api/app/routes/` utilise outbox pattern pour écriture async Neo4j → Graphiti réutilise Neo4j directement

### Integration Points
- `graphiti-service` → Neo4j (existant, port 7687)
- `graphiti-service` → memory-api (appels HTTP pour enrichissement)
- GitHub Actions → Cloud Run deploy → `api.dejavu.cat/v1/memory` (brain indexing)
- Extension Chrome → `api.dejavu.cat/v1/memory` (direct HTTPS)
- `memory-api` → GitHub API (membership verification, avec cache)
- Dashboard → GitHub Org API + `brain.yaml` de chaque repo

</code_context>

<deferred>
## Deferred Ideas

- **RBAC par projet pour Google users** — déféré v2. Requiert `project_members` table + endpoints. Architecture prête, juste pas implémenté.
- **Hot-reload au push** — les projets ne se rechargent pas en temps réel quand quelqu'un édite. C'est un `git push` → deploy → URL mise à jour. Live collaborative editing → Google Docs/Drive.
- **Versioning des projets déployés** — pas de rollback automatique. Git est le rollback.
- **Drive sync autres teams** (Personal, AI Brussels, WWJD, Blockparty) — déféré, Acme configuré et fonctionnel.
- **MCP tool registry public** — MCP-V2-01 dans REQUIREMENTS.md.
- **Notion/Slack/Linear connectors** — INT-V2-01..05.

</deferred>

---

*Phase: 05-plateforme-projets-equipe*
*Context gathered: 2026-05-06*
