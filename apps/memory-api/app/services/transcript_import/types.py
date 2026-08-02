"""Shared, format-agnostic vocabulary for transcript import.

Every parser under this package turns raw bytes-of-a-file into the SAME shape:
a list of :class:`ParsedConversation`, each holding an ordered list of
:class:`Turn`. Nothing downstream (the ingest fan-out, the dedupe rule, the
route) knows or cares which product the transcript came from.

Two rules the parsers all obey, encoded here so they cannot drift:

* **Only human-readable speech.** A tool call, a tool result, a reasoning
  trace, a hidden system preamble — none of those are speech, and importing
  them "as if they were" would poison the brain with JSON blobs and file
  dumps. Parsers drop them; they never smuggle them into ``Turn.content``.
* **Hostile input never explodes.** These files arrive from a picker on
  someone's phone. A parser may raise exactly one exception —
  :class:`TranscriptParseError`, meaning "this is not the format you said it
  was" — and nothing else. Bad *records* inside an otherwise-valid document
  are skipped silently, never fatal.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

# A single turn longer than this is almost certainly a pasted dump rather than
# speech. Truncate instead of dropping: the beginning still carries the point.
MAX_TURN_CHARS = 20_000

# Guard against a single pathological conversation (or a crafted file) turning
# one request into a million-item fan-out. Parsers stop reading a conversation
# past this many kept turns.
MAX_TURNS_PER_CONVERSATION = 5_000

# A source conversation id becomes half of a UNIQUE btree key in Postgres, whose
# index entries top out around 2704 bytes. A crafted export carrying a megabyte
# "id" would turn an import into a 500. Anything beyond a sane bound is treated
# as no id at all, which falls back to the content fingerprint — bounded, and
# just as correct an identity.
MAX_SOURCE_ID_CHARS = 200

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
_VALID_ROLES = frozenset({ROLE_USER, ROLE_ASSISTANT})


class TranscriptParseError(ValueError):
    """The document could not be read as the declared format.

    This is the ONLY exception a parser in this package is allowed to raise.
    It means the top-level container is unreadable (not JSON, not JSONL, not
    the expected shape) — a condition a person can act on by picking a
    different file. It never means "one record inside was malformed".
    """


@dataclass(frozen=True)
class Turn:
    """One human-readable utterance.

    ``timestamp`` is present only when the source format records one; Claude
    Code always does, ChatGPT usually does, and neither is required.
    """

    role: str
    content: str
    timestamp: datetime | None = None


@dataclass
class ParsedConversation:
    """One conversation, as the source product understood it.

    ``source_id`` is the conversation's own identity in the export when the
    format has one (ChatGPT's ``id``, Claude Code's ``sessionId``). It is the
    preferred half of the dedupe rule; ``None`` falls back to a content hash.
    """

    turns: list[Turn] = field(default_factory=list)
    source_id: str | None = None
    title: str | None = None
    started_at: datetime | None = None

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    def content_fingerprint(self) -> str:
        """Stable sha256 over the normalised turns — the fallback identity.

        Deliberately excludes titles and timestamps: the same conversation
        re-exported later carries a fresh ``update_time`` and sometimes a
        regenerated title, but the same words. Words are the identity.
        """
        h = hashlib.sha256()
        for t in self.turns:
            h.update(t.role.encode("utf-8", "replace"))
            h.update(b"\x1f")
            h.update(t.content.encode("utf-8", "replace"))
            h.update(b"\x1e")
        return h.hexdigest()

    def dedupe_key(self, source_format: str) -> str:
        """Identity used to make a re-import a no-op.

        Prefer the source conversation's own id, because it survives an edit
        to the conversation (adding a turn and re-exporting must NOT create a
        second copy in the brain). Fall back to the content fingerprint only
        when the format gave us no id to hold on to.
        """
        if self.source_id:
            return f"{source_format}:{self.source_id}"
        return f"{source_format}:sha256:{self.content_fingerprint()}"


def clean_text(value: object) -> str:
    """Coerce an arbitrary JSON value to speech-shaped text, or ``''``.

    Non-strings become empty rather than ``str(value)`` — a dict rendered as
    ``"{'tool_use_id': ...}"`` is exactly the tool payload we refuse to treat
    as speech.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > MAX_TURN_CHARS:
        text = text[:MAX_TURN_CHARS].rstrip() + "\n…[truncated on import]"
    return text


def clean_source_id(value: object) -> str | None:
    """Return a usable source conversation id, or ``None``.

    ``None`` for anything that is not a non-empty string within
    :data:`MAX_SOURCE_ID_CHARS` — see that constant for why the bound exists.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_SOURCE_ID_CHARS:
        return None
    return text


def make_turn(role: object, content: str, timestamp: datetime | None = None) -> Turn | None:
    """Build a Turn, or ``None`` when the role or content is not speech."""
    if role not in _VALID_ROLES:
        return None
    if not content:
        return None
    return Turn(role=str(role), content=content, timestamp=timestamp)
