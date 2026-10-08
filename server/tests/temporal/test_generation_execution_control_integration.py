"""Native graph/tree control with late roots, parallel drain and history replay."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.deployment.execution_control import transition_generation
from services.temporal.execution_control import ExecutionControl
from services.temporal.execution_control_activities import ExecutionControlActivities
from services.temporal.workflow import MachinaWorkflow
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow
from services.temporal.employee_job_workflow import EmployeeJobWorkflow

# Load the plugin-owned workflow itself without booting every plugin/provider
# as a side effect of importing nodes/__init__.py in this clean replay process.
_cron_spec = importlib.util.spec_from_file_location("execution_control_cron_test",
    Path(__file__).parents[2] / "nodes/scheduler/cron_scheduler/_workflow.py")
_cron_module = importlib.util.module_from_spec(_cron_spec)
sys.modules[_cron_spec.name] = _cron_module
_cron_spec.loader.exec_module(_cron_module)
CronTriggerWorkflow = _cron_module.CronTriggerWorkflow


_started: dict[str, asyncio.Event]
_release: dict[str, asyncio.Event]
_calls: list[str]
_contexts: dict[str, dict]
_deliveries: list[dict]


@activity.defn(name="execute_node_activity")
async def _execute_node(context: dict) -> dict:
    node_id = context["node_id"]
    _calls.append(node_id)
    _contexts[node_id] = context
    if node_id in _release:
        _started[node_id].set()
        await _release[node_id].wait()
    return {"success": True, "node_id": node_id, "result": {"completed": node_id}}


@activity.defn(name="store_node_output_activity")
async def _store_output(_payload: dict) -> dict:
    return {"stored": True}


@activity.defn(name="workflow_runs.record_completion")
async def _record_completion(_payload: dict) -> dict:
    return {"recorded": True}


@activity.defn(name="employee.job.deliver")
async def _deliver(context: dict) -> dict:
    _deliveries.append(context)
    return {"delivered": True, "state": "delivered"}


@activity.defn(name="employee.job.failed")
async def _fail_job(_context: dict) -> dict:
    raise AssertionError("A successful controlled job must not fail")


@workflow.defn(name="AgentWorkflow", sandboxed=False)
class ControlledChildProbe:
    """An attached child uses the shared contract without root enrollment."""

    @workflow.init
    def __init__(self, context: dict | None = None):
        self.control = ExecutionControl()
        self.results = []
        if context is not None:
            self.control.bind(context)

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
    async def run(self, context: dict | None = None) -> dict:
        context = context or {}
        self.control.bind(context)
        if (context.get("node_data") or {}).get("probe_rollover") and not context.get("_probe_after_rollover"):
            await workflow.wait_condition(workflow.all_handlers_finished)
            workflow.continue_as_new({**context, **self.control.carry(), "_probe_after_rollover": True})
        for tool_id in context.get("probe_tool_ids", ("D", "E")):
            async with self.control.action():
                result = await workflow.execute_activity("execute_node_activity", {**context, "node_id": tool_id},
                    start_to_close_timeout=timedelta(minutes=2))
                self.results.append(result["result"])
        await workflow.wait_condition(workflow.all_handlers_finished)
        return {"success": True, "node_id": context["node_id"], "result": {"tools": self.results}}


async def _wait_status(handle, predicate):
    for _ in range(200):
        status = await handle.query("status")
        if predicate(status):
            return status
        await asyncio.sleep(0.01)
    raise AssertionError("Expected controller state was not observed")


async def _chain_histories(client, workflow_id, first_run_id):
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


def _input(controller_id: str, *, late: bool = False) -> dict:
    nodes = [{"id": "late", "type": "controlProbe", "data": {}}] if late else [
        {"id": "A", "type": "controlProbe", "data": {}},
        {"id": "C", "type": "controlProbe", "data": {}},
        {"id": "agent", "type": "aiAgent", "data": {"probe_rollover": True}},
        {"id": "B", "type": "controlProbe", "data": {}},
    ]
    edges = [] if late else [{"source": source, "target": "B"} for source in ("A", "C", "agent")]
    return {"nodes": nodes, "edges": edges, "workflow_id": "graph", "workflow_slug": "graph",
        "execution_control_version": 1, "generation": 7, "graphVersion": 2,
        "controller_workflow_id": controller_id,
        "_temporal_routing_v1": {"version": 1, "agent_workflow_enabled": True,
            "per_type_dispatch_enabled": False, "worker_pool_enabled": False}}


async def _run_gate():
    global _started, _release, _calls, _contexts
    _started = {key: asyncio.Event() for key in ("A", "C", "D")}
    _release = {key: asyncio.Event() for key in _started}
    _calls, _contexts = [], {}
    queue = "generation-control-" + uuid4().hex
    definitions = [MachinaWorkflow, WorkflowControlWorkflow, ControlledChildProbe]
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        client = environment.client
        activities = [_execute_node, *ExecutionControlActivities(client).activities()]
        async with Worker(client, task_queue=queue, workflows=definitions, activities=activities, max_cached_workflows=0):
            controller_id = "controller-" + uuid4().hex
            controller = await client.start_workflow("WorkflowControlWorkflow", {
                "execution_control_version": 1, "generation": 7,
            }, id=controller_id, task_queue=queue)
            root_id = "root-" + uuid4().hex
            root = await client.start_workflow("MachinaWorkflow", _input(controller_id), id=root_id, task_queue=queue)
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in _started.values())), timeout=15)
            child = client.get_workflow_handle(root_id + "-agent-agent")
            parent_description = await root.describe()
            pending = parent_description.raw_description.pending_children[0]
            child_description = await child.describe()
            assert pending.run_id != child_description.run_id
            assert pending.run_id == child_description.raw_description.workflow_execution_info.first_run_id
            scope = SimpleNamespace(controller_workflow_id=controller_id, controller_run_id=controller.first_execution_run_id,
                workflow_id="graph", generation=7, revision=1)
            stop = asyncio.create_task(transition_generation(client, scope, paused=True))
            await _wait_status(controller, lambda status: status["participant_state"] == "paused")
            late_id = "late-root-" + uuid4().hex
            late = await client.start_workflow("MachinaWorkflow", _input(controller_id, late=True), id=late_id, task_queue=queue)
            await _wait_status(controller, lambda status: late_id in status["live_roots"])
            assert "late" not in _calls
            _release["A"].set()
            await asyncio.sleep(0.03)
            assert not stop.done()
            _release["C"].set()
            await asyncio.sleep(0.03)
            assert not stop.done()
            _release["D"].set()
            stopped = await asyncio.wait_for(stop, timeout=15)
            assert stopped["controlled_executions"] == 4
            assert sorted(_calls) == ["A", "C", "D"]
            child_status = await child.query("execution_control_status")
            assert child_status["state"] == "paused"
            assert child_status["results"] == [{"completed": "D"}]
            assert (await root.query("execution_control_status"))["active_actions"] == 0
            assert (await late.query("execution_control_status"))["state"] == "paused"
            scope.revision = 2
            resumed = await asyncio.wait_for(transition_generation(client, scope, paused=False), timeout=15)
            assert resumed["controller_status"]["state"] == "running"
            # Concurrent SDK result interceptors both unlock test-server time.
            # These activities are ready immediately, so use wall-clock waits.
            with environment.auto_time_skipping_disabled():
                root_result, late_result = await asyncio.gather(root.result(), late.result())
            assert root_result["success"] is True
            assert late_result["success"] is True
            assert sorted(_calls) == ["A", "B", "C", "D", "E", "late"]
            assert _contexts["B"]["inputs"] == {"A": {"completed": "A"}, "C": {"completed": "C"},
                "agent": {"tools": [{"completed": "D"}, {"completed": "E"}]}}
            await controller.signal("reset")
            await controller.result()
            legacy_input = _input(controller_id, late=True)
            legacy_input.pop("execution_control_version")
            legacy_input["nodes"][0]["id"] = "legacy"
            legacy = await client.start_workflow("MachinaWorkflow", legacy_input, id="legacy-" + uuid4().hex, task_queue=queue)
            assert (await legacy.result())["success"] is True
            histories = [await handle.fetch_history() for handle in (root, late, legacy, controller)]
            histories.extend(await _chain_histories(client, child.id, pending.run_id))
        for history in histories:
            replay = await Replayer(workflows=definitions).replay_workflow(history)
            assert replay.replay_failure is None


def _cron_job_history_pressure(controller: WorkflowControlWorkflow) -> bool:
    # An explicit acknowledged pause revision follows the complete drain.
    # Pressure only that original-run phase with both roots enrolled, using
    # the same hook in replay, rather than churning after every small Update.
    return (not workflow.info().continued_run_id
        and controller._execution_control.state == "paused"
        and controller._execution_control.revision == 2
        and len(controller._live_roots) >= 2)


async def _run_cron_job_gate():
    global _started, _release, _calls, _contexts, _deliveries
    _started, _release = {"cron-A": asyncio.Event()}, {"cron-A": asyncio.Event()}
    _calls, _contexts, _deliveries = [], {}, []
    queue = "generation-cron-job-" + uuid4().hex
    definitions = [MachinaWorkflow, WorkflowControlWorkflow, ControlledChildProbe, CronTriggerWorkflow, EmployeeJobWorkflow]
    original_pressure = WorkflowControlWorkflow._history_pressure
    WorkflowControlWorkflow._history_pressure = _cron_job_history_pressure
    try:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            client = environment.client
            activities = [_execute_node, _store_output, _record_completion, _deliver, _fail_job,
                          *ExecutionControlActivities(client).activities()]
            async with Worker(client, task_queue=queue, workflows=definitions, activities=activities, max_cached_workflows=0):
                controller_id = "controller-" + uuid4().hex
                controller = await client.start_workflow("WorkflowControlWorkflow", {
                    "execution_control_version": 1, "generation": 7,
                }, id=controller_id, task_queue=queue)
                cron_id = "cron-parent-" + uuid4().hex
                cron_input = {"execution_control_version": 1, "controller_workflow_id": controller_id,
                    "generation": 7, "graphVersion": 2, "workflow_id": None, "workflow_slug": "cron-probe",
                    "trigger_node_id": "cron", "nodes": [
                        {"id": "cron", "type": "cronScheduler", "data": {}},
                        {"id": "cron-A", "type": "controlProbe", "data": {}},
                        {"id": "cron-B", "type": "controlProbe", "data": {}},
                    ], "edges": [{"source": "cron", "target": "cron-A"}, {"source": "cron-A", "target": "cron-B"}],
                    "_temporal_routing_v1": {"version": 1, "agent_workflow_enabled": True,
                        "per_type_dispatch_enabled": False, "worker_pool_enabled": False}}
                cron = await client.start_workflow("CronTriggerWorkflow", cron_input, id=cron_id, task_queue=queue)
                with environment.auto_time_skipping_disabled():
                    firing = await cron.result()
                graph_id = firing["spawned_child_id"]
                graph = client.get_workflow_handle(graph_id)
                await asyncio.wait_for(_started["cron-A"].wait(), timeout=15)
                enrolled = await _wait_status(controller, lambda status: graph_id in status["live_roots"] and cron_id not in status["live_roots"])
                graph_chain = enrolled["live_roots"][graph_id]["first_execution_run_id"]
                assert enrolled["live_roots"][graph_id]["workflow_id"] == graph_id
                scope = SimpleNamespace(controller_workflow_id=controller_id, controller_run_id=controller.first_execution_run_id,
                    workflow_id="graph", generation=7, revision=1)
                stop = asyncio.create_task(transition_generation(client, scope, paused=True))
                await _wait_status(controller, lambda status: status["participant_state"] == "paused")
                job_id = "job-root-" + uuid4().hex
                job = await client.start_workflow("EmployeeJobWorkflow", {
                    "execution_control_version": 1, "controller_workflow_id": controller_id,
                    "generation": 7, "workflow_id": "graph", "node_id": "lead", "probe_tool_ids": ["job-tool"],
                }, id=job_id, task_queue=queue)
                await _wait_status(controller, lambda status: job_id in status["live_roots"])
                assert (await job.describe()).raw_description.pending_children == []
                assert "job-tool" not in _calls
                assert _deliveries == []
                _release["cron-A"].set()
                await asyncio.wait_for(stop, timeout=15)
                assert _calls == ["cron-A"]
                # Pressure the stable stopped phase after enrollment and
                # drain, keeping the detached graph and late job in the carry.
                await controller.execute_update("set_control_state", {
                    "state": "paused", "revision": 2, "generation": 7,
                })
                for _ in range(100):
                    description = await controller.describe()
                    if description.run_id != controller.first_execution_run_id:
                        break
                    await asyncio.sleep(0.01)
                assert description.run_id != controller.first_execution_run_id
                carried = await controller.query("status")
                assert carried["live_roots"][graph_id]["first_execution_run_id"] == graph_chain
                assert job_id in carried["live_roots"]
                scope.revision = 3
                await asyncio.wait_for(transition_generation(client, scope, paused=False), timeout=15)
                with environment.auto_time_skipping_disabled():
                    graph_result, delivered = await asyncio.gather(graph.result(), job.result())
                assert graph_result["success"] is True
                assert delivered["delivered"] is True
                assert sorted(_calls) == ["cron-A", "cron-B", "job-tool"]
                assert len(_deliveries) == 1
                lead = client.get_workflow_handle(job_id + ":lead")
                await controller.signal("reset")
                await controller.result()
                histories = [await handle.fetch_history() for handle in (cron, graph, job, lead)]
                controller_histories = await _chain_histories(client, controller_id, controller.first_execution_run_id)
                assert len(controller_histories) == 2
                histories.extend(controller_histories)
            for history in histories:
                replay = await Replayer(workflows=definitions).replay_workflow(history)
                assert replay.replay_failure is None
    finally:
        WorkflowControlWorkflow._history_pressure = original_pressure


def test_generation_stop_drains_tree_late_roots_and_resume_replays():
    completed = subprocess.run([sys.executable, "-m", "tests.temporal.test_generation_execution_control_integration"],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=120, check=False)
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-10000:]


if __name__ == "__main__":
    asyncio.run(_run_gate())
    asyncio.run(_run_cron_job_gate())
