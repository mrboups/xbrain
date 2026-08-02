"""The capability allow-list — the narrowing gate for scoped API tokens.

The interesting property is not that an import token works on the import
endpoint. It is that it works NOWHERE ELSE, including on endpoints that did
not exist when the gate was written. ``test_every_route_in_the_app_is_refused_
except_the_allow_list`` enumerates the live route table and asserts exactly
that, so a route added in a future phase is covered by this test the day it is
added rather than the day someone remembers to audit it.
"""
from __future__ import annotations

import pytest

from app.services import token_capabilities as tc


def test_an_unrestricted_token_is_not_narrowed():
    """capability=None is every token minted before this feature. Nothing changes for them."""
    assert tc.is_path_allowed(None, "/v1/me") is True
    assert tc.is_path_allowed(None, "/v1/brain/ingest") is True
    assert tc.is_path_allowed(None, "/anything/at/all") is True


def test_import_token_reaches_the_import_endpoint():
    assert tc.is_path_allowed(tc.IMPORT, "/v1/import/transcript") is True


@pytest.mark.parametrize(
    "path",
    [
        "/v1/me",
        "/v1/me/api-token",
        "/v1/me/import-tokens",
        "/v1/teams",
        "/v1/brain/ingest",
        "/v1/memory/search",
        "/v1/media/upload",
        "/v1/import",
        "/v1/import/transcript/../me",
        "/v1/import/transcriptX",
        "//v1/import/transcript",
        "/V1/IMPORT/TRANSCRIPT",
        "",
    ],
)
def test_import_token_is_refused_everywhere_else(path):
    assert tc.is_path_allowed(tc.IMPORT, path) is False


def test_a_trailing_slash_is_the_same_endpoint():
    assert tc.is_path_allowed(tc.IMPORT, "/v1/import/transcript/") is True


@pytest.mark.parametrize("capability", ["admin", "IMPORT", "", "ingest", "*"])
def test_an_unknown_capability_can_reach_nothing(capability):
    """Fail closed: a typo in the column is a token that does nothing, never a wildcard."""
    assert tc.is_path_allowed(capability, "/v1/import/transcript") is False
    assert tc.is_path_allowed(capability, "/v1/me") is False


def test_none_path_is_refused_for_a_scoped_token():
    """No request context = cannot prove which endpoint is being reached = refuse."""
    assert tc.is_path_allowed(tc.IMPORT, None) is False


def test_capability_for_prefix():
    assert tc.capability_for_prefix("xbi_abc") == tc.IMPORT
    assert tc.capability_for_prefix("xbt_abc") is None
    assert tc.capability_for_prefix("gho_abc") is None
    assert tc.capability_for_prefix(None) is None


def test_scoped_prefixes_do_not_collide_with_the_unrestricted_one():
    for prefix in tc.SCOPED_TOKEN_PREFIXES:
        assert not prefix.startswith("xbt_")
        assert not "xbt_".startswith(prefix)


def test_the_allow_list_names_exactly_one_path_today():
    """A widening of this list should be a deliberate, reviewed diff — not a drift.

    The live-route-table version of this assertion (every mounted path refused
    except the allow-list) lives in tests/test_transcript_import_api.py, next to
    the endpoint it protects.
    """
    assert tc.ALLOWED_PATHS == {tc.IMPORT: frozenset({"/v1/import/transcript"})}
