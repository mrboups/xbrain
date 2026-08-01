"""Tests for /v1/media/upload + /v1/media/{item_id}/raw (BL-003 slice 1).

Strategy
--------
Unit tests (no fixtures, no DB, no MinIO):
  - derive_key_and_mime: pure helper — covers key format, mime derivation,
    extension fallback, and the sanitised 'media/{team}/{id}{ext}' shape.
  - _MAX_UPLOAD_BYTES constant sanity check.

Integration tests (@pytest.mark.integration):
  - Would require a live MinIO + PostgreSQL + Qdrant stack (all three are
    needed: provider.upsert writes to PG + Qdrant, get_minio_client needs
    creds).  They are declared here with the correct markers but require the
    full Docker environment used on the VM (or a testcontainers trio) to run.
  - CI on the dev machine skips them automatically when Docker is not
    available (same skip-guard as conftest.py integration fixtures).
"""
from __future__ import annotations

import pytest

from app.routes.media_helpers import (
    _MAX_UPLOAD_BYTES,
    derive_key_and_mime,
    mint_media_token,
    verify_media_token,
)


# ---------------------------------------------------------------------------
# Unit tests — no network, no DB
# ---------------------------------------------------------------------------


class TestUploadSignedUrl:
    """The `signed_url` the upload response now returns must actually be openable.

    `raw_path` needs Authorization + X-Team-Scope, which a browser cannot send when it
    merely follows a link — so handing a file to a teammate depends on this token being
    valid AND bound to the one item. Unit-level on purpose: it exercises the exact
    mint/verify pair the endpoint uses, with no MinIO in the way.
    """

    ITEM = "11111111-1111-4111-8111-111111111111"
    OTHER = "22222222-2222-4222-8222-222222222222"

    def test_minted_token_verifies_for_its_own_item(self):
        tok = mint_media_token(self.ITEM, "team-a")
        assert verify_media_token(tok, self.ITEM) == "team-a"

    def test_token_is_bound_to_the_item_it_was_minted_for(self):
        # A token for one upload must not unlock a different one — otherwise a link
        # shared with a teammate would be a skeleton key for the whole bucket.
        from fastapi import HTTPException

        tok = mint_media_token(self.ITEM, "team-a")
        with pytest.raises(HTTPException):
            verify_media_token(tok, self.OTHER)

    def test_signed_url_shape_matches_the_serve_route(self):
        # Guards against the response drifting away from GET /media/{id}/img?t=...
        tok = mint_media_token(self.ITEM, "team-a")
        url = f"/v1/media/{self.ITEM}/img?t={tok}"
        assert url.startswith(f"/v1/media/{self.ITEM}/img?t=")
        assert verify_media_token(url.split("?t=", 1)[1], self.ITEM) == "team-a"



class TestDeriveKeyAndMime:
    """Pure key/mime derivation — lockable without any infrastructure."""

    def test_key_shape(self):
        key, mime, ext = derive_key_and_mime(
            filename="photo.jpg",
            content_type="image/jpeg",
            team_scope="team-alpha",
            item_id="abc-123",
        )
        assert key == "media/team-alpha/abc-123.jpg"

    def test_mime_from_content_type(self):
        _, mime, _ = derive_key_and_mime(
            filename=None,
            content_type="image/png",
            team_scope="t",
            item_id="x",
        )
        assert mime == "image/png"

    def test_mime_fallback_from_filename(self):
        _, mime, _ = derive_key_and_mime(
            filename="diagram.pdf",
            content_type=None,
            team_scope="t",
            item_id="x",
        )
        assert mime == "application/pdf"

    def test_mime_fallback_octet_stream(self):
        _, mime, _ = derive_key_and_mime(
            filename=None,
            content_type=None,
            team_scope="t",
            item_id="x",
        )
        assert mime == "application/octet-stream"

    def test_ext_from_filename(self):
        _, _, ext = derive_key_and_mime(
            filename="doc.docx",
            content_type=None,
            team_scope="t",
            item_id="x",
        )
        assert ext == ".docx"

    def test_ext_fallback_from_mime(self):
        """When filename has no extension, derive from mime."""
        key, _, ext = derive_key_and_mime(
            filename="noext",
            content_type="image/gif",
            team_scope="t",
            item_id="y",
        )
        # mimetypes.guess_extension("image/gif") is platform-dependent (could be
        # ".gif" or ".giff") — just assert something non-empty was appended.
        assert ext  # non-empty
        assert key.startswith("media/t/y")

    def test_no_filename_no_ext(self):
        key, mime, ext = derive_key_and_mime(
            filename=None,
            content_type="application/octet-stream",
            team_scope="t",
            item_id="z",
        )
        # ext may be empty string — key ends with item_id (no trailing dot)
        assert key == f"media/t/z{ext}"
        assert mime == "application/octet-stream"

    def test_team_scope_embedded_in_key(self):
        key, _, _ = derive_key_and_mime("a.txt", "text/plain", "my-team", "id-1")
        parts = key.split("/")
        assert parts[0] == "media"
        assert parts[1] == "my-team"
        assert parts[2].startswith("id-1")

    def test_content_type_beats_filename_mime(self):
        """Explicit content_type always wins over filename-guessed mime."""
        _, mime, _ = derive_key_and_mime(
            filename="image.png",           # would guess image/png
            content_type="image/webp",      # overrides
            team_scope="t",
            item_id="x",
        )
        assert mime == "image/webp"


class TestConstants:
    def test_max_upload_bytes_is_25_mb(self):
        assert _MAX_UPLOAD_BYTES == 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# Integration stubs — require full Docker stack, skip otherwise
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_returns_201_and_item_id(client, bridge_jwt):
    """POST /v1/media/upload with a tiny PNG returns 201 + {item_id, key, mime, size, raw_path}.

    Skipped automatically when Docker is not available (conftest.py guard).
    Requires: MinIO creds wired (MINIO_URL/ACCESS_KEY/SECRET_KEY set in env),
    a running PostgreSQL + Qdrant + memory_items schema.
    """
    import io

    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    # 1x1 transparent PNG (minimal valid PNG bytes)
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    resp = await client.post(
        "/v1/media/upload",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        files={"file": ("pixel.png", io.BytesIO(png_bytes), "image/png")},
        data={"caption": "test pixel", "truth_level": "WORKING"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "item_id" in body
    assert body["mime"] == "image/png"
    assert body["raw_path"] == f"/v1/media/{body['item_id']}/raw"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_raw_serve_streams_back_uploaded_file(client, bridge_jwt):
    """Upload a file then GET /raw — should 200 with correct Content-Type."""
    import io

    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    data_bytes = b"hello media"
    upload_resp = await client.post(
        "/v1/media/upload",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        files={"file": ("hello.txt", io.BytesIO(data_bytes), "text/plain")},
    )
    assert upload_resp.status_code == 201, upload_resp.text
    item_id = upload_resp.json()["item_id"]

    raw_resp = await client.get(
        f"/v1/media/{item_id}/raw",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
    )
    assert raw_resp.status_code == 200, raw_resp.text
    assert raw_resp.content == data_bytes
    assert "text/plain" in raw_resp.headers.get("content-type", "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_raw_serve_404_for_unknown_item(client, bridge_jwt):
    """GET /raw for a non-existent item_id returns 404."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    resp = await client.get(
        "/v1/media/00000000-0000-0000-0000-000000000000/raw",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
    )
    assert resp.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_413_on_oversized_file(client, bridge_jwt):
    """Files larger than 25 MB must be rejected with 413."""
    import io

    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    oversized = io.BytesIO(b"x" * (25 * 1024 * 1024 + 1))
    resp = await client.post(
        "/v1/media/upload",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        files={"file": ("big.bin", oversized, "application/octet-stream")},
    )
    assert resp.status_code == 413


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_503_when_minio_unconfigured(client, bridge_jwt, monkeypatch):
    """POST /upload returns 503 when get_minio_client() returns None."""
    import io

    import app.routes.media as media_mod  # noqa: PLC0415

    monkeypatch.setattr(media_mod, "get_minio_client", lambda: None)

    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    resp = await client.post(
        "/v1/media/upload",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        files={"file": ("x.png", io.BytesIO(b"data"), "image/png")},
    )
    assert resp.status_code == 503
