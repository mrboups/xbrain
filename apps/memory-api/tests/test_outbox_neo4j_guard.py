"""Phase 15 — neo4j_outbox must not grow when Neo4j is not REACHABLE.

The state under test is the one an OSS-light install is actually in: NEO4J_URI and NEO4J_PASSWORD are
BOTH set (compose passes the URI as a bare literal; .env.example ships a fill-me password) and there is
no Neo4j container. Testing a blank-config state instead would prove the guard works for a state the
deployed system can never reach — the exact "the check never traversed the real path" failure this phase
exists to stop.

httpx.ASGITransport does not run the lifespan, so init_driver() never runs here and
app.neo4j_client._driver stays at its module default of None — i.e. "configured but unreachable", which
is precisely the dangerous state. The test that wants a LIVE driver sets one explicitly.
"""

import json
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from xbrain_memory.provider import MemoryProvider

pytestmark = pytest.mark.integration


class _PgOnlyMemoryProvider(MemoryProvider):
    """Writes straight to `memory_items` via the SAME AsyncSession the test's `client`
    fixture overrides `get_session` with — no Qdrant, no embedder.

    The test-suite default backend (MEMORY_BACKEND=stub -> NativeStubProvider) is a pure
    in-process dict and NEVER touches Postgres, so this plan's decisive assertion ("the
    memory item itself must still be written") would be untestable against a real
    `memory_items` row count without a provider that actually persists there. Using the
    same `session` object as `get_session`'s override keeps the upsert + the route
    handler's own audit-write + commit all inside one transaction, which the conftest
    `session` fixture rolls back at teardown.
    """

    def __init__(self, session) -> None:
        self._session = session

    async def upsert(self, item) -> str:
        item_id = item.id or str(uuid4())
        await self._session.execute(
            sa.text("""
                INSERT INTO memory_items
                  (id, team_scope, project_scope, content, metadata,
                   visibility, truth_level, validation_status, confidence, source)
                VALUES
                  (:id, :ts, :ps, :content, CAST(:metadata AS JSONB),
                   :vis, :tl, :vs, :conf, :src)
            """),
            {
                "id": item_id,
                "ts": item.team_scope,
                "ps": item.project_scope,
                "content": item.content,
                "metadata": json.dumps(item.metadata),
                "vis": item.visibility.value,
                "tl": item.truth_level.value,
                "vs": item.validation_status.value,
                "conf": item.confidence,
                "src": item.source,
            },
        )
        return item_id

    async def get(self, item_id, *, team_scope):
        raise NotImplementedError

    async def search(self, query, *, team_scope, project_scope=None, truth_level_min=None, limit=10):
        raise NotImplementedError

    async def update(self, item_id, *, team_scope, patch):
        raise NotImplementedError

    async def delete(self, item_id, *, team_scope):
        raise NotImplementedError

    async def mark_deleted(self, item_id, deleted_at):
        raise NotImplementedError

    async def mark_restored(self, item_id):
        raise NotImplementedError

    async def history(self, item_id, *, team_scope):
        raise NotImplementedError

    async def health(self):
        return {"status": "ok", "backend": "pg-only-test-stub"}


def _install_pg_only_provider(session):
    from app.deps import get_memory_provider
    from app.main import app

    app.dependency_overrides[get_memory_provider] = lambda: _PgOnlyMemoryProvider(session)


async def _noop(*_args, **_kwargs) -> None:
    return None


def _neutralize_fire_and_forget_enrichment(monkeypatch) -> None:
    """upsert_item() fires three unrelated background tasks via asyncio.create_task()
    whenever body.item.content is non-empty (Graphiti enrichment, CRM contact
    extraction, auto-task creation) — each opens its OWN connection from the shared
    `engine` pool via async_session_factory(). They are fail-soft in production and
    orthogonal to what this test verifies (the neo4j_outbox guard), but being
    fire-and-forget they can outlive this test's event loop and leave a broken pooled
    connection behind for the NEXT test to trip over ("Event loop is closed" on
    checkout). Neutralize them so this test only exercises the neo4j_outbox path.
    """
    import app.routes.memory as memory_module

    monkeypatch.setattr(memory_module, "_enrich_with_graphiti", _noop)
    monkeypatch.setattr(memory_module, "_extract_crm_contacts", _noop)
    monkeypatch.setattr(memory_module, "_maybe_create_task_from_action", _noop)


async def _outbox_count(session) -> int:
    return (await session.execute(text("SELECT count(*) FROM neo4j_outbox"))).scalar_one()


async def _memory_item_count(session, item_id: str) -> int:
    return (
        await session.execute(text("SELECT count(*) FROM memory_items WHERE id = :i"), {"i": item_id})
    ).scalar_one()


def _body(team_scope: str) -> dict:
    """A valid MemoryItem upsert body whose metadata.entities is NON-EMPTY — that is what triggers the
    outbox path. Fills the 7-field tagging contract from packages/memory-models.

    `id` is the empty string — mirrors how NativeStubProvider treats a falsy id ("let the backend
    assign one"); MemoryItem.id has no min_length constraint so this validates.
    """
    now = "2026-07-12T00:00:00Z"
    return {
        "item": {
            "id": "",
            "team_scope": team_scope,
            "project_scope": None,
            "content": "Acme Corp signed the deal today.",
            "metadata": {"entities": [{"name": "Acme Corp", "type": "org"}]},
            "embedding": None,
            "visibility": "team",
            "truth_level": "EPHEMERAL",
            "confidence": 1.0,
            "source": "test",
            "validation_status": "pending",
            "created_at": now,
            "updated_at": now,
        }
    }


@pytest.mark.asyncio
async def test_no_outbox_rows_when_neo4j_unreachable(client, session, seeded_two_teams, bridge_jwt, monkeypatch):
    """THE decisive test: the exact state a real OSS-light install is in."""
    import app.neo4j_client as neo4j_client
    from app.config import settings

    _install_pg_only_provider(session)
    _neutralize_fire_and_forget_enrichment(monkeypatch)

    # Mirror the REAL deployed config: docker-compose.yml:128 + .env.example:199/201.
    monkeypatch.setattr(settings, "NEO4J_URI", "bolt://neo4j:7687")
    monkeypatch.setattr(settings, "NEO4J_PASSWORD", "operator-filled-this-in")
    # ...but nothing is listening, so init_driver() would have left the driver at None.
    # httpx.ASGITransport never runs the lifespan, so _driver is already at its module
    # default of None here — set it explicitly anyway so this test does not depend on
    # ordering against the "live driver" test below.
    monkeypatch.setattr(neo4j_client, "_driver", None)

    # Guard the guard: if either of these were falsy, the test would pass for the WRONG reason.
    assert settings.NEO4J_URI and settings.NEO4J_PASSWORD, "this test must run with config PRESENT"
    assert neo4j_client.get_driver() is None

    team_a = seeded_two_teams["team_a"]
    token = bridge_jwt("outbox-guard-test", team_a.slug)

    before = await _outbox_count(session)
    r = await client.post(
        "/v1/memory/upsert",
        json=_body(team_a.slug),
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]

    assert await _outbox_count(session) == before, (
        "neo4j_outbox grew with no reachable Neo4j to drain it — unbounded growth in the default install"
    )
    assert await _memory_item_count(session, item_id) == 1, "the memory item itself must still be written"


@pytest.mark.asyncio
async def test_outbox_rows_written_when_driver_is_live(client, session, seeded_two_teams, bridge_jwt, monkeypatch):
    """The guard must NOT break graph sync when Neo4j IS reachable."""
    import app.neo4j_client as neo4j_client

    _install_pg_only_provider(session)
    _neutralize_fire_and_forget_enrichment(monkeypatch)

    monkeypatch.setattr(neo4j_client, "_driver", object())   # a live driver, as init_driver() would set
    assert neo4j_client.get_driver() is not None

    team_a = seeded_two_teams["team_a"]
    token = bridge_jwt("outbox-guard-test-live", team_a.slug)

    before = await _outbox_count(session)
    r = await client.post(
        "/v1/memory/upsert",
        json=_body(team_a.slug),
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_a.slug},
    )
    assert r.status_code == 201, r.text
    assert await _outbox_count(session) > before, "outbox rows must still be enqueued when Neo4j is live"
