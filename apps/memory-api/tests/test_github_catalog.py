"""Tests for app.services.github_catalog and GET /v1/internal/github/catalog.

Coverage strategy:
- Pure helpers (no I/O): always run as real assertions.
- Haiku summarization with mocked client: always run.
- HTTP-backed enumerate/fetch helpers: gated with unittest.mock (no live GitHub).
- Catalog endpoint SQL filter assertions: always run (inspects route source text).
- DB-backed endpoint tests: gated on Docker/testcontainers availability.
"""

from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Env defaults must be set before importing app.* modules.
# conftest.py handles DATABASE_URL / BRIDGE_SHARED_SECRET etc. at session scope.
# ---------------------------------------------------------------------------

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

from app.services.github_catalog import (  # noqa: E402
    CATALOG_SOURCE,
    GITHUB_CATALOG_NS,
    build_catalog_content,
    build_catalog_item,
    build_catalog_item_id,
    summarize_readme,
)
from xbrain_memory.types import TruthLevel, ValidationStatus, Visibility  # noqa: E402


# ===========================================================================
# 1. Pure helper — build_catalog_item_id determinism
# ===========================================================================


def test_build_catalog_item_id_deterministic():
    """Same (team_scope, full_name) → same UUID every time."""
    id1 = build_catalog_item_id("aibrussels", "aibrussels/xbrain")
    id2 = build_catalog_item_id("aibrussels", "aibrussels/xbrain")
    assert id1 == id2


def test_build_catalog_item_id_team_scope_sensitivity():
    """Different team_scope → different UUID (cross-team isolation)."""
    id_a = build_catalog_item_id("team-alpha", "owner/repo")
    id_b = build_catalog_item_id("team-beta", "owner/repo")
    assert id_a != id_b


def test_build_catalog_item_id_full_name_sensitivity():
    """Different full_name → different UUID."""
    id_a = build_catalog_item_id("myteam", "owner/repo-a")
    id_b = build_catalog_item_id("myteam", "owner/repo-b")
    assert id_a != id_b


def test_build_catalog_item_id_is_valid_uuid():
    """The returned value is a valid UUID string."""
    item_id = build_catalog_item_id("myteam", "owner/repo")
    parsed = uuid.UUID(item_id)  # raises if invalid
    assert str(parsed) == item_id


def test_build_catalog_item_id_uses_catalog_namespace():
    """The UUID is a uuid5 over GITHUB_CATALOG_NS (not any other namespace)."""
    expected = str(uuid.uuid5(GITHUB_CATALOG_NS, "repo-catalog:myteam:owner/repo"))
    assert build_catalog_item_id("myteam", "owner/repo") == expected


# ===========================================================================
# 2. Pure helper — build_catalog_content shape
# ===========================================================================


def test_build_catalog_content_shape():
    """Content follows the locked shape: '{full_name} — {summary}\\n...'"""
    content = build_catalog_content(
        "aibrussels/xbrain",
        "Collective memory system for teams.",
        "Python",
        ["memory", "ai"],
        "public",
    )
    assert content.startswith("aibrussels/xbrain — ")
    assert "Collective memory system for teams." in content
    assert "Language: Python" in content
    assert "Topics: memory, ai" in content
    assert "Visibility: public" in content


def test_build_catalog_content_no_topics():
    """Topics 'none' when list is empty."""
    content = build_catalog_content(
        "owner/repo",
        "A repo.",
        "Go",
        [],
        "private",
    )
    assert "Topics: none" in content


def test_build_catalog_content_none_topics():
    """Topics 'none' when None passed."""
    content = build_catalog_content(
        "owner/repo",
        "A repo.",
        None,
        None,
        "private",
    )
    assert "Topics: none" in content
    assert "Language: unknown" in content


# ===========================================================================
# 3. Pure helper — build_catalog_item full tagging contract
# ===========================================================================


def test_build_catalog_item_tagging_contract():
    """The built MemoryItem carries the full 7-field tagging contract."""
    repo_obj = {
        "full_name": "aibrussels/xbrain",
        "name": "xbrain",
        "description": "The brain.",
        "default_branch": "main",
        "language": "Python",
        "visibility": "private",
        "html_url": "https://github.com/aibrussels/xbrain",
        "topics": ["memory", "ai"],
    }
    item = build_catalog_item(
        repo_obj,
        "aibrussels",
        summary="Collective memory for teams.",
        readme_summarized=True,
        installation_id=12345,
    )
    # source
    assert item.source == CATALOG_SOURCE
    assert item.source == "github:repo-catalog"
    # truth_level
    assert item.truth_level == TruthLevel.WORKING
    # visibility
    assert item.visibility == Visibility.TEAM
    # validation_status
    assert item.validation_status == ValidationStatus.PENDING
    # team_scope
    assert item.team_scope == "aibrussels"
    # project_scope = short name
    assert item.project_scope == "xbrain"
    # confidence
    assert item.confidence == 0.6
    # id determinism
    expected_id = build_catalog_item_id("aibrussels", "aibrussels/xbrain")
    assert item.id == expected_id


def test_build_catalog_item_metadata_full_name():
    """full_name in metadata (not lost when project_scope uses short name)."""
    repo_obj = {
        "full_name": "org/some-long-repo-name",
        "name": "some-long-repo-name",
        "description": "",
        "default_branch": "main",
        "language": None,
        "visibility": "public",
        "html_url": "https://github.com/org/some-long-repo-name",
        "topics": [],
    }
    item = build_catalog_item(repo_obj, "team-x", summary="A repo.", readme_summarized=False)
    assert item.metadata.get("full_name") == "org/some-long-repo-name"
    assert item.metadata.get("readme_summarized") is False


def test_build_catalog_item_project_scope_clamped():
    """project_scope is clamped to 64 chars (VARCHAR(64) column constraint)."""
    long_name = "x" * 80
    repo_obj = {
        "full_name": f"org/{long_name}",
        "name": long_name,
        "description": "",
        "default_branch": "main",
        "language": None,
        "visibility": "public",
        "html_url": "",
        "topics": [],
    }
    item = build_catalog_item(repo_obj, "team-y", summary="Repo.", readme_summarized=False)
    assert len(item.project_scope) <= 64


def test_build_catalog_item_timestamps_set():
    """created_at and updated_at are set and timezone-aware."""
    repo_obj = {
        "full_name": "org/repo",
        "name": "repo",
        "description": "",
        "default_branch": "main",
        "language": None,
        "visibility": "public",
        "html_url": "",
        "topics": [],
    }
    before = datetime.now(timezone.utc)
    item = build_catalog_item(repo_obj, "team-z", summary="Repo.", readme_summarized=False)
    after = datetime.now(timezone.utc)
    assert item.created_at.tzinfo is not None
    assert before <= item.created_at <= after


# ===========================================================================
# 4. summarize_readme — fail-soft behavior
# ===========================================================================


async def test_summarize_readme_returns_none_when_client_unavailable():
    """Returns None (not raises) when the Anthropic client cannot be initialized."""
    with patch("app.services.github_catalog._get_client", return_value=None):
        result = await summarize_readme("# Hello world\n...", "org/repo", "team-a")
    assert result is None


async def test_summarize_readme_returns_text_when_client_works():
    """Returns the stripped model text on the happy path."""
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text="A memory platform for AI teams.")]
    mock_msg.usage = MagicMock(input_tokens=120)

    mock_client = MagicMock()
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_msg)

    with (
        patch("app.services.github_catalog._get_client", return_value=mock_client),
        patch("app.services.relevance_filter._check_budget", return_value=True),
        patch("app.services.relevance_filter._record_tokens"),
    ):
        result = await summarize_readme(
            "# xbrain\nThis is a memory platform for AI teams.",
            "org/xbrain",
            "team-a",
        )
    assert result == "A memory platform for AI teams."


async def test_summarize_readme_returns_none_on_exception():
    """Returns None (never raises) when the Anthropic call throws."""
    mock_client = MagicMock()
    mock_client.messages = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=Exception("timeout"))

    with (
        patch("app.services.github_catalog._get_client", return_value=mock_client),
        patch("app.services.relevance_filter._check_budget", return_value=True),
        patch("app.services.relevance_filter._record_tokens"),
    ):
        result = await summarize_readme("# Readme", "org/repo", "team-a")
    assert result is None


async def test_summarize_readme_returns_none_budget_exhausted():
    """Returns None when the daily budget is exhausted."""
    mock_client = MagicMock()
    with (
        patch("app.services.github_catalog._get_client", return_value=mock_client),
        patch("app.services.relevance_filter._check_budget", return_value=False),
    ):
        result = await summarize_readme("# Readme", "org/repo", "team-a")
    assert result is None


# ===========================================================================
# 5. Catalog endpoint SQL filter assertions (source-level, no DB needed)
#    Added by Task 2 (once the route is implemented).
# ===========================================================================


def test_catalog_endpoint_sql_filters_present():
    """The /internal/github/catalog route enforces source, team_scope, and deleted_at filters.

    We assert by reading the route source — these filters must never silently regress.
    The SQL string must contain:
      - source = 'github:repo-catalog'
      - deleted_at IS NULL
      - team_scope = $1
    """
    route_src = pathlib.Path(
        "app/routes/internal_github.py"
    ).read_text(encoding="utf-8")
    assert "source = 'github:repo-catalog'" in route_src, (
        "catalog endpoint missing source filter"
    )
    assert "deleted_at IS NULL" in route_src, (
        "catalog endpoint missing soft-delete filter"
    )
    assert "team_scope" in route_src, (
        "catalog endpoint missing team_scope filter"
    )


def test_catalog_endpoint_route_registered():
    """The /internal/github/catalog route is registered in internal_github.py."""
    route_src = pathlib.Path(
        "app/routes/internal_github.py"
    ).read_text(encoding="utf-8")
    assert "/internal/github/catalog" in route_src, (
        "catalog route not registered in internal_github.py"
    )


@pytest.mark.asyncio
async def test_find_user_installation_found_and_missing():
    """find_user_installation returns the id on 200 and None on 404 (Task #149)."""
    from app.services import github_installation as gi

    def _client(resp):
        c = MagicMock()
        c.get = AsyncMock(return_value=resp)
        c.__aenter__ = AsyncMock(return_value=c)
        c.__aexit__ = AsyncMock(return_value=False)
        return c

    resp200 = MagicMock(status_code=200)
    resp200.json = MagicMock(return_value={"id": 137865560})
    resp200.raise_for_status = MagicMock()
    with patch.object(gi.httpx, "AsyncClient", return_value=_client(resp200)), \
            patch.object(gi, "mint_app_jwt", return_value="app.jwt.token"):
        assert await gi.find_user_installation("mrboups") == 137865560

    resp404 = MagicMock(status_code=404)
    with patch.object(gi.httpx, "AsyncClient", return_value=_client(resp404)), \
            patch.object(gi, "mint_app_jwt", return_value="app.jwt.token"):
        assert await gi.find_user_installation("nobody-xyz") is None


def test_index_orgs_catalog_accepts_user_login():
    """index_orgs_catalog exposes user_login (personal-repo path) — Task #149."""
    import inspect

    from app.services.github_catalog import index_orgs_catalog

    params = inspect.signature(index_orgs_catalog).parameters
    assert "user_login" in params, "index_orgs_catalog must accept user_login"
    cat_src = pathlib.Path(
        "app/services/github_catalog.py"
    ).read_text(encoding="utf-8")
    assert "find_user_installation" in cat_src, (
        "index_team_catalog must fall back to the user-account install"
    )
    assert "list_teams_for_user" in cat_src, (
        "index_orgs_catalog must index the user's personal repos into their teams"
    )
