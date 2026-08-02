"""Transcript parsers — Claude Code JSONL and the ChatGPT export.

Pure unit tests: no DB, no Docker, no app.main import.

The two load-bearing assertions here are the ones that fail silently in
production if they are wrong:

* **The branched ChatGPT conversation.** A parser that walks ``mapping`` in
  file order rather than following ``current_node`` produces a transcript
  containing both the abandoned answer and the current one. Nothing crashes;
  the brain simply learns two contradictory facts. ``test_chatgpt_branched_*``
  asserts the abandoned branch is ABSENT, not merely that the current one is
  present.
* **The truncated final line.** Copying a Claude Code session file while the
  session is live cuts the last record mid-write. That is the normal case.
"""
from __future__ import annotations

import json

import pytest

from app.services.transcript_import import (
    SUPPORTED_FORMATS,
    TranscriptParseError,
    chatgpt,
    claude_code,
    parse_transcript,
    sniff_format,
)

# ── fixtures ────────────────────────────────────────────────────────────────


def _jsonl(*records) -> str:
    return "\n".join(json.dumps(r) for r in records)


SESSION = "c0ffee00-1111-2222-3333-444455556666"


CLAUDE_CODE_RECORDS = [
    # Claude Code's own session name — a title, never speech.
    {"type": "summary", "summary": "Wire the import endpoint", "leafUuid": "u9"},
    # A real person typing.
    {
        "parentUuid": None,
        "isSidechain": False,
        "userType": "external",
        "cwd": "/home/nico/xbrain",
        "sessionId": SESSION,
        "version": "2.1.0",
        "type": "user",
        "message": {"role": "user", "content": "Where does the dedupe key come from?"},
        "uuid": "u1",
        "timestamp": "2026-08-01T09:15:00.000Z",
    },
    # Assistant turn mixing thinking + speech + a tool call.
    {
        "parentUuid": "u1",
        "sessionId": SESSION,
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [
                {"type": "thinking", "thinking": "I should grep for dedupe_key first."},
                {"type": "text", "text": "It comes from the conversation's own id."},
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "grep -rn dedupe_key app/"},
                },
            ],
        },
        "uuid": "u2",
        "timestamp": "2026-08-01T09:15:04.000Z",
    },
    # The RUNTIME answering the model. Authored as "user", but nobody spoke.
    {
        "parentUuid": "u2",
        "sessionId": SESSION,
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "tool_use_id": "toolu_1",
                    "type": "tool_result",
                    "content": "app/services/transcript_import/types.py:88: def dedupe_key",
                }
            ],
        },
        "toolUseResult": {"stdout": "…", "stderr": ""},
        "uuid": "u3",
        "timestamp": "2026-08-01T09:15:05.000Z",
    },
    # Slash-command scaffolding injected as a user record.
    {
        "sessionId": SESSION,
        "type": "user",
        "message": {"role": "user", "content": "<command-name>/clear</command-name>"},
        "uuid": "u4",
        "timestamp": "2026-08-01T09:15:06.000Z",
    },
    # Meta bookkeeping.
    {
        "sessionId": SESSION,
        "type": "user",
        "isMeta": True,
        "message": {"role": "user", "content": "Session resumed from a previous run"},
        "uuid": "u5",
        "timestamp": "2026-08-01T09:15:07.000Z",
    },
    # A sub-agent transcript — belongs to a tool invocation, not the conversation.
    {
        "sessionId": SESSION,
        "isSidechain": True,
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Sidechain scratch work"}]},
        "uuid": "u6",
        "timestamp": "2026-08-01T09:15:08.000Z",
    },
    # A file-history snapshot record.
    {"type": "file-history-snapshot", "sessionId": SESSION, "snapshot": {"files": {}}, "uuid": "u7"},
    {
        "parentUuid": "u3",
        "sessionId": SESSION,
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Confirmed: types.py owns it."}],
        },
        "uuid": "u8",
        "timestamp": "2026-08-01T09:15:09.000Z",
    },
]

# The normal case: the file was copied mid-write, so the last line is half a record.
CLAUDE_CODE_TRUNCATED = _jsonl(*CLAUDE_CODE_RECORDS) + '\n{"parentUuid":"u8","sessionId":"c0ffe'


def _chatgpt_node(node_id, parent, children, *, role=None, text=None, create_time=None, **msg):
    node = {"id": node_id, "parent": parent, "children": list(children), "message": None}
    if role is not None:
        message = {
            "id": node_id,
            "author": {"role": role, "name": None, "metadata": {}},
            "create_time": create_time,
            "content": {"content_type": "text", "parts": [text if text is not None else ""]},
            "status": "finished_successfully",
            "recipient": "all",
            "metadata": {},
        }
        message.update(msg)
        node["message"] = message
    return node


def _chatgpt_branched() -> dict:
    """A conversation with a regenerated answer — two sibling assistant branches.

    ``a2-current`` is the answer the person kept; ``a2-abandoned`` is the one
    they regenerated away from. ``current_node`` points into the kept branch.
    """
    nodes = [
        _chatgpt_node("root", None, ["sys"]),
        _chatgpt_node(
            "sys", "root", ["u1"], role="system", text="",
            metadata={"is_visually_hidden_from_conversation": True},
        ),
        _chatgpt_node("u1", "sys", ["a2-abandoned", "a2-current"], role="user",
                      text="What is our deploy target?", create_time=1_754_000_000.0),
        _chatgpt_node("a2-abandoned", "u1", [], role="assistant",
                      text="Your deploy target is Kubernetes on AWS.", create_time=1_754_000_010.0),
        _chatgpt_node("a2-current", "u1", ["u3"], role="assistant",
                      text="Your deploy target is a GCP VM running Docker Compose.",
                      create_time=1_754_000_020.0),
        _chatgpt_node("u3", "a2-current", ["a4"], role="user",
                      text="And the VM size?", create_time=1_754_000_030.0),
        _chatgpt_node("a4", "u3", [], role="assistant",
                      text="e2-standard-2, 8 GB of RAM.", create_time=1_754_000_040.0),
    ]
    return {
        "title": "Deploy target",
        "create_time": 1_754_000_000.0,
        "update_time": 1_754_000_040.0,
        "mapping": {n["id"]: n for n in nodes},
        "current_node": "a4",
        "conversation_id": "conv-abc-123",
        "id": "conv-abc-123",
    }


def _chatgpt_machinery() -> dict:
    """A conversation whose non-speech nodes must all be dropped."""
    nodes = [
        _chatgpt_node("root", None, ["ctx"]),
        _chatgpt_node("ctx", "root", ["u1"], role="user", text="I am a senior engineer.",
                      metadata={"is_user_system_message": True}),
        _chatgpt_node("u1", "ctx", ["tool-call"], role="user",
                      text="Plot the revenue curve", create_time=1_754_100_000.0),
        _chatgpt_node("tool-call", "u1", ["tool-out"], role="assistant",
                      text="import matplotlib", recipient="python"),
        _chatgpt_node("tool-out", "tool-call", ["a1"], role="tool", text="<figure>"),
        _chatgpt_node("a1", "tool-out", ["mm"], role="assistant",
                      text="Here is the curve.", create_time=1_754_100_020.0),
    ]
    # A multimodal turn whose parts mix an image pointer with real words.
    mm = _chatgpt_node("mm", "a1", [], role="user", create_time=1_754_100_030.0)
    mm["message"]["content"] = {
        "content_type": "multimodal_text",
        "parts": [
            {"content_type": "image_asset_pointer", "asset_pointer": "file-service://x"},
            "Can you annotate the peak?",
        ],
    }
    nodes.append(mm)
    return {
        "title": "Revenue",
        "mapping": {n["id"]: n for n in nodes},
        "current_node": "mm",
        "id": "conv-machinery",
    }


# ── Claude Code ─────────────────────────────────────────────────────────────


def test_claude_code_keeps_only_speech():
    convs = claude_code.parse(_jsonl(*CLAUDE_CODE_RECORDS))
    assert len(convs) == 1
    conv = convs[0]
    assert conv.source_id == SESSION
    assert [t.role for t in conv.turns] == ["user", "assistant", "assistant"]
    assert conv.turns[0].content == "Where does the dedupe key come from?"
    assert conv.turns[1].content == "It comes from the conversation's own id."
    assert conv.turns[2].content == "Confirmed: types.py owns it."


@pytest.mark.parametrize(
    "forbidden",
    [
        "toolu_1",                       # tool_use id
        "grep -rn dedupe_key",           # tool_use input
        "app/services/transcript_import/types.py:88",  # tool_result payload
        "I should grep for dedupe_key",  # thinking block
        "<command-name>",                # slash-command scaffolding
        "Session resumed",               # isMeta record
        "Sidechain scratch work",        # isSidechain sub-agent transcript
    ],
)
def test_claude_code_never_imports_machinery_as_speech(forbidden):
    convs = claude_code.parse(_jsonl(*CLAUDE_CODE_RECORDS))
    blob = "\n".join(t.content for c in convs for t in c.turns)
    assert forbidden not in blob


def test_claude_code_survives_a_truncated_final_line():
    """A file copied while the session was live ends mid-record. Normal, not fatal."""
    convs = claude_code.parse(CLAUDE_CODE_TRUNCATED)
    assert len(convs) == 1
    # Every complete record before the truncation is still there.
    assert convs[0].turn_count == 3


def test_claude_code_uses_the_summary_record_as_the_title():
    convs = claude_code.parse(_jsonl(*CLAUDE_CODE_RECORDS))
    assert convs[0].title == "Wire the import endpoint"


def test_claude_code_falls_back_to_the_first_user_line_for_a_title():
    records = [r for r in CLAUDE_CODE_RECORDS if r.get("type") != "summary"]
    convs = claude_code.parse(_jsonl(*records))
    assert convs[0].title == "Where does the dedupe key come from?"


def test_claude_code_groups_by_session_id():
    other = dict(CLAUDE_CODE_RECORDS[1])
    other["sessionId"] = "second-session"
    other["message"] = {"role": "user", "content": "A different session entirely."}
    convs = claude_code.parse(_jsonl(*CLAUDE_CODE_RECORDS, other))
    assert {c.source_id for c in convs} == {SESSION, "second-session"}


def test_claude_code_timestamps_are_parsed_as_aware_datetimes():
    conv = claude_code.parse(_jsonl(*CLAUDE_CODE_RECORDS))[0]
    assert conv.turns[0].timestamp is not None
    assert conv.turns[0].timestamp.tzinfo is not None


# ── ChatGPT ─────────────────────────────────────────────────────────────────


def test_chatgpt_branched_follows_current_node_only():
    conv = chatgpt.parse(json.dumps([_chatgpt_branched()]))[0]
    assert [t.role for t in conv.turns] == ["user", "assistant", "user", "assistant"]
    assert conv.turns[1].content == "Your deploy target is a GCP VM running Docker Compose."


def test_chatgpt_branched_drops_the_abandoned_branch():
    """The contradiction test. Concatenating branches imports both answers."""
    conv = chatgpt.parse(json.dumps([_chatgpt_branched()]))[0]
    blob = "\n".join(t.content for t in conv.turns)
    assert "Kubernetes on AWS" not in blob
    assert "GCP VM running Docker Compose" in blob


def test_chatgpt_keeps_conversation_identity_and_title():
    conv = chatgpt.parse(json.dumps([_chatgpt_branched()]))[0]
    assert conv.source_id == "conv-abc-123"
    assert conv.title == "Deploy target"


def test_chatgpt_falls_back_to_newest_leaf_when_current_node_dangles():
    payload = _chatgpt_branched()
    payload["current_node"] = "a-node-that-was-deleted"
    conv = chatgpt.parse(json.dumps([payload]))[0]
    # a4 is the newest leaf, so the same current branch is reconstructed.
    assert conv.turns[-1].content == "e2-standard-2, 8 GB of RAM."
    assert "Kubernetes on AWS" not in "\n".join(t.content for t in conv.turns)


@pytest.mark.parametrize(
    "forbidden",
    [
        "import matplotlib",        # assistant→python tool call
        "<figure>",                 # tool output
        "I am a senior engineer",   # injected custom-instruction node
        "file-service://x",         # image asset pointer
    ],
)
def test_chatgpt_never_imports_machinery_as_speech(forbidden):
    conv = chatgpt.parse(json.dumps([_chatgpt_machinery()]))[0]
    blob = "\n".join(t.content for t in conv.turns)
    assert forbidden not in blob


def test_chatgpt_drops_a_reasoning_channel_but_keeps_the_final_answer():
    """A reasoning-model export ships the trace as ordinary content_type="text".

    Nothing else filters it: the role is assistant, the recipient is "all", the
    content type is text. Only `channel` distinguishes the model thinking to
    itself from the model answering — so an importer that ignores `channel`
    teaches the brain the model's half-formed guesses alongside its answer.
    """
    nodes = [
        _chatgpt_node("root", None, ["u1"]),
        _chatgpt_node("u1", "root", ["think"], role="user",
                      text="Which database do we use?", create_time=1.0),
        _chatgpt_node("think", "u1", ["final"], role="assistant",
                      text="Maybe MySQL? No, let me reconsider.", create_time=2.0,
                      channel="analysis"),
        _chatgpt_node("final", "think", [], role="assistant",
                      text="PostgreSQL 17, with pgvector for the RAG store.",
                      create_time=3.0, channel="final"),
    ]
    payload = {"mapping": {n["id"]: n for n in nodes}, "current_node": "final",
               "id": "conv-reasoning", "title": "DB"}
    conv = chatgpt.parse(json.dumps([payload]))[0]
    blob = "\n".join(t.content for t in conv.turns)
    assert "Maybe MySQL" not in blob
    assert "PostgreSQL 17" in blob
    assert conv.turn_count == 2


def test_chatgpt_keeps_the_words_of_a_multimodal_turn():
    conv = chatgpt.parse(json.dumps([_chatgpt_machinery()]))[0]
    assert conv.turns[-1].content == "Can you annotate the peak?"


def test_chatgpt_accepts_a_single_conversation_object():
    conv = chatgpt.parse(json.dumps(_chatgpt_branched()))[0]
    assert conv.source_id == "conv-abc-123"


def test_chatgpt_accepts_a_wrapper_object():
    raw = json.dumps({"conversations": [_chatgpt_branched()]})
    assert chatgpt.parse(raw)[0].source_id == "conv-abc-123"


def test_chatgpt_skips_conversations_with_no_speech():
    empty = {"title": "Empty", "mapping": {"root": _chatgpt_node("root", None, [])},
             "current_node": "root", "id": "conv-empty"}
    assert chatgpt.parse(json.dumps([empty, _chatgpt_branched()])) != []
    assert len(chatgpt.parse(json.dumps([empty]))) == 0


def test_chatgpt_survives_a_parent_pointer_cycle():
    """A crafted file must terminate, not hang the worker thread."""
    nodes = {
        "a": {"id": "a", "parent": "b", "children": ["b"],
              "message": {"author": {"role": "user"},
                          "content": {"content_type": "text", "parts": ["ping"]},
                          "recipient": "all"}},
        "b": {"id": "b", "parent": "a", "children": ["a"],
              "message": {"author": {"role": "assistant"},
                          "content": {"content_type": "text", "parts": ["pong"]},
                          "recipient": "all"}},
    }
    convs = chatgpt.parse(json.dumps([{"mapping": nodes, "current_node": "a", "id": "c"}]))
    assert len(convs) == 1
    assert convs[0].turn_count == 2


# ── hostile input ───────────────────────────────────────────────────────────

HOSTILE = [
    "",
    "   \n\t  ",
    "not json at all",
    "null",
    "[]",
    "{}",
    "[1, 2, 3]",
    '[{"mapping": null}]',
    '[{"mapping": {"a": null}, "current_node": "a"}]',
    '[{"mapping": {"a": {"message": {"author": {"role": "user"}, "content": null}}}}]',
    '[{"mapping": {"a": {"message": {"author": "nope", "content": {"parts": 5}}}}}]',
    '{"mapping": {"a": {"parent": "a", "children": [], "message": []}}}',
    '﻿[{"title": "bom"}]',
    "\x00\x01\x02",
    '{"type":"user","message":{"role":"user","content":{"nested":"dict"}}}',
    '{"type":"user","message":{"role":"user","content":[{"type":"text"}]}}',
    '{"type":"assistant","message":null}',
    '{"type":"user","sessionId":123,"message":{"role":"user","content":"hi there"}}',
    "{" * 500,
    '{"type":"user","timestamp":"not-a-date","message":{"role":"user","content":"x"}}',
    '[{"mapping":{"a":{"message":{"author":{"role":"assistant"},'
    '"content":{"content_type":"text","parts":["ok"]},"create_time":"NaN"}}}}]',
]


@pytest.mark.parametrize("raw", HOSTILE)
@pytest.mark.parametrize("parser", [claude_code.parse, chatgpt.parse])
def test_parsers_never_raise_anything_but_transcript_parse_error(parser, raw):
    """These files arrive from a picker on someone's phone. Nothing else may escape."""
    try:
        result = parser(raw)
    except TranscriptParseError:
        return
    assert isinstance(result, list)
    for conv in result:
        assert all(isinstance(t.content, str) and t.content for t in conv.turns)


@pytest.mark.parametrize("raw", HOSTILE)
def test_parse_transcript_auto_never_raises_anything_else(raw):
    try:
        _fmt, result = parse_transcript(raw, "auto")
    except TranscriptParseError:
        return
    assert isinstance(result, list)


# ── dispatch and sniffing ───────────────────────────────────────────────────


def test_sniff_recognises_each_format():
    assert sniff_format(json.dumps([_chatgpt_branched()])) == "chatgpt"
    assert sniff_format(_jsonl(*CLAUDE_CODE_RECORDS)) == "claude-code"


def test_sniff_does_not_mistake_a_one_line_jsonl_session_for_json():
    """A single-record .jsonl file is also valid JSON — the shape decides, not the syntax."""
    one_line = json.dumps(CLAUDE_CODE_RECORDS[1])
    assert sniff_format(one_line) == "claude-code"


def test_sniff_rejects_an_unrecognisable_document():
    with pytest.raises(TranscriptParseError):
        sniff_format('{"invoice_total": 42}')


def test_parse_transcript_rejects_an_unknown_format():
    with pytest.raises(TranscriptParseError):
        parse_transcript("{}", "slack")


def test_parse_transcript_reports_the_resolved_format():
    fmt, convs = parse_transcript(json.dumps([_chatgpt_branched()]), "auto")
    assert fmt == "chatgpt"
    assert len(convs) == 1


def test_supported_formats_are_the_documented_ones():
    assert set(SUPPORTED_FORMATS) == {"chatgpt", "claude-code", "auto"}


# ── dedupe identity ─────────────────────────────────────────────────────────


def test_dedupe_key_prefers_the_source_conversation_id():
    conv = chatgpt.parse(json.dumps([_chatgpt_branched()]))[0]
    assert conv.dedupe_key("chatgpt") == "chatgpt:conv-abc-123"


def test_dedupe_key_is_stable_across_a_re_export_that_added_a_turn():
    """Same conversation id, more turns — still ONE identity, so no second copy."""
    first = chatgpt.parse(json.dumps([_chatgpt_branched()]))[0]
    grown = _chatgpt_branched()
    grown["mapping"]["a4"]["children"] = ["u5"]
    grown["mapping"]["u5"] = _chatgpt_node("u5", "a4", [], role="user",
                                           text="Thanks.", create_time=1_754_000_050.0)
    grown["current_node"] = "u5"
    second = chatgpt.parse(json.dumps([grown]))[0]
    assert second.turn_count > first.turn_count
    assert first.dedupe_key("chatgpt") == second.dedupe_key("chatgpt")


def test_dedupe_key_falls_back_to_a_content_hash_without_an_id():
    payload = _chatgpt_branched()
    payload.pop("conversation_id")
    payload.pop("id")
    conv = chatgpt.parse(json.dumps([payload]))[0]
    key = conv.dedupe_key("chatgpt")
    assert key.startswith("chatgpt:sha256:")
    # Deterministic across parses — a random fallback would defeat dedupe.
    again = chatgpt.parse(json.dumps([payload]))[0]
    assert again.dedupe_key("chatgpt") == key


def test_content_fingerprint_ignores_title_and_timestamps():
    base = _chatgpt_branched()
    renamed = _chatgpt_branched()
    renamed["title"] = "A completely different title"
    renamed["update_time"] = 9_999_999_999.0
    a = chatgpt.parse(json.dumps([base]))[0]
    b = chatgpt.parse(json.dumps([renamed]))[0]
    assert a.content_fingerprint() == b.content_fingerprint()


def test_a_long_turn_is_truncated_not_dropped():
    from app.services.transcript_import.types import MAX_TURN_CHARS

    payload = _chatgpt_branched()
    payload["mapping"]["u1"]["message"]["content"]["parts"] = ["x" * (MAX_TURN_CHARS + 5000)]
    conv = chatgpt.parse(json.dumps([payload]))[0]
    assert conv.turns[0].content.endswith("[truncated on import]")
    assert len(conv.turns[0].content) < MAX_TURN_CHARS + 100
