"""Closed signup, and the three doors it has to close at once.

Until 2026-08-13 anyone could create an account here in one request. The obvious
door was `POST /v1/auth/local/register`. The two easy ones to miss are in
deps.py: every Google and GitHub sign-in path calls `get_or_create_user`, so a
valid provider token minted an account on first use. Closing only the obvious
one would have looked done and changed nothing.

These are unit tests on the policy and the repo contract; the wiring of the
three deps.py paths is asserted structurally at the bottom, which is what fails
loudly if someone adds a fourth creation site later.
"""
from __future__ import annotations

import inspect

import pytest

from app.config import settings
from app.repos import users as users_repo
from app.services import signup_policy


@pytest.fixture
def closed(monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_POLICY", "closed", raising=False)
    monkeypatch.setattr(settings, "SIGNUP_ALLOWLIST", "", raising=False)


def test_open_policy_admits_everyone():
    """The default must stay permissive — an OSS install has to boot usable."""
    assert settings.SIGNUP_POLICY == "open"
    assert signup_policy.account_creation_allowed("anyone@example.com") is True


def test_closed_policy_refuses_an_unlisted_address(closed):
    assert signup_policy.account_creation_allowed("stranger@example.com") is False


def test_closed_policy_admits_an_exact_address(closed, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ALLOWLIST", "her@example.com", raising=False)
    assert signup_policy.account_creation_allowed("her@example.com") is True
    assert signup_policy.account_creation_allowed("him@example.com") is False


def test_a_domain_entry_admits_the_whole_domain(closed, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ALLOWLIST", "@example.com", raising=False)
    assert signup_policy.account_creation_allowed("anyone@example.com") is True
    assert signup_policy.account_creation_allowed("anyone@other.com") is False


def test_a_domain_entry_does_not_match_a_lookalike_suffix(closed, monkeypatch):
    """`@example.com` must not admit `evil-example.com`."""
    monkeypatch.setattr(settings, "SIGNUP_ALLOWLIST", "@example.com", raising=False)
    assert signup_policy.account_creation_allowed("attacker@notexample.com") is False


def test_matching_is_case_insensitive(closed, monkeypatch):
    """A case-sensitive allowlist is one that silently fails."""
    monkeypatch.setattr(settings, "SIGNUP_ALLOWLIST", "Her@Example.COM", raising=False)
    assert signup_policy.account_creation_allowed("her@example.com") is True
    assert signup_policy.account_creation_allowed("  HER@EXAMPLE.com ") is True


def test_a_blank_email_is_refused_when_closed(closed, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_ALLOWLIST", "@example.com", raising=False)
    for blank in (None, "", "   "):
        assert signup_policy.account_creation_allowed(blank) is False


def test_the_list_tolerates_spacing_and_semicolons(closed, monkeypatch):
    monkeypatch.setattr(
        settings, "SIGNUP_ALLOWLIST", " a@x.com ; @y.com ,b@z.com", raising=False
    )
    for addr in ("a@x.com", "someone@y.com", "b@z.com"):
        assert signup_policy.account_creation_allowed(addr) is True
    assert signup_policy.account_creation_allowed("c@w.com") is False


def test_the_refusal_reveals_nothing_about_the_address():
    """One message everywhere, or the 403 becomes an enumeration oracle."""
    detail = signup_policy.refusal_detail()
    for leak in ("allowlist", "registered", "exists", "unknown", "@"):
        assert leak not in detail.lower()


def test_a_typo_in_the_policy_fails_at_boot():
    """Silently defaulting to open on a typo is the failure that matters."""
    from app.config import Settings

    with pytest.raises(ValueError, match="SIGNUP_POLICY"):
        Settings(SIGNUP_POLICY="clsoed")


# --- the repo contract the three deps.py paths depend on ---------------------


def test_get_or_create_user_can_be_told_not_to_create():
    """allow_create=False must be get-or-nothing, never create-anyway."""
    param = inspect.signature(users_repo.get_or_create_user).parameters["allow_create"]
    assert param.default is True, "existing callers must keep creating"
    src = inspect.getsource(users_repo.get_or_create_user)
    assert "if not allow_create:" in src
    assert "return existing.scalar_one_or_none()" in src


def test_every_human_signin_path_consults_the_policy():
    """The three doors in deps.py. A fourth added later fails this test.

    Asserted structurally rather than end-to-end because each path needs a real
    Google/GitHub token to exercise; what must never regress is that no human
    creation site is left unguarded.
    """
    from app import deps

    src = inspect.getsource(deps)
    creation_sites = src.count("user = await get_or_create_user(")
    guarded = src.count("allow_create=signup_policy.account_creation_allowed(")
    refusals = src.count("signup_policy.refusal_detail()")

    assert creation_sites == 5, (
        f"expected 5 creation sites (3 human + 2 service), found {creation_sites} — "
        f"a new one must be classified before this test is updated"
    )
    assert guarded == 3, f"expected the 3 human paths guarded, found {guarded}"
    assert refusals == 3


def test_the_service_paths_are_deliberately_left_open():
    """The bridge and the OpenWebUI pipeline must keep resolving identities.

    They authenticate with the shared secret and assert a subject they were
    already trusted to assert; gating them would break LibreChat and Open WebUI
    for people who already have access. That exemption is a decision, so it is
    pinned rather than left to be rediscovered.
    """
    from app import deps

    src = inspect.getsource(deps)
    assert "openwebui-pipeline" in src
    # The two service creation sites sit outside any policy call: 5 sites, 3 guarded.
    assert src.count("user = await get_or_create_user(") - src.count(
        "allow_create=signup_policy.account_creation_allowed("
    ) == 2
