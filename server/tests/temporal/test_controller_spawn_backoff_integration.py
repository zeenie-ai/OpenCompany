"""A controller spawn that always fails, on the real SDK (RFC-0006 §11.6 rec 2).

Dormant (the production default, ``patches.activate_patch``): today's
head-of-line retry every 5 s, and no ``controller-spawn-backoff-v1`` marker in
history. Active: the event is retried behind the rest of the queue after 5 s,
30 s, 2 min and 10 min, goes to the dead rows on the fifth failure, and the
queue moves on. Each history replays with the same code.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType, IndexedValueType, WorkflowTaskFailedCause
from temporalio.api.operatorservice.v1 import AddSearchAttributesRequest
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

# The controller imports this module when it writes a dead letter. Production
# workers have loaded it, with its database models, by then (they register
# its activities); a first import inside a workflow task can outlast the SDK's
# 2 s deadlock check, whose interrupt leaves sqlalchemy half imported.
import services.temporal.controller_queue  # noqa: F401
from services.temporal.patches import activate_patch
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


_PATCH = "controller-spawn-backoff-v1"
_filtered: list[tuple[str, object]]
_started: list[dict]
_spilled: list[dict]


@activity.defn(name="evaluate_trigger_filter_activity")
async def _filter(payload: dict) -> bool:
    # The trigger's filter call is the first step of a spawn; failing it is
    # the "filter activity that fails deterministically" poison event.
    event_id = payload["event_data"]["value"]
    _filtered.append((event_id, activity.info().scheduled_time))
    first_flaky_call = event_id == "flaky" and [name for name, _ in _filtered].count("flaky") == 1
    if event_id == "poison" or first_flaky_call:
        raise ApplicationError(f"{event_id} cannot start", non_retryable=True)
    return True


@activity.defn(name="broadcast_trigger_status_activity")
async def _broadcast(_payload: dict) -> None:
    return None


@activity.defn(name="controller.queue.spill")
async def _spill(payload: dict) -> dict:
    _spilled.append(payload)
    return {"spilled": len(payload.get("events") or [])}


@activity.defn(name="spawn_backoff.record")
async def _record(envelope: dict) -> str:
    _started.append(envelope)
    return envelope["id"]


@workflow.defn(name="MachinaWorkflow", sandboxed=False)
class SpawnProbe:
    """The child run: records the event envelope it was given."""

    @workflow.run
    async def run(self, payload: dict) -> str:
        trigger = next(node for node in payload["nodes"] if node.get("_pre_executed"))
        return await workflow.execute_activity("spawn_backoff.record", trigger["_trigger_output"]["_event_envelope"],
            start_to_close_timeout=timedelta(seconds=30))


_DEFINITIONS = [WorkflowControlWorkflow, SpawnProbe]
_ACTIVITIES = [_filter, _broadcast, _spill, _record]


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


async def _until(predicate, what: str):
    for _ in range(500):
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for {what}: filtered={_filtered} started={_started} spilled={_spilled}")


def _has_patch(history) -> bool:
    return any(_PATCH.encode() in payload.data
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_MARKER_RECORDED
        for payloads in event.marker_recorded_event_attributes.details.values()
        for payload in payloads.payloads)


def _gaps(event_id: str) -> list[timedelta]:
    times = [scheduled for name, scheduled in _filtered if name == event_id]
    return [later - earlier for earlier, later in zip(times, times[1:])]


async def _start_controller(client, queue: str, slug: str):
    await client.operator_service.add_search_attributes(AddSearchAttributesRequest(
        namespace=client.namespace,
        search_attributes={"ControlEventTypes": IndexedValueType.INDEXED_VALUE_TYPE_KEYWORD_LIST},
    ))
    controller_id = "controller-" + uuid4().hex
    controller = await client.start_workflow("WorkflowControlWorkflow", {
        "execution_control_version": 1, "generation": 7, "state": "running", "revision": 1,
        "participant_state": "running", "participant_revision": 1, "controller_workflow_id": controller_id,
    }, id=controller_id, task_queue=queue)
    await controller.signal("register_trigger", _trigger(slug))
    return controller


async def _replays(history) -> None:
    # Every task replays the whole history (max_cached_workflows=0), so a
    # nondeterministic branch would fail a task. The one allowed failure is
    # the reset Signal meeting a child's completion at the run's last task.
    causes = [event.workflow_task_failed_event_attributes.cause for event in history.events
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED]
    assert set(causes) <= {WorkflowTaskFailedCause.WORKFLOW_TASK_FAILED_CAUSE_UNHANDLED_COMMAND}, causes
    replay = await Replayer(workflows=_DEFINITIONS).replay_workflow(history)
    assert replay.replay_failure is None


async def _run_dormant():
    global _filtered, _started, _spilled
    _filtered, _started, _spilled = [], [], []
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        client = environment.client
        queue = "controller-spawn-dormant-" + uuid4().hex
        async with Worker(client, task_queue=queue, workflows=_DEFINITIONS, activities=_ACTIVITIES,
                max_cached_workflows=0, patch_activation_callback=activate_patch):
            controller = await _start_controller(client, queue, "dormant-" + uuid4().hex)
            await controller.signal("on_event", _event("poison"))
            await controller.signal("on_event", _event("first"))
            await _until(lambda: _filtered, "the first spawn")
            for _ in range(4):
                await environment.sleep(timedelta(seconds=5))
            await _until(lambda: [name for name, _ in _filtered].count("poison") >= 4, "head-of-line retries")
            # Today's behaviour: the failed event holds the head, so the event
            # behind it never starts, and nothing is dead-lettered.
            assert {name for name, _ in _filtered} == {"poison"}
            assert all(timedelta(seconds=5) <= gap < timedelta(seconds=10) for gap in _gaps("poison")), _gaps("poison")
            assert _started == [] and _spilled == []
            await controller.signal("reset")
            await controller.result()
            history = await controller.fetch_history()
        assert not _has_patch(history)
        await _replays(history)


async def _run_active():
    global _filtered, _started, _spilled
    _filtered, _started, _spilled = [], [], []
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        client = environment.client
        queue = "controller-spawn-active-" + uuid4().hex
        async with Worker(client, task_queue=queue, workflows=_DEFINITIONS, activities=_ACTIVITIES,
                max_cached_workflows=0, patch_activation_callback=lambda _input: True):
            controller = await _start_controller(client, queue, "active-" + uuid4().hex)
            for event_id in ("poison", "flaky", "first"):
                await controller.signal("on_event", _event(event_id))
            # The failed spawns wait behind the queue; the event after them starts.
            await _until(lambda: [envelope["id"] for envelope in _started] == ["first"], "the event behind the failures")
            for _ in range(40):
                if _spilled:
                    break
                await environment.sleep(timedelta(seconds=30))
            await _until(lambda: _spilled, "the dead letter")
            assert [envelope["id"] for envelope in _started] == ["first", "flaky"]
            # The run never sees the retry bookkeeping.
            assert _started[1] == _event("flaky")
            assert [name for name, _ in _filtered].count("poison") == 5
            delays = [timedelta(seconds=5), timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=10)]
            gaps = _gaps("poison")
            assert len(gaps) == len(delays) and all(delay <= gap < delay + timedelta(seconds=10) for gap, delay in zip(gaps, delays)), gaps
            controller_id = controller.id
            assert _spilled == [{"controller_id": f"{controller_id}:dead", "prepend": False, "events": [
                ["push", {**_event("poison"), "_spawn_attempts": 5, "_dead_reason": "ActivityError"}]]}]
            # The queue moves on.
            await controller.signal("on_event", _event("after"))
            await _until(lambda: [envelope["id"] for envelope in _started] == ["first", "flaky", "after"], "the next event")
            status = await controller.query("status")
            assert status["queued_events"] == 0
            await controller.signal("reset")
            await controller.result()
            history = await controller.fetch_history()
        assert _has_patch(history)
        await _replays(history)


@pytest.mark.parametrize("mode", ["dormant", "active"])
def test_controller_spawn_failure_on_the_real_sdk(mode):
    completed = subprocess.run([sys.executable, "-m", "tests.temporal.test_controller_spawn_backoff_integration", mode],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=240, check=False)
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-12000:]


if __name__ == "__main__":
    asyncio.run({"dormant": _run_dormant, "active": _run_active}[sys.argv[1]]())
