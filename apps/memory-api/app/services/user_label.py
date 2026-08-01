"""THE label ladder — the one place that decides what a person is called.

Every surface that renders a human's name resolves it here: the chat bubble
(routes/team_chat.py::_serialize_message), the mention push notification, and
the nudge push notification. Two implementations of one rule is how they drift
apart, and drift is exactly the bug this module was written to end — the three
call sites previously carried three different fallback chains, so the same
account could read "Excalibur Team" on a lock screen and "Teammate" in the chat
it was sent from.

The ladder, in order, first non-empty wins:

  1. ``preferred_name``  — the name the person set on themselves. Nothing else
                           writes this column; a provider sign-in never touches it.
  2. ``display_name``    — the identity provider's name (Google). Better than an
                           email fragment when the provider gave us a real one.
  3. email local part    — everything before the first ``@``. The fallback for an
                           account whose provider sent no name at all.
  4. ``source_user_id``  — the last identifier we actually have.
  5. ``"A teammate"``    — LAST RESORT, and unreachable for any real account: a
                           user row always has an email and a source_user_id, so
                           this string can only surface for a genuinely nameless
                           record (a message whose author_user_id was NULLed when
                           the account was deleted, or no user object at all).

Capitalisation: the local part is taken VERBATIM. `nicoboups@gmail.com` reads
`nicoboups`, not `Nicoboups` — case-munging mangles handles that are
deliberately lowercase, and the value is user-overridable anyway.

Everything here is pure: no session, no I/O, no ORM import. Callers hand in
either a user-like object or the four raw strings.
"""
from __future__ import annotations

from typing import Any

# The genuinely-nameless last resort. Not a fallback for "the name was hard to
# load" — every tier above it comes from a column that a real user row has.
LAST_RESORT_LABEL = "A teammate"

# A chat label is rendered in a fixed-width bubble header and in an OS
# notification title, both of which a 300-character name would destroy. The cap
# is applied to the RESOLVED label whatever tier it came from, so a hostile
# preferred_name and a pathological email local part are bounded by the same
# rule.
MAX_LABEL_LENGTH = 64
_ELLIPSIS = "…"


def email_local_part(email: str | None) -> str:
    """Return the part of `email` before the first ``@``, stripped.

    Returns "" when there is nothing usable — an empty local part
    (``@example.com``), a whitespace-only one, or no email at all. A string
    with no ``@`` at all yields the whole string: it is still the best name-ish
    token we hold, and rejecting it would drop a tier for no gain.
    """
    if not email:
        return ""
    return email.split("@", 1)[0].strip()


def _clean(value: Any) -> str:
    """Normalise one candidate: non-strings and whitespace-only become ""."""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _truncate(label: str) -> str:
    """Bound the rendered label so no single account can break a layout."""
    if len(label) <= MAX_LABEL_LENGTH:
        return label
    return label[: MAX_LABEL_LENGTH - 1].rstrip() + _ELLIPSIS


def label_from_parts(
    *,
    preferred_name: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    source_user_id: str | None = None,
) -> str:
    """Resolve the ladder from raw values. See module docstring for the order."""
    for candidate in (
        _clean(preferred_name),
        _clean(display_name),
        email_local_part(email),
        _clean(source_user_id),
    ):
        if candidate:
            return _truncate(candidate)
    return LAST_RESORT_LABEL


def resolve_user_label(user: Any) -> str:
    """Resolve the ladder for a user-like object (ORM row, or any duck type).

    `getattr` with a default rather than attribute access on purpose: this is
    called with ORM rows, with the SimpleNamespace principals the tests install,
    and with rows selected before migration 0030 exists in a given environment.
    A missing attribute must fall through the ladder, never raise.

    Passing None (no author on the row at all) yields the last-resort string —
    that is the one case it legitimately covers.
    """
    if user is None:
        return LAST_RESORT_LABEL
    return label_from_parts(
        preferred_name=getattr(user, "preferred_name", None),
        display_name=getattr(user, "display_name", None),
        email=getattr(user, "email", None),
        source_user_id=getattr(user, "source_user_id", None),
    )
