"""A token minted at sign-in must be able to reach a real team.

The bug this locks down, found on 2026-08-02 by an image upload failing with
`403 API token team_scope mismatch with X-Team-Scope header` on BOTH the extension
and the PWA:

`POST /v1/me/api-token` declared `team_scope` as REQUIRED with `min_length=1`, so the
multi-team sentinel (the empty string) was unreachable through that route. Every web
and extension sign-in therefore sent the literal string `"default"` — because it had to
send *something* — and got a token pinned to a scope that matches no real team slug.

Nothing noticed for a long time because no feature had yet sent a genuine slug in
`X-Team-Scope`. Media upload was the first, and it 403'd everywhere at once.

`deps.get_team_scope` treats an empty `api_token_team_scope` as "not pinned — fall
through to the membership check", which is exactly what a sign-in token needs and
exactly what `services/api_tokens.mint_xbt_for_user` already wrote for every GitHub and
local sign-in. The web path was the odd one out.

These tests assert the contract in both directions: omitting the field yields a
multi-team token, and passing a real slug still pins one.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


def _model():
    from app.routes.me import ApiTokenCreateBody as ApiTokenCreate

    return ApiTokenCreate


async def test_omitting_team_scope_yields_the_multi_team_sentinel():
    """The default must be the sentinel, not a made-up scope.

    This is the whole bug: a required field forced every caller to invent a value,
    and the value they invented ("default") named no team.
    """
    body = _model()(name="pwa")
    assert body.team_scope == "", (
        "a token minted without an explicit scope must carry the multi-team sentinel; "
        f"got {body.team_scope!r}, which pins the token to a scope of that literal name"
    )


async def test_the_empty_string_is_accepted_explicitly():
    """Clients send `team_scope: ""` today; the schema must not reject it.

    A min_length=1 here is what made the sentinel unreachable in the first place.
    """
    body = _model()(team_scope="", name="join-page")
    assert body.team_scope == ""


async def test_a_real_slug_still_pins_the_token():
    """Scoping stays available — this fix widens the default, it does not remove pinning."""
    body = _model()(team_scope="excalibur-game", name="ci")
    assert body.team_scope == "excalibur-game"


async def test_the_sentinel_is_what_get_team_scope_treats_as_multi_team():
    """Guard the two halves against drifting apart.

    `get_team_scope` only skips the pin-check when the stored scope is falsy. If that
    ever becomes a check against some other marker, the mint default must move with it,
    or sign-in tokens go back to being unusable against every real team.
    """
    import inspect

    from app.deps import get_team_scope

    src = inspect.getsource(get_team_scope)
    assert "api_token_team_scope" in src
    # The falsy-scope skip: `if scope and scope != x_team_scope:` — a pinned token is
    # rejected on mismatch, an unpinned one falls through to the membership check.
    assert "if scope and scope != x_team_scope" in src, (
        "get_team_scope no longer skips the pin-check on a falsy scope. The empty-string "
        "sentinel written by mint_xbt_for_user AND by ApiTokenCreate's default depends on "
        "that behaviour; changing one without the other silently breaks every sign-in token."
    )


async def test_no_client_mints_a_token_pinned_to_a_scope_that_names_no_team():
    """The clients are part of this contract, so assert on them too.

    Four separate mint sites sent `team_scope: "default"`. A server-side default fixes
    new code, but a client that keeps passing the old literal overrides it — the fix has
    to hold on both sides or it only half-works.
    """
    import pathlib
    import re

    repo = pathlib.Path(__file__).resolve().parents[3]
    offenders = []
    for path in list((repo / "app-site").rglob("*.js")) + list(
        (repo / "app-site").rglob("*.html")
    ) + list((repo / "chrome-extension").glob("*.js")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r'team_scope:\s*["\']default["\']', text):
            offenders.append(str(path.relative_to(repo)))

    assert not offenders, (
        "these mint a token pinned to the literal scope \"default\", which matches no "
        f"real team and 403s on every team-scoped endpoint: {offenders}"
    )
