"""What a chat run's tools show in its reply (services/chat/parts.py): a UI
is saved on the run and streamed as patches; the reply carries it once
saved, also when the run wrote nothing; snapshots fold the stream; a button
press is checked against the UI it came from and reaches the employee as a
[ui-event] line; what the owner sets is kept on the reply."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from services.chat import ledger, parts, reducer
from services.genui.spec import check_spec
from tests.services.chat._helpers import FakeSocket, talking

FIXTURES = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "chat" / "__fixtures__"
CLAIM = dict(temporal_workflow_id="tw-1", temporal_run_id="tr-1")


def booking():
    raw = json.loads((FIXTURES / "saturday-booking.spec.json").read_text(encoding="utf-8"))
    return check_spec(raw).spec


async def running(database, session_id="wf"):
    admission = await ledger.admit_message(
        database, session_id=session_id, workflow_id=session_id, execution_id="gen-1", text="Book Saturday", track=True
    )
    return await ledger.start_run(database, run_id=admission.run.run_id, **CLAIM)


def stream_of(run):
    return {"run_id": run.run_id, "session_id": run.session_id, "workflow_id": run.workflow_id, "reply_message_id": run.reply_message_uid}


@pytest.fixture
def roomy_hub():
    """A hub whose sockets hold a whole UI's patches."""
    from services.chat.hub import ChatRunHub, reset_chat_hub_for_tests

    fresh = ChatRunHub(queue_size=64)
    previous = reset_chat_hub_for_tests(fresh)
    try:
        yield fresh
    finally:
        reset_chat_hub_for_tests(previous)


async def test_a_ui_is_saved_on_the_run_and_streamed_as_patches(database, roomy_hub):
    hub = roomy_hub
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    run = await running(database)
    spec = booking()
    part_id = await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=spec)
    assert part_id == parts.ui_part_id(run.run_id, "call_1") and part_id.startswith("ui_")
    # A retried call saves the same part again, not a second one, and says nothing new.
    assert await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=spec) == part_id
    [saved] = await parts.run_parts(database, run.run_id)
    assert (saved.kind, saved.key) == ("ui", f"ui:{part_id}")
    assert saved.payload["spec"] == spec and saved.payload["state"] == spec["state"] and saved.payload["state_revision"] == 0

    await asyncio.sleep(0)
    events = [event for event in socket.events() if event["type"].startswith("com.opencompany.chat.run.activity")]
    snapshot, *deltas = events
    assert snapshot["data"]["content"] == {"root": "", "state": {}, "elements": {}}
    assert {event["data"]["message_id"] for event in events} == {part_id}
    assert len(deltas) == 2 + len(spec["elements"])
    # The run's snapshot folds the stream into the spec.
    live = hub.live_snapshot(run.run_id)
    [activity] = live["activities"]
    assert (activity["activity_type"], activity["content"], activity["patches"]) == ("json_render", spec, len(deltas))


async def test_the_reply_carries_the_ui_from_the_moment_it_is_saved(database, hub):
    run = await running(database)
    part_id = await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=booking())
    reply = await ledger.post_reply(database, run=run, node_id="wf:chatReply:1", text="Here are Saturday's slots.", execution_id="gen-1")
    assert [item["part_id"] for item in reply["parts"]["ui"]] == [part_id]
    finished = await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    assert finished.result == {"reply_message_id": run.reply_message_uid}
    [_, saved] = await database.read_chat_messages("wf")
    assert saved["parts"]["ui"][0]["part_id"] == part_id


async def test_a_run_that_only_showed_a_ui_gets_a_reply_holding_it(database, hub, frames):
    run = await running(database)
    part_id = await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=booking())
    finished = await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    assert finished.result == {"reply_message_id": run.reply_message_uid}
    [_, reply] = await database.read_chat_messages("wf")
    assert (reply["role"], reply["message"], reply["uid"]) == ("assistant", "", run.reply_message_uid)
    assert reply["parts"]["ui"][0]["part_id"] == part_id
    # Open threads were told to read it.
    assert any(frame["type"] == "chat.updated" for frame in frames)


async def test_the_end_keeps_what_the_owner_set_after_the_reply_was_saved(database, hub):
    run = await running(database)
    first = await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=booking())
    await ledger.post_reply(database, run=run, node_id="n", text="Pick one.", execution_id="gen-1")
    await parts.update_ui_state(database, "wf", first, [{"path": "/slot", "value": "s4"}])
    # A second interface after the reply was saved is added at the end.
    second = await parts.show_ui(database, stream_of(run), tool_call_id="call_2", spec=booking())
    await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    [_, reply] = await database.read_chat_messages("wf")
    assert [item["part_id"] for item in reply["parts"]["ui"]] == [first, second]
    assert reply["parts"]["ui"][0]["state"]["slot"] == "s4"


async def test_sealing_twice_changes_nothing_and_no_parts_seals_nothing(database, hub):
    run = await running(database)
    assert await parts.seal_parts(database, run) is False
    await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=booking())
    assert await parts.seal_parts(database, run) is True
    assert await parts.seal_parts(database, run) is False


async def test_a_press_is_checked_against_its_ui(database, hub):
    run = await running(database)
    part_id = await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=booking())
    part = await parts.find_ui_part(database, "wf", part_id)
    assert part is not None and part["part_id"] == part_id
    assert await parts.find_ui_part(database, "other", part_id) is None

    pressed = parts.check_ui_event(part, "hold", "holdSlot", {"slot": "s3"})
    assert pressed == {"label": "Hold this slot for 15 min", "params": {"slot": "s3"}}
    with pytest.raises(parts.UiRefused):
        parts.check_ui_event(part, "svc", "holdSlot", {})
    with pytest.raises(parts.UiRefused):
        parts.check_ui_event(part, "hold", "deleteEverything", {})
    with pytest.raises(parts.UiRefused):
        parts.check_ui_event(part, "hold", "holdSlot", {"slot": "s3", "extra": 1})
    line = parts.ui_event_message(part_id=part_id, element_id="hold", label="Hold this slot for 15 min", action="holdSlot", params={"slot": "s3"})
    assert line.startswith("[ui-event]{") and line.endswith("}[/ui-event]")
    assert json.loads(line[len("[ui-event]"):-len("[/ui-event]")]) == {
        "ui_id": part_id, "element": "hold", "label": "Hold this slot for 15 min", "action": "holdSlot", "params": {"slot": "s3"},
    }


async def test_what_the_owner_sets_is_kept_on_the_ui(database, hub):
    run = await running(database)
    part_id = await parts.show_ui(database, stream_of(run), tool_call_id="call_1", spec=booking())
    # Before the reply is saved, on the run.
    first = await parts.update_ui_state(database, "wf", part_id, [{"path": "/slot", "value": "s4"}])
    assert first == {"state": {**booking()["state"], "slot": "s4"}, "state_revision": 1}
    await ledger.post_reply(database, run=run, node_id="n", text="Pick one.", execution_id="gen-1")
    # After, on the reply.
    second = await parts.update_ui_state(database, "wf", part_id, [{"path": "/note", "value": "Patch test done"}, {"path": "/a/b", "value": 1}])
    assert second["state_revision"] == 2 and second["state"]["note"] == "Patch test done" and second["state"]["a"] == {"b": 1}
    [_, reply] = await database.read_chat_messages("wf")
    assert reply["parts"]["ui"][0]["state"]["slot"] == "s4"

    with pytest.raises(parts.UiRefused):
        await parts.update_ui_state(database, "wf", part_id, [{"path": "/__proto__/x", "value": 1}])
    with pytest.raises(parts.UiRefused):
        await parts.update_ui_state(database, "wf", part_id, [])
    with pytest.raises(parts.UiRefused):
        await parts.update_ui_state(database, "wf", part_id, [{"path": "/big", "value": "x" * 5000}])
    assert await parts.update_ui_state(database, "wf", "ui_nope", [{"path": "/x", "value": 1}]) is None


def test_the_reducer_folds_activity_events():
    snapshot = reducer.empty_snapshot("r_1")

    def event(seq, suffix, **data):
        return {"type": f"com.opencompany.chat.run.{suffix}", "data": {"seq": seq, **data}}

    snapshot = reducer.apply_event(snapshot, event(1, "activity.snapshot", message_id="ui_1", activity_type="json_render", content={"root": "", "state": {}, "elements": {}}, replace=True))
    snapshot = reducer.apply_event(snapshot, event(2, "activity.delta", message_id="ui_1", activity_type="json_render", patch=[{"op": "add", "path": "/root", "value": "a"}]))
    snapshot = reducer.apply_event(snapshot, event(3, "activity.delta", message_id="ui_1", activity_type="json_render", patch=[{"op": "add", "path": "/__proto__/x", "value": 1}]))
    [activity] = snapshot["activities"]
    assert activity == {"message_id": "ui_1", "activity_type": "json_render", "content": {"root": "a", "state": {}, "elements": {}}, "patches": 2}


# ----- the commands -----


async def test_a_press_goes_to_the_employee_as_a_ui_event(chat):
    await talking(chat.database)
    first = await chat.handlers.handle_send_chat_message({"message": "Book Saturday", "session_id": "wf"}, None)
    run = await ledger.start_run(chat.database, run_id=first["run_id"], **CLAIM)
    part_id = await parts.show_ui(chat.database, stream_of(run), tool_call_id="call_1", spec=booking())
    await ledger.post_reply(chat.database, run=run, node_id="n", text="Pick a slot.", execution_id="gen-1")
    await ledger.finish_run(chat.database, run_id=run.run_id, success=True, **CLAIM)

    press = {"part_id": part_id, "element_id": "hold", "action": "holdSlot", "params": {"slot": "s2"}}
    sent = await chat.handlers.handle_send_chat_message({"message": "anything", "session_id": "wf", "ui_event": press}, None)
    assert sent["success"] is True
    pressed_run = await ledger.get_run(chat.database, sent["run_id"])
    assert pressed_run.kind == "action"
    rows = await chat.database.read_chat_messages("wf")
    owner = rows[-1]
    # The owner's message reads the button's label, whatever the client sent.
    assert (owner["message"], owner["kind"], owner["meta"]["ui_event"]) == ("Hold this slot for 15 min", "action", press)
    dispatched = chat.dispatched[-1]["data"]["message"]
    assert dispatched.startswith("[ui-event]") and '"action": "holdSlot"' in dispatched

    bad = await chat.handlers.handle_send_chat_message(
        {"message": "x", "session_id": "wf", "ui_event": {**press, "action": "somethingElse"}}, None
    )
    assert bad["error"] == "ui_event_rejected"


async def test_the_ui_state_command(chat):
    await talking(chat.database)
    first = await chat.handlers.handle_send_chat_message({"message": "Book Saturday", "session_id": "wf"}, None)
    run = await ledger.start_run(chat.database, run_id=first["run_id"], **CLAIM)
    part_id = await parts.show_ui(chat.database, stream_of(run), tool_call_id="call_1", spec=booking())
    saved = await chat.handlers.handle_chat_ui_state({"session_id": "wf", "part_id": part_id, "changes": [{"path": "/slot", "value": "s1"}]}, None)
    assert saved == {"success": True, "part_id": part_id, "state_revision": 1}
    assert (await chat.handlers.handle_chat_ui_state({"session_id": "wf", "part_id": "ui_x", "changes": [{"path": "/a", "value": 1}]}, None))["error"] == "not_found"
    bad = await chat.handlers.handle_chat_ui_state({"session_id": "wf", "part_id": part_id, "changes": "nope"}, None)
    assert bad["error"] == "invalid_request"


# ----- documents the run wrote -----


def document(version=1, item_id="item_1"):
    return {"workflow_id": "wf", "canvas_node_id": "wf:canvas:1", "item_id": item_id, "version": version, "title": "Plan", "format": "markdown"}


async def test_a_document_the_run_wrote_shows_once_at_its_latest_version(database, hub):
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    run = await running(database)
    await parts.show_artifact(database, stream_of(run), artifact=document(1))
    await parts.show_artifact(database, stream_of(run), artifact=document(2))
    # One part per document, at the latest version.
    [saved] = await parts.run_parts(database, run.run_id)
    assert (saved.kind, saved.key, saved.payload) == ("artifacts", "artifact:item_1", document(2))
    await asyncio.sleep(0)
    shown = [event["data"] for event in socket.events() if event["type"].endswith("activity.snapshot")]
    assert [(data["activity_type"], data["message_id"], data["content"]["version"]) for data in shown] == [
        ("artifact", "artifact_item_1", 1),
        ("artifact", "artifact_item_1", 2),
    ]
    # A run that wrote nothing but the document gets a reply holding its card.
    finished = await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    assert finished.result == {"reply_message_id": run.reply_message_uid}
    [_, reply] = await database.read_chat_messages("wf")
    assert (reply["message"], reply["parts"]["artifacts"]) == ("", [document(2)])


async def test_a_newer_version_replaces_the_card_on_a_saved_reply(database, hub):
    run = await running(database)
    await parts.show_artifact(database, stream_of(run), artifact=document(1))
    reply = await ledger.post_reply(database, run=run, node_id="n", text="Here is the plan.", execution_id="gen-1")
    assert reply["parts"]["artifacts"] == [document(1)]
    await parts.show_artifact(database, stream_of(run), artifact=document(2))
    await parts.show_artifact(database, stream_of(run), artifact=document(1, item_id="item_2"))
    await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    [_, saved] = await database.read_chat_messages("wf")
    # In place, then the new one.
    assert saved["parts"]["artifacts"] == [document(2), document(1, item_id="item_2")]


async def test_a_document_without_an_item_shows_nothing(database, hub):
    run = await running(database)
    await parts.show_artifact(database, stream_of(run), artifact={**document(), "item_id": ""})
    assert await parts.run_parts(database, run.run_id) == []
