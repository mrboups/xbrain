---
phase: 2
phase_name: Mémoire Intelligente + Agents
created: 2026-05-03
author: orchestrator (rédigé directement, comme Phase 1 — pas de subagent researcher)
sources:
  - D:/VSC/xbrain/CLAUDE.md
  - D:/VSC/xbrain/.planning/REQUIREMENTS.md
  - D:/VSC/xbrain/.planning/ROADMAP.md (Phase 2 success criteria)
  - C:/Users/userx/.claude/projects/D--VSC-xbrain/memory/project_xbrain_memory_layer_decision.md
  - Phase 1 codebase (memory-api, librechat-bridge, openwebui-pipeline)
---

# Phase 2 — RESEARCH

7 questions critiques qui débloquent le planning. Phase 2 ajoute **l'intelligence** par-dessus le scaffold Phase 1 (mem0 + truth-level + LangGraph + RAG + Langfuse). 28 requirements couverts.

---

## Q1 — Spike mem0 vs native vs Zep (LE choix structurant)

### Constat post-Phase 1

Le RESEARCH de Phase 1 a tranché sur le papier : **mem0 + memory-api natif** (cf. memory `project_xbrain_memory_layer_decision.md`). Mais cette décision était basée sur de la doc, pas sur du code qui tourne. Phase 2 démarre par un **spike d'1 jour** pour valider en pratique.

### Critères de choix (à mesurer pendant le spike)

| Critère | Poids | mem0 (1.x) | native (Postgres+pgvector+code) | Zep/Graphiti |
|---|---|---|---|---|
| **Truth-level state machine fit** | ★★★ | mem0 = timestamped facts, **PAS** un workflow d'état explicite. Faut wrapper. | natif = full control, on code la state machine | Zep a "fact accuracy" (pas de truth-level) |
| **Team isolation native** | ★★★ | mem0 a `user_id` mais pas `team_id` first-class. Workaround : encoder team dans user_id. | natif = WHERE team_scope partout (déjà fait Phase 1) | Zep multi-user OK |
| **Conflict resolution / versioning** | ★★ | mem0 v1+ versionne les facts via timestamp + `metadata.timestamp` param | natif = on code (non-trivial) | Zep "fact accuracy" + memory updates |
| **Embeddings + retrieval** | ★★ | mem0 abstrait Qdrant/Chroma/pgvector | natif = qdrant-client direct (déjà fait P1 schema) | Zep utilise Postgres+pgvector |
| **Maturité** | ★★ | 40k★, alpha→stable, breaking changes risque | natif = 0 deps externes nouvelles | Zep 2k★, plus jeune |
| **Effort intégration** | ★ | `pip install mem0ai`, ~1 jour | ~3-5 jours code | ~2-3 jours |
| **Observabilité** | ★ | mem0 a peu de hooks, métriques limitées | natif = on instrumente comme on veut | Zep dashboard SaaS-first |

### Méthodologie spike

Plan 02-01 (1 jour max) :
1. **Setup local** : `pip install mem0ai`, container Postgres+pgvector + Qdrant local
2. **Test 1** : ingest 100 faits avec team_scope encoded → query par team, vérifier 0 leak
3. **Test 2** : update un fait, vérifier versioning timestamp accessible
4. **Test 3** : essayer de wrapper le truth-level state machine par-dessus mem0 → mesurer le code "glue" requis
5. **Test 4** : performance — 1000 facts, mesurer latence retrieval P95
6. **Decision matrix scoring** + écrire `02-SPIKE-RESULT.md` (go mem0 / go native / hybrid)

### Pre-decision (si spike confirme la doc Phase 1)

**Recommandation par défaut** : **mem0 derrière `MemoryProvider` interface**, avec :
- `team_scope` encoded comme `user_id = "team:{slug}:project:{slug}"` (workaround)
- Truth-level state machine **GARDÉ NATIF** dans memory-api Python (pas dans mem0)
- mem0 = facts storage + retrieval ; memory-api = workflow + audit + team-scope enforcement

Si le spike révèle que la latence ou le team-scope workaround sont blockers : **fallback Postgres+pgvector natif** (memory-api gagne 3-5j d'effort mais garde 100% control).

[OPEN-1] : Le spike Plan 02-01 doit produire un `02-SPIKE-RESULT.md` clair avec décision GO/NO-GO mem0. Toutes les autres plans assument l'interface `MemoryProvider` donc le swap mem0↔native est local au plan 02-03.

---

## Q2 — `MemoryProvider` interface design

### Objectif

Permettre swap mem0 ↔ native ↔ Zep en changeant 1 ligne d'env (`MEMORY_BACKEND=mem0|native|zep`). Toutes les routes memory-api appellent l'interface, pas l'impl.

### Module + interface (Python)

`/packages/memory-models/xbrain_memory/__init__.py` :

```python
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class MemoryItem(BaseModel):
    """Un fait extrait, indépendant du backend."""
    id: str
    team_scope: str
    project_scope: str | None
    content: str
    metadata: dict[str, Any]
    embedding: list[float] | None  # None si backend ne le stocke pas explicit
    truth_level: str
    confidence: float
    source: str
    created_at: datetime
    updated_at: datetime


class SearchHit(BaseModel):
    item: MemoryItem
    score: float


class MemoryProvider(ABC):
    """Backend abstraction — mem0, native, Zep, etc."""

    @abstractmethod
    async def upsert(self, item: MemoryItem) -> str: ...

    @abstractmethod
    async def get(self, item_id: str, team_scope: str) -> MemoryItem | None: ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        team_scope: str,
        project_scope: str | None = None,
        truth_level_min: str | None = None,
        limit: int = 10,
    ) -> list[SearchHit]: ...

    @abstractmethod
    async def update(self, item_id: str, team_scope: str, patch: dict) -> MemoryItem: ...

    @abstractmethod
    async def delete(self, item_id: str, team_scope: str) -> None: ...

    @abstractmethod
    async def history(self, item_id: str, team_scope: str) -> list[MemoryItem]:
        """Versions historiques d'un fait (versioning)."""

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...
```

**Truth-level NE PASSE PAS** par cette interface. Le state machine reste dans memory-api (lecture/écriture Postgres `messages.truth_level` + audit). MemoryProvider stocke juste la valeur courante via le champ `truth_level` du `MemoryItem`.

### Implémentations Phase 2

- `Mem0Provider` (plan 02-03) — wrappe `mem0.MemoryClient`
- `NativeProvider` (fallback) — code direct sur Postgres+Qdrant (existe déjà partiellement Phase 1)

### Tests

- `tests/test_provider_contract.py` parameterized sur `[Mem0Provider, NativeProvider]` → mêmes assertions, différentes impls
- Garantit la swappability

---

## Q3 — Truth-level promotion workflow

### State machine

```
EPHEMERAL ──┬──> WORKING ──┬──> VALIDATED ──┬──> CANONICAL ──┬──> PUBLIC
            │              │                │                │
            └─ rejected ────┴─ rejected ─────┴─ rejected ─────┴─ rejected (back to WORKING)
```

### Règles

1. Phase 1 : tous les chats arrivent à `EPHEMERAL` automatiquement
2. **Promotion `EPHEMERAL → WORKING`** : auto par memory-api après N>=3 messages dans la conv (signe que l'utilisateur a engagé le contenu)
3. **Promotion `WORKING → VALIDATED`** : **demande humaine** via UI admin Open WebUI (Pipeline custom). Cible : 1 fait précis, justification obligatoire.
4. **Promotion `VALIDATED → CANONICAL`** : 2 admins distincts approuvent (4-eyes principle).
5. **Promotion `CANONICAL → PUBLIC`** : 1 admin org. Cross-team visibility.
6. **Rejection** à n'importe quel niveau → retour à `WORKING` avec note `validation_status=rejected` + raison.
7. **JAMAIS** de PATCH direct sur `truth_level` via API. **Tous** les changements passent par `POST /v1/promotions` avec workflow.

### Tables Postgres (migration 0002)

```sql
CREATE TABLE promotions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID NOT NULL REFERENCES messages(id),
  from_level TEXT NOT NULL,
  to_level TEXT NOT NULL,
  proposed_by UUID NOT NULL REFERENCES users(id),
  approved_by UUID REFERENCES users(id),
  status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','auto')),
  rationale TEXT,
  rejection_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE INDEX idx_promotions_message ON promotions(message_id);
CREATE INDEX idx_promotions_status ON promotions(status) WHERE status = 'pending';
```

### Endpoints memory-api Phase 2

- `POST /v1/promotions` (user) — propose promotion d'un fait
- `GET /v1/promotions?status=pending&team_scope=X` (admin) — pending queue
- `POST /v1/promotions/{id}/approve` (admin) — approuve
- `POST /v1/promotions/{id}/reject` (admin) — rejette + raison
- `GET /v1/messages/{id}/promotion-history` — full audit trail

### UI

**Open WebUI Pipeline custom** "Promotion Manager" — affiche queue pending, bouton approve/reject, modal rationale. Pas de nouveau frontend custom (réutilise OWUI pour pas inventer une admin UI).

[OPEN-2] : Mécanisme exact OUI Pipeline pour UI custom. Possiblement OUI Functions ou OUI Apps SDK. À valider plan 02-04.

---

## Q4 — LangGraph integration (agents + HITL)

### Stack

- `langgraph==1.1.0` (Python lib MIT)
- Checkpointer : `langgraph.checkpoint.postgres.PostgresSaver` (utilise notre Postgres existant, ajout 1 schema)
- HITL via `interrupt_after=[node]` ou `Command(goto=...)` pattern

### Pattern checkpointing

```python
from langgraph.graph import StateGraph
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string(settings.DATABASE_URL)

graph = StateGraph(IngestionState)
graph.add_node("extract_facts", extract_node)
graph.add_node("await_human_review", lambda s: s)  # HITL pause point
graph.add_node("write_to_memory", write_node)

graph.add_edge("extract_facts", "await_human_review")
graph.add_edge("await_human_review", "write_to_memory")
graph.set_entry_point("extract_facts")
graph.set_finish_point("write_to_memory")

# Compile with interrupt before HITL node
app = graph.compile(checkpointer=checkpointer, interrupt_before=["await_human_review"])

# Run — pauses at HITL
config = {"configurable": {"thread_id": str(conversation_id)}}
for event in app.stream({"document_url": url}, config):
    print(event)

# Later: resume after human approved
app.invoke(None, config)  # resumes from checkpoint
```

### Service `agent-runtime`

Nouveau container Python `apps/agent-runtime/` :
- FastAPI HTTP API (`POST /v1/agents/run`, `GET /v1/agents/{thread_id}/state`, `POST /v1/agents/{thread_id}/resume`)
- Hosts les graphs LangGraph
- Auth : same dual JWT pattern que memory-api (Google OIDC + bridge service tokens)
- Talks to memory-api via HTTP, never direct DB

### Agents Phase 2 (3 minimum)

1. **`ingestion-agent`** : URL/PDF → extract facts → HITL approve → write to memory
2. **`fact-validator-agent`** : pour un fait `WORKING`, valide cohérence avec autres faits CANONICAL → propose promotion
3. **`second-opinion-agent`** (CHAT-06) : prompt user envoyé à Claude ET Grok en parallèle → renvoie les 2 réponses + diff highlights

[OPEN-3] : `agent-runtime` mem_limit + scaling. LangGraph est lourd. e2-standard-2 8GB suffira pour 3 agents + petits workloads. Si Phase 3 ajoute plus → upgrade VM ou move agent-runtime sur VM séparée.

---

## Q5 — RAG team-scoped (CHAT-07)

### Objectif

Quand user pose une question dans LibreChat, le system prompt envoyé à Claude inclut **les faits CANONICAL pertinents de la team du user** récupérés via vector search.

### Architecture

```
LibreChat → Claude API
            ↑
            librechat-bridge intercepte AVANT envoi (LibreChat MongoDB change stream)
              │
              └── enrich avec memory-api search?
                  Problème : timing — bridge voit le message APRÈS qu'il soit envoyé.
```

**Vrai problème** : LibreChat envoie directement à Claude API. Pour injecter du contexte AVANT, il faut intercepter côté LibreChat.

### Solutions

| Option | Complexité | Pros | Cons |
|---|---|---|---|
| **A. LibreChat custom endpoint** (proxy via openwebui-pipeline-style) | Haut | Full control | Re-architect LibreChat → Anthropic flow |
| **B. LibreChat Plugin** (server-side code injection) | Moyen | Pattern officiel LibreChat | Plugin API instable v0.8.x |
| **C. Pre-conversation enrichment** (add a system message to conversation BEFORE user types) | Bas | Simple, pas d'intercept | Coût : facts injected au start de chaque conv, peuvent devenir stale |
| **D. RAG via LibreChat's own RAG-API** (qui existe déjà avec pgvector) | Bas | Déjà construit | Notre data n'est pas dans leur pgvector — il faut sync continue |

**Recommandation Phase 2** : **Option C** simplest pour validation, **+ Option B custom-plugin si time permits**. Phase 3 peut faire Option A si nécessaire.

### Implementation Option C

`memory-api` expose `GET /v1/system-prompt?team_scope=X&project_scope=Y` qui retourne :
```json
{
  "system_addendum": "## Team facts (CANONICAL)\n- Fact 1 (id=abc, confidence=0.95)\n- Fact 2 ...\n\nUse only these as ground truth..."
}
```

`librechat-bridge` (Phase 1) ne fait que LIRE les chats. On ajoute un **2nd sidecar** OU on étend le bridge pour ALSO **PUSH** au moment de la création de conversation : injecter un system message avec les facts CANONICAL pertinents.

[OPEN-4] : Detection : "facts pertinents" = comment ? Top 5 par embedding similarity au TITRE de la conv ? Au premier user message ? Tous les CANONICAL si <50 ?

---

## Q6 — Open WebUI Pipeline pour promotion UI (custom admin)

### Constat

OWUI Pipelines (Phase 1 = chat logger) peuvent **AUSSI** servir de UI custom via leurs webhooks. Plus moderne : **OWUI Functions** ou **OWUI Apps SDK** (depuis v0.6+).

### Apps SDK (preferred)

Open WebUI Apps SDK (mcp-server-dev plugin existant dans .claude/) permet de :
- Render des UI components **dans le chat** (forms, dialogs, dashboards)
- Resources MCP (data fetching)
- Tools MCP (actions)

Pour la promotion workflow :
- User type "promote-fact <id>" → OWUI App ouvre formulaire de proposition
- Admin tape "/promotions-pending" → OWUI App fetch memory-api + render queue avec boutons approve/reject

[OPEN-5] : OWUI Apps SDK est en beta (v0.6+). Risk d'instabilité. Fallback = créer un mini admin UI dans memory-api avec FastAPI + Jinja templates ou htmx.

---

## Q7 — Langfuse self-hosted (OBS-02/03/05)

### Stack

- `langfuse/langfuse:3` (web) + `langfuse/langfuse-worker:3`
- Dependencies : ClickHouse (~1-2 GB RAM, **gros**), Redis 7 (~50 MB), bundled Postgres + MinIO
- License : MIT (core)

### RAM budget impact

Phase 1 : VM e2-standard-2 = 8 GB, used ~2.5 GB. Headroom 5.5 GB.
Phase 2 ajoute :
- `agent-runtime` ~300 MB
- `mem0` (si pas embed dans memory-api) ~200 MB
- Langfuse **3-4 GB** (gros)
- ClickHouse **1-2 GB**

**Total Phase 2 estimé : ~7-8 GB** → on est à la limite e2-standard-2.

### Décisions

**Plan A** : Langfuse sur la même VM Phase 2, surveiller OOM, upgrade vers `e2-standard-4` (16 GB, ~98€/mo) si nécessaire.

**Plan B** : Langfuse sur VM séparée `e2-small` (~12€/mo, 2 GB), partage Postgres principal via tunnel. Total ~62€/mo.

**Recommandation** : **Plan A** Phase 2 entry, monitor 1 semaine, décide upgrade selon `docker stats`. Plan B si la séparation simplifie le restore-test ou la sécurité.

### Intégration

- `langfuse.CallbackHandler` dans LangGraph runs (1 ligne par graph)
- Trace par-conversation, lier `thread_id` LangGraph ↔ `conversation_id` memory-api
- Cost tracking : Langfuse calcule token spend par appel LLM, group_by team_scope

[OPEN-6] : ClickHouse est lourd. Si on sait qu'on aura <100K traces/mois, on peut éviter et faire du Postgres direct. À évaluer.

---

## Items ouverts pour le planner

1. **`[OPEN-1]`** Spike mem0 décide stack memory definitive — Plan 02-01 doit produire `02-SPIKE-RESULT.md`. Si NO-GO mem0 → Plan 02-03 implémente NativeProvider à la place.
2. **`[OPEN-2]`** Open WebUI mécanisme UI custom : Pipeline / Function / Apps SDK ? Plan 02-04 doit trancher après prototype 30 min.
3. **`[OPEN-3]`** `agent-runtime` mem_limit + scaling — bench post-déploiement, possible upgrade VM Phase 2 ou Phase 3.
4. **`[OPEN-4]`** RAG detection des facts pertinents — top 5 par embedding similarity au titre conv ? Plan 02-05 décide stratégie après prototype.
5. **`[OPEN-5]`** OWUI Apps SDK fallback = mini admin UI memory-api FastAPI/htmx. Plan 02-04.
6. **`[OPEN-6]`** Langfuse ClickHouse vs Postgres direct si volume bas.

---

## Pitfalls connus

- **mem0 versioning timestamp** : v1+ uniquement, anciennes versions ne supportent pas. Pin `mem0ai>=1.0.4`.
- **LangGraph Postgres checkpointer** : le schema crée des tables `checkpoints` qui peuvent grossir vite (1 row par node-step). Vacuum + retention policy obligatoire.
- **Langfuse v3 ClickHouse migration** : v2 → v3 demande data migration. On démarre direct en v3.
- **Open WebUI Apps SDK** : beta, breaking changes possibles. Pin version exact.
- **HITL UX** : si user attend longtemps approval, cookie/session expire. Persistent state via PostgresSaver checkpointer = OK, mais le user doit pouvoir voir "ton agent attend ton input" dans l'UI.

---

## RESEARCH COMPLETE

Recommandations clés :
- **Plan 02-01** = spike mem0 1 jour → décision GO/NO-GO documentée dans `02-SPIKE-RESULT.md`
- **Plan 02-02** = `MemoryProvider` interface dans `/packages/memory-models/` (test avec NativeProvider stub)
- **Plan 02-03** = implémentation Mem0Provider (ou NativeProvider si NO-GO mem0)
- **Plan 02-04** = truth-level promotion workflow + Open WebUI Pipeline UI
- **Plan 02-05** = RAG team-scoped via Option C (system prompt enrichment)
- **Plan 02-06** = LangGraph agent-runtime + 3 agents
- **Plan 02-07** = ingestion-agent (PDF → facts → HITL → memory)
- **Plan 02-08** = second-opinion-agent (Claude || Grok parallel)
- **Plan 02-09** = Langfuse self-hosted + integration LangGraph

[OPEN] items à trancher pendant l'exécution : spike result, OWUI UI mechanism, RAG fact selection strategy, Langfuse ClickHouse-or-not, agent-runtime sizing.
