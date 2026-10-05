"""Bounded controller queue rollover, FIFO handoff and paused review draining."""
from __future__ import annotations
import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from services.temporal.controller_queue import event_batches, spill_controller_events, read_controller_events
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow
from tests.services.employees.test_jobs import database as database

async def test_durable_spill_is_idempotent_and_review_scan_reaches_buried_task(database, monkeypatch):
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


async def test_large_accepted_event_survives_chunked_rollover_once(database, wf, monkeypatch):
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
    controller = WorkflowControlWorkflow()
    calls = []
    async def sleep(_delay):
        return None
    async def spawn(event, _spec):
        calls.append(event["id"])
        if len(calls) == 1:
            raise RuntimeError("temporary dispatch failure")
        controller._closed = True
    monkeypatch.setattr(wf, "sleep", sleep)
    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    monkeypatch.setattr(controller, "_history_pressure", lambda: False)
    monkeypatch.setattr(controller, "_upsert_event_types_attribute", lambda: None)
    spec = {"trigger_node_id": "trigger", "workflow_type": "TriggerListenerWorkflow", "listener_args": {"node_type": "chatTrigger"}}
    await controller.run({"pending_events": [["mail", {"id": "retry"}]], "triggers": {"mail": spec}})
    assert calls == ["retry", "retry"]
