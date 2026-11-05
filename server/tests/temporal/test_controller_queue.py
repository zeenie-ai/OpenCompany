"""Bounded controller queue rollover, FIFO handoff and paused review draining."""
from __future__ import annotations
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from sqlmodel import select
from temporalio.exceptions import ApplicationError
from models.employees import WorkflowQueuedEvent
from services.temporal.controller_queue import clear_dead_letters, event_batches, spill_controller_events, read_controller_events
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow

async def test_durable_spill_is_idempotent_and_review_scan_reaches_buried_task(real_database, monkeypatch):
    database = real_database
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: database))
    ordinary = [["mail", {"id": str(i), "type": "mail"}] for i in range(150)]
    payload = {"controller_id": "controller", "events": ordinary + [["review", {"id": "review", "type": "task"}]]}
    await spill_controller_events(payload)
    await spill_controller_events(payload)
    review = await read_controller_events({"controller_id": "controller", "request_id": "review-read", "review_listener_ids": ["review"]})
    assert [item[1]["id"] for item in review["events"]] == ["review"]
    assert review["more"] is True
    assert await read_controller_events({"controller_id": "controller", "request_id": "review-read", "review_listener_ids": ["review"]}) == review
    first = await read_controller_events({"controller_id": "controller", "request_id": "first"})
    second = await read_controller_events({"controller_id": "controller", "request_id": "second"})
    assert [item[1]["id"] for item in first["events"] + second["events"]] == [str(i) for i in range(150)]


def test_batches_are_bounded_by_bytes_not_only_event_count():
    items = [["mail", {"id": str(i), "body": "x" * 200_000}] for i in range(12)]
    chunks = list(event_batches(items))
    assert len(chunks) == 6
    assert all(len(json.dumps(chunk).encode()) < 513_000 for chunk in chunks)
    assert [item[1]["id"] for chunk in chunks for item in chunk] == [str(i) for i in range(12)]


async def test_large_accepted_event_survives_chunked_rollover_once(real_database, wf, monkeypatch):
    database = real_database
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: database))
    controller = WorkflowControlWorkflow()
    item = ["mail", {"id": "large", "body": "x" * 1_700_000}]
    controller._events = [tuple(item), ("mail", {"id": "after"})]
    carried = {}
    async def invoke(name, payload, **kwargs):
        assert len(json.dumps(payload).encode()) < 512_000
        await spill_controller_events(payload)
        return await spill_controller_events(payload)  # retry every chunk/finalize
    monkeypatch.setattr(wf, "execute_activity", invoke)
    monkeypatch.setattr(wf, "continue_as_new", lambda *, args: carried.update(args[0]))
    await controller._continue_as_new({})
    assert carried["pending_events"] == []
    first = await read_controller_events({"controller_id": "controller", "request_id": "large-read"})
    second = await read_controller_events({"controller_id": "controller", "request_id": "after-read"})
    assert first["events"] == [item]
    assert second["events"] == [["mail", {"id": "after"}]]
    assert second["more"] is False

@pytest.fixture
def wf(monkeypatch):
    from services.temporal import workflow_control_workflow as module
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="controller", run_id="run"))
    monkeypatch.setattr(module.workflow, "wait_condition", wait)
    return module.workflow

async def wait(predicate):
    assert predicate(), "Controller blocked despite an eligible durable event"

async def test_older_spill_page_precedes_signals_arriving_after_carried_prefix(wf, monkeypatch):
    controller = WorkflowControlWorkflow()
    emitted = []
    spec = {"trigger_node_id": "trigger", "workflow_type": "TriggerListenerWorkflow", "listener_args": {"node_type": "chatTrigger"}}
    async def read(name, payload, **kwargs):
        assert name == "controller.queue.read"
        return {"events": [["mail", {"id": "old-spilled"}]], "more": False}
    async def spawn(event, spec):
        emitted.append(event["id"])
        if event["id"] == "old-carried":
            controller._events.append(("mail", {"id": "new-signal"}))
        if len(emitted) == 3:
            controller._closed = True
    monkeypatch.setattr(wf, "execute_activity", read)
    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    monkeypatch.setattr(controller, "_history_pressure", lambda: False)
    monkeypatch.setattr(controller, "_upsert_event_types_attribute", lambda: None)
    await controller.run({"state": "running", "overflow": True, "durable_prefix": 1,
        "pending_events": [["mail", {"id": "old-carried"}]], "triggers": {"mail": spec}})
    assert emitted == ["old-carried", "old-spilled", "new-signal"]

async def test_paused_review_is_found_behind_spilled_ordinary_events(wf, monkeypatch):
    controller = WorkflowControlWorkflow()
    ordinary = {"trigger_node_id": "mail", "workflow_type": "TriggerListenerWorkflow", "listener_args": {"node_type": "gmailTrigger"}}
    review = {"trigger_node_id": "task", "workflow_type": "TriggerListenerWorkflow", "listener_args": {"node_type": "taskTrigger"}}
    async def read(name, payload, **kwargs):
        assert payload["review_listener_ids"] == ["review"]
        return {"events": [["review", {"id": "submitted"}]], "more": True}
    async def spawn(event, spec):
        assert event["id"] == "submitted"
        controller._closed = True
    monkeypatch.setattr(wf, "execute_activity", read)
    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    monkeypatch.setattr(controller, "_history_pressure", lambda: False)
    monkeypatch.setattr(controller, "_upsert_event_types_attribute", lambda: None)
    await controller.run({"state": "paused", "drain_tasks": True, "overflow": True, "durable_prefix": 1,
        "pending_events": [["mail", {"id": "waiting-mail"}]], "triggers": {"mail": ordinary, "review": review}})
    assert controller._events == [("mail", {"id": "waiting-mail"})]

async def test_failed_accepted_spawn_requeues_the_same_event(wf, monkeypatch):
    # Without controller-spawn-backoff-v1 (every history before it, and every
    # run while it is dormant) a failed event goes back to the head and is
    # tried again 5 s later, ahead of everything behind it.
    monkeypatch.setattr(wf, "patched", lambda patch_id: patch_id != "controller-spawn-backoff-v1")
    controller = WorkflowControlWorkflow()
    calls, sleeps = [], []
    async def sleep(delay):
        sleeps.append(delay)
    async def spawn(event, _spec):
        calls.append(event["id"])
        if len(calls) == 1:
            raise ApplicationError("temporary dispatch failure")
        if event["id"] == "next":
            controller._closed = True
    monkeypatch.setattr(wf, "sleep", sleep)
    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    monkeypatch.setattr(controller, "_history_pressure", lambda: False)
    monkeypatch.setattr(controller, "_upsert_event_types_attribute", lambda: None)
    spec = {"trigger_node_id": "trigger", "workflow_type": "TriggerListenerWorkflow", "listener_args": {"node_type": "chatTrigger"}}
    await controller.run({"pending_events": [["mail", {"id": "retry"}], ["mail", {"id": "next"}]], "triggers": {"mail": spec}})
    assert calls == ["retry", "retry", "next"]
    assert sleeps == [timedelta(seconds=5)]


class _Clock:
    """Workflow time for direct runs: a wait with nothing ready and a timeout
    moves the clock to the timeout and raises, as Temporal's timer would."""

    def __init__(self):
        self.now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        self.timeouts = []

    async def wait(self, predicate, timeout=None):
        if predicate():
            return
        assert timeout is not None, "Controller blocked with nothing due and no retry time"
        self.timeouts.append(timeout)
        self.now += timeout
        raise asyncio.TimeoutError


_SPEC = {"trigger_node_id": "trigger", "workflow_type": "TriggerListenerWorkflow", "listener_args": {"node_type": "chatTrigger"}}


async def test_failed_spawn_waits_behind_the_queue_then_is_dead_lettered(wf, monkeypatch):
    # controller-spawn-backoff-v1 (RFC-0006 §11.6 rec 2, §7.4 A1): a spawn that
    # keeps failing is tried again after 5 s, 30 s, 2 min and 10 min while
    # later events start, then goes to the dead rows and the queue moves on.
    clock = _Clock()
    monkeypatch.setattr(wf, "now", lambda: clock.now)
    monkeypatch.setattr(wf, "wait_condition", clock.wait)
    controller = WorkflowControlWorkflow()
    started = clock.now
    calls, spills = [], []

    async def spawn(event, _spec):
        calls.append((event["id"], event.get("_spawn_attempts"), clock.now - started))
        if event["id"] == "poison":
            raise ApplicationError("cannot start")
        if event["id"] == "after":
            controller._closed = True

    async def spill(name, payload, **_kwargs):
        assert name == "controller.queue.spill"
        spills.append(payload)
        controller._events.append(("mail", {"id": "after"}))

    monkeypatch.setattr(wf, "execute_activity", spill)
    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    monkeypatch.setattr(controller, "_history_pressure", lambda: False)
    monkeypatch.setattr(controller, "_upsert_event_types_attribute", lambda: None)
    await controller.run({"execution_control_version": 1, "generation": 7, "controller_workflow_id": "controller",
        "pending_events": [["mail", {"id": "poison"}], ["mail", {"id": "first"}]], "triggers": {"mail": _SPEC}})
    assert calls == [
        ("poison", None, timedelta(0)), ("first", None, timedelta(0)),
        ("poison", 1, timedelta(seconds=5)), ("poison", 2, timedelta(seconds=35)),
        ("poison", 3, timedelta(seconds=155)), ("poison", 4, timedelta(seconds=755)),
        ("after", None, timedelta(seconds=755)),
    ]
    assert clock.timeouts == [timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10)]
    assert spills == [{"controller_id": "controller:dead", "prepend": False, "events": [
        ["mail", {"id": "poison", "_spawn_attempts": 5, "_dead_reason": "ApplicationError"}]]}]
    assert controller._events == []
    # A dead-lettered event is finished the way a started one is.
    assert not controller._pending_event_ids and "mail:poison" in controller._seen_event_ids


async def test_a_failed_spawn_carried_in_the_prefix_does_not_hold_back_spilled_pages(wf, monkeypatch):
    # The carried prefix and the first spilled page hold only failed spawns
    # waiting for their retry times: the next page is read at once, and its
    # event starts before them.
    clock = _Clock()
    monkeypatch.setattr(wf, "now", lambda: clock.now)
    monkeypatch.setattr(wf, "wait_condition", clock.wait)
    controller = WorkflowControlWorkflow()
    started = clock.now
    calls, reads = [], []
    later = {"id": "later", "_spawn_attempts": 4, "_spawn_retry_at": (started + timedelta(minutes=10)).isoformat()}
    pages = [{"events": [["mail", later]], "more": True}, {"events": [["mail", {"id": "spilled"}]], "more": False}]

    async def read(name, _payload, **_kwargs):
        assert name == "controller.queue.read"
        reads.append(clock.now - started)
        return pages.pop(0)

    async def spawn(event, _spec):
        calls.append((event["id"], clock.now - started))
        if event["id"] == "later":
            controller._closed = True

    monkeypatch.setattr(wf, "execute_activity", read)
    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    monkeypatch.setattr(controller, "_history_pressure", lambda: False)
    monkeypatch.setattr(controller, "_upsert_event_types_attribute", lambda: None)
    waiting = {"id": "waiting", "_spawn_attempts": 1, "_spawn_retry_at": (started + timedelta(seconds=5)).isoformat()}
    await controller.run({"state": "running", "overflow": True, "durable_prefix": 1,
        "pending_events": [["mail", waiting]], "triggers": {"mail": _SPEC}})
    assert reads == [timedelta(0), timedelta(0)]
    assert calls == [("spilled", timedelta(0)), ("waiting", timedelta(seconds=5)), ("later", timedelta(minutes=10))]


async def test_reading_past_waiting_spawns_stops_for_a_rollover(wf, monkeypatch):
    # With the history asking for a rollover, the run carries the waiting
    # event, count and retry time included, instead of reading on; the next
    # run reads the page and still waits for that retry time.
    clock = _Clock()
    monkeypatch.setattr(wf, "now", lambda: clock.now)
    monkeypatch.setattr(wf, "wait_condition", clock.wait)
    started = clock.now
    calls, carried = [], {}
    waiting = {"id": "waiting", "_spawn_attempts": 4, "_spawn_retry_at": (started + timedelta(minutes=10)).isoformat()}
    pages = [{"events": [["mail", waiting]], "more": True}, {"events": [["mail", {"id": "spilled"}]], "more": False}]

    async def read(name, _payload, **_kwargs):
        assert name == "controller.queue.read"
        return pages.pop(0)

    async def spawn(event, _spec):
        calls.append((event["id"], clock.now - started))
        if event["id"] == "waiting":
            second._closed = True

    monkeypatch.setattr(wf, "execute_activity", read)
    monkeypatch.setattr(wf, "continue_as_new", lambda *, args: carried.update(args[0]))
    first, second = WorkflowControlWorkflow(), WorkflowControlWorkflow()
    for controller, pressure in ((first, True), (second, False)):
        monkeypatch.setattr(controller, "_spawn_push_run", spawn)
        monkeypatch.setattr(controller, "_history_pressure", lambda pressure=pressure: pressure)
        monkeypatch.setattr(controller, "_upsert_event_types_attribute", lambda: None)
    await first.run({"state": "running", "overflow": True, "durable_prefix": 0, "triggers": {"mail": _SPEC}})
    assert len(pages) == 1 and calls == []
    assert carried["pending_events"] == [["mail", waiting]] and carried["overflow"] is True
    await second.run(carried)
    assert calls == [("spilled", timedelta(0)), ("waiting", timedelta(minutes=10))]


async def test_the_child_run_never_sees_the_retry_bookkeeping(monkeypatch):
    from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow

    seen = []

    async def spawn_child_run(_self, event, _listener_args, **_kwargs):
        seen.append(event)

    monkeypatch.setattr(TriggerListenerWorkflow, "_spawn_child_run", spawn_child_run)
    await WorkflowControlWorkflow()._spawn_push_run(
        {"id": "retry", "type": "mail", "_spawn_attempts": 2, "_spawn_retry_at": "2026-11-04T00:00:35+00:00"}, _SPEC)
    assert seen == [{"id": "retry", "type": "mail"}]


async def test_dead_rows_are_never_read_back_and_reset_clears_only_them(real_database, monkeypatch):
    database = real_database
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: database))
    await spill_controller_events({"controller_id": "controller", "events": [["mail", {"id": "queued"}]]})
    await spill_controller_events({"controller_id": "controller:dead", "events": [["mail", {"id": "poison", "_dead_reason": "ActivityError"}]]})
    # A large dead letter whose spill stopped after its first chunk.
    await spill_controller_events({"controller_id": "controller:dead", "chunk_id": "large", "index": 0, "chunk": "["})
    await spill_controller_events({"controller_id": "other:dead", "events": [["mail", {"id": "poison"}]]})
    page = await read_controller_events({"controller_id": "controller", "request_id": "after-dead-letter"})
    assert page == {"events": [["mail", {"id": "queued"}]], "more": False}
    await spill_controller_events({"controller_id": "controller", "events": [["mail", {"id": "still-queued"}]]})
    assert await clear_dead_letters(database, "controller") == 2
    assert await clear_dead_letters(database, "controller") == 0
    async with database.get_session() as session:
        left = (await session.execute(select(WorkflowQueuedEvent.controller_id))).scalars().all()
    assert sorted(left) == ["controller", "other:dead"]
