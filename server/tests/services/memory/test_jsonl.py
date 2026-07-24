"""Coverage for ``services.memory.jsonl`` — parse / append / trim.

Anthropic Messages API JSONL is the storage format the
``claude_code_agent`` bridge round-trips. These tests pin the public
contract: standard parsers ignore unknown metadata, append always
emits a trailing newline, and trim returns removed lines verbatim for
vector-store archival.
"""

from __future__ import annotations

import json

from services.llm.protocol import Message
from services.memory.jsonl import append_message, parse_jsonl, trim_window
from services.memory import (  # public re-exports
    parse_jsonl as parse_jsonl_reexport,
    append_message as append_message_reexport,
    trim_window as trim_window_reexport,
)


# ---------------------------------------------------------------------------
# parse_jsonl
# ---------------------------------------------------------------------------


def test_parse_jsonl_empty_input_returns_empty_list():
    assert parse_jsonl("") == []
    assert parse_jsonl(None) == []  # type: ignore[arg-type]


def test_parse_jsonl_basic_user_assistant_pair():
    text = '{"role": "user", "content": "hi"}\n' '{"role": "assistant", "content": "hello"}\n'
    msgs = parse_jsonl(text)
    assert len(msgs) == 2
    assert isinstance(msgs[0], Message)
    assert (msgs[0].role, msgs[0].content) == ("user", "hi")
    assert isinstance(msgs[1], Message)
    assert (msgs[1].role, msgs[1].content) == ("assistant", "hello")


def test_parse_jsonl_skips_unparseable_lines_forward_compat():
    text = '{"role": "user", "content": "ok"}\n' "this is not json\n" '{"role": "assistant", "content": "still ok"}\n'
    msgs = parse_jsonl(text)
    assert [m.content for m in msgs] == ["ok", "still ok"]


def test_parse_jsonl_skips_unknown_roles():
    text = (
        '{"role": "system", "content": "unknown role"}\n'
        '{"role": "tool_use", "content": "skipped"}\n'
        '{"role": "user", "content": "kept"}\n'
    )
    msgs = parse_jsonl(text)
    assert [m.content for m in msgs] == ["kept"]


def test_parse_jsonl_collapses_content_blocks_to_text():
    text = (
        json.dumps(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Got it"},
                    {"type": "tool_use", "id": "tu_1", "name": "search", "input": {}},
                    {"type": "text", "text": "blue."},
                ],
            }
        )
        + "\n"
    )
    msgs = parse_jsonl(text)
    assert len(msgs) == 1
    assert msgs[0].content == "Got it blue."  # text blocks joined; tool_use dropped


def test_parse_jsonl_metadata_keys_are_ignored():
    text = (
        json.dumps(
            {
                "role": "user",
                "content": "hi",
                "timestamp": "2026-05-10T00:00:00Z",
                "session_id": "abc-123",
                "model": "claude",
            }
        )
        + "\n"
    )
    msgs = parse_jsonl(text)
    assert len(msgs) == 1 and msgs[0].content == "hi"


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


def test_append_message_to_empty_string_yields_single_line_with_newline():
    out = append_message("", "user", "hi")
    assert out == '{"role": "user", "content": "hi"}\n'


def test_append_message_chains_cleanly_across_calls():
    text = ""
    text = append_message(text, "user", "q1")
    text = append_message(text, "assistant", "a1")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["role"] == "user"
    assert json.loads(lines[1])["role"] == "assistant"


def test_append_message_normalises_missing_trailing_newline():
    base = '{"role": "user", "content": "hi"}'  # no trailing newline
    out = append_message(base, "assistant", "ok")
    assert out.startswith('{"role": "user", "content": "hi"}\n')
    assert out.endswith('"content": "ok"}\n')


def test_append_message_metadata_round_trips_via_parse_jsonl():
    text = append_message(
        "",
        "assistant",
        "blue.",
        timestamp="2026-05-10T12:34:56+00:00",
        session_id="abc-123",
        model="claude",
    )
    obj = json.loads(text.strip())
    assert obj["timestamp"] == "2026-05-10T12:34:56+00:00"
    assert obj["session_id"] == "abc-123"
    assert obj["model"] == "claude"
    # parse_jsonl still returns the message; metadata is preserved on the
    # wire even if it isn't surfaced on the normalized Message.
    msgs = parse_jsonl(text)
    assert msgs[0].content == "blue."


def test_append_message_supports_non_ascii_content():
    out = append_message("", "user", "héllo — 你好")
    obj = json.loads(out.strip())
    assert obj["content"] == "héllo — 你好"  # ensure_ascii=False preserves UTF-8


# ---------------------------------------------------------------------------
# trim_window
# ---------------------------------------------------------------------------


def test_trim_window_under_capacity_returns_input_unchanged_and_no_removed():
    text = ""
    text = append_message(text, "user", "q1")
    text = append_message(text, "assistant", "a1")
    trimmed, removed = trim_window(text, window_size=2)
    assert trimmed == text
    assert removed == []


def test_trim_window_removes_oldest_pairs_first():
    text = ""
    text = append_message(text, "user", "q1")
    text = append_message(text, "assistant", "a1")
    text = append_message(text, "user", "q2")
    text = append_message(text, "assistant", "a2")
    text = append_message(text, "user", "q3")
    text = append_message(text, "assistant", "a3")
    trimmed, removed = trim_window(text, window_size=1)
    # window=1 keeps last 2 lines; removes 4 oldest.
    assert len(removed) == 4
    kept = [json.loads(ln) for ln in trimmed.splitlines() if ln.strip()]
    assert [k["content"] for k in kept] == ["q3", "a3"]
    # Removed entries are returned verbatim so the vector store gets
    # the raw JSONL line back.
    removed_objs = [json.loads(ln) for ln in removed]
    assert [o["content"] for o in removed_objs] == ["q1", "a1", "q2", "a2"]


def test_trim_window_handles_empty_text():
    trimmed, removed = trim_window("", 5)
    assert trimmed == "" and removed == []


# ---------------------------------------------------------------------------
# Public-API re-exports through services.memory
# ---------------------------------------------------------------------------


def test_services_memory_reexports_match_jsonl_module():
    """`services.memory.__init__` re-exports the JSONL public surface."""
    assert parse_jsonl_reexport is parse_jsonl
    assert append_message_reexport is append_message
    assert trim_window_reexport is trim_window
