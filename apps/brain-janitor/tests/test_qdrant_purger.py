"""Unit tests for qdrant_purger — mocks AsyncQdrantClient.delete."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.qdrant_purger import purge_qdrant


@pytest.mark.asyncio
async def test_purge_qdrant_noop_on_empty_list():
    """No IDs → no client constructed, no delete issued, returns 0."""
    with patch("qdrant_client.AsyncQdrantClient") as MockClient:
        result = await purge_qdrant("http://q:6333", "messages", [])
    MockClient.assert_not_called()
    assert result == 0


@pytest.mark.asyncio
async def test_purge_qdrant_deletes_point_ids_as_strings():
    """UUIDs converted to str, passed via PointIdsList to delete()."""
    ids = [uuid4(), uuid4(), uuid4()]
    fake_client = AsyncMock()
    fake_client.delete = AsyncMock()
    fake_client.close = AsyncMock()
    with patch(
        "qdrant_client.AsyncQdrantClient",
        return_value=fake_client,
    ):
        result = await purge_qdrant("http://q:6333", "messages", ids)
    assert result == 3
    fake_client.delete.assert_awaited_once()
    kwargs = fake_client.delete.call_args.kwargs
    assert kwargs["collection_name"] == "messages"
    selector = kwargs["points_selector"]
    # PointIdsList.points should be the str representation of every UUID
    assert selector.points == [str(u) for u in ids]
    fake_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_qdrant_fails_soft_on_qdrant_error():
    """Qdrant exception is swallowed, logged; returns 0; client is closed."""
    ids = [uuid4()]
    fake_client = AsyncMock()
    fake_client.delete = AsyncMock(side_effect=RuntimeError("qdrant down"))
    fake_client.close = AsyncMock()
    with patch(
        "qdrant_client.AsyncQdrantClient",
        return_value=fake_client,
    ):
        result = await purge_qdrant("http://q:6333", "messages", ids)
    assert result == 0
    # close() still runs in the finally clause
    fake_client.close.assert_awaited_once()
