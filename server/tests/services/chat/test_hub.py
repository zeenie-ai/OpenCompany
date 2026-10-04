"""Run events (services/chat/hub.py, reducer.py, events.py): delivered only to
the sockets subscribed to the session, in order, one ``seq`` per run, once
per ``event_key``, nothing after the terminal event, a resync for a socket
that falls behind; and the snapshot folded from a run's events matches the
protocol fixture."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketState

from services.chat import reducer
from services.chat.events import RUN_SUFFIXES, chat_run_event, run_event_suffix
from services.chat.hub import ChatRunHub
from tests.services.chat._helpers import FakeSocket

FIXTURE = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "chat" / "__fixtures__" / "saturday-booking.events.json"


def publish(hub, suffix, *, run_id="r_1", session_id="wf", **fields):
    event_key = fields.pop("event_key", None)
    return hub.publish(run_id=run_id, session_id=session_id, workflow_id=session_id, suffix=suffix, fields=fields, event_key=event_key)


async def drained():
    for _ in range(3):
        await asyncio.sleep(0)


async def test_a_subscriber_gets_its_sessions_events_in_order():
    hub = ChatRunHub(queue_size=16)
    mine, other = FakeSocket(), FakeSocket()
    hub.subscribe(mine, "wf")
    hub.subscribe(other, "another")
    publish(hub, "started", kind="message")
    publish(hub, "step.started", step_id="s1", step_name="Checking")
    publish(hub, "finished", outcome={"type": "success"}, result={"no_reply": True})
    await drained()
    events = mine.events()
    assert [event["type"] for event in events] == [
        "com.opencompany.chat.run.started",
        "com.opencompany.chat.run.step.started",
        "com.opencompany.chat.run.finished",
    ]
    assert [event["data"]["seq"] for event in events] == [1, 2, 3]
    assert [event["id"] for event in events] == ["r_1:1", "r_1:2", "r_1:3"]
    assert all(event["subject"] == "r_1" and event["data"]["hub_epoch"] == hub.epoch for event in events)
    # Scope rides in data, never as top-level extension attributes.
    assert all("workflow_id" not in event for event in events)
    assert other.sent == []


async def test_repeated_keys_and_events_after_the_end_are_dropped():
    hub = ChatRunHub(queue_size=16)
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    assert publish(hub, "started", event_key="started") is not None
    assert publish(hub, "started", event_key="started") is None
    publish(hub, "failed", message="no", code="x", event_key="terminal")
    assert publish(hub, "step.started", step_id="late", step_name="Late") is None
    assert hub.ended("r_1") and hub.live_snapshot("r_1") is None and hub.current_seq("r_1") == 0
    await drained()
    assert [event["data"]["seq"] for event in socket.events()] == [1, 2]


async def test_unsubscribing_and_disconnecting_stop_delivery():
    hub = ChatRunHub(queue_size=16)
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    hub.subscribe(socket, "second")
    hub.unsubscribe(socket, "wf")
    assert not hub.subscribed(socket, "wf") and hub.subscribed(socket, "second")
    publish(hub, "started")
    publish(hub, "started", run_id="r_2", session_id="second")
    await drained()
    assert [event["data"]["run_id"] for event in socket.events()] == ["r_2"]
    hub.drop_socket(socket)
    assert not hub.subscribed(socket, "second")


async def test_a_closed_socket_is_dropped_when_written_to():
    hub = ChatRunHub(queue_size=16)
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    socket.client_state = WebSocketState.DISCONNECTED
    publish(hub, "started")
    await drained()
    assert socket.sent == [] and not hub.subscribed(socket, "wf")


async def test_a_socket_that_falls_behind_gets_a_resync():
    hub = ChatRunHub(queue_size=3)
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    # No await between publishes: the writer cannot drain.
    for n in range(5):
        publish(hub, "step.started", step_id=f"s{n}", step_name="Step")
    await drained()
    events = socket.events()
    assert events[0]["data"]["name"] == "opencompany.resync"
    assert events[0]["data"]["session_id"] == "wf"
    # What follows the resync is newer than anything it replaced.
    assert [event["data"]["seq"] for event in events[1:]] == [4, 5]


async def test_the_disconnect_listener_drops_subscriptions(monkeypatch, hub):
    import services.chat  # noqa: F401 - registers the listener
    from services.status_broadcaster import StatusBroadcaster

    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    await StatusBroadcaster().disconnect(socket)
    assert not hub.subscribed(socket, "wf")


async def test_a_session_snapshot_reads_the_live_runs():
    hub = ChatRunHub(queue_size=16)
    publish(hub, "started", kind="message", reply_message_id="a_r_1")
    publish(hub, "text.started", message_id="r_1.1.1")
    publish(hub, "text.content", message_id="r_1.1.1", delta="Hel")
    publish(hub, "text.content", message_id="r_1.1.1", delta="lo")
    publish(hub, "started", run_id="r_9", session_id="elsewhere")
    live = hub.session_live("wf")
    assert list(live) == ["r_1"]
    assert live["r_1"]["seq"] == 4 and live["r_1"]["state"] == "running"
    assert live["r_1"]["segments"] == [{"message_id": "r_1.1.1", "text": "Hello", "final": None}]


# ----- events -----


def test_run_events_are_only_the_protocols():
    with pytest.raises(ValueError):
        chat_run_event(suffix="exploded", run_id="r", seq=1, hub_epoch="e", workflow_id=None, session_id="s")
    assert run_event_suffix("com.opencompany.chat.run.step.finished") == "step.finished"
    assert run_event_suffix("com.opencompany.chat.updated") is None
    # Fields can never override the scope.
    event = chat_run_event(suffix="started", run_id="r", seq=3, hub_epoch="e", workflow_id="w", session_id="s", fields={"seq": 99, "run_id": "x"})
    assert (event.data["seq"], event.data["run_id"]) == (3, "r")


def test_the_protocol_doc_lists_every_suffix():
    doc = (Path(__file__).resolve().parents[4] / "docs-internal" / "chat_protocol.md").read_text(encoding="utf-8")
    for suffix in RUN_SUFFIXES:
        assert f"`{suffix}`" in doc, suffix


# ----- the reducer against the protocol fixture -----


def fixture_events():
    frames = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [frame["data"] for frame in frames]


def test_the_fixture_run_folds_to_its_final_state():
    events = [event for event in fixture_events() if event["subject"] == "r_8f2a1c"]
    snapshot = reducer.replay("r_8f2a1c", events)
    assert snapshot["state"] == "finished" and snapshot["seq"] == 26
    assert snapshot["outcome"]["type"] == "success"
    # The interface it streamed, and the card of the draft it made.
    assert [activity["activity_type"] for activity in snapshot["activities"]] == ["json_render", "approval"]
    assert [step["state"] for step in snapshot["steps"]] == ["done", "done", "done"]
    assert snapshot["steps"][0]["name"] == "Checked Google Calendar"
    [segment] = snapshot["segments"]
    assert segment["final"] is True and segment["text"].startswith("Saturday is fairly full")
    assert (snapshot["user_message_id"], snapshot["reply_message_id"]) == ("m_owner_1", "a_r_8f2a1c")


def test_every_prefix_folds_and_duplicates_change_nothing():
    events = [event for event in fixture_events() if event["subject"] == "r_8f2a1c"]
    snapshot = reducer.empty_snapshot("r_8f2a1c")
    for event in events:
        snapshot = reducer.apply_event(snapshot, event)
        assert reducer.apply_event(snapshot, event) == snapshot


def test_a_discarded_segment_and_stopping_fold():
    snapshot = reducer.empty_snapshot("r")
    for seq, (suffix, data) in enumerate(
        [
            ("started", {}),
            ("text.started", {"message_id": "r.1.1"}),
            ("text.content", {"message_id": "r.1.1", "delta": "draft"}),
            ("custom", {"name": "opencompany.segment_discarded", "value": {"message_id": "r.1.1"}}),
            ("custom", {"name": "opencompany.stopping", "value": {}}),
        ],
        start=1,
    ):
        snapshot = reducer.apply_event(snapshot, {"type": f"com.opencompany.chat.run.{suffix}", "data": {"seq": seq, **data}})
    assert snapshot["segments"] == [] and snapshot["state"] == "stopping"


def test_the_stored_run_wins_once_it_has_ended():
    stored = {**reducer.empty_snapshot("r"), "state": "finished", "seq": 0}
    live = {**reducer.empty_snapshot("r"), "state": "running", "seq": 7, "steps": [{"step_id": "s"}]}
    merged = reducer.merge_snapshot(stored, live)
    assert merged["state"] == "finished" and merged["seq"] == 7 and merged["steps"] == [{"step_id": "s"}]
    assert reducer.merge_snapshot({**stored, "state": "pending"}, live)["state"] == "running"
