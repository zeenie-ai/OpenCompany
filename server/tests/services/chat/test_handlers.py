"""Chat's WebSocket commands (services/chat/handlers.py) with runs: a message
a deployed chat trigger will answer starts a run named in the response and
dispatched as the event id; a second send while it is live is refused; a
re-sent message returns the first send; reads return the v2 shape with the
thread and live runs; subscribing returns snapshots and delivers events; and
only the workflow's owner may use its session."""

from __future__ import annotations

import asyncio

from services.chat import ledger
from tests.services.chat._helpers import (
    FakeSocket,
    add_control,
    chat_trigger,
    chat_updates,
    saved_workflow,
    talking,
    updates_at_dispatch,
)


async def send(chat, message="Book Saturday", session_id="wf", socket=None, **extra):
    return await chat.handlers.handle_send_chat_message({"message": message, "session_id": session_id, "timestamp": "t", **extra}, socket)


async def test_a_message_the_employee_will_answer_starts_a_run(chat):
    await talking(chat.database)
    result = await send(chat)
    assert result["success"] is True and result["delivery"] == "now"
    run = await ledger.get_run(chat.database, result["run_id"])
    assert run.state == "pending" and run.user_message_uid == result["message_id"]
    [dispatched] = chat.dispatched
    # The run id is the event id, so the run a listener spawns has a predictable id.
    assert dispatched["event_id"] == result["run_id"]
    assert dispatched["workflow_id"] == "wf"
    assert dispatched["data"] == {
        "message": "Book Saturday",
        "timestamp": "t",
        "session_id": "wf",
        "message_id": result["message_id"],
        "run_id": result["run_id"],
    }
    assert chat_updates(chat.frames) == [{"workflow_id": "wf", "session_id": "wf", "role": "user"}]


async def test_the_message_is_sent_before_open_threads_hear_of_it(chat, monkeypatch):
    # Told first, the sender's thread could read the run before the answer
    # registering it arrived, and ask the server for it.
    await talking(chat.database)
    seen = updates_at_dispatch(chat, monkeypatch)
    await send(chat)
    assert seen == [0] and len(chat_updates(chat.frames)) == 1


async def test_a_paused_employee_queues_the_run(chat):
    await talking(chat.database, status="paused")
    result = await send(chat)
    assert result["delivery"] == "queued"
    assert (await ledger.get_run(chat.database, result["run_id"])).state == "queued"


async def test_nothing_is_saved_when_temporal_cannot_deliver_it(chat):
    # Running or paused, the message would never reach the employee.
    for generation, status in enumerate(("running", "paused"), start=1):
        await talking(chat.database, status=status, generation=generation)
        chat.engine.is_connected = False
        assert await send(chat) == {"success": False, "error": "engine_unavailable"}
        assert await chat.database.read_chat_messages("wf") == []
        assert await ledger.session_runs(chat.database, "wf") == []
        assert chat.dispatched == [] and chat_updates(chat.frames) == []
        chat.engine.is_connected = True
    # The editor's default chat has no workflow and is not refused.
    chat.engine.is_connected = False
    assert (await send(chat, session_id="default"))["success"] is True
    chat.engine.is_connected = True
    assert (await send(chat))["success"] is True


async def test_a_second_message_while_a_run_is_live_is_refused(chat):
    await talking(chat.database)
    first = await send(chat)
    second = await send(chat, "And Sunday?")
    assert second == {"success": False, "error": "run_in_progress", "run_id": first["run_id"]}
    assert len(chat.dispatched) == 1
    assert [row["message"] for row in await chat.database.read_chat_messages("wf")] == ["Book Saturday"]


async def test_a_resent_message_returns_the_first_send_and_dispatches_once(chat):
    await talking(chat.database)
    first = await send(chat, client_message_id="c-1")
    again = await send(chat, client_message_id="c-1")
    assert (again["message_id"], again["run_id"]) == (first["message_id"], first["run_id"])
    assert len(chat.dispatched) == 1
    bad = await send(chat, client_message_id="not ok")
    assert bad["error"] == "invalid_request"


async def test_no_run_when_no_deployed_chat_trigger_answers_the_session(chat):
    # A graph without a chat trigger, then one whose trigger listens to
    # another session, then a disabled one: the message is saved, no run.
    await add_control(chat.database, "wf", "running", nodes=[{"id": "wf:console:1", "type": "console", "data": {}}])
    assert (await send(chat))["run_id"] is None
    await add_control(chat.database, "wf2", "running", nodes=[chat_trigger("wf2")])
    await chat.database.save_node_parameters("wf2:chatTrigger:1", {"session_id": "elsewhere"})
    assert (await send(chat, session_id="wf2"))["run_id"] is None
    await add_control(chat.database, "wf3", "running", nodes=[chat_trigger("wf3", disabled=True)])
    assert (await send(chat, session_id="wf3"))["run_id"] is None
    assert len(await chat.database.read_chat_messages("wf")) == 1


async def test_untracked_chat_redelivery_uses_the_saved_message_identity(chat):
    await chat.handlers._dispatch(
        "default", None, message_uid="message-1", prompt="hello", run_id=None, timestamp="first"
    )
    await chat.handlers._dispatch(
        "default", None, message_uid="message-1", prompt="hello", run_id=None, timestamp="later"
    )
    await chat.handlers._dispatch(
        "default", None, message_uid="message-2", prompt="hello", run_id=None, timestamp="later"
    )

    assert [event["event_id"] for event in chat.dispatched] == ["message-1", "message-1", "message-2"]


async def test_the_owner_writes_only_their_own_messages(chat):
    await talking(chat.database)
    assert (await send(chat, role="assistant"))["error"] == "invalid_request"
    assert (await send(chat, message="   "))["error"] == "invalid_request"
    assert chat.dispatched == []


async def test_only_the_workflows_owner_may_use_its_chat(chat):
    await talking(chat.database)
    await chat.database.save_workflow("wf", "Salon", "Salon_1", {"nodes": [], "edges": [], "owner_id": "alice"})
    stranger = FakeSocket(user_id="mallory")
    owner = FakeSocket(user_id="alice")
    worker = FakeSocket(path="/ws/internal")
    for socket in (stranger, worker):
        assert (await send(chat, socket=socket))["error"] == "access_denied"
        assert (await chat.handlers.handle_get_chat_messages({"session_id": "wf"}, socket))["error"] == "access_denied"
        assert (await chat.handlers.handle_chat_subscribe({"session_id": "wf"}, socket))["error"] == "access_denied"
        assert (await chat.handlers.handle_clear_chat_messages({"session_id": "wf"}, socket))["error"] == "access_denied"
    assert chat.dispatched == []
    assert (await send(chat, socket=owner))["success"] is True


async def test_reading_the_thread_returns_the_v2_shape(chat):
    await talking(chat.database)
    sent = await send(chat, client_message_id="c-7")
    result = await chat.handlers.handle_get_chat_messages({"session_id": "wf", "all_generations": True}, None)
    assert result["success"] is True and result["protocol_version"] == 2
    [message] = result["messages"]
    assert message["id"] == sent["message_id"] and message["run_id"] == sent["run_id"]
    assert (message["text"], message["message"], message["kind"], message["status"]) == ("Book Saturday",) * 2 + ("text", "complete")
    assert message["client_message_id"] == "c-7" and message["parent_id"] is None
    assert message["siblings"] == {"index": 0, "count": 1, "ids": [message["id"]]}
    assert result["thread"] == {"active_leaf_id": message["id"], "revision": 1}
    [run] = result["active_runs"]
    assert (run["run_id"], run["state"], run["user_message_id"], run["seq"]) == (sent["run_id"], "pending", message["id"], 0)


async def test_each_message_says_how_its_run_ended(chat):
    """A reload still shows a failed run's error (it leaves no reply), and a
    finished run on both the owner's message and the reply."""
    await talking(chat.database)
    failed = await send(chat)
    await ledger.start_run(chat.database, run_id=failed["run_id"], temporal_workflow_id="tw", temporal_run_id="tr")
    await ledger.finish_run(
        chat.database, run_id=failed["run_id"], temporal_workflow_id="tw", temporal_run_id="tr",
        success=False, error="Calendar said no", hint="Reconnect Google",
    )
    answered = await send(chat, "Try again")
    run = await ledger.start_run(chat.database, run_id=answered["run_id"], temporal_workflow_id="tw2", temporal_run_id="tr")
    await ledger.post_reply(chat.database, run=run, node_id="n", text="Booked.", execution_id="gen-1")
    await ledger.finish_run(chat.database, run_id=answered["run_id"], temporal_workflow_id="tw2", temporal_run_id="tr", success=True)

    await ledger.record_step(chat.database, answered["run_id"], {"step_id": "c1", "state": "done", "name": "Checked Google Calendar"})

    result = await chat.handlers.handle_get_chat_messages({"session_id": "wf", "all_generations": True}, None)
    first, second, reply = result["messages"]
    # How long each run worked comes with it.
    assert all(message["run"].pop("duration_ms") >= 0 for message in (first, second, reply))
    assert first["run"] == {
        "run_id": failed["run_id"],
        "state": "error",
        "outcome": None,
        "error": {"message": "Calendar said no", "code": "run_failed", "hint": "Reconnect Google"},
    }
    assert second["run"] == reply["run"] == {
        "run_id": answered["run_id"],
        "state": "finished",
        "outcome": "success",
        "steps": [{"step_id": "c1", "state": "done", "name": "Checked Google Calendar"}],
    }
    assert result["active_runs"] == []


async def test_a_failed_read_is_never_an_empty_thread(chat, monkeypatch):
    await saved_workflow(chat.database, "wf")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(chat.database, "read_chat_messages", broken)
    result = await chat.handlers.handle_get_chat_messages({"session_id": "wf", "all_generations": True}, None)
    assert result == {"success": False, "error": "read_failed", "session_id": "wf"}


async def test_subscribing_returns_snapshots_and_then_delivers_events(chat):
    await talking(chat.database)
    sent = await send(chat)
    socket = FakeSocket()
    subscribed = await chat.handlers.handle_chat_subscribe({"session_id": "wf"}, socket)
    assert subscribed["hub_epoch"] == chat.hub.epoch
    assert [run["run_id"] for run in subscribed["active_runs"]] == [sent["run_id"]]

    await ledger.start_run(chat.database, run_id=sent["run_id"], temporal_workflow_id="tw", temporal_run_id="tr")
    await asyncio.sleep(0)
    [event] = socket.events()
    assert event["type"] == "com.opencompany.chat.run.started" and event["data"]["seq"] == 1

    snapshot = await chat.handlers.handle_get_chat_run({"run_id": sent["run_id"]}, socket)
    assert (snapshot["run"]["state"], snapshot["run"]["seq"]) == ("running", 1)

    await chat.handlers.handle_chat_unsubscribe({"session_id": "wf"}, socket)
    await ledger.finish_run(chat.database, run_id=sent["run_id"], temporal_workflow_id="tw", temporal_run_id="tr", success=True)
    await asyncio.sleep(0)
    assert len(socket.events()) == 1
    assert (await chat.handlers.handle_get_chat_run({"run_id": "r_unknown"}, socket))["error"] == "not_found"


async def test_the_owners_clear_ends_a_live_run(chat):
    await talking(chat.database)
    sent = await send(chat)
    socket = FakeSocket()
    await chat.handlers.handle_chat_subscribe({"session_id": "wf"}, socket)
    cleared = await chat.handlers.handle_clear_chat_messages({"session_id": "wf"}, socket)
    assert cleared["cleared_count"] == 1
    await asyncio.sleep(0)
    [event] = socket.events()
    assert (event["data"]["run_id"], event["data"]["code"]) == (sent["run_id"], "cleared")
    assert await ledger.get_run(chat.database, sent["run_id"]) is None
    # The lane is free: a new conversation starts.
    assert (await send(chat, "Hello again"))["run_id"] is not None
