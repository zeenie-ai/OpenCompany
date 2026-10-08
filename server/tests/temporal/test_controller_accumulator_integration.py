"""Native controller accumulation, spill handoff, restart, and patch replay."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType, IndexedValueType
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.execution_control import ExecutionControl
from services.temporal.execution_control_activities import ExecutionControlActivities
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


_PATCH = "controller-event-accumulator-v1"
_spill_started: asyncio.Event
_spill_release: asyncio.Event
_spill_blocked: bool
_spilled: list[list]
_spill_keys: set[tuple[str, str]]
_read_receipts: dict[str, dict]
_recorded: list[str]


@activity.defn(name="controller.queue.spill")
async def _spill(payload: dict) -> dict:
    global _spill_blocked
    # The first tail spill is the last Activity in the original rollover
    # snapshot. Signals delivered while it is in flight must be carried or
    # spilled too, rather than disappearing when that snapshot continues.
    if not _spill_blocked:
        _spill_blocked = True
        _spill_started.set()
        await _spill_release.wait()
    incoming = []
    for listener_id, event in payload["events"]:
        key = (listener_id, event["id"])
        if key not in _spill_keys:
            _spill_keys.add(key)
            incoming.append([listener_id, event])
    if payload.get("prepend"):
        _spilled[0:0] = incoming
    else:
        _spilled.extend(incoming)
    return {"spilled": len(incoming)}


@activity.defn(name="controller.queue.read")
async def _read(payload: dict) -> dict:
    # Model the production Activity's mutation receipt: an Activity retry
    # receives the same page, even after its rows have been removed.
    request_id = payload["request_id"]
    if request_id not in _read_receipts:
        page = _spilled[:100]
        del _spilled[:100]
        _read_receipts[request_id] = {"events": page, "more": bool(_spilled)}
    return _read_receipts[request_id]


@activity.defn(name="broadcast_trigger_status_activity")
async def _broadcast(_payload: dict) -> None:
    return None


@activity.defn(name="evaluate_trigger_filter_activity")
async def _filter(_payload: dict) -> bool:
    return True


@activity.defn(name="accumulator.record")
async def _record(payload: dict) -> str:
    event_id = payload["event_id"]
    _recorded.append(event_id)
    return event_id


@workflow.defn(name="MachinaWorkflow", sandboxed=False)
class AccumulatorGraphProbe:
    """A short real child, with the same independent-root admission contract."""

    @workflow.init
    def __init__(self, payload: dict | None = None):
        self.control = ExecutionControl()
        if payload is not None:
            self.control.bind(payload)

    @workflow.update
    async def set_control_state(self, payload: dict) -> dict:
        return await self.control.set_control_state(payload)

    @workflow.update
    async def wait_for_checkpoint(self, payload: dict | None = None) -> dict:
        return await self.control.wait_for_checkpoint(payload)

    @workflow.query
    def execution_control_status(self) -> dict:
        return self.control.status()

    @workflow.run
    async def run(self, payload: dict | None = None) -> str:
        payload = payload or {}
        self.control.bind(payload)
        await self.control.register_root()
        event_id = payload.get("probe_event")
        if event_id is None:
            trigger = next(node for node in payload["nodes"] if node.get("_pre_executed"))
            event_id = trigger["_trigger_output"]["_event_envelope"]["id"]
        async with self.control.action():
            result = await workflow.execute_activity("accumulator.record", {"event_id": event_id},
                start_to_close_timeout=timedelta(seconds=30))
        await self.control.unregister_root()
        await workflow.wait_condition(workflow.all_handlers_finished)
        return result


def _trigger(slug: str) -> dict:
    return {"listener_id": "push", "trigger_node_id": "trigger", "event_type": "probe.event",
        "workflow_type": "TriggerListenerWorkflow", "listener_args": {
            "trigger_node_id": "trigger", "trigger_label": "trigger", "node_type": "webhookTrigger",
            "workflow_slug": slug, "workflow_id": None, "graphVersion": 2, "generation": 7,
            "data_scope_id": "generation-7", "filter_params": {"test": True},
            "nodes": [{"id": "trigger", "type": "webhookTrigger", "data": {}}], "edges": [],
        }}


def _event(event_id: str) -> dict:
    return {"id": event_id, "type": "probe.event", "data": {"value": event_id}}


async def _wait_status(handle, predicate):
    for _ in range(300):
        state = await handle.query("status")
        if predicate(state):
            return state
        await asyncio.sleep(0.02)
    raise AssertionError(f"Controller did not reach expected state: {state}")


async def _wait_recorded(event_ids):
    for _ in range(500):
        if all(event_id in _recorded for event_id in event_ids):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"Expected graph runs {event_ids}; recorded {_recorded}")


async def _chain_histories(client, workflow_id: str, first_run_id: str):
    histories = []
    run_id = first_run_id
    while True:
        history = await client.get_workflow_handle(workflow_id, run_id=run_id).fetch_history()
        histories.append(history)
        continued = next((event.workflow_execution_continued_as_new_event_attributes
            for event in history.events
            if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW), None)
        if continued is None:
            return histories
        run_id = continued.new_execution_run_id


async def _register_search_attributes(client):
    await client.operator_service.add_search_attributes(AddSearchAttributesRequest(
        namespace=client.namespace,
        search_attributes={"ControlEventTypes": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST},
    ))


def _has_accumulator_patch(history) -> bool:
    return any(_PATCH.encode() in payload.data
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_MARKER_RECORDED
        for payloads in event.marker_recorded_event_attributes.details.values()
        for payload in payloads.payloads)


async def _run_accumulator_gate():
    global _spill_started, _spill_release, _spill_blocked, _spilled, _spill_keys, _read_receipts, _recorded
    _spill_started, _spill_release = asyncio.Event(), asyncio.Event()
    _spill_blocked, _spilled, _spill_keys, _read_receipts, _recorded = False, [], set(), {}, []
    from services.temporal import workflow_control_workflow as controller_module

    original_pressure = WorkflowControlWorkflow._history_pressure
    original_carry_limit = controller_module._MAX_CARRIED_EVENTS
    original_seen_limit = controller_module._MAX_CARRIED_SEEN_IDS
    # Small, deterministic pressure avoids thousands of Signals. The same
    # production method override is used for execution and history replay.
    WorkflowControlWorkflow._history_pressure = lambda self: len(self._events) >= 3 and not self._overflow
    controller_module._MAX_CARRIED_EVENTS = 2
    controller_module._MAX_CARRIED_SEEN_IDS = 2
    definitions = [WorkflowControlWorkflow, AccumulatorGraphProbe]
    try:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            client = environment.client
            await _register_search_attributes(client)
            queue = "controller-accumulator-" + uuid4().hex
            slug = "accumulator-" + uuid4().hex
            controller_id = "controller-" + uuid4().hex
            activities = [_spill, _read, _broadcast, _filter, _record,
                *ExecutionControlActivities(client).activities()]
            async with Worker(client, task_queue=queue, workflows=definitions, activities=activities,
                    max_cached_workflows=0, identity="accumulator-before-restart"):
                controller = await client.start_workflow("WorkflowControlWorkflow", {
                    "execution_control_version": 1, "generation": 7, "state": "paused",
                    "revision": 1, "participant_state": "paused", "participant_revision": 1,
                    "controller_workflow_id": controller_id,
                }, id=controller_id, task_queue=queue)
                await controller.signal("register_trigger", _trigger(slug))
                await _wait_status(controller, lambda state: "push" in state["triggers"])
                for event_id in ("A", "A", "B", "B"):
                    await controller.signal("on_event", _event(event_id))
                await _wait_status(controller, lambda state: state["queued_events"] == 2)
                assert _recorded == []
                await controller.signal("on_event", _event("C"))
                await asyncio.wait_for(_spill_started.wait(), timeout=15)
                await controller.signal("on_event", _event("D"))
                await controller.signal("on_event", _event("D"))
                await controller.signal("on_event", _event("A"))
                # An acknowledged Update after the Signals ensures their
                # handlers ran before completing the last original spill.
                status = await controller.execute_update("set_control_state", {
                    "state": "paused", "revision": 1, "generation": 7,
                })
                assert status["queued_events"] == 4
                _spill_release.set()
                for _ in range(300):
                    description = await controller.describe()
                    if description.run_id != controller.first_execution_run_id:
                        break
                    await asyncio.sleep(0.02)
                assert description.run_id != controller.first_execution_run_id
                stopped = await controller.query("status")
                assert stopped["state"] == "paused"
                assert stopped["queued_events"] == 2
                assert [item[1]["id"] for item in _spilled] == ["C", "D"]
                assert _recorded == []

            async with Worker(client, task_queue=queue, workflows=definitions, activities=activities,
                    max_cached_workflows=0, identity="accumulator-after-restart"):
                stopped = await controller.execute_update("set_control_state", {
                    "state": "paused", "revision": 1, "generation": 7,
                })
                assert stopped["state"] == "paused"
                # Dedup applies to events held in memory and the spillway.
                for event_id in ("A", "C", "D"):
                    await controller.signal("on_event", _event(event_id))
                await controller.execute_update("set_control_state", {
                    "state": "running", "revision": 2, "generation": 7, "producers_held": False,
                })
                await _wait_recorded("ABCD")
                with environment.auto_time_skipping_disabled():
                    results = await asyncio.wait_for(asyncio.gather(*(client.get_workflow_handle(
                        f"{slug}-trigger-{event_id}").result() for event_id in ("A", "B", "C", "D"))), timeout=20)
                assert results == ["A", "B", "C", "D"]
                assert Counter(_recorded) == Counter({"A": 1, "B": 1, "C": 1, "D": 1})
                assert _spilled == []

                # A stable event-derived child ID may already have completed
                # even after the controller's dedup window has expired. Native
                # duplicate-start rejection must not poison the queue head.
                existing = await client.start_workflow("MachinaWorkflow", {"probe_event": "E"},
                    id=f"{slug}-trigger-E", task_queue=queue)
                assert await existing.result() == "E"
                await controller.signal("on_event", _event("E"))
                await controller.signal("on_event", _event("F"))
                await _wait_recorded("F")
                with environment.auto_time_skipping_disabled():
                    assert await asyncio.wait_for(client.get_workflow_handle(f"{slug}-trigger-F").result(), timeout=20) == "F"
                await _wait_status(controller, lambda state: state["queued_events"] == 0 and not state["live_roots"])
                assert Counter(_recorded) == Counter({event_id: 1 for event_id in "ABCDEF"})
                await controller.signal("reset")
                await controller.result()
                histories = await _chain_histories(client, controller_id, controller.first_execution_run_id)
                histories.extend([await client.get_workflow_handle(f"{slug}-trigger-{event_id}").fetch_history()
                    for event_id in "ABCDEF"])

            controller_histories = [history for history in histories if history.workflow_id == controller_id]
            assert len(controller_histories) == 2
            assert all(_has_accumulator_patch(history) for history in controller_histories)
            child_starts = [event.start_child_workflow_execution_initiated_event_attributes.workflow_id
                for history in controller_histories for event in history.events
                if event.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED]
            assert child_starts == [f"{slug}-trigger-{event_id}" for event_id in "ABCDEF"]
            for history in histories:
                replay = await Replayer(workflows=definitions).replay_workflow(history)
                assert replay.replay_failure is None
    finally:
        WorkflowControlWorkflow._history_pressure = original_pressure
        controller_module._MAX_CARRIED_EVENTS = original_carry_limit
        controller_module._MAX_CARRIED_SEEN_IDS = original_seen_limit


async def _run_pre_marker_replay():
    global _recorded
    _recorded = []
    original_patched = workflow.patched
    # Record the actual prior command path with the new marker absent, then
    # replay with the normal SDK patched() implementation. No edited history
    # is substituted for a genuine pre-marker execution.
    workflow.patched = lambda patch_id: False if patch_id == _PATCH else original_patched(patch_id)
    definitions = [WorkflowControlWorkflow, AccumulatorGraphProbe]
    histories = []
    try:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            client = environment.client
            await _register_search_attributes(client)
            queue = "controller-accumulator-legacy-" + uuid4().hex
            activities = [_broadcast, _filter, _record, *ExecutionControlActivities(client).activities()]
            async with Worker(client, task_queue=queue, workflows=definitions, activities=activities,
                    max_cached_workflows=0):
                for version in (0, 1):
                    slug = "pre-marker-" + uuid4().hex
                    controller_id = "controller-" + uuid4().hex
                    controller = await client.start_workflow("WorkflowControlWorkflow", {
                        "execution_control_version": version, "generation": 7,
                        "controller_workflow_id": controller_id,
                    }, id=controller_id, task_queue=queue)
                    await controller.signal("register_trigger", _trigger(slug))
                    await _wait_status(controller, lambda state: "push" in state["triggers"])
                    await controller.signal("on_event", _event(str(version)))
                    await _wait_recorded([str(version)])
                    child = client.get_workflow_handle(f"{slug}-trigger-{version}")
                    with environment.auto_time_skipping_disabled():
                        assert await asyncio.wait_for(child.result(), timeout=15) == str(version)
                    await controller.signal("reset")
                    await controller.result()
                    histories.extend([await controller.fetch_history(), await child.fetch_history()])
            workflow.patched = original_patched
            assert not any(_has_accumulator_patch(history) for history in histories)
            for history in histories:
                replay = await Replayer(workflows=definitions).replay_workflow(history)
                assert replay.replay_failure is None
    finally:
        workflow.patched = original_patched


def test_controller_accumulation_spill_signal_restart_and_replay():
    completed = subprocess.run([sys.executable, "-m", "tests.temporal.test_controller_accumulator_integration"],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=150, check=False)
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-12000:]


if __name__ == "__main__":
    asyncio.run(_run_accumulator_gate())
    asyncio.run(_run_pre_marker_replay())
