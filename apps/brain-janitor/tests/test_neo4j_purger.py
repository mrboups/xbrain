"""Unit tests for neo4j_purger — mocks AsyncGraphDatabase.driver."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.neo4j_purger import purge_neo4j


@pytest.mark.asyncio
async def test_purge_neo4j_noop_on_empty_list():
    """No IDs → no driver constructed, returns 0."""
    with patch("neo4j.AsyncGraphDatabase.driver") as drv_factory:
        result = await purge_neo4j(
            "bolt://neo4j:7687", "neo4j", "pwd", []
        )
    drv_factory.assert_not_called()
    assert result == 0


def _make_async_cm(inner):
    """Return an object whose __aenter__ yields ``inner`` and __aexit__ no-ops."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_purge_neo4j_runs_detach_delete_per_id():
    """Each UUID triggers one MATCH (n {external_id: $id}) DETACH DELETE n."""
    ids = [uuid4(), uuid4()]

    fake_session = MagicMock()
    fake_session.run = AsyncMock()
    sess_cm = _make_async_cm(fake_session)

    fake_drv = MagicMock()
    fake_drv.session = MagicMock(return_value=sess_cm)
    drv_cm = _make_async_cm(fake_drv)

    with patch("neo4j.AsyncGraphDatabase.driver", return_value=drv_cm) as drv_factory:
        result = await purge_neo4j(
            "bolt://neo4j:7687", "neo4j", "pwd", ids
        )

    drv_factory.assert_called_once_with("bolt://neo4j:7687", auth=("neo4j", "pwd"))
    assert result == len(ids)
    assert fake_session.run.await_count == len(ids)
    # Every call uses the expected Cypher + id as string
    for i, call in enumerate(fake_session.run.call_args_list):
        args, kwargs = call
        cypher = args[0]
        assert "MATCH (n {external_id: $id}) DETACH DELETE n" in cypher
        assert kwargs["id"] == str(ids[i])


@pytest.mark.asyncio
async def test_purge_neo4j_continues_after_per_id_failure():
    """One bad Cypher run shouldn't abort the rest of the batch."""
    ids = [uuid4(), uuid4(), uuid4()]

    fake_session = MagicMock()
    # Second call raises, first + third succeed
    fake_session.run = AsyncMock(
        side_effect=[None, RuntimeError("bad node"), None]
    )
    sess_cm = _make_async_cm(fake_session)

    fake_drv = MagicMock()
    fake_drv.session = MagicMock(return_value=sess_cm)
    drv_cm = _make_async_cm(fake_drv)

    with patch("neo4j.AsyncGraphDatabase.driver", return_value=drv_cm):
        result = await purge_neo4j(
            "bolt://neo4j:7687", "neo4j", "pwd", ids
        )

    assert fake_session.run.await_count == 3  # all three attempted
    assert result == 2  # only 2 succeeded


@pytest.mark.asyncio
async def test_purge_neo4j_fails_soft_on_driver_error():
    """Connection-level failure → log + return 0, don't raise."""
    ids = [uuid4()]
    with patch(
        "neo4j.AsyncGraphDatabase.driver",
        side_effect=ConnectionError("neo4j unreachable"),
    ):
        result = await purge_neo4j(
            "bolt://neo4j:7687", "neo4j", "pwd", ids
        )
    assert result == 0
