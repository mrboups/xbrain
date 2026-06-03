"""Tests for app.services.github_contents.

Covers:
  - list_repo_files: success (dir returns entries), 404 → [], 403 → GithubPermissionDenied
  - read_repo_file: success (base64 content + truncation flag), 403 → GithubPermissionDenied
  - _resolve_installation_token: org path, user-installation fallback, not-installed → GithubAppNotInstalled
  - Invalid repo format → ValueError

Mocking strategy (mirrors test_phase12_installation_token.py):
  - RSA key fixture + monkeypatched settings so mint_app_jwt() works without prod secrets.
  - respx.mock intercepts all httpx calls — any unmocked URL raises immediately.
  - The DB session is stubbed with a minimal AsyncMock (no real DB needed for pure-service tests).

Note: tests that rely on find_installation_for_org doing a DB lookup use a stub session
that simulates "no row found" so the code falls through to the GitHub API path (user install).
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import github_app_jwt, github_installation
from app.services.github_contents import (
    GithubAppNotInstalled,
    GithubPermissionDenied,
    list_repo_files,
    read_repo_file,
)
from app.services.github_installation import _reset_caches_for_tests

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app_pem_b64() -> str:
    """Fresh RSA key per test — mirrors test_phase12_installation_token.py."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode()


@pytest.fixture(autouse=True)
def _configure_github_app(monkeypatch, app_pem_b64):
    """Inject synthetic GitHub App credentials + reset module caches each test."""
    monkeypatch.setattr(github_app_jwt.settings, "GITHUB_APP_CLIENT_ID", "Iv23li_test")
    monkeypatch.setattr(
        github_app_jwt.settings, "GITHUB_APP_PRIVATE_KEY_B64", app_pem_b64
    )
    github_app_jwt._reset_private_key_cache_for_tests()
    _reset_caches_for_tests()
    yield
    _reset_caches_for_tests()
    github_app_jwt._reset_private_key_cache_for_tests()


def _make_session(installation_id: int | None = None):
    """Build a minimal stub AsyncSession.

    If installation_id is given, the scalar_one_or_none() result on the first
    execute call will return that id (simulating a DB hit for
    find_installation_for_org). Otherwise returns None (DB miss → fallback to
    GitHub API).
    """
    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = installation_id
    session.execute.return_value = result_mock
    session.commit = AsyncMock()
    return session


def _mint_token_response(token: str = "ghs_test", ttl_hours: int = 1) -> httpx.Response:
    expires_at = (
        datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return httpx.Response(201, json={"token": token, "expires_at": expires_at})


def _b64_encode(text: str) -> str:
    """Encode text to base64 with embedded newlines (GitHub API style)."""
    raw = base64.b64encode(text.encode("utf-8")).decode("ascii")
    # GitHub wraps at 60 chars
    return "\n".join(raw[i:i+60] for i in range(0, len(raw), 60)) + "\n"


# ---------------------------------------------------------------------------
# list_repo_files
# ---------------------------------------------------------------------------


async def test_list_repo_files_dir_returns_entries():
    """list_repo_files with a dir response returns normalised entry dicts."""
    session = _make_session(installation_id=42)
    with respx.mock(assert_all_called=False) as mock:
        # Mint installation token
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        # Contents API — returns a list (directory)
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/"
        ).mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"name": "README.md", "path": "README.md", "type": "file", "size": 1234, "sha": "abc"},
                    {"name": "src", "path": "src", "type": "dir", "size": 0, "sha": "def"},
                ],
            )
        )
        entries = await list_repo_files(session, "myorg/myrepo")

    assert len(entries) == 2
    assert entries[0] == {"name": "README.md", "path": "README.md", "type": "file", "size": 1234, "sha": "abc"}
    assert entries[1]["type"] == "dir"


async def test_list_repo_files_single_file_response_normalised():
    """When GitHub returns a single dict (file path), it is normalised to a list."""
    session = _make_session(installation_id=42)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/README.md"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"name": "README.md", "path": "README.md", "type": "file", "size": 42, "sha": "abc"},
            )
        )
        entries = await list_repo_files(session, "myorg/myrepo", path="README.md")

    assert len(entries) == 1
    assert entries[0]["name"] == "README.md"


async def test_list_repo_files_404_returns_empty():
    """404 from GitHub → empty list (path doesn't exist)."""
    session = _make_session(installation_id=42)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/nonexistent"
        ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
        entries = await list_repo_files(session, "myorg/myrepo", path="nonexistent")

    assert entries == []


async def test_list_repo_files_403_raises_permission_denied():
    """403 from GitHub → GithubPermissionDenied."""
    session = _make_session(installation_id=42)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/"
        ).mock(return_value=httpx.Response(403, json={"message": "Forbidden"}))

        with pytest.raises(GithubPermissionDenied) as exc_info:
            await list_repo_files(session, "myorg/myrepo")

    assert "contents:read" in str(exc_info.value)


async def test_list_repo_files_invalid_repo_format():
    """repo without slash raises ValueError before any HTTP call."""
    session = _make_session()
    with pytest.raises(ValueError, match="owner/name"):
        await list_repo_files(session, "noslash")


async def test_list_repo_files_ref_passed_as_query_param():
    """Non-HEAD ref is passed as ?ref= query parameter."""
    session = _make_session(installation_id=42)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        route = mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/"
        ).mock(return_value=httpx.Response(200, json=[]))

        await list_repo_files(session, "myorg/myrepo", ref="main")

    # Verify the ref param was included in the request
    assert route.called
    request = route.calls.last.request
    assert b"ref=main" in request.url.query


# ---------------------------------------------------------------------------
# read_repo_file
# ---------------------------------------------------------------------------


async def test_read_repo_file_decodes_base64_content():
    """Base64-encoded file content is decoded and returned as text."""
    session = _make_session(installation_id=42)
    file_text = "Hello, xbrain!\nThis is a test file."
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/hello.txt"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "hello.txt",
                    "path": "hello.txt",
                    "type": "file",
                    "size": len(file_text),
                    "sha": "abc123",
                    "content": _b64_encode(file_text),
                    "encoding": "base64",
                },
            )
        )
        result = await read_repo_file(session, "myorg/myrepo", "hello.txt")

    assert result["content"] == file_text
    assert result["repo"] == "myorg/myrepo"
    assert result["path"] == "hello.txt"
    assert result["ref"] == "HEAD"
    assert result["truncated"] is False


async def test_read_repo_file_truncation_at_100kb():
    """Files larger than 100 KB are truncated and truncated=True is set."""
    session = _make_session(installation_id=42)
    big_text = "x" * 150_000  # 150 KB
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/big.txt"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "name": "big.txt",
                    "path": "big.txt",
                    "type": "file",
                    "size": len(big_text),
                    "sha": "bbb",
                    "content": _b64_encode(big_text),
                    "encoding": "base64",
                },
            )
        )
        result = await read_repo_file(session, "myorg/myrepo", "big.txt")

    assert result["truncated"] is True
    assert len(result["content"]) == 100_000
    assert result["size"] == len(big_text)


async def test_read_repo_file_403_raises_permission_denied():
    """403 from GitHub → GithubPermissionDenied."""
    session = _make_session(installation_id=42)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/secret.txt"
        ).mock(return_value=httpx.Response(403, json={"message": "Forbidden"}))

        with pytest.raises(GithubPermissionDenied):
            await read_repo_file(session, "myorg/myrepo", "secret.txt")


async def test_read_repo_file_dir_raises_value_error():
    """If the path is a directory, ValueError is raised."""
    session = _make_session(installation_id=42)
    with respx.mock(assert_all_called=False) as mock:
        mock.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=_mint_token_response())
        mock.get(
            "https://api.github.com/repos/myorg/myrepo/contents/src"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"name": "src", "path": "src", "type": "dir", "size": 0, "sha": "ddd"},
            )
        )

        with pytest.raises(ValueError, match="directory"):
            await read_repo_file(session, "myorg/myrepo", "src")


# ---------------------------------------------------------------------------
# _resolve_installation_token: app-not-installed path
# ---------------------------------------------------------------------------


async def test_resolve_token_app_not_installed_raises():
    """Both org and user installation return 404 → GithubAppNotInstalled."""
    session = _make_session(installation_id=None)  # DB miss
    with respx.mock(assert_all_called=False) as mock:
        # Org fallback → 404
        mock.get(
            "https://api.github.com/orgs/ghostorg/installation"
        ).mock(return_value=httpx.Response(404))
        # User fallback → 404
        mock.get(
            "https://api.github.com/users/ghostorg/installation"
        ).mock(return_value=httpx.Response(404))

        with pytest.raises(GithubAppNotInstalled) as exc_info:
            await list_repo_files(session, "ghostorg/somerepo")

    assert "ghostorg" in str(exc_info.value)


async def test_resolve_token_user_installation_fallback():
    """Org install missing → fall through to user install → token resolved."""
    session = _make_session(installation_id=None)  # DB miss (org path)
    with respx.mock(assert_all_called=False) as mock:
        # Org fallback: 404 (no org installation)
        mock.get(
            "https://api.github.com/orgs/mrboups/installation"
        ).mock(return_value=httpx.Response(404))
        # User install: 200 with installation_id=99
        mock.get(
            "https://api.github.com/users/mrboups/installation"
        ).mock(
            return_value=httpx.Response(200, json={"id": 99, "account": {"login": "mrboups", "type": "User"}})
        )
        # Mint token for user installation
        mock.post(
            "https://api.github.com/app/installations/99/access_tokens"
        ).mock(return_value=_mint_token_response("ghs_user_token"))
        # Actual contents call (list root)
        mock.get(
            "https://api.github.com/repos/mrboups/myrepo/contents/"
        ).mock(return_value=httpx.Response(200, json=[]))

        entries = await list_repo_files(session, "mrboups/myrepo")

    assert entries == []
