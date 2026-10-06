"""The employee's answer streamed into the chat (services/chat/stream.py):
which agent streams, what text may be shown while it is written, and the text
events one step publishes (one segment per attempt, batched, held back while
it could still be NO_REPLY or a follow-ups block, reply or narration)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from services.chat import stream
from services.llm.protocol import StreamEvent


# ----- which agent streams -----


def _graph(agent_id="wf:aiAgent:1", target_type="chatReply"):
    nodes = [
        {"id": agent_id, "type": "aiAgent"},
        {"id": "wf:out:1", "type": target_type},
    ]
    edges = [{"source": agent_id, "target": "wf:out:1"}]
    return nodes, edges


@pytest.fixture
def registry(monkeypatch):
    import services.node_registry as node_registry

    classes = {"chatReply": SimpleNamespace(answers_chat_run=True), "console": SimpleNamespace()}
    monkeypatch.setattr(node_registry, "get_node_class", lambda node_type: classes.get(node_type))


def _context(**overrides):
    nodes, edges = _graph()
    context = {
        "node_id": "wf:aiAgent:1",
        "workflow_id": "wf",
        "nodes": nodes,
        "edges": edges,
        "run_scope": {"run_id": "r_1", "session_id": "wf"},
    }
    context.update(overrides)
    return context


def test_the_agent_whose_answer_is_the_reply_streams(registry):
    assert stream.chat_stream_for(_context()) == {
        "run_id": "r_1",
        "session_id": "wf",
        "workflow_id": "wf",
        "reply_message_id": "a_r_1",
    }
    assert stream.chat_run_id_of(_context()) == "r_1"


def test_no_other_agent_streams(registry):
    nodes, edges = _graph(target_type="console")
    # Its output goes elsewhere; it works for another agent; no chat run.
    assert stream.chat_stream_for(_context(nodes=nodes, edges=edges)) is None
    assert stream.chat_stream_for(_context(parent_node_id="wf:lead:1")) is None
    assert stream.chat_stream_for(_context(run_scope=None)) is None
    assert stream.chat_stream_for(_context(run_scope={"run_id": "r_1"})) is None
    assert stream.chat_run_id_of({"run_scope": {"run_id": ""}}) is None


def test_reply_in_chat_answers_the_run():
    from nodes.chat.chat_reply import ChatReplyNode

    assert ChatReplyNode.answers_chat_run is True


# ----- what may be shown -----


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("", ""),
        ("NO", ""),
        ("  NO_REP", ""),
        ("NO_REPLY", ""),
        ("No problem", "No problem"),
        ("NOTE: done", "NOTE: done"),
        ("Booked.\n<followups>[\"Change it\"]</followups>", "Booked.\n"),
        ("Booked.\n<follow", "Booked.\n"),
        ("Booked.\n<", "Booked.\n"),
        ("a < b", "a < b"),
    ],
)
def test_text_that_could_still_be_hidden_waits(text, shown):
    assert stream.shown_text(text) == shown


# ----- the events -----


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _emitter(published: List[Dict[str, Any]], *, attempt=1, flush_chars=10, clock=None, iteration=2):
    def publish(**kwargs):
        published.append(kwargs)

    return stream.ChatStreamEmitter.from_payload(
        {
            "iteration": iteration,
            "chat_stream": {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf", "reply_message_id": "a_r_1"},
        },
        attempt=attempt,
        publish=publish,
        flush_ms=50,
        flush_chars=flush_chars,
        clock=clock or Clock(),
    )


def _text(published):
    return "".join(event["fields"]["delta"] for event in published if event["suffix"] == "text.content")


async def test_a_reply_streams_in_batches_and_ends_final():
    published: List[Dict[str, Any]] = []
    clock = Clock()
    emitter = _emitter(published, clock=clock)
    emitter.begin()
    for piece in ["Your ", "booking ", "is set ", "for 10:00."]:
        await emitter(StreamEvent("text", piece))
    await emitter(StreamEvent("reasoning", "ignored"))
    emitter.end(final=True)
    suffixes = [event["suffix"] for event in published]
    assert suffixes[0] == "text.started" and suffixes[-1] == "text.ended"
    assert published[0]["fields"] == {"message_id": "r_1.2.1", "role": "assistant"}
    assert _text(published) == "Your booking is set for 10:00."
    # Batched: four deltas, fewer events than deltas.
    assert suffixes.count("text.content") < 4
    assert published[-1]["fields"] == {"message_id": "r_1.2.1", "final": True, "reply_message_id": "a_r_1"}
    assert {event["run_id"] for event in published} == {"r_1"}


async def test_time_flushes_a_short_delta():
    published: List[Dict[str, Any]] = []
    clock = Clock()
    emitter = _emitter(published, clock=clock, flush_chars=1000)
    await emitter(StreamEvent("text", "Hi"))
    assert published == []
    clock.now = 0.06
    await emitter(StreamEvent("text", " there"))
    assert _text(published) == "Hi there"


async def test_narration_beside_tool_calls_is_not_the_reply():
    published: List[Dict[str, Any]] = []
    emitter = _emitter(published)
    await emitter(StreamEvent("text", "Let me check the calendar."))
    emitter.end(final=False)
    assert published[-1]["fields"] == {"message_id": "r_1.2.1", "final": False}


async def test_no_reply_never_shows():
    published: List[Dict[str, Any]] = []
    emitter = _emitter(published, flush_chars=1)
    for piece in ["NO", "_RE", "PLY"]:
        await emitter(StreamEvent("text", piece))
    emitter.end(final=True)
    assert published == []


async def test_a_followups_block_never_streams():
    published: List[Dict[str, Any]] = []
    emitter = _emitter(published, flush_chars=1)
    for piece in ["Booked.", " <fol", "lowups>[\"Move it\"]", "</followups>"]:
        await emitter(StreamEvent("text", piece))
    emitter.end(final=True)
    assert _text(published) == "Booked. "
    assert emitter.text.endswith("</followups>")


async def test_a_retry_withdraws_what_earlier_attempts_streamed():
    published: List[Dict[str, Any]] = []
    emitter = _emitter(published, attempt=3)
    emitter.begin()
    discarded = [event for event in published if event["suffix"] == "custom"]
    assert [event["fields"]["value"]["message_id"] for event in discarded] == ["r_1.2.1", "r_1.2.2"]
    assert {event["fields"]["name"] for event in discarded} == {"opencompany.segment_discarded"}
    assert [event["event_key"] for event in discarded] == ["discard:r_1.2.1", "discard:r_1.2.2"]
    await emitter(StreamEvent("text", "Third time lucky."))
    emitter.end(final=True)
    assert published[-1]["fields"]["message_id"] == "r_1.2.3"


async def test_a_publishing_failure_never_fails_the_step():
    def broken(**_kwargs):
        raise RuntimeError("hub is gone")

    emitter = stream.ChatStreamEmitter.from_payload(
        {"chat_stream": {"run_id": "r_1", "session_id": "wf"}}, attempt=1, publish=broken, flush_chars=1
    )
    await emitter(StreamEvent("text", "Still answering"))
    emitter.end(final=True)
    assert emitter.text == "Still answering"


async def test_failed_attempt_is_withdrawn_before_retry_and_cannot_finish():
    published: List[Dict[str, Any]] = []
    emitter = _emitter(published, flush_chars=1)
    await emitter(StreamEvent("text", "Incomplete answer"))
    emitter.discard()
    discard = published[-1]
    assert discard["suffix"] == "custom"
    assert discard["fields"] == {"name": "opencompany.segment_discarded", "value": {"message_id": "r_1.2.1"}}
    assert discard["event_key"] == "discard:r_1.2.1"
    count = len(published)
    emitter.discard()
    await emitter(StreamEvent("text", "late delta"))
    emitter.end(final=True)
    assert len(published) == count


async def test_discard_does_not_flush_an_unpublished_partial_answer():
    published: List[Dict[str, Any]] = []
    emitter = _emitter(published, flush_chars=1000)
    await emitter(StreamEvent("text", "Partial"))
    emitter.discard()
    emitter.end(final=True)
    assert published == []


def test_a_step_without_a_chat_stream_has_no_emitter():
    assert stream.ChatStreamEmitter.from_payload({}, attempt=1) is None
    assert stream.ChatStreamEmitter.from_payload({"chat_stream": {"run_id": "r_1"}}, attempt=1) is None
