"""Shared fixtures for brain-janitor unit tests.

We deliberately stub `qdrant_client` and `neo4j` at sys.modules level so the
test suite runs on hosts that don't have those heavyweight clients installed
(the prod image installs them via pyproject.toml; CI for unit tests doesn't
need to). All tests that exercise dispatch logic mock these surfaces anyway.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _ensure_qdrant_stub() -> None:
    """Install a minimal stub for `qdrant_client` so imports succeed."""
    if "qdrant_client" in sys.modules:
        return

    qdrant_client = types.ModuleType("qdrant_client")
    qdrant_client.AsyncQdrantClient = MagicMock(name="AsyncQdrantClient")

    http = types.ModuleType("qdrant_client.http")
    http_models = types.ModuleType("qdrant_client.http.models")

    class _PointIdsList:
        def __init__(self, points):
            self.points = points

    http_models.PointIdsList = _PointIdsList

    sys.modules["qdrant_client"] = qdrant_client
    sys.modules["qdrant_client.http"] = http
    sys.modules["qdrant_client.http.models"] = http_models


def _ensure_neo4j_stub() -> None:
    """Install a minimal stub for `neo4j` so imports succeed."""
    if "neo4j" in sys.modules:
        return
    neo4j = types.ModuleType("neo4j")
    neo4j.AsyncGraphDatabase = MagicMock(name="AsyncGraphDatabase")
    sys.modules["neo4j"] = neo4j


# Install BEFORE any test module imports app.qdrant_purger / app.neo4j_purger
_ensure_qdrant_stub()
_ensure_neo4j_stub()
