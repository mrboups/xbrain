---
phase: 4
phase_name: Consolidation MCP Frontends + Intégrations Avancées
phase_slug: consolidation-mcp-frontends-et-integrations
date: 2026-05-05
goal_from_roadmap: |
  Fermer la boucle multi-frontend des MCP tools (LibreChat + agent-runtime appellent
  réellement la gateway), supprimer les frictions résiduelles (Open WebUI logging,
  latence Drive sync), livrer le dernier MCP tool requirement (deck-service), et
  ouvrir le mapping Drive multi-dossier par équipe — sans ajouter de service lourd.
requirements_in_scope:
  - MEM-04   # Open WebUI conversations correctement persistées via memory-api (fix Phase 2 résiduel)
  - MCP-05   # tool call from LibreChat (configuration librechat.yaml mcpServers)
  - MCP-06   # tool call from agent-runtime via gateway (LangGraph tool wrapper)
  - MCP-07   # pitch deck editor MCP tool (deck-service, déféré de Phase 3)
  - INT-02   # Drive sync incremental — passage du polling 5min aux push webhooks
  - INT-03   # Multi-folder per team mapping (élargissement de l'admin endpoint Phase 3)
---

# Phase 4 — Consolidation MCP Frontends + Intégrations Avancées — CONTEXT

## Goal en une phrase

**Phase 3 a posé les briques (gateway, sidecars, drive-sync, neo4j) ; Phase 3.5 a réparé le cœur de la gateway. Phase 4 fait que tout cela soit réellement utilisable depuis n'importe quel frontend ou agent, comble les MCP-tools manquants, et améliore la latence/expressivité des intégrations Drive — sans ajouter de service lourd qui mettrait la VM e2-standard-2 sous pression.**

## Locked Decisions

### Décision 1 — Périmètre court, focus consolidation

Phase 4 livre **6 plans** maximum. Pas de nouvelle DB, pas de nouveau frontend, pas de nouveau modèle d'extraction. Toute Phase 4 doit pouvoir tenir dans `~3 GB headroom restant` sur la VM. Si un plan menace ce budget, il est sorti et reporté en Phase 5.

### Décision 2 — Open WebUI conversation flow : upsert silencieux côté memory-api

**Problème (Trou résiduel Phase 2)** : `apps/openwebui-pipeline/app/pipelines/xbrain_logger.py` appelle `mem.post_message(...)` mais ne crée jamais de conversation au préalable. Résultat : `POST /v1/messages` retourne 404 "conversation not found" et le chat OWUI ne se loggue pas dans memory-api (LibreChat passe via le bridge Mongo qui crée la row, donc lui marche).

**Décision** : implémenter un **upsert silencieux côté memory-api** sur `POST /v1/messages` :
- Si `conversation_id` n'existe pas, créer la row avec `title=null, source=<source du message>, sub=<JWT principal>, team_scope=<X-Team-Scope>` puis insérer le message.
- Idempotent : on s'appuie sur le UUID v5 déterministe que la pipeline calcule déjà (`_make_conversation_id(sub, messages)` dans `apps/openwebui-pipeline/app/main.py:120`) — deux upserts simultanés = 1 row gagnante via `INSERT ... ON CONFLICT (id) DO NOTHING`.
- Plus simple que de faire un `POST /v1/conversations` explicite côté pipeline (évite race conditions + double round-trip + retry-safety).
- N'altère PAS le flux LibreChat (le bridge crée explicitement la conversation, donc l'upsert est un no-op pour lui).
- Ajout d'un test : POST `/v1/messages` avec `conversation_id` inconnu → conversation créée, message inséré, 201.

**Alternative rejetée** : faire que la pipeline appelle `post_conversation()` au début de chaque chat. Rejeté car (a) `post_conversation()` ne supporte pas un `id` deterministe en input (il génère le UUID côté memory-api), donc il faudrait d'abord refactorer le contract, et (b) ça ajoute une race condition entre deux requêtes parallèles sur la même conversation.

### Décision 3 — LibreChat → MCP gateway : configuration via `mcpServers` dans `librechat.yaml`

**Problème** : `infrastructure/librechat/librechat.yaml` ne déclare actuellement aucun `mcpServers`. La gateway Phase 3.5 marchera côté HTTP, mais LibreChat n'a aucun moyen de la découvrir.

**Décision** : Ajouter dans `librechat.yaml` un bloc `mcpServers:` qui pointe vers `mcp-gateway:8080` via le transport `streamable-http` (pas stdio, parce que LibreChat tourne dans son propre conteneur et n'exécute pas de processus enfants). LibreChat v0.8.5 supporte ce pattern depuis Q4 2025.

```yaml
mcpServers:
  xbrain:
    type: streamable-http
    url: http://mcp-gateway:8080/mcp/aggregate
    headers:
      X-Team-Scope: "${LIBRECHAT_DEFAULT_TEAM_SCOPE}"  # injecté par container env
      X-User-Sub: "{{LIBRECHAT_USER_SUB}}"             # template variable LibreChat
```

**Implication côté gateway** : `mcp-gateway` doit exposer un **endpoint MCP agrégé** `GET /mcp/aggregate` qui présente l'union des tools des sidecars enregistrés comme un seul serveur MCP. Sinon LibreChat ne voit qu'une URL = un serveur. Ce endpoint sera ajouté en Phase 4 (NOT en Phase 3.5 qui ne s'occupe que du `POST /tools/{name}/call`).

**Auth pattern** : la gateway doit aussi accepter un header `X-LibreChat-User-Email` que LibreChat injecte (template var natif), et faire la résolution `email → sub` via memory-api (même pattern que `librechat-bridge`).

### Décision 4 — Agent-runtime → MCP gateway : LangGraph `Tool` wrapper

**Problème** : `apps/agent-runtime/app/tools/` contient seulement `document_loader.py` et `extract_facts.py` — aucun appel à la gateway MCP. Les agents Phase 2 ne peuvent donc PAS utiliser les MCP tools. MCP-06 ("tool call from agent-runtime") n'est pas réellement satisfait — Phase 3 ne l'a couvert que via la gateway HTTP, pas via une intégration LangGraph.

**Décision** : Créer `apps/agent-runtime/app/tools/mcp_gateway_client.py` qui :
1. Au démarrage, appelle `GET /tools` sur la gateway pour découvrir les tools enregistrés.
2. Pour chaque tool découvert, génère dynamiquement un `langgraph.prebuilt.ToolNode`-compatible callable (avec input/output schema dérivé du MCP `inputSchema`).
3. Injecte un Bridge JWT signé + `X-Team-Scope` à chaque appel `POST /tools/{name}/call`.
4. Cache les tools en mémoire ; refresh toutes les 5min OU sur signal (futur).
5. Exposé via `get_mcp_tools(team_scope: str) -> list[Tool]` que les graphes LangGraph importent.

**Garde-fou** : si la gateway est down au démarrage, l'agent-runtime ne crash PAS — `get_mcp_tools()` retourne liste vide + log warning. Les graphes existants (Phase 2) ne dépendent pas des MCP tools, donc dégradation gracieuse.

### Décision 5 — Drive push webhooks : remplacement du polling 5min

**Problème** : `apps/drive-sync/app/drive_poller.py` poll toutes les 5min (configurable via `POLL_INTERVAL_SECONDS`). Latence moyenne save→queryable = 2.5min + extraction. Acceptable pour Phase 3, sub-optimal pour usage quotidien (un user qui sauve un doc et veut chatter dessus immédiatement).

**Décision** : Implémenter Google Drive **`files.watch` push notifications** :
- Au démarrage du drive-sync, pour chaque mapping actif, appeler `files.watch(fileId=root_folder_id)` avec `address=https://api.dejavu.cat/v1/drive-webhook` et `expiration=24h` (Google max 7 jours).
- Nginx route `/v1/drive-webhook` → drive-sync container (nouveau endpoint `POST /webhook` exposé sur port 8200).
- À réception d'une notification, drive-sync déclenche immédiatement un `process_changes(team_scope)` au lieu d'attendre le tick suivant.
- **Polling 5min reste en place comme safety-net** : si Google rate une notification (réseau, expiration channel, etc.), le poller rattrape. Latence pire cas reste 5min ; latence cas nominal devient ~5-10s.
- Channel renewal : un task périodique (toutes les 12h) renouvelle les channels avant expiration.

**Persistence** : nouvelle table `drive_watch_channels (channel_id, resource_id, mapping_id, expires_at)` — migration Alembic 0005.

**Auth webhook** : Google envoie `X-Goog-Channel-Token` (configurable) — drive-sync vérifie ce token contre celui stocké en DB. Si invalide, 401.

**Pas de nouveau service** : le webhook endpoint vit dans le container `drive-sync` existant (FastAPI ajouté par-dessus le scheduler). RAM delta : ~10MB (FastAPI + uvicorn déjà en RAM si on factorise).

### Décision 6 — Multi-folder Drive mapping par équipe

**Problème** : `team_drive_mappings` (migration 0004) enforce 1 dossier max par team via une `UNIQUE(team_scope)`. Limite réelle : une équipe a souvent un dossier "Marketing", un dossier "Engineering", un dossier "Sales" — pas un seul dossier racine.

**Décision** : Migration Alembic 0006 :
- Drop `UNIQUE(team_scope)`.
- Add `UNIQUE(team_scope, folder_id)`.
- Add nullable `project_scope` column (permet de mapper "Marketing folder → team:acme + project:marketing", "Sales folder → team:acme + project:sales").
- Endpoint `POST /v1/admin/drive-mapping` accepte un nouveau body `{team_scope, folder_id, project_scope?}`. Si project_scope fourni, les facts ingérés héritent du `project_scope`.
- `GET /v1/admin/drive-mapping?team_scope=...` retourne la liste des mappings pour cette team (au lieu d'un seul).
- `DELETE /v1/admin/drive-mapping/{mapping_id}` pour retirer un dossier.

**Watch channels alignés** : un watch par mapping (et non par team).

**Pas d'impact RAM**, juste un refactor schéma + endpoints.

### Décision 7 — deck-service MCP tool (slide generator)

**Problème** : MCP-07 ("Pitch deck editor service is available as an MCP tool") était dans le scope Phase 3 mais déféré explicitement (cf. `03-CONTEXT.md` "Deferred to Phase 4"). Il manque pour fermer le décompte des 12 requirements Phase 3.

**Décision** : Créer `apps/mcp-deck/` — sidecar Python FastMCP qui expose 2 tools :
1. **`deck_create`** : input `{title: str, sections: list[{heading: str, bullets: list[str]}]}` → génère un fichier `.pptx` via `python-pptx`, l'upload dans MinIO sous `decks/{team_scope}/{deck_id}.pptx`, et retourne l'URL signée + un `memory_item_id` (l'asset est indexé dans memory-api avec le tagging contract complet).
2. **`deck_update`** : input `{deck_id: str, sections: list[...]}` → re-génère et remplace le fichier dans MinIO. Garde la version précédente (versioning natif MinIO si activé, sinon copie `deck_id-v{n}.pptx`).

**Out of scope Phase 4** :
- Édition rich-text inline (formules, charts complexes) — stays template-based bullets.
- Édition collaborative live (Google Slides handle this).
- Vraie UI d'édition de deck — l'utilisateur édite via prompt LLM, pas via clicks.

**Stack** : `python-pptx==1.0.x` (Apache 2.0), `boto3` pour MinIO. ~80 MB RAM idle.

**Enregistrement** : ajout au `register-mcp-tools.sh` pour qu'il soit visible dès le boot.

### Décision 8 — Pas de nouveau service lourd

**Exclu de Phase 4** :
- **OCR pour scanned PDFs (vision LLM)** : haute latence par doc (~20s+), coût LLM significatif, faible volume attendu en v1. Reporté à V2 ou Phase 5 si demande utilisateur émerge.
- **MCP tool discovery from external registries** : explicitement "MCP-V2-01" dans REQUIREMENTS.md.
- **Apache AGE migration** : Neo4j tient parfaitement la charge actuelle (~800 MB RAM observed Phase 3).
- **mcp-proxy adoption** : custom gateway est mature après Phase 3.5.
- **Schema evolution UI per team** : "MEM-V2-01".
- **Notion/Slack/Linear/GitHub connectors** : tous classés V2 (cf. INT-V2-01..05).
- **Frontend changes** : ni LibreChat upgrade, ni Open WebUI upgrade (les versions Phase 1 marchent).

## Architecture Phase 4

```
                  ┌─────────────────────┐
                  │ LibreChat v0.8.5    │
                  │  + librechat.yaml   │ ◀──────── ajout mcpServers (Décision 3)
                  │    mcpServers:      │
                  │      xbrain ─────┐  │
                  └──────────────────┼──┘
                                     │ MCP streamable-http
                                     ▼
                  ┌─────────────────────────────────┐
                  │ mcp-gateway (Phase 3.5 + Phase 4)│
                  │  • POST /tools/{name}/call      │ (existant Phase 3.5)
                  │  • GET  /mcp/aggregate          │ (nouveau Phase 4 — MCP server agrégé)
                  │  • GET  /tools                  │ (existant)
                  └─────────────────────────────────┘
                                     │
            ┌───────────┬─────────────┼──────────────┬─────────────┐
            ▼           ▼             ▼              ▼             ▼
       mcp-scraper   mcp-drive    mcp-calendar   mcp-deck       (futur)
                     -read                       (NEW Phase 4)
                                                       │
                                                       ▼
                                                ┌──────────┐
                                                │  MinIO   │ ◀── stockage .pptx
                                                └──────────┘

  ┌──────────────┐                                     ▲
  │ Open WebUI   │ ──── pipeline ──── memory-api ──────┤
  │  + pipeline  │      (upsert      (Décision 2)      │
  └──────────────┘       silencieux)                   │
                                                        │
                                                        │
  ┌──────────────┐                                      │
  │ agent-runtime│ ── mcp_gateway_client.py ────────────┘
  │  LangGraph   │       (Décision 4 — wrapper LangGraph)
  └──────────────┘

  ┌──────────────┐         Google Drive
  │ drive-sync   │ ◀────── files.watch (Décision 5)
  │  • polling   │            push notifications
  │  • webhook   │
  │    (NEW)     │ ───────► /v1/drive-webhook (via nginx)
  └──────────────┘
       │
       ▼
  team_drive_mappings (Multi-folder, Décision 6)
  drive_watch_channels (NEW table, migration 0005)
```

## Memory Budget (nouveaux services Phase 4)

| Service | mem_limit | Real expected | Notes |
|---|---|---|---|
| mcp-deck (Python FastMCP + python-pptx) | 192m | ~80 MB | Sidecar léger, charge python-pptx à la demande |
| drive-sync webhook addition | (existant, +0m) | +10 MB | FastAPI ajouté au container drive-sync existant |
| **Phase 4 total delta** | **~192m hard caps** | **~90 MB real** | |

**Combined** : Phase 3 réel ~4.9 GB → Phase 4 réel ~5.0 GB sur VM 7.8 GB → headroom **~2.8 GB**. Pas d'upgrade VM nécessaire.

Note : mcp-gateway, agent-runtime et memory-api gagnent du code mais restent dans leur mem_limit existant (la gateway agrégée + tools wrapper ajoutent <20 MB chacun).

## Canonical Refs

- `.planning/ROADMAP.md` — Phase 4 goal + success criteria + entry gate (à compléter après ce CONTEXT)
- `.planning/REQUIREMENTS.md` — MEM-04, MCP-05, MCP-06, MCP-07, INT-02, INT-03 (élargi)
- `.planning/phases/03-graphe-extraction-integrations/03-CONTEXT.md` — section "Deferred to Phase 4" (origine deck-service, multi-folder, push webhooks)
- `.planning/phases/03.5-mcp-gateway-et-fixes/03.5-CONTEXT.md` — gateway client MCP stateful (préreq Phase 4)
- `apps/openwebui-pipeline/app/pipelines/xbrain_logger.py` — site du Trou 1 (logging sans création de conversation)
- `apps/openwebui-pipeline/app/main.py:120` — `_make_conversation_id` (UUID v5 déterministe à réutiliser pour upsert)
- `apps/memory-api/app/routes/messages.py` (à confirmer chemin) — site du fix upsert silencieux
- `apps/memory-api/alembic/versions/0004_neo4j_outbox.py` — référence pour migration 0005 (drive_watch_channels) et 0006 (multi-folder)
- `apps/mcp-gateway/app/main.py` — site de l'endpoint `/mcp/aggregate` à ajouter
- `apps/agent-runtime/app/tools/` — site du nouveau `mcp_gateway_client.py`
- `apps/drive-sync/app/drive_poller.py` — site de l'ajout webhook + multi-mapping
- `apps/memory-api/app/routes/admin_drive.py` — site du refactor multi-folder admin endpoints
- `infrastructure/librechat/librechat.yaml` — site de l'ajout `mcpServers`
- `infrastructure/scripts/register-mcp-tools.sh` — ajout enregistrement deck-service
- LibreChat MCP docs : https://www.librechat.ai/docs/configuration/librechat_yaml/mcp_servers
- Google Drive push notifications : https://developers.google.com/drive/api/guides/push
- python-pptx docs : https://python-pptx.readthedocs.io/

## Code Context — Reusable Assets

| Asset | Reuse for |
|---|---|
| `apps/openwebui-pipeline/app/main.py:_make_conversation_id` | UUID v5 deterministic id pour upsert silencieux memory-api |
| `apps/memory-api/app/routes/admin_drive.py` (Fernet pattern) | Refactor multi-folder garde le même chiffrement OAuth |
| `apps/agent-runtime/app/auth.py` (`make_bridge_jwt`) | Bridge JWT signé pour mcp_gateway_client.py |
| `apps/mcp-scraper/` (FastMCP pattern) | Modèle de référence pour mcp-deck |
| `apps/mcp-drive-read/` (Google API auth pattern) | Pattern OAuth pour deck si on veut Google Slides plus tard |
| `apps/drive-sync/app/drive_poller.py` (`process_changes`) | Réutilisé tel quel par le webhook handler (juste déclenché ad-hoc) |
| `infrastructure/scripts/register-mcp-tools.sh` | Étendre pour enregistrer mcp-deck idempotent |
| `infrastructure/scripts/verify-phase3.sh` (parser fix Phase 3.5) | Pattern parser à dupliquer pour `verify-phase4.sh` |
| Phase 2 alembic style | Migrations 0005 (drive_watch_channels) + 0006 (multi-folder) |

## New Services to Build

1. **`apps/mcp-deck/`** — Python FastMCP sidecar (port 8103) — slide generator (deck_create, deck_update)
2. **`infrastructure/docker-compose.yml`** — ajout du service `mcp-deck` (1 nouveau container, ~80 MB)

## Modifications de services existants

1. **`apps/memory-api`** — upsert silencieux conversations + multi-folder admin endpoints + nouvelle table `drive_watch_channels` (migrations 0005, 0006)
2. **`apps/mcp-gateway`** — endpoint `GET /mcp/aggregate` (MCP server unifié pour LibreChat)
3. **`apps/agent-runtime`** — nouveau `tools/mcp_gateway_client.py` + intégration dans graphes existants (optionnelle, dégradation gracieuse)
4. **`apps/drive-sync`** — ajout endpoint `POST /webhook` + boucle de renewal channels + support multi-mapping par team
5. **`infrastructure/librechat/librechat.yaml`** — bloc `mcpServers`
6. **`infrastructure/scripts/register-mcp-tools.sh`** — enregistrement deck

## Plans prévus

| Plan | Sujet | Effort estimé |
|---|---|---|
| 04-01-PLAN.md | memory-api upsert silencieux conversations sur POST /v1/messages + tests | S (1 fichier route + test) |
| 04-02-PLAN.md | mcp-gateway endpoint `GET /mcp/aggregate` (serveur MCP agrégé pour LibreChat) | M (nouveau MCP server façade) |
| 04-03-PLAN.md | LibreChat config `mcpServers` + smoke test E2E LibreChat → gateway → scraper | S (config + verification) |
| 04-04-PLAN.md | agent-runtime `mcp_gateway_client.py` (LangGraph Tool wrapper) + intégration ingestion graph | M (nouveau module + tests) |
| 04-05-PLAN.md | Drive push webhooks : migration 0005 (drive_watch_channels) + endpoint POST /webhook + channel renewal loop | L (Google API + crypto + DB + scheduler) |
| 04-06-PLAN.md | Multi-folder Drive mapping : migration 0006 + refactor admin_drive.py + drive-sync multi-loop | M (schema + endpoints + scheduler) |
| 04-07-PLAN.md | mcp-deck sidecar (deck_create/deck_update via python-pptx + MinIO upload + memory-api index) | M (nouveau service complet) |
| 04-08-PLAN.md | Update register-mcp-tools.sh + verify-phase4.sh + UAT | S (scripts + UAT manuel) |

**Total : 8 plans, dont 4 small + 4 medium-large.** Pas de plan XL — cohérent avec la philosophie "consolidation" de Phase 4.

## Entry Gate

| Gate | Status |
|---|---|
| Phase 3.5 fully shipped (mcp-gateway client MCP stateful + verify-phase3 parser fix) | À VALIDER avant `/gsd:plan-phase 4` |
| VM e2-standard-2 stable, headroom ≥ 2 GB observed via `docker stats` | À CONFIRMER (Phase 3 réel ~4.9 GB) |
| Pas de regression Phase 1/2/3 (verify-phase1/2/3 PASS) | À CONFIRMER |

## Open Questions for Planner (research will resolve)

- LibreChat v0.8.5 supporte-t-il vraiment le transport `streamable-http` pour MCP, ou faut-il un transport `http` plus simple (anciens noms) ? Vérifier sur la doc LibreChat actuelle, sinon downgrade `mcp-gateway` vers SSE/HTTP simple.
- Google `files.watch` : limite quotidienne de channels par projet GCP ? (1M/day par défaut, OK pour notre échelle)
- Gestion `404 / channelId already exists` au renewal : faut-il purger systématiquement avant renew ?
- python-pptx supporte-t-il les templates `.potx` chargés depuis MinIO, ou doit-on packager des templates dans l'image ?
- Pattern d'agrégation MCP : un seul serveur façade qui multiplexe N sidecars — y a-t-il un exemple canonique dans le SDK MCP Python, ou est-ce à coder de zéro ?
- agent-runtime tool refresh : push (websocket from gateway) vs pull (5min polling) ? Probablement pull en v1 par simplicité.

## Success Criteria (à reprendre dans ROADMAP)

1. Un user qui chatte dans LibreChat peut écrire "scrape https://example.com" et le LLM appelle réellement `mcp-scraper` via la gateway, retourne le contenu, et l'appel apparaît dans `audit_log` avec `team_scope` + `user_sub` corrects.
2. Un user qui chatte dans Open WebUI dans une conversation neuve voit le chat correctement loggé dans memory-api (`POST /v1/messages` retourne 201, conversation row créée silencieusement) — vérifié via SELECT direct sur la table `conversations`.
3. Un agent LangGraph (`ingestion-agent`) peut appeler `mcp-scraper` ou `mcp-drive-read` via le wrapper `mcp_gateway_client.py` et l'output atterrit dans memory-api avec le tagging contract complet (`source: "mcp:scraper"`, etc.).
4. Un fichier sauvegardé dans Drive devient queryable dans memory-api en moins de 30 secondes en cas nominal (push webhook), avec le polling 5min comme fallback en cas de manqué notification.
5. Un admin peut mapper 2+ dossiers Drive distincts à la même équipe (avec project_scope distinct chacun) via `POST /v1/admin/drive-mapping`, et `GET /v1/admin/drive-mapping?team_scope=...` retourne la liste des mappings.
6. Un user peut prompter le LLM "génère-moi un deck de pitch pour X" et un fichier `.pptx` est créé dans MinIO + indexé dans memory-api + URL signée retournée dans le chat.
