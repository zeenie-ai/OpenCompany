"""Real SDK execution, paused worker restart, rollover and replay control gate."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.execution_control import ExecutionControl
from services.temporal.execution_control_activities import ExecutionControlActivities
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


_started: asyncio.Event
_release: asyncio.Event
_calls: list[str]


@activity.defn(name="control.probe.tool")
async def _tool(payload: dict) -> str:
    name = payload["name"]
    _calls.append(name)
    if name == "A":
        _started.set()
        await _release.wait()
    return f"{name}-result"


@workflow.defn(sandboxed=False)
class ExecutionControlProbeWorkflow:
    def __init__(self):
        self.control = ExecutionControl()
        self.results: list[str] = []

    @workflow.update
    async def set_control_state(self, payload: dict) -> dict:
        return await self.control.set_control_state(payload)

    @workflow.update
    async def wait_for_checkpoint(self, payload: dict | None = None) -> dict:
        return await self.control.wait_for_checkpoint(payload)

    @workflow.query
    def execution_control_status(self) -> dict:
        return {**self.control.status(), "results": self.results}

    @workflow.run
    async def run(self, payload: dict) -> list[str]:
        self.control.bind(payload)
        self.results = list(payload.get("results") or [])
        await self.control.register_root()
        phase = int(payload.get("phase") or 0)
        async with self.control.action():
            result = await workflow.execute_activity(
                "control.probe.tool", {"name": "ABC"[phase]},
                start_to_close_timeout=timedelta(minutes=2),
            )
            self.results.append(result)
        await self.control.wait_until_running()
        if phase < 2:
            await workflow.wait_condition(workflow.all_handlers_finished)
            workflow.continue_as_new({**payload, **self.control.carry(), "phase": phase + 1, "results": self.results})
        await self.control.unregister_root()
        return self.results


async def _histories(client, workflow_id, first_run_id):
    histories = []
    run_id = first_run_id
    while True:
        history = await client.get_workflow_handle(workflow_id, run_id=run_id).fetch_history()
        histories.append(history)
        continued = next((event.workflow_execution_continued_as_new_event_attributes for event in history.events
                          if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW), None)
        if continued is None:
            return histories
        run_id = continued.new_execution_run_id


def _paused_controller_history_pressure(controller: WorkflowControlWorkflow) -> bool:
    # A tiny global event limit would now roll on every successful Update.
    # Pressure the intended paused phase once, and retain this deterministic
    # hook while replaying the history that records the actual continuation.
    return (not workflow.info().continued_run_id
        and controller._execution_control.state == "paused"
        and bool(controller._live_roots))


async def _run_gate():
    original_pressure = WorkflowControlWorkflow._history_pressure
    WorkflowControlWorkflow._history_pressure = _paused_controller_history_pressure
    try:
        await _run_gate_with_pressure()
    finally:
        WorkflowControlWorkflow._history_pressure = original_pressure


async def _run_gate_with_pressure():
    global _started, _release, _calls
    _started, _release, _calls = asyncio.Event(), asyncio.Event(), []
    queue = "control-native-" + uuid4().hex
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        client = environment.client
        definitions = [ExecutionControlProbeWorkflow, WorkflowControlWorkflow]
        activities = [_tool, *ExecutionControlActivities(client).activities()]
        controller_id = "controller-" + uuid4().hex
        root_id = "root-" + uuid4().hex
        async with Worker(client, task_queue=queue, workflows=definitions, activities=activities, max_cached_workflows=0,
                          identity="control-probe-before-restart"):
            controller = await client.start_workflow("WorkflowControlWorkflow", {
                "execution_control_version": 1, "generation": 7,
            }, id=controller_id, task_queue=queue)
            root = await client.start_workflow("ExecutionControlProbeWorkflow", {
                "execution_control_version": 1, "generation": 7,
                "controller_workflow_id": controller_id,
            }, id=root_id, task_queue=queue)
            await asyncio.wait_for(_started.wait(), timeout=15)
            pause = {"state": "paused", "revision": 1, "generation": 7}
            await controller.execute_update("set_control_state", pause)
            admitted = await root.execute_update("set_control_state", {**pause, "first_execution_run_id": root.first_execution_run_id})
            assert admitted["active_actions"] == 1
            checkpoint = asyncio.create_task(root.execute_update("wait_for_checkpoint", pause))
            await asyncio.sleep(0.05)
            assert not checkpoint.done()
            _release.set()
            assert (await asyncio.wait_for(checkpoint, timeout=15))["checkpoint"] is True
            stopped = await root.query("execution_control_status")
            assert stopped["results"] == ["A-result"]
            assert _calls == ["A"]
            # The acknowledged pause Update pressures this original run.
            # Its chain roster and state survive while the root is stopped.
            for _ in range(100):
                description = await controller.describe()
                if description.run_id != controller.first_execution_run_id:
                    break
                await asyncio.sleep(0.02)
            assert description.run_id != controller.first_execution_run_id
            status = await controller.query("status")
            assert status["live_roots"][root_id]["first_execution_run_id"] == root.first_execution_run_id
            assert status["participant_state"] == "paused"

        # A new SDK Worker rebuilds all Python workflow state from history.
        async with Worker(client, task_queue=queue, workflows=definitions, activities=activities, max_cached_workflows=0,
                          identity="control-probe-after-restart"):
            # Synchronize the restarted worker with an idempotent Update before
            # querying the workflow state reconstructed by its first task.
            await root.execute_update("set_control_state", {"state": "paused", "revision": 1, "generation": 7})
            stopped = await root.query("execution_control_status")
            assert stopped["results"] == ["A-result"]
            resume = {"state": "running", "revision": 2, "generation": 7, "producers_held": True}
            await controller.execute_update("set_control_state", resume)
            await root.execute_update("set_control_state", resume)
            assert (await root.query("execution_control_status"))["results"] == ["A-result"]
            assert _calls == ["A"]
            await root.execute_update("set_control_state", {**resume, "producers_held": False})
            await controller.execute_update("set_control_state", {**resume, "producers_held": False})
            assert await root.result() == ["A-result", "B-result", "C-result"]
            assert _calls == ["A", "B", "C"]
            assert (await controller.query("status"))["live_roots"] == {}
            await controller.signal("reset")
            await controller.result()
            legacy = await client.start_workflow("ExecutionControlProbeWorkflow", {"phase": 2},
                id="legacy-" + uuid4().hex, task_queue=queue)
            assert await legacy.result() == ["C-result"]
            legacy_history = await legacy.fetch_history()

        roots = await _histories(client, root_id, root.first_execution_run_id)
        controllers = await _histories(client, controller_id, controller.first_execution_run_id)
        assert len(roots) == 3
        assert len(controllers) == 2
        for history in [*roots, *controllers, legacy_history]:
            result = await Replayer(workflows=definitions).replay_workflow(history)
            assert result.replay_failure is None
        legacy_activities = [event.activity_task_scheduled_event_attributes.activity_type.name
            for event in legacy_history.events if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED]
        assert legacy_activities == ["control.probe.tool"]


def test_control_restart_rollover_and_histories_replay():
    completed = subprocess.run([sys.executable, "-m", "tests.temporal.test_execution_control_replay"],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=120, check=False)
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-8000:]


if __name__ == "__main__":
    asyncio.run(_run_gate())
