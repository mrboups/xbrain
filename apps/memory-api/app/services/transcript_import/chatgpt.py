"""Parser for the official ChatGPT export (``conversations.json``).

The file is an array of conversations. Each conversation's messages live in
``mapping`` — a dict of node-id → node, where every node carries ``parent``
and ``children``. That is a TREE, not a list, and the difference is not
cosmetic: every message a person edited and every answer they regenerated
adds a SIBLING branch. Concatenating ``mapping.values()`` in file order yields
a transcript in which the model both agreed and disagreed with itself, the
user both asked for A and asked for B, and no reader — human or retrieval —
can tell which one actually happened.

So this parser reconstructs the ONE path the export says is current: start at
``current_node`` (the leaf ChatGPT was showing when the export was taken),
walk ``parent`` pointers to the root, reverse. Abandoned branches are simply
not on that path and never enter the brain.

Everything that is not speech is dropped: system preambles, the hidden
"user_editable_context" custom-instruction node, tool calls and their output,
reasoning summaries, and any assistant node addressed to a tool
(``recipient != "all"``) rather than to the person.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.services.transcript_import.types import (
    MAX_TURNS_PER_CONVERSATION,
    ParsedConversation,
    TranscriptParseError,
    clean_source_id,
    clean_text,
    make_turn,
)

FORMAT = "chatgpt"

# Only these two content_types ever hold something a person said or read.
# Everything else in a ChatGPT export — code, execution_output, tether_quote,
# tether_browsing_display, system_error, thoughts, reasoning_recap,
# user_editable_context, model_editable_context — is machinery.
_SPEECH_CONTENT_TYPES = frozenset({"text", "multimodal_text"})

# Reasoning-model exports carry a `channel` on assistant nodes. "final" is the
# answer the person read; "analysis" and "commentary" are the model thinking to
# itself, shipped as ordinary content_type="text" so nothing else filters them.
# Absent (every pre-reasoning export) means "this is the answer".
_NON_FINAL_CHANNELS = frozenset({"analysis", "commentary", "critic"})

_TITLE_MAX_CHARS = 120

# A conversation tree deeper than this is a crafted file, not a chat. The walk
# is already cycle-guarded; this bounds the pathological-but-acyclic case.
_MAX_PATH_NODES = 100_000


def _parse_ts(value: Any) -> datetime | None:
    """ChatGPT timestamps are epoch seconds (float). Never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _text_from_message(message: dict[str, Any]) -> str:
    """Extract speech from one ChatGPT message node, or ``''``."""
    content = message.get("content")
    if not isinstance(content, dict):
        return ""
    if content.get("content_type") not in _SPEECH_CONTENT_TYPES:
        return ""

    parts = content.get("parts")
    if parts is None:
        # `parts` is genuinely absent on some node shapes. Not an error —
        # there is simply nothing to read here.
        return ""
    if isinstance(parts, str):
        parts = [parts]
    if not isinstance(parts, list):
        return ""

    pieces: list[str] = []
    for part in parts:
        # multimodal_text mixes strings with image_asset_pointer dicts.
        # clean_text() returns '' for anything that is not a string, which
        # drops the pointers without special-casing each variant.
        piece = clean_text(part)
        if piece:
            pieces.append(piece)
    if not pieces:
        return ""
    return clean_text("\n\n".join(pieces))


def _is_speech_node(message: dict[str, Any]) -> bool:
    author = message.get("author")
    role = author.get("role") if isinstance(author, dict) else None
    if role not in ("user", "assistant"):
        return False

    # An assistant message addressed to anything other than "all" is a tool
    # call (python, browser, dalle...), not an answer to the person.
    recipient = message.get("recipient")
    if role == "assistant" and recipient not in (None, "all"):
        return False

    # Reasoning traces are shipped as plain text on a non-final channel.
    channel = message.get("channel")
    if isinstance(channel, str) and channel.lower() in _NON_FINAL_CHANNELS:
        return False

    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        if metadata.get("is_visually_hidden_from_conversation") is True:
            return False
        # The injected "custom instructions" node is authored as the user but
        # was never typed into this conversation.
        if metadata.get("is_user_system_message") is True:
            return False
    return True


def _current_path(mapping: dict[str, Any], current_node: Any) -> list[dict[str, Any]]:
    """Return the root→leaf node list for the branch the export marks current.

    Falls back to the newest leaf when ``current_node`` is missing or dangles
    (older exports, hand-edited files). Cycle-guarded: a file whose parent
    pointers loop terminates instead of hanging the worker thread.
    """
    leaf_id = current_node if isinstance(current_node, str) else None
    if leaf_id not in mapping:
        leaf_id = _newest_leaf(mapping)
    if leaf_id is None:
        return []

    path: list[dict[str, Any]] = []
    seen: set[str] = set()
    node_id: Any = leaf_id
    while isinstance(node_id, str) and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if not isinstance(node, dict):
            break
        path.append(node)
        if len(path) >= _MAX_PATH_NODES:
            break
        node_id = node.get("parent")
    path.reverse()
    return path


def _newest_leaf(mapping: dict[str, Any]) -> str | None:
    """Pick the leaf whose message is most recent — the best guess at 'current'.

    A leaf is a node nobody claims as a parent. Among them the largest
    ``create_time`` is the branch the person was last on. Ties fall back to a
    sorted id so the choice is deterministic across runs (a non-deterministic
    fallback would break dedupe by fingerprint).
    """
    parents: set[str] = set()
    for node in mapping.values():
        if isinstance(node, dict) and isinstance(node.get("parent"), str):
            parents.add(node["parent"])

    best_id: str | None = None
    best_key: tuple[float, str] | None = None
    for node_id, node in mapping.items():
        if not isinstance(node_id, str) or node_id in parents:
            continue
        create_time = 0.0
        if isinstance(node, dict):
            message = node.get("message")
            if isinstance(message, dict):
                ts = message.get("create_time")
                if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                    create_time = float(ts)
        key = (create_time, node_id)
        if best_key is None or key > best_key:
            best_key, best_id = key, node_id
    return best_id


def _parse_one(conv: Any) -> ParsedConversation | None:
    if not isinstance(conv, dict):
        return None
    mapping = conv.get("mapping")
    if not isinstance(mapping, dict) or not mapping:
        return None

    turns = []
    for node in _current_path(mapping, conv.get("current_node")):
        message = node.get("message")
        if not isinstance(message, dict):
            continue  # root nodes carry message=None
        if not _is_speech_node(message):
            continue
        text = _text_from_message(message)
        if not text:
            continue
        author = message.get("author")
        role = author.get("role") if isinstance(author, dict) else None
        turn = make_turn(role, text, _parse_ts(message.get("create_time")))
        if turn is None:
            continue
        turns.append(turn)
        if len(turns) >= MAX_TURNS_PER_CONVERSATION:
            break

    if not turns:
        return None

    source_id = clean_source_id(conv.get("conversation_id")) or clean_source_id(conv.get("id"))
    title = clean_text(conv.get("title"))[:_TITLE_MAX_CHARS] or None

    return ParsedConversation(
        turns=turns,
        source_id=source_id,
        title=title,
        started_at=_parse_ts(conv.get("create_time")) or turns[0].timestamp,
    )


def parse(raw: str) -> list[ParsedConversation]:
    """Parse a ChatGPT ``conversations.json`` export.

    Accepts the array the export ships, a single conversation object (what a
    per-conversation export or a hand-extracted slice looks like), or a wrapper
    object with a ``conversations`` key.

    Raises:
        TranscriptParseError: when the document is not JSON, or is JSON of a
            shape that holds no conversations at all.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise TranscriptParseError(
            "The file is empty. Use conversations.json from your ChatGPT data "
            "export (Settings → Data controls → Export data)."
        )

    try:
        document = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise TranscriptParseError(
            "This file is not valid JSON. Use conversations.json from your "
            "ChatGPT data export, not the zip and not a screenshot."
        ) from exc

    if isinstance(document, dict):
        if isinstance(document.get("conversations"), list):
            document = document["conversations"]
        elif "mapping" in document:
            document = [document]
        else:
            raise TranscriptParseError(
                "This JSON file holds no conversations. Use conversations.json "
                "from your ChatGPT data export."
            )
    if not isinstance(document, list):
        raise TranscriptParseError(
            "This JSON file holds no conversations. Use conversations.json "
            "from your ChatGPT data export."
        )

    result: list[ParsedConversation] = []
    for conv in document:
        parsed = _parse_one(conv)
        if parsed is not None:
            result.append(parsed)
    return result
