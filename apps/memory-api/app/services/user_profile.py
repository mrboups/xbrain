"""The profile a person owns: what they may write about themselves, and how it reads back.

Everything here is pure — no session, no I/O, no ORM import. The routes in
app/routes/me_profile.py do the reading and writing; this module decides what is
acceptable text and what a profile looks like once resolved. Keeping it pure is
what lets the whole validation contract be tested without a database, which
matters because the interesting cases (a control character, a 300-character
name, an empty string that means "undo my rename") have nothing to do with
storage.

Three rules drive the design.

**Every field here is user-supplied text that a chat client renders.** It is
validated server-side, on the way in, and never on the way out — a client that
forgot to validate, or a caller that is not a browser at all, must not be able to
put a newline, a terminal escape, or a right-to-left override into a name that
another member's screen will draw. The client is not part of this trust chain.

**Empty string clears.** `PATCH {"preferred_name": ""}` writes NULL, and the
ladder in app/services/user_label.py then falls back to the provider name (or the
email local part). A person who renamed themselves badly can undo it alone, with
no support ticket and no admin. That is the reason `preferred_name` is a separate
column from the provider's `display_name` in the first place (see migration
0030_user_profile), and this module is where that promise is actually kept:
normalisation returns None, not "", so the route writes SQL NULL.

**What you type is what chat shows.** `preferred_name` is validated against
`MAX_LABEL_LENGTH` — the same constant the label ladder truncates at — rather
than against the 128-character column. Accepting 100 characters into a column
that holds them, only for every bubble header to render an ellipsis, is a silent
surprise; rejecting them with a stated limit is not. The wider column stays as
headroom, and the ladder's own truncation stays as defence in depth for rows that
predate this route.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from app.services.user_label import MAX_LABEL_LENGTH, resolve_user_label

# A chosen name is capped at what the chat bubble actually renders. Imported, not
# re-declared: two copies of one limit is how they drift apart.
MAX_PREFERRED_NAME_LENGTH = MAX_LABEL_LENGTH

# Mirrors the users.bio column width (migration 0030). A bio is prose, so it is
# allowed newlines — unlike a name, which is drawn on a single line.
MAX_BIO_LENGTH = 400

# Invisible and direction-controlling code points, rejected on top of the whole
# Cc (control) category.
#
# These are not paranoia about exotic Unicode: each one lets a name LOOK like
# another name on the screen it is drawn on. U+202E (right-to-left override)
# reverses everything after it; the isolates U+2066–U+2069 do the same within a
# span; U+200B (zero-width space) pads a name invisibly so "Nico" and "Nic<ZWSP>o"
# are pixel-identical; U+2028/U+2029 are line breaks that `str.strip()` removes
# but JSON and JS treat as newlines.
#
# Deliberately NOT rejected: U+200C (zero-width non-joiner) and U+200D (zero-width
# joiner). Both are required to spell ordinary words in Persian, Hindi and other
# scripts, and U+200D is what holds a multi-person emoji together. Banning them
# would break real names to defend against a variant of an attack the entries
# below already cover.
# Written as chr() calls and escapes on purpose: a literal zero-width space in
# this source file is invisible to the next reader and survives a copy-paste as a
# silent edit.
_FORBIDDEN_CODEPOINTS = frozenset(
    {
        chr(0x200B),  # zero-width space — invisible padding
        chr(0x200E),  # left-to-right mark
        chr(0x200F),  # right-to-left mark
        chr(0x2028),  # line separator
        chr(0x2029),  # paragraph separator
        chr(0xFEFF),  # zero-width no-break space / BOM
    }
    | {chr(cp) for cp in range(0x202A, 0x202F)}  # LRE, RLE, PDF, LRO, RLO
    | {chr(cp) for cp in range(0x2066, 0x206A)}  # LRI, RLI, FSI, PDI
)


def _has_forbidden_char(value: str, *, allow_newlines: bool) -> bool:
    """True when `value` holds a control, invisible, or direction-override char.

    `unicodedata.category(c) == "Cc"` covers C0 (including NUL, ESC and every
    terminal escape lead-in), DEL, and C1 in one test rather than a hand-written
    range that a future edit could get wrong.
    """
    for char in value:
        if char == "\n" and allow_newlines:
            continue
        if unicodedata.category(char) == "Cc" or char in _FORBIDDEN_CODEPOINTS:
            return True
    return False


def normalize_profile_text(
    raw: Any,
    *,
    field_label: str,
    max_length: int,
    allow_newlines: bool = False,
) -> str | None:
    """Clean one profile field, or raise ValueError with a message for the person.

    Returns the value to store: a stripped, NFC-normalised string, or **None**
    when the field is cleared. An empty string, a whitespace-only string and an
    explicit null all mean the same thing — "I no longer want a value here" — and
    all three return None so the route writes SQL NULL and the label ladder takes
    over again.

    Order matters: normalise, then strip, then decide "cleared", then reject bad
    characters, then check length. Length is measured on the STORED value, so
    trailing spaces never push an otherwise-fine name over the limit, and NFC
    composition (which can shorten a string) is applied before counting.

    Raises:
        ValueError: with an English, user-facing message naming the field. The
            message never echoes the rejected input back — a validation error is
            not a place to reflect hostile text.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{field_label} must be text.")

    # NFC so the stored form is the composed one: "é" typed as e + combining
    # accent and "é" typed as one code point are the same name, and must not
    # produce two different rows or two different lengths.
    value = unicodedata.normalize("NFC", raw)

    if allow_newlines:
        # Normalise line endings before stripping so a CRLF-only body clears.
        value = value.replace("\r\n", "\n").replace("\r", "\n")

    value = value.strip()
    if not value:
        return None

    if _has_forbidden_char(value, allow_newlines=allow_newlines):
        raise ValueError(
            f"{field_label} cannot contain control or invisible characters."
        )

    if len(value) > max_length:
        raise ValueError(f"{field_label} must be {max_length} characters or fewer.")

    return value


def normalize_preferred_name(raw: Any) -> str | None:
    """Validate the name a person chose for themselves. Single line, ≤ the label cap."""
    return normalize_profile_text(
        raw,
        field_label="Preferred name",
        max_length=MAX_PREFERRED_NAME_LENGTH,
        allow_newlines=False,
    )


def normalize_bio(raw: Any) -> str | None:
    """Validate a bio. Prose, so newlines are kept; every other control char is not."""
    return normalize_profile_text(
        raw,
        field_label="Bio",
        max_length=MAX_BIO_LENGTH,
        allow_newlines=True,
    )


def avatar_url_for(user: Any) -> str | None:
    """Mint a FRESH signed URL for this user's avatar, or None if they have none.

    Never persist what this returns. The token embedded in the URL is the
    short-lived one `GET /v1/media/{id}/img` validates (routes/media_helpers.py,
    1 hour), so a stored copy is a link that works until it silently stops. Every
    profile read mints a new one; that is the whole contract.

    The token is bound to the team scope the avatar was UPLOADED under
    (`users.avatar_media_team_scope`), not to whatever scope the caller happens to
    be reading under. A person belongs to several teams and their avatar lives in
    exactly one of them; minting from the stored scope is what makes the picture
    load from any of them without the token's scope ever being widened.

    Returns a root-relative path, matching the `signed_url` that
    POST /v1/media/upload already returns and the `metadata.media.url` chat
    messages already carry — clients resolve all three the same way.
    """
    media_id = getattr(user, "avatar_media_id", None)
    team_scope = getattr(user, "avatar_media_team_scope", None)
    if not media_id or not team_scope:
        return None

    from app.routes.media_helpers import mint_media_token  # lazy: authlib import

    token = mint_media_token(str(media_id), str(team_scope))
    return f"/v1/media/{media_id}/img?t={token}"


def profile_payload(user: Any) -> dict[str, Any]:
    """The full profile of the caller's OWN account.

    `email` and `bio` are in here because this shape is only ever returned to the
    person it describes. No route hands this dict to anyone else; what other
    members see of each other is the label (and, once wired, the avatar) that chat
    already shows — see routes/team_chat.py::_serialize_message.

    `label` is resolved through the one ladder rather than recomputed, so the
    profile screen and the chat bubble can never disagree about what a person is
    called. `display_name` travels with it so the UI can say what clearing the
    preferred name will restore, instead of asking the person to guess.
    """
    return {
        "user_id": str(getattr(user, "id", "")) or None,
        "email": getattr(user, "email", None),
        "label": resolve_user_label(user),
        "preferred_name": getattr(user, "preferred_name", None),
        "display_name": getattr(user, "display_name", None),
        "bio": getattr(user, "bio", None),
        "avatar_media_id": (
            str(user.avatar_media_id) if getattr(user, "avatar_media_id", None) else None
        ),
        "avatar_url": avatar_url_for(user),
    }
