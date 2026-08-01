"""The label ladder — app/services/user_label.py.

Pure unit tests: no DB, no Docker, no app import beyond the helper itself.

What these lock down:
  - the precedence order, tier by tier, including the "clearing preferred_name
    falls back to the provider name" path that makes a rename undoable;
  - the email edge cases (no @, empty local part, whitespace, very long);
  - that "A teammate" is UNREACHABLE for any account that has any identifier at
    all. That string leaking into the UI as "Teammate" is the defect that
    started this work, so it gets its own class.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.user_label import (
    LAST_RESORT_LABEL,
    MAX_LABEL_LENGTH,
    email_local_part,
    label_from_parts,
    resolve_user_label,
)


def _user(**kw):
    """A user-like row; every profile attribute defaults to None."""
    base = {
        "preferred_name": None,
        "display_name": None,
        "email": None,
        "source_user_id": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestPrecedenceLadder:
    """1. preferred_name → 2. display_name → 3. email local part → 4. sub."""

    def test_preferred_name_wins_over_everything(self):
        u = _user(
            preferred_name="Nico",
            display_name="Excalibur Team",
            email="team@excalibur.game",
            source_user_id="google-123",
        )
        assert resolve_user_label(u) == "Nico"

    def test_provider_display_name_beats_the_email(self):
        # The correction that matters: a real Google name is better than an
        # email fragment. `team@excalibur.game` with a provider name reads
        # "Excalibur Team", not "team".
        u = _user(
            display_name="Excalibur Team",
            email="team@excalibur.game",
            source_user_id="google-123",
        )
        assert resolve_user_label(u) == "Excalibur Team"

    def test_email_local_part_when_the_provider_sent_no_name(self):
        u = _user(email="nicoboups@gmail.com", source_user_id="google-123")
        assert resolve_user_label(u) == "nicoboups"

    def test_source_user_id_when_there_is_no_usable_email(self):
        u = _user(email="@nodomain.example", source_user_id="google-123")
        assert resolve_user_label(u) == "google-123"

    def test_local_part_is_taken_verbatim_not_capitalised(self):
        # A handle that is deliberately lowercase stays lowercase.
        assert resolve_user_label(_user(email="nicoboups@gmail.com")) == "nicoboups"
        assert resolve_user_label(_user(email="team@excalibur.game")) == "team"

    def test_clearing_preferred_name_restores_the_provider_name(self):
        # PATCH with "" writes NULL; the ladder must fall back on its own, with
        # no backfill and no support ticket. This is the undo path.
        renamed = _user(preferred_name="Nico", display_name="Excalibur Team", email="team@excalibur.game")
        cleared = _user(preferred_name=None, display_name="Excalibur Team", email="team@excalibur.game")
        assert resolve_user_label(renamed) == "Nico"
        assert resolve_user_label(cleared) == "Excalibur Team"

    def test_whitespace_only_preferred_name_is_not_a_name(self):
        # Defence in depth: the route strips before writing, but a row that
        # already holds "   " must not render as a blank bubble header.
        u = _user(preferred_name="   ", display_name="Excalibur Team")
        assert resolve_user_label(u) == "Excalibur Team"

    def test_surrounding_whitespace_is_stripped_from_the_winner(self):
        assert resolve_user_label(_user(preferred_name="  Nico  ")) == "Nico"


class TestEmailEdgeCases:
    def test_no_at_sign_uses_the_whole_string(self):
        assert email_local_part("notanemail") == "notanemail"
        assert resolve_user_label(_user(email="notanemail")) == "notanemail"

    def test_empty_local_part_yields_nothing(self):
        assert email_local_part("@example.com") == ""

    def test_whitespace_only_local_part_yields_nothing(self):
        assert email_local_part("   @example.com") == ""

    def test_whitespace_around_the_local_part_is_stripped(self):
        assert email_local_part("  team@excalibur.game") == "team"

    def test_none_and_empty_email(self):
        assert email_local_part(None) == ""
        assert email_local_part("") == ""

    def test_several_at_signs_split_on_the_first(self):
        assert email_local_part("a@b@example.com") == "a"

    def test_a_local_part_long_enough_to_break_a_layout_is_bounded(self):
        long_local = "x" * 300
        label = resolve_user_label(_user(email=f"{long_local}@example.com"))
        assert len(label) == MAX_LABEL_LENGTH
        assert label.endswith("…")

    def test_a_hostile_preferred_name_is_bounded_by_the_same_rule(self):
        label = resolve_user_label(_user(preferred_name="N" * 128))
        assert len(label) == MAX_LABEL_LENGTH
        assert label.endswith("…")

    def test_a_name_exactly_at_the_cap_is_untouched(self):
        exact = "n" * MAX_LABEL_LENGTH
        assert resolve_user_label(_user(preferred_name=exact)) == exact


class TestLastResortIsUnreachableForRealAccounts:
    """"A teammate" is for a nameless record, never for a real user row."""

    def test_the_account_that_rendered_as_teammate_never_does_again(self):
        # The reported defect: team@excalibur.game HAS a provider display name,
        # and still rendered as "Teammate".
        u = _user(
            display_name="Excalibur Team",
            email="team@excalibur.game",
            source_user_id="google-108…",
        )
        assert resolve_user_label(u) == "Excalibur Team"
        assert resolve_user_label(u) != LAST_RESORT_LABEL

    def test_an_account_with_only_an_email_never_renders_the_last_resort(self):
        assert resolve_user_label(_user(email="someone@example.com")) != LAST_RESORT_LABEL

    def test_an_account_with_only_a_sub_never_renders_the_last_resort(self):
        assert resolve_user_label(_user(source_user_id="google-999")) != LAST_RESORT_LABEL

    def test_no_author_object_at_all_is_the_one_case_it_covers(self):
        # kind='user' row whose author_user_id was NULLed by account deletion.
        assert resolve_user_label(None) == LAST_RESORT_LABEL

    def test_a_row_with_every_identifier_blank(self):
        assert resolve_user_label(_user(email="", source_user_id="")) == LAST_RESORT_LABEL


class TestDuckTyping:
    def test_a_row_predating_migration_0030_falls_through_instead_of_raising(self):
        # An object with NO preferred_name attribute at all (pre-0030 shape, or
        # the SimpleNamespace principals the route tests install).
        legacy = SimpleNamespace(display_name="Alice", email="alice@test.local")
        assert resolve_user_label(legacy) == "Alice"

    def test_non_string_values_are_ignored_rather_than_rendered(self):
        assert resolve_user_label(_user(preferred_name=123, display_name="Alice")) == "Alice"

    def test_label_from_parts_matches_resolve_user_label(self):
        parts = {
            "preferred_name": None,
            "display_name": "Excalibur Team",
            "email": "team@excalibur.game",
            "source_user_id": "google-1",
        }
        assert label_from_parts(**parts) == resolve_user_label(_user(**parts))
