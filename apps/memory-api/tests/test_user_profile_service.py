"""What a person may write about themselves — app/services/user_profile.py.

Pure unit tests: no DB, no Docker, no route. Every one of these runs anywhere,
which is the point of keeping the validation contract out of the route body.

What these lock down:
  - **empty string clears** — the undo path. A rename must be reversible by the
    person who made it, alone. Normalisation returns None (SQL NULL), never "",
    so the ladder in user_label.py takes over again and the provider name comes
    back. Asserted here as a ROUND TRIP (write "" → read the label), because
    "returns None" on its own does not prove the person got their name back.
  - **control characters are rejected server-side**, including the invisible and
    direction-overriding ones a client-side `trim()` would happily pass through.
    Every field here is drawn on someone else's screen.
  - **lengths are checked against what chat renders**, on the stored (stripped,
    NFC) value — not on the raw input, so trailing spaces never fail a name that
    is otherwise fine.
  - **the profile's label is the ladder's label** — the profile screen and the
    chat bubble cannot disagree about what a person is called.
  - **a signed avatar URL is minted fresh, in the scope the avatar lives in.**
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.user_label import LAST_RESORT_LABEL, resolve_user_label
from app.services.user_profile import (
    MAX_BIO_LENGTH,
    MAX_PREFERRED_NAME_LENGTH,
    avatar_url_for,
    normalize_bio,
    normalize_preferred_name,
    normalize_profile_text,
    profile_payload,
)


def _user(**kw):
    """A user-like row; every profile attribute defaults to None/absent."""
    base = {
        "id": "11111111-1111-4111-8111-111111111111",
        "preferred_name": None,
        "display_name": None,
        "email": None,
        "source_user_id": None,
        "bio": None,
        "avatar_media_id": None,
        "avatar_media_team_scope": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


class TestEmptyStringClears:
    """The undo path: a person renames themselves badly and fixes it alone."""

    def test_empty_string_returns_none_not_empty_string(self):
        # None means SQL NULL, which is what the ladder falls through. Writing ""
        # would store a value that is falsy in Python but PRESENT in the column —
        # the ladder's own _clean() would still skip it, but the profile would
        # then report preferred_name="" and the UI would render an empty input as
        # "set". None is the only honest representation of "cleared".
        assert normalize_preferred_name("") is None
        assert normalize_bio("") is None

    def test_whitespace_only_clears_too(self):
        assert normalize_preferred_name("   ") is None
        assert normalize_preferred_name("\t  \t") is None
        assert normalize_bio("  \n \n ") is None

    def test_explicit_null_clears(self):
        assert normalize_preferred_name(None) is None
        assert normalize_bio(None) is None

    def test_clearing_restores_the_provider_name_end_to_end(self):
        """Write "" → store None → the label reads the Google name again."""
        renamed = _user(
            preferred_name=normalize_preferred_name("Nico"),
            display_name="Excalibur Team",
            email="team@excalibur.game",
        )
        assert profile_payload(renamed)["label"] == "Nico"

        cleared = _user(
            preferred_name=normalize_preferred_name(""),
            display_name="Excalibur Team",
            email="team@excalibur.game",
        )
        assert cleared.preferred_name is None
        assert profile_payload(cleared)["label"] == "Excalibur Team", (
            "clearing a preferred name must hand the label back to the provider "
            "name with no admin, no backfill and no support ticket"
        )

    def test_clearing_with_no_provider_name_falls_to_the_email_local_part(self):
        cleared = _user(
            preferred_name=normalize_preferred_name(""),
            display_name=None,
            email="nicoboups@gmail.com",
            source_user_id="google-123",
        )
        assert profile_payload(cleared)["label"] == "nicoboups"

    def test_clearing_never_reaches_the_last_resort_for_a_real_account(self):
        # A real row always has an email and a sub, so undoing a rename can never
        # strand someone on "A teammate".
        cleared = _user(
            preferred_name=normalize_preferred_name(""),
            email="someone@example.com",
            source_user_id="google-999",
        )
        assert profile_payload(cleared)["label"] != LAST_RESORT_LABEL


class TestControlCharactersAreRejected:
    """User-supplied text that a chat client renders. Validated server-side."""

    @pytest.mark.parametrize(
        "hostile",
        [
            "Nico\nAdmin",          # newline — two lines in a one-line header
            "Nico\rAdmin",          # carriage return
            "Nico\tAdmin",          # tab
            "Nico\x00",             # NUL
            "Nico\x1b[31m",         # ANSI escape — a terminal client renders colour
            "Nico\x7f",             # DEL
            "Nico\x85Admin",        # C1 next-line
        ],
    )
    def test_a_name_with_a_control_character_is_refused(self, hostile):
        with pytest.raises(ValueError) as exc:
            normalize_preferred_name(hostile)
        assert "control" in str(exc.value).lower()

    @pytest.mark.parametrize("trailing", ["Nico\n", "Nico\r\n", "Nico\x85", "\x0bNico\x0c"])
    def test_a_control_character_that_is_only_padding_is_stripped_not_refused(
        self, trailing
    ):
        """A stray newline at the end of a pasted name is padding, not an attack.

        Python's `str.strip()` treats \\n, \\r, \\x0b, \\x0c and the C1 next-line as
        whitespace, so they are removed before the control-character check ever
        runs. That ordering is deliberate: someone who pastes a name out of a
        document and brings a trailing newline with it gets their name, not an
        error they cannot see the cause of. The same characters in the MIDDLE of a
        name are still refused — that is the case above.
        """
        assert normalize_preferred_name(trailing) == "Nico"

    @pytest.mark.parametrize(
        "spoof",
        [
            "Nico\u202enimdA",      # right-to-left override
            "\u2066Nico\u2069",     # bidi isolates
            "Nic\u200bo",           # zero-width space — pixel-identical to "Nico"
            "Nico\ufeff",           # BOM
            "Nico\u2028Admin",      # line separator: an invisible line break mid-name
        ],
    )
    def test_invisible_and_direction_overriding_characters_are_refused(self, spoof):
        # These are the ones a client-side trim() lets through: they are not
        # whitespace, they are not visible, and they change what another member
        # sees on their screen.
        with pytest.raises(ValueError):
            normalize_preferred_name(spoof)

    def test_the_error_never_echoes_the_rejected_input(self):
        with pytest.raises(ValueError) as exc:
            normalize_preferred_name("Nico\x1b[31mRED")
        assert "RED" not in str(exc.value), (
            "a validation error must not reflect hostile input back into a "
            "response another surface may render"
        )

    def test_a_bio_keeps_its_newlines(self):
        # A bio is prose; a name is not. This is the one deliberate difference.
        assert normalize_bio("Line one\nLine two") == "Line one\nLine two"

    def test_a_bio_normalises_crlf_rather_than_rejecting_it(self):
        # A Windows browser posts \r\n. Refusing it would fail a person for their
        # operating system's line ending.
        assert normalize_bio("Line one\r\nLine two") == "Line one\nLine two"
        assert normalize_bio("Line one\rLine two") == "Line one\nLine two"

    def test_a_bio_still_refuses_every_other_control_character(self):
        with pytest.raises(ValueError):
            normalize_bio("Bio\x00")
        with pytest.raises(ValueError):
            normalize_bio("Bio\x1b[31m")

    def test_legitimate_scripts_and_emoji_survive(self):
        # ZWNJ/ZWJ are needed to spell ordinary words and to hold an emoji
        # together. Rejecting the whole Cf category would break real names.
        assert normalize_preferred_name("Zero\u200cWidth") == "Zero\u200cWidth"
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        assert normalize_preferred_name(family) == family
        assert normalize_preferred_name("Nicolás") == "Nicolás"
        assert normalize_preferred_name("日本語の名前") == "日本語の名前"


class TestWhitespaceAndLength:
    def test_surrounding_whitespace_is_stripped(self):
        assert normalize_preferred_name("  Nico  ") == "Nico"
        assert normalize_bio("  hello  ") == "hello"

    def test_interior_spaces_are_kept(self):
        assert normalize_preferred_name("Excalibur Team") == "Excalibur Team"

    def test_a_name_at_the_cap_is_accepted(self):
        exact = "n" * MAX_PREFERRED_NAME_LENGTH
        assert normalize_preferred_name(exact) == exact

    def test_a_name_one_over_the_cap_is_refused(self):
        with pytest.raises(ValueError) as exc:
            normalize_preferred_name("n" * (MAX_PREFERRED_NAME_LENGTH + 1))
        assert str(MAX_PREFERRED_NAME_LENGTH) in str(exc.value)

    def test_length_is_measured_after_stripping(self):
        # Trailing spaces must not fail an otherwise-fine name — the stored value
        # is what counts, and the stored value is stripped.
        padded = "  " + "n" * MAX_PREFERRED_NAME_LENGTH + "   "
        assert normalize_preferred_name(padded) == "n" * MAX_PREFERRED_NAME_LENGTH

    def test_length_is_measured_after_nfc_composition(self):
        # "é" as e + U+0301 is 2 code points before NFC and 1 after. Counting
        # before composing would reject a name that fits.
        decomposed = "é" * MAX_PREFERRED_NAME_LENGTH
        assert len(decomposed) == MAX_PREFERRED_NAME_LENGTH * 2
        assert normalize_preferred_name(decomposed) == "é" * MAX_PREFERRED_NAME_LENGTH

    def test_the_name_cap_is_what_chat_renders_so_nothing_is_silently_truncated(self):
        from app.services.user_label import MAX_LABEL_LENGTH

        assert MAX_PREFERRED_NAME_LENGTH == MAX_LABEL_LENGTH, (
            "accepting a name longer than the ladder renders would ship an "
            "ellipsis the person never asked for"
        )
        longest = normalize_preferred_name("n" * MAX_PREFERRED_NAME_LENGTH)
        assert resolve_user_label(_user(preferred_name=longest)) == longest
        assert "…" not in resolve_user_label(_user(preferred_name=longest))

    def test_a_bio_at_and_over_its_cap(self):
        assert normalize_bio("b" * MAX_BIO_LENGTH) == "b" * MAX_BIO_LENGTH
        with pytest.raises(ValueError) as exc:
            normalize_bio("b" * (MAX_BIO_LENGTH + 1))
        assert str(MAX_BIO_LENGTH) in str(exc.value)

    def test_the_bio_cap_matches_the_column(self):
        from app.models.user import User

        assert MAX_BIO_LENGTH == User.__table__.c.bio.type.length, (
            "a bio the API accepts but the column cannot hold is a 500 waiting "
            "for the first person who writes a long one"
        )

    def test_the_name_cap_fits_inside_the_column(self):
        from app.models.user import User

        assert MAX_PREFERRED_NAME_LENGTH <= User.__table__.c.preferred_name.type.length

    def test_a_non_string_is_refused_rather_than_stringified(self):
        for bad in (123, 4.5, True, ["Nico"], {"name": "Nico"}):
            with pytest.raises(ValueError):
                normalize_preferred_name(bad)


class TestTheLabelOnAProfileIsTheLaddersLabel:
    """The profile screen and the chat bubble cannot disagree."""

    @pytest.mark.parametrize(
        "kw",
        [
            {"preferred_name": "Nico", "display_name": "Excalibur Team", "email": "team@excalibur.game"},
            {"display_name": "Excalibur Team", "email": "team@excalibur.game"},
            {"email": "nicoboups@gmail.com", "source_user_id": "google-123"},
            {"email": "@nodomain.example", "source_user_id": "google-123"},
            {"source_user_id": "google-123"},
        ],
    )
    def test_payload_label_equals_resolve_user_label(self, kw):
        u = _user(**kw)
        assert profile_payload(u)["label"] == resolve_user_label(u)


class TestEmailEdgeCasesOnTheProfileSurface:
    """The email edge cases, asserted where the profile actually reads them."""

    def test_an_email_with_no_at_sign(self):
        p = profile_payload(_user(email="notanemail", source_user_id="google-1"))
        assert p["label"] == "notanemail"
        assert p["email"] == "notanemail"

    def test_an_empty_local_part_falls_through_to_the_sub(self):
        assert profile_payload(_user(email="@example.com", source_user_id="google-1"))["label"] == "google-1"

    def test_a_whitespace_only_local_part_falls_through_to_the_sub(self):
        assert profile_payload(_user(email="   @example.com", source_user_id="google-1"))["label"] == "google-1"

    def test_whitespace_around_the_local_part_is_stripped(self):
        assert profile_payload(_user(email="  team@excalibur.game"))["label"] == "team"

    def test_an_email_long_enough_to_break_a_layout_is_bounded(self):
        p = profile_payload(_user(email="x" * 300 + "@example.com"))
        assert len(p["label"]) == MAX_PREFERRED_NAME_LENGTH
        assert p["label"].endswith("…")
        # The raw email is returned unmodified — it is the caller's OWN address,
        # and truncating it in the payload would show them a wrong address.
        assert p["email"] == "x" * 300 + "@example.com"

    def test_an_email_with_several_at_signs_splits_on_the_first(self):
        assert profile_payload(_user(email="a@b@example.com"))["label"] == "a"

    def test_a_missing_email_does_not_raise(self):
        p = profile_payload(_user(email=None, source_user_id="google-1"))
        assert p["email"] is None
        assert p["label"] == "google-1"


class TestAvatarUrl:
    def test_no_avatar_yields_none(self):
        assert avatar_url_for(_user()) is None
        assert profile_payload(_user())["avatar_url"] is None

    def test_an_id_without_a_scope_yields_none_rather_than_an_unusable_url(self):
        # A URL minted with no scope could not be verified by the serve endpoint;
        # returning None makes the client render the fallback initial instead of
        # a broken image.
        u = _user(avatar_media_id="22222222-2222-4222-8222-222222222222")
        assert avatar_url_for(u) is None

    def test_the_url_points_at_the_existing_media_endpoint(self):
        u = _user(
            avatar_media_id="22222222-2222-4222-8222-222222222222",
            avatar_media_team_scope="team-a",
        )
        url = avatar_url_for(u)
        assert url.startswith("/v1/media/22222222-2222-4222-8222-222222222222/img?t=")

    def test_the_token_verifies_and_carries_the_stored_scope(self):
        from app.routes.media_helpers import verify_media_token

        item_id = "22222222-2222-4222-8222-222222222222"
        u = _user(avatar_media_id=item_id, avatar_media_team_scope="team-a")
        token = avatar_url_for(u).split("?t=", 1)[1]

        # Verifies against the REAL verifier the serve endpoint uses, and is bound
        # to THIS item: a token minted for one avatar cannot fetch another.
        assert verify_media_token(token, item_id) == "team-a"
        with pytest.raises(Exception):
            verify_media_token(token, "33333333-3333-4333-8333-333333333333")

    def test_the_token_is_minted_from_the_stored_scope_not_the_readers(self):
        """A person in several teams still loads their own avatar.

        The scope in the token is the one the avatar was UPLOADED under, recorded
        on the row. Nothing about the reading request influences it — which is
        exactly why reading a profile from team-b still renders an avatar stored
        under team-a, with the token's scope never widened.
        """
        from app.routes.media_helpers import verify_media_token

        item_id = "22222222-2222-4222-8222-222222222222"
        u = _user(avatar_media_id=item_id, avatar_media_team_scope="team-a")
        token = avatar_url_for(u).split("?t=", 1)[1]
        assert verify_media_token(token, item_id) == "team-a"

    def test_every_read_mints_a_fresh_token(self):
        """Never persist a signed URL — it expires. Two reads, two tokens.

        The token carries `iat`/`exp`, so a stored copy is a link that works until
        it silently stops. This asserts the mint happens per call rather than
        being computed once and cached on the row.
        """
        import time

        item_id = "22222222-2222-4222-8222-222222222222"
        u = _user(avatar_media_id=item_id, avatar_media_team_scope="team-a")
        first = avatar_url_for(u)
        time.sleep(1.05)  # the JWT's iat has 1-second resolution
        second = avatar_url_for(u)
        assert first != second, (
            "each read must mint a new token; a URL held on the row would be a "
            "link that expires with no way to notice"
        )

    def test_the_payload_exposes_the_id_as_a_string(self):
        import uuid

        raw = uuid.UUID("22222222-2222-4222-8222-222222222222")
        p = profile_payload(_user(avatar_media_id=raw, avatar_media_team_scope="team-a"))
        assert p["avatar_media_id"] == str(raw)
        # The scope the avatar lives in is an internal storage detail; the client
        # gets a working URL and never needs to know which team holds the blob.
        assert "avatar_media_team_scope" not in p


class TestPayloadShape:
    def test_the_payload_is_exactly_the_agreed_field_set(self):
        # The client wires against this shape. A field added here without a
        # decision is a field some surface will start rendering.
        assert set(profile_payload(_user())) == {
            "user_id",
            "email",
            "label",
            "preferred_name",
            "display_name",
            "bio",
            "avatar_media_id",
            "avatar_url",
        }

    def test_no_credential_or_token_column_leaks_into_the_payload(self):
        # A User row also carries github_access_token_enc and friends. Building
        # the payload from an explicit field list (not vars(row)) is what keeps
        # them out; this fails if someone ever switches to a dict dump.
        u = _user(
            github_access_token_enc="secret-ciphertext",
            github_refresh_token_enc="secret-ciphertext",
            github_access_token_hash="hash",
            merged_into_user_id="44444444-4444-4444-8444-444444444444",
        )
        rendered = repr(profile_payload(u))
        assert "secret-ciphertext" not in rendered
        assert "hash" not in rendered

    def test_a_row_predating_migration_0030_does_not_raise(self):
        # A row selected before 0030 exists in a given environment has no
        # profile attributes at all. Reading a profile must degrade, not 500.
        legacy = SimpleNamespace(
            id="55555555-5555-4555-8555-555555555555",
            email="alice@test.local",
            display_name="Alice",
            source_user_id="alice-sub",
        )
        p = profile_payload(legacy)
        assert p["label"] == "Alice"
        assert p["preferred_name"] is None
        assert p["bio"] is None
        assert p["avatar_url"] is None


class TestNormalizeProfileTextDirectly:
    def test_the_field_label_appears_in_the_message_for_the_ui(self):
        with pytest.raises(ValueError) as exc:
            normalize_profile_text("x" * 11, field_label="Nickname", max_length=10)
        assert "Nickname" in str(exc.value)

    def test_messages_are_english(self):
        # Product strings are English only (CLAUDE.md), including error text.
        with pytest.raises(ValueError) as exc:
            normalize_preferred_name("n" * 500)
        msg = str(exc.value)
        assert msg.isascii(), f"error message must be plain English ASCII: {msg!r}"
        assert msg.endswith(".")
