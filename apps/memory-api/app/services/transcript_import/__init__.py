"""Transcript import — turn an exported AI conversation into team-brain turns.

One module per source format, all producing the same normalised
:class:`~app.services.transcript_import.types.ParsedConversation` list, so the
ingest path and the dedupe rule stay format-agnostic.

Public surface:
    ``SUPPORTED_FORMATS`` — the values the API accepts for ``format``
    ``parse_transcript(raw, source_format)`` — dispatch
    ``sniff_format(raw)`` — best-effort detection for ``format="auto"``
    ``TranscriptParseError`` — the only exception a parser raises
"""
from __future__ import annotations

import json

from app.services.transcript_import import chatgpt, claude_code
from app.services.transcript_import.types import (
    MAX_TURN_CHARS,
    MAX_TURNS_PER_CONVERSATION,
    ParsedConversation,
    TranscriptParseError,
    Turn,
)

_PARSERS = {
    claude_code.FORMAT: claude_code.parse,
    chatgpt.FORMAT: chatgpt.parse,
}

SUPPORTED_FORMATS: tuple[str, ...] = tuple(sorted(_PARSERS)) + ("auto",)

# What the provenance tag becomes for each format (CLAUDE.md tagging contract:
# `source` must say where a data point came from, precisely enough to audit).
SOURCE_TAGS = {
    claude_code.FORMAT: "import:claude-code",
    chatgpt.FORMAT: "import:chatgpt",
}

__all__ = [
    "MAX_TURNS_PER_CONVERSATION",
    "MAX_TURN_CHARS",
    "SOURCE_TAGS",
    "SUPPORTED_FORMATS",
    "ParsedConversation",
    "TranscriptParseError",
    "Turn",
    "parse_transcript",
    "sniff_format",
]


def sniff_format(raw: str) -> str:
    """Guess the source format from the document itself.

    ChatGPT's export is a single JSON document whose conversations carry a
    ``mapping``; a Claude Code session is line-delimited JSON. Checking for the
    ``mapping`` key rather than "does it start with ``[``" matters because a
    one-line Claude Code file is also valid JSON.

    Raises:
        TranscriptParseError: when the document matches neither shape.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TranscriptParseError("The file is empty — there is nothing to import.")

    head = raw.lstrip()[:1]
    if head in ("[", "{"):
        try:
            document = json.loads(raw)
        except (ValueError, TypeError):
            document = None
        if document is not None and _looks_like_chatgpt(document):
            return chatgpt.FORMAT

    # Any line decoding to a JSON object with a Claude Code record shape.
    for line in raw.splitlines()[:200]:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except (ValueError, TypeError):
            continue
        if isinstance(record, dict) and (
            "sessionId" in record or record.get("type") in ("user", "assistant", "summary")
        ):
            return claude_code.FORMAT

    raise TranscriptParseError(
        "Could not tell what this file is. Send format=\"chatgpt\" for a "
        "conversations.json export, or format=\"claude-code\" for a .jsonl "
        "session file."
    )


def _looks_like_chatgpt(document: object) -> bool:
    if isinstance(document, dict):
        if "mapping" in document:
            return True
        document = document.get("conversations")
    if isinstance(document, list):
        for item in document[:20]:
            if isinstance(item, dict) and isinstance(item.get("mapping"), dict):
                return True
    return False


def parse_transcript(raw: str, source_format: str) -> tuple[str, list[ParsedConversation]]:
    """Parse ``raw`` as ``source_format``; returns ``(resolved_format, conversations)``.

    CPU-bound on a large export — call it through ``asyncio.to_thread`` from a
    request handler. This codebase has already frozen its API once by parsing a
    document synchronously at ``UVICORN_WORKERS=1``.

    Raises:
        TranscriptParseError: unknown format, or the document is unreadable as
            the declared one.
    """
    resolved = source_format
    if source_format == "auto":
        resolved = sniff_format(raw)

    parser = _PARSERS.get(resolved)
    if parser is None:
        raise TranscriptParseError(
            f"Unsupported format {source_format!r}. Supported: "
            f"{', '.join(SUPPORTED_FORMATS)}."
        )
    return resolved, parser(raw)
