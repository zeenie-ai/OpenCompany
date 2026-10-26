"""The Approval node and Stop (RFC-0007 §4.8), on the real SDK with stub
activities: in a generation that waits for the owner, Stop completes while
the gate waits, and the node after it runs only after Resume; in an older
generation the gate holds Stop until the owner decides. Every history
replays."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.workflow import APPROVAL_GATE_TYPE, MachinaWorkflow

QUEUE = "approval-gate-stop"
CALLS: list[str] = []
GATE: dict[str, asyncio.Event] = {}


@activity.defn(name="execute_node_activity")
async def _execute_node(context: dict) -> dict:
    node_id = context["node_id"]
    CALLS.append(node_id)
    if node_id == "gate":
        GATE["started"].set()
        await GATE["decided"].wait()
    return {"success": True, "node_id": node_id, "result": {"done": node_id}}


@activity.defn(name="execution_control.register")
async def _register(payload: dict) -> dict:
    return {"participant_state": "running", "participant_revision": 0}


@activity.defn(name="execution_control.unregister")
async def _unregister(payload: dict) -> dict:
    return {}


ACTIVITIES = [_execute_node, _register, _unregister]
PAUSED = {"state": "paused", "revision": 1, "generation": 7}
RUNNING = {"state": "running", "revision": 2, "generation": 7, "producers_held": False}


def _input(waits: bool) -> dict:
    routing = {"version": 1, "agent_workflow_enabled": False, "per_type_dispatch_enabled": False, "worker_pool_enabled": False}
    if waits:
        routing["approval_wait_version"] = 1
    return {
        "nodes": [{"id": "gate", "type": APPROVAL_GATE_TYPE, "data": {}}, {"id": "reply", "type": "controlProbe", "data": {}}],
        "edges": [{"source": "gate", "target": "reply"}],
        "workflow_id": "graph",
        "workflow_slug": "graph",
        "execution_control_version": 1,
        "generation": 7,
        "graphVersion": 2,
        "controller_workflow_id": "controller",
        "_temporal_routing_v1": routing,
    }


def _reset() -> None:
    CALLS.clear()
    GATE.update(started=asyncio.Event(), decided=asyncio.Event())


async def _replay(history: WorkflowHistory) -> None:
    result = await Replayer(workflows=[MachinaWorkflow]).replay_workflow(history)
    assert result.replay_failure is None


async def _stop_completes_while_the_gate_waits(environment: WorkflowEnvironment) -> None:
    _reset()
    async with Worker(environment.client, task_queue=QUEUE, workflows=[MachinaWorkflow], activities=ACTIVITIES, max_cached_workflows=0):
        handle = await environment.client.start_workflow("MachinaWorkflow", _input(waits=True), id=f"waits-{uuid4()}", task_queue=QUEUE)
        await asyncio.wait_for(GATE["started"].wait(), 10)
        await handle.execute_update("set_control_state", PAUSED)
        drained = await asyncio.wait_for(handle.execute_update("wait_for_checkpoint", PAUSED), 10)
        assert drained["checkpoint"] and drained["active_actions"] == 0
        # Decided while stopped: the node after it runs only after Resume.
        GATE["decided"].set()
        await asyncio.sleep(0.3)
        assert CALLS == ["gate"]
        await handle.execute_update("set_control_state", RUNNING)
        result = await asyncio.wait_for(handle.result(), 10)
        history = await handle.fetch_history()
    assert result["success"] is True and CALLS == ["gate", "reply"]
    await _replay(history)


async def _an_older_generation_s_gate_holds_stop(environment: WorkflowEnvironment) -> None:
    _reset()
    async with Worker(environment.client, task_queue=QUEUE, workflows=[MachinaWorkflow], activities=ACTIVITIES, max_cached_workflows=0):
        handle = await environment.client.start_workflow("MachinaWorkflow", _input(waits=False), id=f"holds-{uuid4()}", task_queue=QUEUE)
        await asyncio.wait_for(GATE["started"].wait(), 10)
        await handle.execute_update("set_control_state", PAUSED)
        drain = asyncio.ensure_future(handle.execute_update("wait_for_checkpoint", PAUSED))
        await asyncio.sleep(0.3)
        assert not drain.done()
        GATE["decided"].set()
        assert (await asyncio.wait_for(drain, 10))["checkpoint"]
        await handle.execute_update("set_control_state", RUNNING)
        result = await asyncio.wait_for(handle.result(), 10)
        history = await handle.fetch_history()
    assert result["success"] is True and CALLS == ["gate", "reply"]
    await _replay(history)


async def _main() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        with environment.auto_time_skipping_disabled():
            await _stop_completes_while_the_gate_waits(environment)
            await _an_older_generation_s_gate_holds_stop(environment)


def test_the_workflow_names_the_gate_as_the_approvals_contract_does() -> None:
    from services.approvals.contract import APPROVAL_GATE_TYPE as CONTRACT_GATE_TYPE

    assert APPROVAL_GATE_TYPE == CONTRACT_GATE_TYPE


def test_stop_completes_while_the_gate_waits_for_the_owner() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tests.temporal.test_approval_gate_stop_integration"],
        cwd=Path(__file__).parents[2],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-12000:]


if __name__ == "__main__":
    asyncio.run(_main())
