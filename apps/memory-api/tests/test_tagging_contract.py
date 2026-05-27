"""Contract enforcement tests — these LOCK the success criterion 2 of Phase 1.

Each missing/invalid field in the tagging payload MUST trigger HTTP 422 (or Pydantic
ValidationError when imported directly). If any test here regresses, the contract is broken.
"""

import pytest
from pydantic import ValidationError

from app.models.tagging import (
    SOURCE_PATTERN,
    TaggingContract,
    TruthLevel,
    ValidationStatus,
    Visibility,
)

# --- Pydantic-level validation (no FastAPI / no DB) ---


def test_full_payload_validates():
    t = TaggingContract(
        team_scope="team-a",
        project_scope="proj-x",
        visibility=Visibility.TEAM,
        confidence=0.9,
        truth_level=TruthLevel.EPHEMERAL,
        source="librechat:claude-3-5-sonnet",
        validation_status=ValidationStatus.PENDING,
    )
    assert t.team_scope == "team-a"
    assert t.confidence == 0.9


def test_missing_team_scope_raises():
    with pytest.raises(ValidationError) as ei:
        TaggingContract(
            visibility=Visibility.TEAM,
            confidence=0.5,
            source="librechat:gpt-4o",
        )
    assert any("team_scope" in str(err) for err in ei.value.errors())


def test_missing_visibility_raises():
    with pytest.raises(ValidationError):
        TaggingContract(team_scope="t", confidence=0.5, source="x:y")


def test_missing_confidence_raises():
    with pytest.raises(ValidationError):
        TaggingContract(team_scope="t", visibility=Visibility.TEAM, source="x:y")


def test_missing_source_raises():
    with pytest.raises(ValidationError):
        TaggingContract(team_scope="t", visibility=Visibility.TEAM, confidence=0.5)


def test_extra_field_rejected():
    """`extra='forbid'` is the differentiator — silent passthrough would leak fields into DB."""
    with pytest.raises(ValidationError) as ei:
        TaggingContract(
            team_scope="t",
            visibility=Visibility.TEAM,
            confidence=0.5,
            source="x:y",
            unknown_extra_field="should be rejected",
        )
    assert "unknown_extra_field" in str(ei.value)


def test_invalid_source_format_rejected():
    """Source must match `^[a-z][a-z0-9_-]*:[a-z0-9._-]+$` — prevents free-form garbage."""
    bad_sources = ["NoColon", "BAD:gpt", "a-b:gpt", "1lower:x", "free form:gpt"]
    for s in bad_sources:
        with pytest.raises(ValidationError):
            TaggingContract(
                team_scope="t",
                visibility=Visibility.TEAM,
                confidence=0.5,
                source=s,
            )


def test_valid_source_formats_accepted():
    good_sources = [
        "librechat:claude-3-5-sonnet",
        "openwebui:gpt-4o",
        "agent:ingestion-v1",
        "manual:user-import",
    ]
    for s in good_sources:
        TaggingContract(
            team_scope="t", visibility=Visibility.TEAM, confidence=0.5, source=s
        )


def test_confidence_out_of_range_rejected():
    for bad in [-0.1, 1.5, 2.0]:
        with pytest.raises(ValidationError):
            TaggingContract(
                team_scope="t",
                visibility=Visibility.TEAM,
                confidence=bad,
                source="a:b",
            )


def test_default_truth_level_is_ephemeral():
    t = TaggingContract(
        team_scope="t", visibility=Visibility.TEAM, confidence=0.5, source="a:b"
    )
    assert t.truth_level == TruthLevel.EPHEMERAL


def test_default_validation_status_is_pending():
    t = TaggingContract(
        team_scope="t", visibility=Visibility.TEAM, confidence=0.5, source="a:b"
    )
    assert t.validation_status == ValidationStatus.PENDING


def test_invalid_truth_level_rejected():
    with pytest.raises(ValidationError):
        TaggingContract(
            team_scope="t",
            visibility=Visibility.TEAM,
            confidence=0.5,
            source="a:b",
            truth_level="bogus",
        )


def test_invalid_visibility_rejected():
    with pytest.raises(ValidationError):
        TaggingContract(
            team_scope="t",
            visibility="overlord",  # type: ignore
            confidence=0.5,
            source="a:b",
        )


def test_team_scope_too_long_rejected():
    with pytest.raises(ValidationError):
        TaggingContract(
            team_scope="x" * 200,
            visibility=Visibility.TEAM,
            confidence=0.5,
            source="a:b",
        )


# --- HTTP-level enforcement via FastAPI (integration: needs DB) ---


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_message_missing_field_returns_422(client, seeded_two_teams, bridge_jwt):
    """The FastAPI route must reject incomplete payloads with 422 (not 400 / not silent)."""
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    # Missing `confidence`
    r = await client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        json={
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "role": "user",
            "content": "hello",
            "metadata": {},
            "tagging": {
                "team_scope": "team-a",
                "visibility": "team",
                "source": "librechat:claude-3-5",
            },
        },
    )
    assert r.status_code == 422, r.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_message_extra_field_returns_422(client, seeded_two_teams, bridge_jwt):
    token = bridge_jwt(sub="alice-sub", team_scope="team-a")
    r = await client.post(
        "/v1/messages",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": "team-a"},
        json={
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "role": "user",
            "content": "hi",
            "metadata": {},
            "tagging": {
                "team_scope": "team-a",
                "visibility": "team",
                "confidence": 1.0,
                "source": "librechat:gpt-4o",
                "validation_status": "pending",
                "truth_level": "EPHEMERAL",
                "unauthorized_extra": "leak",
            },
        },
    )
    assert r.status_code == 422
    assert "unauthorized_extra" in r.text


# Verify regex pattern is what we expect (regression guard for the source_pattern constant)
def test_source_pattern_constant_is_correct():
    assert SOURCE_PATTERN == r"^[a-z][a-z0-9_]*:[a-z0-9._-]+$"
