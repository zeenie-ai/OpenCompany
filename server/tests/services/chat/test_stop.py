"""Stopping a chat run (services/chat/ledger.py, services/chat/handlers.py):
a run nothing picked up ends at once and frees the lane, and a workflow that
picks it up later claims it stopped; a running run moves to ``stopping``, its
reply so far is saved stopped and it finishes ``stopped``; the watchdog ends a
run still stopping after the grace and cancels its workflow. Steps are saved
on the run as they finish."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlalchemy import update

from models.chat import ChatRun
from models.database import WorkflowControlExecution
from services.chat import ledger
from tests.services.chat._helpers import FakeSocket, talking

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
CLAIM = dict(temporal_workflow_id="tw-1", temporal_run_id="tr-1")


async def admit(database, *, state="pending", session_id="wf"):
    admission = await ledger.admit_message(
        database, session_id=session_id, workflow_id=session_id, execution_id="gen-1",
        text="Book Saturday", track=True, state=state,
    )
    return admission.run


async def running(database):
    run = await admit(database)
    return await ledger.start_run(database, run_id=run.run_id, **CLAIM)


def kinds(socket):
    return [event["type"].removeprefix("com.opencompany.chat.run.") for event in socket.events()]


# ----- a run nothing picked up -----


async def test_stopping_a_pending_run_ends_it_at_once(database, hub):
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    run = await admit(database)
    stopped = await ledger.request_stop(database, run.run_id)
    assert (stopped.state, stopped.outcome, stopped.result) == ("stopped", "stopped", {"no_reply": True})
    assert stopped.stop_requested_at is not None and stopped.finished_at is not None
    await asyncio.sleep(0)
    [event] = socket.events()
    assert event["type"] == "com.opencompany.chat.run.finished"
    assert event["data"]["outcome"] == {"type": "stopped"}
    # The lane is free at once.
    assert await admit(database) is not None


async def test_stopping_a_queued_run_ends_it_before_resume(database, hub):
    run = await admit(database, state="queued")
    assert (await ledger.request_stop(database, run.run_id)).state == "stopped"


async def test_a_workflow_that_picks_up_a_stopped_run_claims_it_stopped(database, hub):
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    run = await admit(database)
    await ledger.request_stop(database, run.run_id)
    claimed = await ledger.start_run(database, run_id=run.run_id, **CLAIM)
    # Claimed as it is, so the agent stops at its first step instead of
    # answering untracked; nothing says it started.
    assert claimed is not None and claimed.state == "stopped"
    assert (claimed.temporal_workflow_id, claimed.temporal_run_id) == ("tw-1", "tr-1")
    assert await ledger.is_stopping(database, run.run_id) is True
    # Another workflow does not get it, and the claimant's finish changes nothing.
    assert await ledger.start_run(database, run_id=run.run_id, temporal_workflow_id="tw-2", temporal_run_id="tr-1") is None
    finished = await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    assert (finished.state, finished.outcome) == ("stopped", "stopped")
    await asyncio.sleep(0)
    assert kinds(socket) == ["finished"]


# ----- a running run -----


async def test_stopping_a_running_run_lets_it_stop_itself(database, hub):
    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    run = await running(database)
    stopping = await ledger.request_stop(database, run.run_id)
    assert stopping.state == "stopping" and stopping.stop_requested_at is not None
    assert await ledger.is_stopping(database, run.run_id) is True
    # A second press changes nothing and says nothing new.
    assert (await ledger.request_stop(database, run.run_id)).state == "stopping"
    await asyncio.sleep(0)
    assert kinds(socket) == ["started", "custom"]
    assert socket.events()[-1]["data"]["name"] == "opencompany.stopping"


async def test_a_stopped_runs_reply_so_far_is_saved_stopped(database, hub):
    run = await running(database)
    await ledger.request_stop(database, run.run_id)
    reply = await ledger.post_reply(database, run=run, node_id="wf:chatReply:1", text="I was about to", execution_id="gen-1")
    assert (reply["uid"], reply["status"]) == (run.reply_message_uid, "stopped")
    finished = await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    assert (finished.state, finished.outcome) == ("stopped", "stopped")
    assert finished.result == {"reply_message_id": run.reply_message_uid}
    # The lane is free again.
    assert await admit(database) is not None


async def test_a_run_that_stopped_before_writing_ends_without_a_reply(database, hub):
    run = await running(database)
    await ledger.request_stop(database, run.run_id)
    # A failure while stopping is still a stop: the owner asked for it.
    finished = await ledger.finish_run(database, run_id=run.run_id, success=False, error="cancelled", **CLAIM)
    assert (finished.state, finished.outcome, finished.result) == ("stopped", "stopped", {"no_reply": True})


async def test_a_run_that_ended_is_not_stopped(database, hub):
    run = await running(database)
    await ledger.finish_run(database, run_id=run.run_id, success=True, **CLAIM)
    assert (await ledger.request_stop(database, run.run_id)).state == "finished"
    assert await ledger.request_stop(database, "r_unknown") is None
    assert await ledger.is_stopping(database, "r_unknown") is False


# ----- the watchdog -----


async def stop_requested(database, run_id, moment):
    async with database.get_session() as session:
        await session.execute(update(ChatRun).where(ChatRun.run_id == run_id).values(stop_requested_at=moment))
        await session.commit()


async def test_the_watchdog_ends_a_run_still_stopping_after_the_grace(database, hub):
    from services.chat.config import runs_setting

    run = await running(database)
    await ledger.request_stop(database, run.run_id)
    await ledger.post_reply(database, run=run, node_id="n", text="Half an answer", execution_id="gen-1")
    cancelled = []

    async def cancel(workflow_id, temporal_run_id):
        cancelled.append((workflow_id, temporal_run_id))

    grace = timedelta(seconds=runs_setting("stop_grace_s"))
    await stop_requested(database, run.run_id, NOW - grace + timedelta(seconds=5))
    assert await ledger.sweep(database, now=NOW, temporal_cancel=cancel) == []
    assert cancelled == []

    await stop_requested(database, run.run_id, NOW - grace - timedelta(seconds=1))
    assert await ledger.sweep(database, now=NOW, temporal_cancel=cancel) == [run.run_id]
    assert cancelled == [("tw-1", "tr-1")]
    ended = await ledger.get_run(database, run.run_id)
    assert (ended.state, ended.outcome, ended.result) == ("stopped", "stopped", {"reply_message_id": run.reply_message_uid})


async def test_a_failed_cancel_still_ends_the_run(database, hub):
    run = await running(database)
    await ledger.request_stop(database, run.run_id)
    await stop_requested(database, run.run_id, NOW - timedelta(days=1))

    async def broken(*_):
        raise RuntimeError("temporal is down")

    assert await ledger.sweep(database, now=NOW, temporal_cancel=broken) == [run.run_id]
    assert (await ledger.get_run(database, run.run_id)).state == "stopped"


# ----- steps -----


async def test_steps_are_saved_once_each_up_to_the_cap(database, hub, monkeypatch):
    run = await running(database)
    await ledger.record_step(database, run.run_id, {"step_id": "c1", "state": "done", "name": "Searched the web"})
    await ledger.record_step(database, run.run_id, {"step_id": "c2", "state": "failed", "name": "Used Gmail"})
    # A retried step replaces its earlier save.
    await ledger.record_step(database, run.run_id, {"step_id": "c1", "state": "done", "name": "Searched the web", "duration_ms": 900})
    saved = (await ledger.get_run(database, run.run_id)).steps
    assert [(step["step_id"], step.get("duration_ms")) for step in saved] == [("c2", None), ("c1", 900)]

    monkeypatch.setattr(ledger, "steps_setting", lambda name: 2)
    await ledger.record_step(database, run.run_id, {"step_id": "c3", "state": "done", "name": "Used Drive"})
    assert len((await ledger.get_run(database, run.run_id)).steps) == 2
    # Nothing to save without an id, or for a run that is gone.
    await ledger.record_step(database, run.run_id, {"state": "done"})
    await ledger.record_step(database, "r_unknown", {"step_id": "c4"})


# ----- the command -----


async def test_the_stop_command(chat):
    await talking(chat.database)
    sent = await chat.handlers.handle_send_chat_message({"message": "Book Saturday", "session_id": "wf"}, None)
    stopped = await chat.handlers.handle_stop_chat_run({"run_id": sent["run_id"]}, None)
    assert stopped == {"success": True, "run_id": sent["run_id"], "state": "stopped"}
    # Pressed again, it answers the same.
    assert (await chat.handlers.handle_stop_chat_run({"run_id": sent["run_id"]}, None))["state"] == "stopped"

    second = await chat.handlers.handle_send_chat_message({"message": "And Sunday?", "session_id": "wf"}, None)
    await ledger.start_run(chat.database, run_id=second["run_id"], **CLAIM)
    assert (await chat.handlers.handle_stop_chat_run({"run_id": second["run_id"]}, None))["state"] == "stopping"
    await ledger.finish_run(chat.database, run_id=second["run_id"], success=True, **CLAIM)

    third = await chat.handlers.handle_send_chat_message({"message": "Thanks", "session_id": "wf"}, None)
    await ledger.start_run(chat.database, run_id=third["run_id"], temporal_workflow_id="tw-3", temporal_run_id="tr-3")
    await ledger.finish_run(chat.database, run_id=third["run_id"], temporal_workflow_id="tw-3", temporal_run_id="tr-3", success=True)
    refused = await chat.handlers.handle_stop_chat_run({"run_id": third["run_id"]}, None)
    assert refused == {"success": False, "error": "not_stoppable", "run_id": third["run_id"], "state": "finished"}
    assert (await chat.handlers.handle_stop_chat_run({"run_id": "r_unknown"}, None))["error"] == "not_found"


async def test_only_the_workflows_owner_may_stop_its_runs(chat):
    await talking(chat.database)
    await chat.database.save_workflow("wf", "Salon", "Salon_1", {"nodes": [], "edges": [], "owner_id": "alice"})
    sent = await chat.handlers.handle_send_chat_message({"message": "Book Saturday", "session_id": "wf"}, FakeSocket(user_id="alice"))
    for socket in (FakeSocket(user_id="mallory"), FakeSocket(path="/ws/internal")):
        assert (await chat.handlers.handle_stop_chat_run({"run_id": sent["run_id"]}, socket))["error"] == "access_denied"
    assert (await ledger.get_run(chat.database, sent["run_id"])).state == "pending"


async def version_control(database, *, status="running", resumed=None):
    manifest = {"execution_control_version": 1}
    if resumed:
        manifest["last_resumed_at"] = resumed.isoformat()
    async with database.get_session() as session:
        await session.execute(update(WorkflowControlExecution).where(WorkflowControlExecution.id == "wf-1").values(
            resource_manifest=manifest, status=status, revision=3,
        ))
        await session.commit()


async def test_controlled_stop_keeps_the_chat_run_reply_and_lane(chat, monkeypatch):
    from services.deployment import handlers as deployment_handlers

    await talking(chat.database)
    await version_control(chat.database)
    run = await running(chat.database)
    await ledger.post_reply(chat.database, run=run, node_id="n", text="A partial answer", execution_id="gen-1")
    pause = AsyncMock(return_value={"success": True, "state": "paused", "revision": 5})
    monkeypatch.setattr(deployment_handlers, "handle_pause_workflow", pause)
    reply = await chat.handlers.handle_stop_chat_run({
        "run_id": run.run_id, "expected_revision": 3, "idempotency_key": "stop-1",
    }, None)
    assert reply["resumable"] is True and reply["state"] == "paused"
    pause.assert_awaited_once_with({
        "workflow_id": "wf", "expected_root_execution_id": "gen-1", "expected_revision": 3,
        "idempotency_key": "stop-1",
    }, None)
    saved = await ledger.get_run(chat.database, run.run_id)
    assert saved.state == "running" and saved.stop_requested_at is None and saved.finished_at is None
    assert (await ledger.lane_run(chat.database, "wf")).run_id == run.run_id
    assert (await ledger.saved_message(chat.database, saved.reply_message_uid)).message == "A partial answer"


async def test_controlled_chat_reload_uses_its_owning_generation(chat):
    await talking(chat.database)
    await version_control(chat.database, status="paused")
    run = await running(chat.database)
    # A newer generation must not masquerade as this suspended run's owner.
    await talking(chat.database, generation=2)
    [snapshot] = await chat.handlers.run_snapshots(chat.database, "wf")
    assert snapshot["run_id"] == run.run_id and snapshot["state"] == "running"
    assert snapshot["workflow_control"]["root_execution_id"] == "gen-1"
    assert snapshot["workflow_control"]["state"] == "paused"


async def test_controlled_stop_requires_an_idempotency_key(chat):
    await talking(chat.database)
    await version_control(chat.database)
    run = await running(chat.database)
    result = await chat.handlers.handle_stop_chat_run({"run_id": run.run_id, "expected_revision": 3}, None)
    assert result == {"success": False, "error": "idempotency_key_required"}
    assert (await ledger.get_run(chat.database, run.run_id)).state == "running"


async def test_long_controlled_pause_does_not_expire_and_resume_grants_a_fresh_window(database, hub):
    from services.chat.config import runs_setting

    await talking(database)
    run = await running(database)
    longest = timedelta(seconds=runs_setting("max_running_s"))
    async with database.get_session() as session:
        await session.execute(update(ChatRun).where(ChatRun.run_id == run.run_id).values(
            started_at=NOW - timedelta(days=30), created_at=NOW - timedelta(days=30),
        ))
        await session.commit()
    for state in ("pausing", "paused", "resuming"):
        await version_control(database, status=state)
        assert await ledger.sweep(database, now=NOW, process_started=NOW - timedelta(days=30)) == []
    await version_control(database, status="running", resumed=NOW)
    assert await ledger.sweep(database, now=NOW + longest - timedelta(seconds=1), process_started=NOW) == []
    assert await ledger.sweep(database, now=NOW + longest + timedelta(seconds=1), process_started=NOW) == [run.run_id]
    assert (await ledger.get_run(database, run.run_id)).error_code == "timed_out"
