"""Phase 27 — "does this message @mention a PERSON?" (PUSH-01, D-27-06).

The agent detector already answers "does this message summon the agent?" with a
boundary-anchored regex that has been tuned by two phases of edge cases:
`alice@groove.com` is not a mention, `@google` is not a mention, `@gr` does not match
`@group`. A human-mention check needs EXACTLY those properties — an email address in a
message must not notify whoever owns that local part, and a longer handle must not be
matched by a shorter one's prefix.

So the human path reuses `_regex_for` rather than growing a second regex with its own
subtly different boundary rule. The tests below therefore assert two things at once:
that human mentions resolve, and that they inherit the agent detector's non-matches.

The one asymmetry worth stating: `@agent` (and the reserved `@claude`) can never resolve
to a person, even if someone's display name is literally "Agent". A message that summons
the agent must not also push a notification to an unlucky teammate.
"""
from __future__ import annotations

from uuid import uuid4

ADA = str(uuid4())
BOB = str(uuid4())
CAROL = str(uuid4())


def _member(user_id, *, display_name=None, email=None, github_username=None) -> dict:
    return {
        "user_id": user_id,
        "display_name": display_name,
        "email": email,
        "github_username": github_username,
    }


ADA_M = _member(ADA, display_name="Ada Lovelace", email="ada@x.com", github_username="adal")
BOB_M = _member(BOB, display_name="Bob", email="bob@x.com", github_username=None)
TEAM = [ADA_M, BOB_M]


# ── user_mention_tokens — the handles a person can be reached by ──────────────


def test_tokens_are_derived_from_every_identity_a_member_already_has():
    """There is no @handle field in the product, so a mention has to resolve against
    what a member ALREADY has: a GitHub username, an email local part, a display name."""
    from app.services.mention_detector import user_mention_tokens

    tokens = user_mention_tokens(
        display_name="Ada Lovelace", email="ada@x.com", github_username="adal"
    )
    assert set(tokens) == {"adal", "ada", "adalovelace"}
    assert tokens == [t.lower() for t in tokens], "tokens must be lowercase"
    assert tokens == user_mention_tokens(
        display_name="Ada Lovelace", email="ada@x.com", github_username="adal"
    ), "derivation must be deterministic — the regex cache is keyed on it"


def test_tokens_drop_an_email_plus_tag():
    """`ada+xbrain@x.com` is the same person as `ada@x.com`; nobody types the tag."""
    from app.services.mention_detector import user_mention_tokens

    assert "ada" in user_mention_tokens(
        display_name=None, email="ada+xbrain@x.com", github_username=None
    )


def test_tokens_reject_single_characters_and_unusable_shapes():
    """A one-character handle would fire on ordinary text (`@ 5pm`, `@a`), and a token
    outside [a-z0-9_-] cannot be a handle anyone types — accented names, spaces and
    dotted email locals are left to the member's other identities rather than mangled
    into a wrong handle (`josé` must not become `jos`)."""
    from app.services.mention_detector import user_mention_tokens

    assert user_mention_tokens(display_name="A", email=None, github_username=None) == []
    assert user_mention_tokens(display_name="José", email=None, github_username=None) == []
    assert user_mention_tokens(display_name=None, email=None, github_username=None) == []
    assert user_mention_tokens(display_name="  ", email="", github_username=None) == []


# ── detect_user_mentions ──────────────────────────────────────────────────────


def test_a_mention_resolves_to_the_member():
    from app.services.mention_detector import detect_user_mentions

    assert detect_user_mentions("hey @ada can you look", TEAM) == [ADA]


def test_a_message_mentioning_nobody_resolves_to_nobody():
    """The load-bearing negative for D-27-06: most messages must push to NO ONE."""
    from app.services.mention_detector import detect_user_mentions

    assert detect_user_mentions("shipping the release now", TEAM) == []
    assert detect_user_mentions("", TEAM) == []


def test_an_email_address_is_not_a_mention():
    """Inherited from the agent detector's boundary rule: `@` preceded by a word
    character is part of an address, not a mention. Getting this wrong would notify
    someone every time their address is pasted."""
    from app.services.mention_detector import detect_user_mentions

    assert detect_user_mentions("mail me at ada@x.com", TEAM) == []
    assert detect_user_mentions("cc bob@x.com please", TEAM) == []


def test_a_longer_handle_is_not_matched_by_a_shorter_one():
    """`@adalovelace2` is somebody else (or nobody). Prefix matching here would make
    every `@ada*` handle in the world notify Ada."""
    from app.services.mention_detector import detect_user_mentions

    assert detect_user_mentions("ping @adalovelace2 about it", TEAM) == []
    assert detect_user_mentions("see @adalovelace for it", TEAM) == [ADA]


def test_several_mentions_return_every_member_once_in_order():
    from app.services.mention_detector import detect_user_mentions

    assert detect_user_mentions("@ada and @bob, then @ada again", TEAM) == [ADA, BOB]
    assert detect_user_mentions("@bob and @ada", TEAM) == [BOB, ADA], "order must follow the text"


def test_matching_is_case_insensitive():
    """People capitalise names. `@AdA` is Ada."""
    from app.services.mention_detector import detect_user_mentions

    assert detect_user_mentions("@AdA look", TEAM) == [ADA]
    assert detect_user_mentions("@ADAL look", TEAM) == [ADA], "the GitHub handle too"


def test_a_member_with_only_an_email_still_resolves():
    """Local sign-in accounts have no display name and no GitHub username. They must
    still be reachable, or push silently works for some members and not others."""
    from app.services.mention_detector import detect_user_mentions

    carol = _member(CAROL, email="carol@x.com")
    assert detect_user_mentions("@carol are you around", [carol]) == [CAROL]


def test_colliding_tokens_notify_every_member_that_owns_them():
    """Two people can genuinely share a handle (`Chris Adams` and `chris@x.com`). The
    ambiguous case must notify BOTH rather than silently pick one — a dropped mention is
    invisible to the sender, who believes they reached someone."""
    from app.services.mention_detector import detect_user_mentions

    chris_a = _member(ADA, display_name="Chris Adams")
    chris_b = _member(BOB, email="chris@x.com")
    assert detect_user_mentions("@chris ping", [chris_a, chris_b]) == [ADA, BOB]


def test_the_agent_aliases_never_resolve_to_a_human():
    """`@agent` summons the agent. If a member happens to be called "Agent", the summon
    must not ALSO fire a push at them — and `@claude` is reserved everywhere (D-21-01)."""
    from app.services.mention_detector import detect_user_mentions

    impostor = _member(CAROL, display_name="Agent", github_username="claude")
    assert detect_user_mentions("@agent summarise this", [impostor]) == []
    assert detect_user_mentions("@claude summarise this", [impostor]) == []


def test_a_member_without_a_user_id_is_ignored():
    """Defensive: a malformed member dict must not crash the message-post path."""
    from app.services.mention_detector import detect_user_mentions

    assert detect_user_mentions("@ada hi", [{"display_name": "Ada"}]) == []
    assert detect_user_mentions("@ada hi", []) == []


def test_the_human_path_reuses_the_agent_detector_regex_builder():
    """Structural, because the boundary rule is the thing that must not fork. Two
    regexes for "is this an @mention" drift, and the drift shows up as a mention that
    notifies nobody (or an email address that notifies someone)."""
    import ast
    from pathlib import Path

    from app.services import mention_detector

    tree = ast.parse(Path(mention_detector.__file__).read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "detect_user_mentions"
    )
    called = {
        node.func.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_regex_for" in called, "the human path built its own regex instead of reusing _regex_for"
    assert "re.compile" not in ast.unparse(fn)
