# xbrain-memory

Shared memory abstraction for xbrain — Pydantic types + abstract `MemoryProvider` interface.

## Why

memory-api Phase 2 + agent-runtime + future services all need to read/write memory without knowing whether the backend is mem0, native Postgres+pgvector, Zep, or whatever comes next. This package is the contract.

## Install (from memory-api or other apps)

In `pyproject.toml` of the consumer:
```toml
dependencies = [
  "xbrain-memory @ file:///app/packages/memory-models",
]
```

Or via pip:
```bash
pip install -e ../../packages/memory-models
```

## Usage

```python
from xbrain_memory import MemoryProvider, MemoryItem, TruthLevel
from xbrain_memory.providers.native_stub import NativeStubProvider

provider: MemoryProvider = NativeStubProvider()

await provider.upsert(MemoryItem(
    id="",
    team_scope="team-a",
    content="Important fact",
    source="agent:ingestion-v1",
    created_at=now(), updated_at=now(),
))

hits = await provider.search(
    "Important",
    team_scope="team-a",
    truth_level_min=TruthLevel.WORKING,
    limit=5,
)
```

## Implementations

- `NativeStubProvider` (in-process dict — tests, bootstrap)
- `Mem0Provider` (Plan 02-03, only if spike GO)
- `NativeProvider` (Plan 02-03, Postgres + Qdrant direct — fallback)

## Contract tests

`tests/test_provider_contract.py` runs the same assertions against any provider.
Add a new provider by appending to `PROVIDERS_TO_TEST` in `tests/conftest.py` (1 line).

## Invariants enforced by every implementation

1. **Team isolation** : `search`/`get`/`update`/`delete` with `team_scope=A` MUST NOT return or affect items belonging to `team_scope=B`, even if the underlying backend supports cross-tenant queries.
2. **truth_level filter** : `search(truth_level_min=X)` MUST return only items where `item.truth_level >= X`.
3. **Idempotent delete** : deleting a non-existent (or wrong-team) item is a no-op, never raises.
