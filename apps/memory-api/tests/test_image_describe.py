"""Tests for image_describe — making an uploaded IMAGE's content recallable.

What is and is not faked here matters. The thing under test is OUR service: the guards,
the tagging, the budget, the parent flag, the fail-soft. So the ONLY thing mocked is the
Anthropic HTTP TRANSPORT (respx). The real `anthropic.AsyncAnthropic` client builds and
parses the request, which means these tests also prove our request is well-formed — a
fake `_get_client` would have proved nothing about the payload we actually ship.

The guard tests assert `route.call_count == 0`. That is the load-bearing assertion for
"refused BEFORE the API call": an oversized image that is merely rejected after the round
trip would still pass a state check, but it cannot pass a call count of zero.

The real-infra retrieval gate (a description that is actually FOUND by a semantic search
for its content, against real Postgres + Qdrant + the real keyless embedder) lives at the
bottom behind @pytest.mark.integration, mirroring test_doc_body_extraction.py.
"""
from __future__ import annotations

import asyncio
import base64
import struct
import zlib

import httpx
import pytest
import respx
from xbrain_memory.types import MemoryItem, TruthLevel, ValidationStatus, Visibility

from app.config import settings
from app.services import image_describe
from app.services.image_describe import (
    DESCRIPTION_CONFIDENCE,
    FLAG_KEY,
    IMAGE_DESCRIBE_NS,
    VISION_MIMES,
    describe_and_ingest_image,
    is_image_mime,
    probe_image_size,
)

TEAM = "team-alpha"
OTHER_TEAM = "team-beta"
MESSAGES_URL = "https://api.anthropic.com/v1/messages"

DESCRIPTION = (
    "An architecture diagram. A box labelled 'memory-api' connects to boxes labelled "
    "'Qdrant' and 'PostgreSQL'. An arrow from 'LibreChat' points at 'memory-api'."
)


# === Real image bytes (generated, not fixtures on disk) ======================


def make_png(width: int, height: int, *, payload_padding: int = 0) -> bytes:
    """A real, structurally valid PNG with the given dimensions in its IHDR."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
    if payload_padding:
        # A real ancillary chunk, so the file is genuinely this big on the wire.
        out += chunk(b"tEXt", b"pad\x00" + b"z" * payload_padding)
    return out + chunk(b"IEND", b"")


def make_gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 10


def make_jpeg(width: int, height: int) -> bytes:
    """A real JPEG header chain: SOI, an APP0 segment to skip over, then SOF0."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", height, width) + b"\x01"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def make_webp_vp8x(width: int, height: int) -> bytes:
    body = b"WEBP" + b"VP8X" + struct.pack("<I", 10) + b"\x00" * 4
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body)) + body


TINY_PNG = make_png(64, 48)


# === Fakes: a provider, never the service under test ========================


class FakeProvider:
    """Captures upserts and updates. `get` returns whatever the test seeds as CURRENT."""

    def __init__(self, current: MemoryItem | None = None, raise_on_upsert: bool = False) -> None:
        self.items: list[MemoryItem] = []
        self.updates: list[tuple[str, str, dict]] = []
        self.current = current
        self.get_calls = 0
        self._raise_on_upsert = raise_on_upsert

    async def get(self, item_id: str, *, team_scope: str):
        self.get_calls += 1
        return self.current

    async def upsert(self, item: MemoryItem) -> str:
        if self._raise_on_upsert:
            raise RuntimeError("simulated upsert failure")
        self.items.append(item)
        return item.id

    async def update(self, item_id: str, *, team_scope: str, patch: dict) -> None:
        self.updates.append((item_id, team_scope, patch))

    # convenience
    @property
    def flag(self) -> dict:
        assert self.updates, "the parent was never flagged"
        return self.updates[-1][2]["metadata"][FLAG_KEY]


def anthropic_response(text: str = DESCRIPTION, input_tokens: int = 1234) -> httpx.Response:
    """A real Anthropic Messages response body, parsed by the real SDK."""
    return httpx.Response(
        200,
        json={
            "id": "msg_01test",
            "type": "message",
            "role": "assistant",
            "model": settings.VISION_DESCRIBE_MODEL,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 80},
        },
    )


@pytest.fixture(autouse=True)
def vision_env(monkeypatch):
    """Enable the path with a key, and reset the module client + budget between tests."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr(settings, "VISION_DESCRIBE_ENABLED", True)
    image_describe._vision_client = None
    from app.services import relevance_filter

    relevance_filter._daily_budget.clear()
    yield
    image_describe._vision_client = None
    relevance_filter._daily_budget.clear()


async def _describe(provider, **kw):
    base = dict(
        provider=provider,
        data=TINY_PNG,
        mime="image/png",
        filename="diagram.png",
        media_key="media/team-alpha/parent.png",
        parent_item_id="PARENT",
        team_scope=TEAM,
        project_scope="proj",
        truth_level="WORKING",
        visibility="team",
        parent_metadata={"media": {"key": "media/team-alpha/parent.png"}},
    )
    base.update(kw)
    return await describe_and_ingest_image(**base)


# === The description becomes a linked, correctly-tagged item ================


@respx.mock
async def test_description_becomes_a_linked_child_item_with_full_tagging():
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    res = await _describe(provider)

    assert route.call_count == 1
    assert res.state == "described"
    assert len(provider.items) == 1
    child = provider.items[0]

    # The description itself is the content — that is what makes the image recallable.
    assert child.content == DESCRIPTION

    # Full 7-field tagging contract, team/project INHERITED from the upload.
    assert child.team_scope == TEAM
    assert child.project_scope == "proj"
    assert child.visibility == Visibility.TEAM
    assert child.truth_level == TruthLevel.WORKING
    assert child.validation_status == ValidationStatus.PENDING
    assert child.confidence == DESCRIPTION_CONFIDENCE
    assert child.confidence < 1.0  # a machine guess is not as certain as the upload itself

    # source NAMES THE MODEL — a human caption stays tellable from a machine guess.
    assert child.source == f"vision:{settings.VISION_DESCRIBE_MODEL}"
    assert child.source.startswith("vision:")
    assert settings.VISION_DESCRIBE_MODEL in child.source

    # Linked back to the parent and the stored object.
    assert child.metadata["parent_item_id"] == "PARENT"
    assert child.metadata["media_key"] == "media/team-alpha/parent.png"
    assert child.metadata["kind"] == "image_description"
    assert child.metadata["model"] == settings.VISION_DESCRIBE_MODEL

    # Deterministic id: a retry overwrites instead of duplicating the description.
    import uuid as _uuid

    assert child.id == str(_uuid.uuid5(IMAGE_DESCRIBE_NS, "PARENT"))

    # The parent is flagged "described" and points at the child.
    assert provider.flag["state"] == "described"
    assert provider.flag["item_id"] == child.id


@respx.mock
async def test_the_human_caption_on_the_parent_is_never_overwritten():
    """Caption and description are different things; the child carries one, not both."""
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    await _describe(provider)

    # The ONLY item written is the child. The parent's content is never upserted, and the
    # only thing touched on it is its metadata flag.
    assert [i.metadata.get("kind") for i in provider.items] == ["image_description"]
    assert all(i.id != "PARENT" for i in provider.items)
    for _item_id, _team, patch in provider.updates:
        assert set(patch) == {"metadata"}  # never a content patch


@respx.mock
async def test_child_never_lands_under_another_team():
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    await _describe(provider, team_scope=TEAM)

    assert provider.items
    assert all(i.team_scope == TEAM for i in provider.items)
    assert all(i.team_scope != OTHER_TEAM for i in provider.items)


@respx.mock
async def test_request_carries_the_real_image_and_an_accepted_media_type():
    """Proves the payload we actually ship is well-formed — not just that we called out."""
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    await _describe(provider)

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["model"] == settings.VISION_DESCRIBE_MODEL
    blocks = body["messages"][0]["content"]
    image_block = next(b for b in blocks if b["type"] == "image")
    assert image_block["source"]["media_type"] in VISION_MIMES
    assert base64.b64decode(image_block["source"]["data"]) == TINY_PNG  # the real bytes


# === Truth level: inference, never testimony ================================


@respx.mock
async def test_description_truth_level_is_capped_at_working_even_for_a_canonical_upload():
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    await _describe(provider, truth_level="CANONICAL")

    # A model's account of an image must not inherit CANONICAL standing.
    assert provider.items[0].truth_level == TruthLevel.WORKING


@respx.mock
async def test_description_is_never_promoted_above_its_parent():
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    await _describe(provider, truth_level="EPHEMERAL")

    assert provider.items[0].truth_level == TruthLevel.EPHEMERAL


# === Refusals: BEFORE the call, with a recorded reason ======================


@respx.mock
async def test_unsupported_mime_is_skipped_with_a_reason_and_no_call():
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    res = await _describe(provider, data=b"BM-not-really", mime="image/bmp", filename="x.bmp")

    assert route.call_count == 0  # never shipped to be rejected
    assert res.state == "skipped"
    assert res.reason == "unsupported_image_mime:image/bmp"
    assert provider.items == []
    assert provider.flag == {
        "state": "skipped",
        "model": settings.VISION_DESCRIBE_MODEL,
        "reason": "unsupported_image_mime:image/bmp",
    }


@respx.mock
async def test_svg_is_an_image_mime_but_still_refused():
    """image/svg+xml reaches the service (so it is flagged) but is never sent."""
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    res = await _describe(provider, data=b"<svg/>", mime="image/svg+xml", filename="a.svg")

    assert is_image_mime("image/svg+xml")  # the route-level gate lets it through...
    assert route.call_count == 0  # ...and the service refuses it
    assert res.reason.startswith("unsupported_image_mime")


@respx.mock
async def test_oversized_image_is_refused_before_the_api_call(monkeypatch):
    """A 20 MB photo must not become a failed API call per upload."""
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()
    big = make_png(1000, 1000, payload_padding=6 * 1024 * 1024)
    assert len(big) > settings.VISION_MAX_IMAGE_BYTES

    res = await _describe(provider, data=big)

    assert route.call_count == 0  # refused BEFORE the request, not rejected after it
    assert res.state == "skipped"
    assert res.reason.startswith("image_too_large:")
    assert provider.items == []
    assert provider.flag["state"] == "skipped"


@respx.mock
async def test_image_under_the_raw_cap_but_over_it_once_encoded_is_still_refused():
    """base64 inflates ~33%; the provider measures the encoded payload, so we do too."""
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()
    cap = settings.VISION_MAX_IMAGE_BYTES
    # Under the cap raw, over it once base64-encoded.
    sneaky = make_png(100, 100, payload_padding=int(cap * 0.9))
    assert len(sneaky) < cap < len(base64.b64encode(sneaky))

    res = await _describe(provider, data=sneaky)

    assert route.call_count == 0
    assert res.state == "skipped"
    assert "image_too_large" in res.reason


@respx.mock
async def test_oversized_dimensions_are_refused_before_the_api_call():
    """A tiny FILE can still be far past the pixel limit — checked from the header."""
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()
    huge = make_png(12000, 12000)
    assert len(huge) < settings.VISION_MAX_IMAGE_BYTES  # small file, enormous canvas

    res = await _describe(provider, data=huge)

    assert route.call_count == 0
    assert res.state == "skipped"
    assert res.reason == "dimensions_too_large:12000x12000"


@respx.mock
async def test_an_image_at_the_dimension_limit_is_still_described():
    """The guard must refuse the oversized, not everything large."""
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()

    res = await _describe(provider, data=make_png(settings.VISION_MAX_IMAGE_DIMENSION, 100))

    assert route.call_count == 1
    assert res.state == "described"


# === Budget: skip, never retry ==============================================


@respx.mock
async def test_budget_exhausted_skips_rather_than_retries():
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider()
    from app.services import relevance_filter

    # Spend this team's vision allowance for the day.
    relevance_filter.record_token_usage(
        TEAM, settings.VISION_DAILY_TOKEN_CAP_PER_TEAM, bucket=image_describe.BUDGET_BUCKET
    )

    res = await _describe(provider)

    assert route.call_count == 0  # skipped, and NOT retried in a loop
    assert res.state == "skipped"
    assert res.reason == "over_budget"
    assert provider.items == []
    assert provider.flag["reason"] == "over_budget"


@respx.mock
async def test_the_vision_budget_is_separate_from_the_relevance_budget():
    """A screenshot flood must not decide whether chat lines still reach the brain."""
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response(input_tokens=9_999))
    from app.services import relevance_filter

    await _describe(FakeProvider())

    # The vision call was billed to the vision bucket, leaving the relevance one untouched.
    assert relevance_filter._daily_budget[f"{image_describe.BUDGET_BUCKET}:{TEAM}"][
        "tokens_used"
    ] == 9_999
    assert TEAM not in relevance_filter._daily_budget  # the relevance bucket's own key


@respx.mock
async def test_a_team_flooding_screenshots_cannot_starve_another_team():
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    from app.services import relevance_filter

    relevance_filter.record_token_usage(
        TEAM, settings.VISION_DAILY_TOKEN_CAP_PER_TEAM, bucket=image_describe.BUDGET_BUCKET
    )

    other = FakeProvider()
    res = await _describe(other, team_scope=OTHER_TEAM)

    assert route.call_count == 1
    assert res.state == "described"


# === Fail-soft: the upload is never affected ================================


@respx.mock
async def test_a_model_failure_never_raises_and_flags_the_parent():
    route = respx.post(MESSAGES_URL).mock(return_value=httpx.Response(400, json={"error": "nope"}))
    provider = FakeProvider()

    res = await _describe(provider)  # must return, not raise

    assert route.called
    assert res.state == "failed"
    assert res.reason.startswith("describe_error:")
    assert provider.items == []  # nothing half-written
    assert provider.flag["state"] == "failed"  # visible, not silent


@respx.mock
async def test_a_network_failure_never_raises():
    respx.post(MESSAGES_URL).mock(side_effect=httpx.ConnectError("no route to host"))
    provider = FakeProvider()

    res = await _describe(provider)

    assert res.state == "failed"
    assert provider.flag["state"] == "failed"


@respx.mock
async def test_an_empty_description_is_never_embedded():
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response(text="   "))
    provider = FakeProvider()

    res = await _describe(provider)

    assert res.state == "failed"
    assert res.reason == "empty_description"
    assert provider.items == []  # never an empty vector


@respx.mock
async def test_a_provider_upsert_failure_never_raises():
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider(raise_on_upsert=True)

    res = await _describe(provider)

    assert res.state == "failed"
    assert provider.flag["state"] == "failed"


async def test_no_api_key_is_recorded_as_skipped_not_silence(monkeypatch):
    """A zero-key OSS install records a reason instead of leaving no trace."""
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    image_describe._vision_client = None
    provider = FakeProvider()

    res = await _describe(provider)

    assert res.state == "skipped"
    assert res.reason == "vision_unavailable"
    assert provider.flag["reason"] == "vision_unavailable"


async def test_the_kill_switch_stops_the_path_entirely(monkeypatch):
    """A self-hoster who refuses the third-party image egress gets exactly that."""
    monkeypatch.setattr(settings, "VISION_DESCRIBE_ENABLED", False)
    image_describe._vision_client = None
    provider = FakeProvider()

    with respx.mock:
        route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
        res = await _describe(provider)

    assert route.call_count == 0  # nothing left the box
    assert res.state == "skipped"
    assert res.reason == "disabled"


# === The HI-01 lesson: flag off a FRESH read ================================


@respx.mock
async def test_parent_flag_is_written_off_a_fresh_read_not_the_captured_snapshot():
    """A PATCH landing during the seconds-long vision call must not be clobbered.

    provider.update() REPLACES metadata wholesale. The snapshot passed in at schedule time
    is stale by the time the model answers, so the flag must be merged onto a FRESH get.
    Here the "concurrent write" (a caption edit) exists only in the fresh read; if the code
    patched off the snapshot it would be silently lost.
    """
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())

    stale_snapshot = {"media": {"key": "media/team-alpha/parent.png"}}
    concurrently_written = MemoryItem(
        id="PARENT",
        team_scope=TEAM,
        content="a caption",
        metadata={
            **stale_snapshot,
            "edited_during_the_vision_call": "must survive",
        },
        source="upload:extension",
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        updated_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    provider = FakeProvider(current=concurrently_written)

    await _describe(provider, parent_metadata=stale_snapshot)

    assert provider.get_calls >= 1, "the parent was never re-read before patching"
    patched = provider.updates[-1][2]["metadata"]
    assert patched["edited_during_the_vision_call"] == "must survive"  # NOT clobbered
    assert patched[FLAG_KEY]["state"] == "described"
    assert patched["media"] == stale_snapshot["media"]


@respx.mock
async def test_flag_falls_back_to_the_snapshot_when_the_parent_vanished():
    """Deleted mid-flight: get returns None, so the snapshot is the only base available."""
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    provider = FakeProvider(current=None)

    await _describe(provider, parent_metadata={"media": {"key": "k"}})

    patched = provider.updates[-1][2]["metadata"]
    assert patched["media"] == {"key": "k"}
    assert patched[FLAG_KEY]["state"] == "described"


# === The route: the 201 is not negotiable ===================================


@pytest.fixture
async def upload_client(monkeypatch):
    """The real FastAPI app with a fake provider + fake object storage.

    Deliberately NOT the Docker-backed `client` fixture: what is under test here is that
    the upload's 201 is unaffected by the vision path, which needs no database.
    """
    from app.deps import get_current_principal, get_memory_provider, get_team_scope
    from app.main import app
    from app.routes import media as media_mod

    provider = FakeProvider()

    class FakeMinio:
        def head_bucket(self, **kw):
            return {}

        def put_object(self, **kw):
            return {}

    monkeypatch.setattr(media_mod, "get_minio_client", lambda: FakeMinio())
    # _ensure_bucket lazily imports botocore, which is a Docker-only dep here. Stubbing it
    # replaces INFRASTRUCTURE, not the path under test.
    monkeypatch.setattr(media_mod, "_ensure_bucket", lambda client, bucket: None)
    app.dependency_overrides[get_memory_provider] = lambda: provider
    app.dependency_overrides[get_current_principal] = lambda: {"sub": "alice"}
    app.dependency_overrides[get_team_scope] = lambda: TEAM
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, provider
    app.dependency_overrides.clear()


async def _drain_background_tasks() -> None:
    """Let the detached describe task finish before asserting on its effects."""
    for _ in range(50):
        pending = [
            t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()
        ]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


@respx.mock
async def test_a_model_failure_leaves_the_uploads_201_untouched(upload_client):
    """The upload's 201 is not negotiable — a broken vision path is invisible to the uploader."""
    respx.post(MESSAGES_URL).mock(return_value=httpx.Response(500, json={"error": "boom"}))
    client, provider = upload_client

    resp = await client.post(
        "/v1/media/upload",
        files={"file": ("diagram.png", TINY_PNG, "image/png")},
        data={"caption": "the arch diagram", "truth_level": "WORKING"},
    )

    assert resp.status_code == 201, resp.text
    assert resp.json()["mime"] == "image/png"

    await _drain_background_tasks()

    # ...and the failure is recorded on the parent rather than silently dropped.
    assert provider.updates, "the parent was never flagged after the failure"
    assert provider.flag["state"] in ("failed", "skipped")


@respx.mock
async def test_a_successful_upload_gets_its_description_after_the_201(upload_client):
    respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    client, provider = upload_client

    resp = await client.post(
        "/v1/media/upload",
        files={"file": ("diagram.png", TINY_PNG, "image/png")},
        data={"caption": "the arch diagram"},
    )
    assert resp.status_code == 201
    parent_id = resp.json()["item_id"]

    # The parent committed by the request carries the CAPTION, not the description.
    parent = next(i for i in provider.items if i.id == parent_id)
    assert parent.content == "the arch diagram"

    await _drain_background_tasks()

    child = next(i for i in provider.items if i.metadata.get("kind") == "image_description")
    assert child.content == DESCRIPTION
    assert child.metadata["parent_item_id"] == parent_id
    assert child.source.startswith("vision:")


@respx.mock
async def test_a_non_image_upload_never_reaches_the_vision_path(upload_client):
    """A PDF is the document path's business; an image-description flag there is noise."""
    route = respx.post(MESSAGES_URL).mock(return_value=anthropic_response())
    client, provider = upload_client

    resp = await client.post(
        "/v1/media/upload",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )
    assert resp.status_code == 201

    await _drain_background_tasks()

    assert route.call_count == 0
    assert all(FLAG_KEY not in (p.get("metadata") or {}) for _i, _t, p in provider.updates)


# === Dimension probe: pure header parsing ===================================


class TestProbeImageSize:
    def test_png(self):
        assert probe_image_size(make_png(1920, 1080), "image/png") == (1920, 1080)

    def test_gif(self):
        assert probe_image_size(make_gif(640, 480), "image/gif") == (640, 480)

    def test_jpeg_skips_intervening_segments(self):
        assert probe_image_size(make_jpeg(800, 600), "image/jpeg") == (800, 600)

    def test_webp_vp8x(self):
        assert probe_image_size(make_webp_vp8x(2048, 1536), "image/webp") == (2048, 1536)

    @pytest.mark.parametrize(
        "data",
        [b"", b"not an image", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF____WEBP"],
        ids=["empty", "garbage", "truncated-png", "truncated-jpeg", "truncated-webp"],
    )
    def test_hostile_or_truncated_bytes_return_none_never_raise(self, data):
        for mime in VISION_MIMES:
            assert probe_image_size(data, mime) is None

    def test_unknown_mime_returns_none(self):
        assert probe_image_size(make_png(10, 10), "application/pdf") is None


class TestIsImageMime:
    @pytest.mark.parametrize(
        "mime,expected",
        [
            ("image/png", True),
            ("image/jpeg", True),
            ("IMAGE/PNG", True),
            ("image/png; charset=binary", True),
            ("image/bmp", True),
            ("application/pdf", False),
            ("text/plain", False),
            ("", False),
            (None, False),
        ],
    )
    def test_gate(self, mime, expected):
        assert is_image_mime(mime) is expected


# === THE GATE: the description is actually RETRIEVABLE (real infra) =========


@pytest.mark.integration
async def test_image_description_is_retrieved_by_a_search_for_its_content(pg_url, qdrant_url):
    """A phrase present ONLY in the model's description retrieves the image.

    This is the gate: the parent item's content is the caption/filename, and the search
    phrase appears in NEITHER. Only a real, embedded description can answer this query —
    against real Postgres + real Qdrant with the real keyless local embedder. A description
    that was stored but never embedded, or embedded under the wrong scope, fails here.
    """
    import os
    import tempfile

    from xbrain_memory.providers.native_provider import NativeProvider

    from app.embedders import get_embedder, local_embedder
    from app.qdrant_setup import ensure_collections

    phrase = "a peregrine falcon perched on a rusted radio mast at dusk"

    orig = (
        settings.EMBEDDINGS_PROVIDER,
        settings.OPENAI_API_KEY,
        settings.EMBEDDING_CACHE_DIR,
    )
    cache_dir = os.path.join(tempfile.gettempdir(), "xbrain_fastembed_cache")
    os.makedirs(cache_dir, exist_ok=True)
    settings.EMBEDDINGS_PROVIDER = "local"
    settings.OPENAI_API_KEY = ""
    settings.EMBEDDING_CACHE_DIR = cache_dir
    try:
        try:
            await local_embedder("warmup: materialize the real fastembed model")
        except (ConnectionError, OSError) as e:
            pytest.skip(f"local model not cached and no network to download it: {e}")

        await ensure_collections()
        provider = NativeProvider(
            pg_dsn=settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"),
            qdrant_url=settings.QDRANT_URL,
            embedder=get_embedder(),
            qdrant_api_key=settings.QDRANT_API_KEY,
        )
        try:
            # Mock ONLY the Anthropic transport; Qdrant's httpx traffic must reach the real
            # container, so everything else passes through untouched.
            with respx.mock(assert_all_called=False) as router:
                router.post(MESSAGES_URL).mock(
                    return_value=anthropic_response(text=f"A photograph showing {phrase}.")
                )
                router.route().pass_through()

                res = await describe_and_ingest_image(
                    provider=provider,
                    data=TINY_PNG,
                    mime="image/png",
                    filename="IMG_4821.png",   # the phrase is in NEITHER filename...
                    media_key="media/vision-gate/IMG_4821.png",
                    parent_item_id="VISION-GATE-PARENT",
                    team_scope="vision-gate",
                    project_scope="gate",
                    truth_level="WORKING",
                    parent_metadata={},
                )
                assert res.state == "described", res.reason

                hits = await provider.search(phrase, team_scope="vision-gate", limit=5)
        finally:
            try:
                if getattr(provider, "_pool", None) is not None:
                    await provider._pool.close()
            finally:
                try:
                    await provider._qdrant.close()
                except Exception:
                    pass
    finally:
        (
            settings.EMBEDDINGS_PROVIDER,
            settings.OPENAI_API_KEY,
            settings.EMBEDDING_CACHE_DIR,
        ) = orig

    assert hits, "a search for the description's content returned nothing"
    top = hits[0]
    assert top.item.metadata["parent_item_id"] == "VISION-GATE-PARENT"
    assert top.item.source.startswith("vision:")
    assert top.item.team_scope == "vision-gate"
    assert all(h.item.team_scope == "vision-gate" for h in hits)
