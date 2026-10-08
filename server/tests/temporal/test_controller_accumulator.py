"""Versioned accumulator durability at dispatch and rollover boundaries."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from temporalio.converter import DefaultPayloadConverter
from temporalio.exceptions import WorkflowAlreadyStartedError

from services.temporal import workflow_control_workflow as module
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


def control_data(**extra):
    return {
        "execution_control_version": 1,
        "generation": 7,
        "controller_workflow_id": "controller",
        **extra,
    }


def push_spec():
    return {
        "trigger_node_id": "trigger",
        "workflow_type": "TriggerListenerWorkflow",
        "event_types": ["mail"],
        "listener_args": {"node_type": "gmailTrigger"},
    }


@pytest.fixture
def wf(monkeypatch):
    async def wait(predicate):
        assert predicate(), "An accumulator fence unexpectedly blocked"

    async def sleep(_delay):
        pass

    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="controller", run_id="run"))
    monkeypatch.setattr(module.workflow, "wait_condition", wait)
    monkeypatch.setattr(module.workflow, "sleep", sleep)
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(module.workflow, "payload_converter", lambda: DefaultPayloadConverter())
    monkeypatch.setattr(WorkflowControlWorkflow, "_upsert_event_types_attribute", lambda _: None)
    monkeypatch.setattr(WorkflowControlWorkflow, "_history_pressure", lambda _: False)
    return module.workflow


async def test_signal_during_final_spill_is_carried_or_spilled_in_fifo_order(wf, monkeypatch):
    monkeypatch.setattr(module, "_MAX_CARRIED_EVENTS", 2)
    controller = WorkflowControlWorkflow(control_data(state="paused", triggers={"mail": push_spec()}))
    for index in range(5):
        await controller.on_event({"id": str(index), "type": "mail"})
    spilled, carried = [], {}

    async def spill(name, payload, **_kwargs):
        assert name == "controller.queue.spill"
        items = payload["events"]
        if payload["prepend"]:
            spilled[0:0] = items
        else:
            spilled.extend(items)
        if [item[1]["id"] for item in items] == ["2", "3"]:
            await controller.on_event({"id": "late", "type": "mail"})
            await controller.on_event({"id": "late", "type": "mail"})

    monkeypatch.setattr(wf, "execute_activity", spill)
    monkeypatch.setattr(wf, "continue_as_new", lambda *, args: carried.update(args[0]))
    await controller._continue_as_new(control_data())
    assert [item[1]["id"] for item in carried["pending_events"] + spilled] == ["0", "1", "2", "3", "4", "late"]
    assert "mail:late" in carried["seen_event_ids"]
    assert carried["state"] == "paused"


async def test_signal_while_handlers_finish_is_rechecked_before_rollover(wf, monkeypatch):
    controller = WorkflowControlWorkflow(control_data(state="paused", triggers={"mail": push_spec()}))
    await controller.on_event({"id": "first", "type": "mail"})
    waits, spilled, carried = 0, [], {}

    async def wait(predicate):
        nonlocal waits
        waits += 1
        if waits == 3:
            await controller.on_event({"id": "last-handler", "type": "mail"})
        assert predicate()

    async def spill(_name, payload, **_kwargs):
        spilled.extend(payload["events"])

    monkeypatch.setattr(wf, "wait_condition", wait)
    monkeypatch.setattr(wf, "execute_activity", spill)
    monkeypatch.setattr(wf, "continue_as_new", lambda *, args: carried.update(args[0]))
    await controller._continue_as_new(control_data())
    assert [item[1]["id"] for item in carried["pending_events"] + spilled] == ["first", "last-handler"]


async def test_pending_retry_survives_recent_id_cache_eviction(wf):
    controller = WorkflowControlWorkflow(control_data(state="paused", triggers={"mail": push_spec()}))
    for index in range(module._MAX_CARRIED_SEEN_IDS + 10):
        await controller.on_event({"id": str(index), "type": "mail"})
    assert "mail:0" not in controller._seen_event_ids
    await controller.on_event({"id": "0", "type": "mail"})
    assert sum(event["id"] == "0" for _, event in controller._events) == 1
    assert len(controller._seen_event_ids) == module._MAX_CARRIED_SEEN_IDS


async def test_pending_key_is_rebuilt_from_continuation(wf):
    controller = WorkflowControlWorkflow(control_data(
        state="paused", triggers={"mail": push_spec()},
        pending_events=[["mail", {"id": "old", "type": "mail"}]],
        seen_event_ids=[f"mail:recent-{index}" for index in range(module._MAX_CARRIED_SEEN_IDS)],
    ))
    await controller.on_event({"id": "old", "type": "mail"})
    assert len(controller._events) == 1


async def test_dispatching_key_survives_arrival_cache_eviction(wf, monkeypatch):
    controller = WorkflowControlWorkflow()
    seen_queue = []

    async def spawn(event, _spec):
        assert event["id"] == "active"
        for index in range(module._MAX_CARRIED_SEEN_IDS + 1):
            await controller.on_event({"id": str(index), "type": "mail"})
        assert "mail:active" not in controller._seen_event_ids
        await controller.on_event({"id": "active", "type": "mail"})
        seen_queue.extend(item[1]["id"] for item in controller._events)
        controller._closed = True

    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    await controller.run(control_data(
        pending_events=[["mail", {"id": "active", "type": "mail"}]],
        triggers={"mail": push_spec()},
    ))
    assert "active" not in seen_queue
    assert "mail:active" not in controller._pending_event_ids
    assert "mail:active" in controller._seen_event_ids


async def test_page_restores_pending_keys_and_retains_older_fifo_position(wf, monkeypatch):
    controller = WorkflowControlWorkflow()
    dispatched = []

    async def read(_name, _payload, **_kwargs):
        return {"events": [["mail", {"id": "durable", "type": "mail"}]], "more": False}

    async def spawn(event, _spec):
        dispatched.append(event["id"])
        if event["id"] == "carried":
            await controller.on_event({"id": "durable", "type": "mail"})
            await controller.on_event({"id": "new", "type": "mail"})
        if event["id"] == "durable":
            await controller.on_event({"id": "durable", "type": "mail"})
        if event["id"] == "new":
            controller._closed = True

    monkeypatch.setattr(wf, "execute_activity", read)
    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    await controller.run(control_data(
        overflow=True, durable_prefix=1,
        pending_events=[["mail", {"id": "carried", "type": "mail"}]],
        triggers={"mail": push_spec()},
    ))
    assert dispatched == ["carried", "durable", "new"]
    assert not controller._events


async def test_duplicate_child_start_does_not_block_next_event(wf, monkeypatch):
    controller = WorkflowControlWorkflow()
    calls = []

    async def spawn(event, _spec):
        calls.append(event["id"])
        if event["id"] == "duplicate":
            raise WorkflowAlreadyStartedError("duplicate-child", "MachinaWorkflow")
        controller._closed = True

    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    await controller.run(control_data(
        pending_events=[["mail", {"id": value, "type": "mail"}] for value in ("duplicate", "next")],
        triggers={"mail": push_spec()},
    ))
    assert calls == ["duplicate", "next"]
    assert not controller._pending_event_ids


async def test_transient_failure_keeps_same_event_pending_until_success(wf, monkeypatch):
    controller = WorkflowControlWorkflow()
    calls = []

    async def spawn(event, _spec):
        calls.append(event["id"])
        assert "mail:retry" in controller._pending_event_ids
        if len(calls) == 1:
            await controller.on_event(event)
            raise RuntimeError("temporary dispatch failure")
        controller._closed = True

    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    await controller.run(control_data(
        pending_events=[["mail", {"id": "retry", "type": "mail"}]],
        triggers={"mail": push_spec()},
    ))
    assert calls == ["retry", "retry"]
    assert not controller._events
    assert not controller._pending_event_ids


async def test_reset_during_spill_does_not_resurrect_controller(wf, monkeypatch):
    monkeypatch.setattr(module, "_MAX_CARRIED_EVENTS", 1)
    controller = WorkflowControlWorkflow(control_data(state="paused", triggers={"mail": push_spec()}))
    for index in range(2):
        await controller.on_event({"id": str(index), "type": "mail"})
    continued = []

    async def spill(_name, _payload, **_kwargs):
        await controller.reset()

    monkeypatch.setattr(wf, "execute_activity", spill)
    monkeypatch.setattr(wf, "continue_as_new", lambda *, args: continued.append(args[0]))
    await controller._continue_as_new(control_data())
    assert not continued
    assert controller._closed is True
    assert controller._state == "resetting"


async def test_patch_off_preserves_prior_v1_spill_commands(wf, monkeypatch):
    monkeypatch.setattr(module, "_MAX_CARRIED_EVENTS", 1)
    monkeypatch.setattr(wf, "patched", lambda name: name != "controller-event-accumulator-v1")
    controller = WorkflowControlWorkflow(control_data(state="paused", triggers={"mail": push_spec()}))
    for index in range(2):
        await controller.on_event({"id": str(index), "type": "mail"})
    commands, carried = [], {}

    async def spill(name, payload, **_kwargs):
        commands.append((name, payload["events"]))
        await controller.on_event({"id": "late", "type": "mail"})

    monkeypatch.setattr(wf, "execute_activity", spill)
    monkeypatch.setattr(wf, "continue_as_new", lambda *, args: carried.update(args[0]))
    await controller._continue_as_new(control_data())
    # Existing pre-marker histories scheduled one prefix and one tail spill.
    assert [item[1]["id"] for _, batch in commands for item in batch] == ["1", "late"]
    assert carried["pending_events"][0][1]["id"] == "0"
    assert controller._event_accumulator_v1 is False
    assert not controller._pending_event_ids


async def test_patch_off_discards_provisional_index_without_changing_recent_ids(wf, monkeypatch):
    patches, dispatched = [], []

    def patched(name):
        patches.append(name)
        return name != "controller-event-accumulator-v1"

    monkeypatch.setattr(wf, "patched", patched)
    data = control_data(
        pending_events=[["mail", {"id": value, "type": "mail"}] for value in ("old", "next")],
        seen_event_ids=["mail:old", "mail:prior", "mail:next"],
        triggers={"mail": push_spec()},
    )
    controller = WorkflowControlWorkflow(data)
    assert not patches, "Initializing a carried controller must issue no patch commands"
    assert controller._pending_event_ids == {"mail:old", "mail:next"}

    async def spawn(event, _spec):
        assert not controller._pending_event_ids
        dispatched.append(event["id"])
        if event["id"] == "next":
            controller._closed = True

    monkeypatch.setattr(controller, "_spawn_push_run", spawn)
    await controller.run(data)
    assert dispatched == ["old", "next"]
    assert not controller._pending_event_ids
    assert list(controller._seen_event_ids) == ["mail:old", "mail:prior", "mail:next"]


async def test_legacy_controller_does_not_record_accumulator_marker(wf, monkeypatch):
    patches = []
    monkeypatch.setattr(wf, "patched", lambda name: patches.append(name) or True)
    controller = WorkflowControlWorkflow()
    controller._triggers["mail"] = push_spec()
    await controller.on_event({"id": "legacy", "type": "mail"})
    assert "controller-event-accumulator-v1" not in patches
    assert not controller._pending_event_ids
