"""Tests for app.services.github_sync.

Covers:
  - _should_index: extension allowlist, name allowlist, size guard
  - _line_windows: chunking (multi-window file → N windows), empty-window skip
  - uuid5 determinism: same inputs → same item id
  - sync_repo: caps (MAX_FILES, MAX_CHUNKS_TOTAL), per-file error isolation
  - sync_repo: happy-path counts (files_indexed, chunks_upserted, skipped)

Mocking strategy:
  - list_repo_files and read_repo_file are patched with unittest.mock.AsyncMock
    (no real HTTP / GitHub App credentials needed).
  - provider.upsert is an AsyncMock that records all MemoryItem calls.
  - No DB writes; the session is an AsyncMock (only passed through to the
    patched GitHub helpers).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.github_sync import (
    ALLOWED_EXTS,
    ALLOWED_NAMES,
    GITHUB_SYNC_NS,
    MAX_CHUNKS_TOTAL,
    MAX_FILES,
    _line_windows,
    _should_index,
    sync_repo,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# _should_index
# ---------------------------------------------------------------------------


def test_should_index_known_exts():
    """Common text/code extensions are indexed."""
    assert _should_index("README.md", 100) is True
    assert _should_index("main.py", 500) is True
    assert _should_index("app.ts", 1000) is True
    assert _should_index("schema.sql", 200) is True
    assert _should_index("config.yaml", 50) is True
    assert _should_index("config.yml", 50) is True
    assert _should_index("go.mod", 100) is False  # .mod not in allowlist
    assert _should_index("main.go", 100) is True


def test_should_index_allowed_names():
    """Extensionless well-known filenames are indexed."""
    assert _should_index("Dockerfile", 800) is True
    assert _should_index("Makefile", 400) is True
    assert _should_index("LICENSE", 1000) is True
    assert _should_index("README", 500) is True


def test_should_index_binary_skipped():
    """Binary and non-text extensions are not indexed."""
    assert _should_index("image.png", 5000) is False
    assert _should_index("archive.zip", 10000) is False
    assert _should_index("binary.exe", 50000) is False
    assert _should_index("font.woff2", 3000) is False


def test_should_index_size_guard():
    """Files exceeding MAX_FILE_BYTES (100_000) are skipped regardless of extension."""
    assert _should_index("big.py", 100_001) is False
    assert _should_index("big.py", 100_000) is True


def test_should_index_compound_extension():
    """.env.example is recognised as a compound extension."""
    assert _should_index(".env.example", 200) is True


def test_should_index_size_none():
    """None size (unknown from listing API) does not trigger the size guard."""
    assert _should_index("main.py", None) is True
    assert _should_index("image.png", None) is False


# ---------------------------------------------------------------------------
# _line_windows
# ---------------------------------------------------------------------------


def test_line_windows_single_window():
    """Short file fits in one window."""
    text = "\n".join(f"line {i}" for i in range(10))
    windows = _line_windows(text, window=120)
    assert len(windows) == 1
    assert "line 0" in windows[0]


def test_line_windows_multiple_windows():
    """File with more lines than window size produces multiple windows."""
    lines = [f"line {i}" for i in range(250)]
    text = "\n".join(lines)
    windows = _line_windows(text, window=120)
    assert len(windows) == 3  # ceil(250/120) = 3


def test_line_windows_exact_boundary():
    """Exactly window_size lines → one window."""
    text = "\n".join(["x"] * 120)
    windows = _line_windows(text, window=120)
    assert len(windows) == 1


def test_line_windows_empty_window_skipped():
    """All-whitespace windows are dropped."""
    text = "code line\n" + "   \n" * 200 + "more code\n"
    windows = _line_windows(text, window=120)
    # The 200-line whitespace block spans two 120-line windows — both all-whitespace
    # and therefore dropped. Only the first (code) and third (more code) windows survive.
    for w in windows:
        assert w.strip() != ""


def test_line_windows_empty_text():
    """Completely empty text → no windows."""
    assert _line_windows("", window=120) == []
    assert _line_windows("   \n\n  ", window=120) == []


# ---------------------------------------------------------------------------
# uuid5 determinism
# ---------------------------------------------------------------------------


def test_uuid5_determinism():
    """Same repo/path/sha/chunk_idx always produces the same item id."""
    key = "myorg/myrepo:src/main.py:abc123:0"
    id1 = str(uuid.uuid5(GITHUB_SYNC_NS, key))
    id2 = str(uuid.uuid5(GITHUB_SYNC_NS, key))
    assert id1 == id2


def test_uuid5_different_inputs():
    """Different inputs produce different ids."""
    id1 = str(uuid.uuid5(GITHUB_SYNC_NS, "myorg/repo:a.py:sha1:0"))
    id2 = str(uuid.uuid5(GITHUB_SYNC_NS, "myorg/repo:a.py:sha1:1"))
    id3 = str(uuid.uuid5(GITHUB_SYNC_NS, "myorg/repo:b.py:sha1:0"))
    assert id1 != id2
    assert id1 != id3
    assert id2 != id3


def test_uuid5_namespace_is_stable():
    """GITHUB_SYNC_NS has not been accidentally changed."""
    assert str(GITHUB_SYNC_NS) == "3f7a4c10-9b2e-5d84-a1f6-7c3e8b0d2a95"


# ---------------------------------------------------------------------------
# sync_repo — happy path
# ---------------------------------------------------------------------------


def _make_provider():
    """Return an AsyncMock provider that records all upsert calls."""
    p = MagicMock()
    p.upsert = AsyncMock()
    return p


def _make_session():
    return AsyncMock()


@pytest.fixture
def mock_list_files():
    """Patch list_repo_files in github_sync module."""
    with patch("app.services.github_sync.list_repo_files") as m:
        yield m


@pytest.fixture
def mock_read_file():
    """Patch read_repo_file in github_sync module."""
    with patch("app.services.github_sync.read_repo_file") as m:
        yield m


async def test_sync_repo_happy_path(mock_list_files, mock_read_file):
    """Two indexable files → correct files_indexed / chunks_upserted."""
    mock_list_files.side_effect = [
        # Root listing: one dir + two files
        [
            {"name": "src", "path": "src", "type": "dir", "size": 0, "sha": "d1"},
            {"name": "README.md", "path": "README.md", "type": "file", "size": 100, "sha": "f1"},
        ],
        # src/ listing: one Python file
        [
            {"name": "app.py", "path": "src/app.py", "type": "file", "size": 200, "sha": "f2"},
        ],
        # No more dirs
    ]

    # Both files fit in a single window
    readme_text = "# Hello\n" + "\n".join(f"line {i}" for i in range(5))
    apppy_text = "import os\n" + "\n".join(f"# line {i}" for i in range(5))
    mock_read_file.side_effect = [
        {"content": readme_text, "repo": "myorg/repo", "path": "README.md", "ref": "HEAD", "size": 100, "truncated": False},
        {"content": apppy_text, "repo": "myorg/repo", "path": "src/app.py", "ref": "HEAD", "size": 200, "truncated": False},
    ]

    provider = _make_provider()
    result = await sync_repo(
        _make_session(),
        provider,
        repo="myorg/repo",
        team_scope="eng",
        project_scope="backend",
    )

    assert result["files_indexed"] == 2
    assert result["chunks_upserted"] == 2
    assert result["skipped"] == 0
    assert result["capped"] is False
    assert provider.upsert.call_count == 2

    # Verify MemoryItem fields on the first call
    first_item = provider.upsert.call_args_list[0].args[0]
    assert first_item.team_scope == "eng"
    assert first_item.project_scope == "backend"
    assert first_item.source == "github:myorg/repo"
    assert first_item.truth_level.value == "WORKING"
    assert first_item.confidence == 0.6
    assert first_item.visibility.value == "team"
    assert first_item.validation_status.value == "pending"
    assert first_item.metadata["ingestion_origin"] == "github-sync"


async def test_sync_repo_project_scope_defaults_to_repo(mock_list_files, mock_read_file):
    """When project_scope is None, it defaults to repo."""
    mock_list_files.return_value = [
        {"name": "a.py", "path": "a.py", "type": "file", "size": 50, "sha": "s1"},
    ]
    mock_read_file.return_value = {"content": "x = 1\n", "repo": "o/r", "path": "a.py", "ref": "HEAD", "size": 50, "truncated": False}

    provider = _make_provider()
    await sync_repo(_make_session(), provider, repo="o/r", team_scope="t1")

    item = provider.upsert.call_args_list[0].args[0]
    assert item.project_scope == "o/r"


# ---------------------------------------------------------------------------
# sync_repo — binary / non-text files are skipped
# ---------------------------------------------------------------------------


async def test_sync_repo_skips_non_text(mock_list_files, mock_read_file):
    """Binary/non-text files are skipped; skipped counter incremented."""
    mock_list_files.return_value = [
        {"name": "image.png", "path": "image.png", "type": "file", "size": 5000, "sha": "img"},
        {"name": "app.py", "path": "app.py", "type": "file", "size": 100, "sha": "py1"},
    ]
    mock_read_file.return_value = {"content": "print('ok')\n", "repo": "o/r", "path": "app.py", "ref": "HEAD", "size": 100, "truncated": False}

    provider = _make_provider()
    result = await sync_repo(_make_session(), provider, repo="o/r", team_scope="t1")

    assert result["files_indexed"] == 1
    assert result["skipped"] == 1
    assert provider.upsert.call_count == 1


# ---------------------------------------------------------------------------
# sync_repo — per-file error isolation
# ---------------------------------------------------------------------------


async def test_sync_repo_per_file_error_does_not_abort(mock_list_files, mock_read_file):
    """A read error on one file is caught; remaining files are still processed."""
    mock_list_files.return_value = [
        {"name": "broken.py", "path": "broken.py", "type": "file", "size": 100, "sha": "br"},
        {"name": "ok.py", "path": "ok.py", "type": "file", "size": 200, "sha": "ok"},
    ]
    mock_read_file.side_effect = [
        RuntimeError("simulated read failure"),
        {"content": "# ok\n", "repo": "o/r", "path": "ok.py", "ref": "HEAD", "size": 200, "truncated": False},
    ]

    provider = _make_provider()
    result = await sync_repo(_make_session(), provider, repo="o/r", team_scope="t1")

    assert result["files_indexed"] == 1
    assert result["skipped"] == 1
    assert result["capped"] is False
    assert provider.upsert.call_count == 1


# ---------------------------------------------------------------------------
# sync_repo — MAX_FILES cap
# ---------------------------------------------------------------------------


async def test_sync_repo_max_files_cap(mock_list_files, mock_read_file):
    """MAX_FILES cap stops processing after the limit is reached."""
    # Create MAX_FILES + 5 file entries in a single directory listing
    entries = [
        {"name": f"f{i}.py", "path": f"f{i}.py", "type": "file", "size": 50, "sha": f"s{i}"}
        for i in range(MAX_FILES + 5)
    ]
    mock_list_files.return_value = entries
    mock_read_file.return_value = {"content": "x = 1\n", "repo": "o/r", "path": "x.py", "ref": "HEAD", "size": 50, "truncated": False}

    provider = _make_provider()
    result = await sync_repo(_make_session(), provider, repo="o/r", team_scope="t1")

    assert result["files_indexed"] == MAX_FILES
    assert result["capped"] is True
    assert provider.upsert.call_count == MAX_FILES


# ---------------------------------------------------------------------------
# sync_repo — MAX_CHUNKS_TOTAL cap
# ---------------------------------------------------------------------------


async def test_sync_repo_max_chunks_cap(mock_list_files, mock_read_file):
    """MAX_CHUNKS_TOTAL cap stops upserting once the chunk limit is hit."""
    # 5 files, each with enough lines to produce 500 chunks (well over MAX_CHUNKS_TOTAL)
    entries = [
        {"name": f"big{i}.py", "path": f"big{i}.py", "type": "file", "size": 50, "sha": f"s{i}"}
        for i in range(5)
    ]
    mock_list_files.return_value = entries

    # Each file has 600 × 120 = 72 000 lines → 600 windows of 120 lines each
    big_text = "\n".join(f"line {j}" for j in range(600 * 120))
    mock_read_file.return_value = {"content": big_text, "repo": "o/r", "path": "big.py", "ref": "HEAD", "size": 50, "truncated": False}

    provider = _make_provider()
    result = await sync_repo(_make_session(), provider, repo="o/r", team_scope="t1")

    assert result["chunks_upserted"] == MAX_CHUNKS_TOTAL
    assert result["capped"] is True


# ---------------------------------------------------------------------------
# sync_repo — multi-chunk file produces correct item ids
# ---------------------------------------------------------------------------


async def test_sync_repo_multi_chunk_ids(mock_list_files, mock_read_file):
    """A file that splits into 3 chunks produces 3 items with sequential chunk_idx."""
    # 300 lines = 3 × 120-line windows (with a 60-line remainder that merges into the 3rd)
    # Actually with window=120: 0-119, 120-239, 240-299 = 3 windows
    lines = [f"code line {i}" for i in range(300)]
    text = "\n".join(lines)

    mock_list_files.return_value = [
        {"name": "multi.py", "path": "multi.py", "type": "file", "size": len(text), "sha": "msha"},
    ]
    mock_read_file.return_value = {"content": text, "repo": "o/r", "path": "multi.py", "ref": "HEAD", "size": len(text), "truncated": False}

    provider = _make_provider()
    result = await sync_repo(_make_session(), provider, repo="o/r", team_scope="t1")

    assert result["files_indexed"] == 1
    assert result["chunks_upserted"] == 3

    # Verify chunk_idx in metadata
    items = [call.args[0] for call in provider.upsert.call_args_list]
    assert [item.metadata["chunk_idx"] for item in items] == [0, 1, 2]

    # Verify uuid5 determinism: recompute and compare
    for item in items:
        expected_id = str(uuid.uuid5(GITHUB_SYNC_NS, f"o/r:multi.py:msha:{item.metadata['chunk_idx']}"))
        assert item.id == expected_id
