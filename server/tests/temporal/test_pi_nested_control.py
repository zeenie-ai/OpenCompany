"""Real Pi grandchildren, bounded teammates, and central controller drain.

Model and permit storage are deterministic Activity fixtures; the Temporal
coordinators, SDK hosts, native delegation lifecycle, and deployment controller
are real. The root is explicitly enrolled to isolate the control-tree contract
from graph admission (covered by the routing tests).
"""
from __future__ import annotations

import asyncio
from collections import Counter
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from uuid import uuid4

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.deployment.execution_control import transition_generation
from services.llm.protocol import Message, ToolCall, message_to_wire
from services.temporal.agent_workflow import AgentWorkflow
from services.temporal.pi_activities import PiControlActivities, PiDriverActivities, pi_put_blob
from services.temporal.pi_protocol import DRIVER_QUEUE
from services.temporal.pi_workflow import PiCodingAgentWorkflow
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow
from tests.temporal.test_pi_worker_recovery import install_shared_blobs, wait_until
from tests.temporal import test_pi_workflow_integration as fixture

QUEUE = "pi-nested-central-control"
COUNTS = Counter()
PERMITS = set()
PREPARED = []
STARTED = asyncio.Event()
RELEASE = asyncio.Event()
DRIVERS = Counter()


def binding(node):
    tool = fixture._binding("delegate_to_" + node, "coding_agent", node)
    tool["definition"]["parameters"] = {"type": "object", "properties": {
        "task": {"type": "string"}}, "required": ["task"]}
    return tool


@activity.defn(name="agent.prepare_payload")
async def prepare(context):
    PREPARED.append(context)
    node = context["node_id"]
    tools = ([binding("child-" + str(index)) for index in range(5)] if node == "root" else
        [binding("grandchild-" + node.removeprefix("child-"))] if node.startswith("child-") else [])
    return {"node_id": node, "node_type": "coding_agent", "provider": "openai", "model": "stub",
        "workflow_id": "graph", "session_id": "session", "system_message": "Follow the assignment",
        "user_prompt": "nested", "tools": tools, "max_iterations": 3, "max_concurrent_subagents": 2,
        "max_delegation_depth": 2, "memory_node_id": "", "compaction_threshold": None,
        "team_id": "team", "pi_harness_version": 1}


@activity.defn(name="agent.execute_llm_step")
async def model(payload):
    node = payload["node_id"]
    COUNTS["llm:" + node] += 1
    if payload["iteration"] == 1:
        assert [message["tool_call_id"] for message in payload["messages"] if message["role"] == "tool"] == [
            node + "-" + str(index) for index in range(len(payload["tools"]))]
    if node.startswith("grandchild-") and not STARTED.is_set():
        STARTED.set()
        await RELEASE.wait()
    if payload["iteration"] == 0 and payload["tools"]:
        calls = [{"id": node + "-" + str(index), "name": tool["name"], "args": {"task": "Complete this nested task"}}
            for index, tool in enumerate(payload["tools"])]
        return {"kind": "tool_calls", "calls": calls, "assistant_message": message_to_wire(
            Message(role="assistant", tool_calls=[ToolCall(**call) for call in calls])), "usage": {"total_tokens": 3}}
    return {"kind": "final", "content": "done: " + node, "assistant_message": message_to_wire(
        Message(role="assistant", content="done: " + node)), "usage": {"total_tokens": 3}}


@activity.defn(name="agent.acquire_subagent_permit")
async def acquire(payload):
    identity = payload["permit_id"]
    while identity not in PERMITS and len(PERMITS) >= payload["limit"]:
        activity.heartbeat()
        await asyncio.sleep(0.01)
    PERMITS.add(identity)
    COUNTS["max_permits"] = max(COUNTS["max_permits"], len(PERMITS))
    COUNTS["acquire"] += 1
    return {"lease_id": identity}


@activity.defn(name="agent.release_subagent_permit")
async def release(payload):
    PERMITS.discard(payload["permit_id"])
    COUNTS["release"] += 1
    return {"released": True}


@activity.defn(name="agent.queue_delegation")
async def queue(payload):
    COUNTS["queued"] += 1
    return {}


@activity.defn(name="agent.begin_delegation")
async def begin(payload):
    COUNTS["begun"] += 1
    return {}


@activity.defn(name="agent.register_task_execution")
async def register(payload):
    COUNTS["registered"] += 1
    return {}


@activity.defn(name="agent.finish_delegation")
async def finish(payload):
    COUNTS["finished"] += 1
    return {}


@activity.defn(name="agent.cancel_delegation")
async def cancel(payload):
    COUNTS["cancelled"] += 1
    return {}


async def nested_gate(directory):
    from services.pi_runtime import resolve_pi_host
    await asyncio.to_thread(resolve_pi_host)
    install_shared_blobs(directory / "blobs")
    COUNTS.clear()
    PERMITS.clear()
    PREPARED.clear()
    STARTED.clear()
    RELEASE.clear()
    DRIVERS.clear()
    definitions = [PiCodingAgentWorkflow, AgentWorkflow, WorkflowControlWorkflow]
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        with environment.auto_time_skipping_disabled():
            driver = PiDriverActivities(environment.client)
            original_host = driver._run_host
            async def counted_host(*args, **kwargs):
                DRIVERS["active"] += 1
                DRIVERS["max"] = max(DRIVERS["max"], DRIVERS["active"])
                try:
                    return await original_host(*args, **kwargs)
                finally:
                    DRIVERS["active"] -= 1
            driver._run_host = counted_host
            async with Worker(environment.client, task_queue=QUEUE, workflows=definitions,
                    activities=[prepare, model, acquire, release, queue, begin, register, finish, cancel,
                        fixture._broadcast, fixture._store, fixture._clear, pi_put_blob,
                        *PiControlActivities(environment.client).activities()], max_cached_workflows=0), \
                    Worker(environment.client, task_queue=DRIVER_QUEUE, activities=[driver.drive_turn],
                        max_concurrent_activities=1):
                controller = await environment.client.start_workflow("WorkflowControlWorkflow", {
                    "execution_control_version": 1, "generation": 7},
                    id="pi-controller-" + uuid4().hex, task_queue=QUEUE)
                handle = await environment.client.start_workflow("PiCodingAgentWorkflow", {
                    "node_id": "root", "node_type": "coding_agent", "pi_harness_version": 1,
                    "root_execution_id": "pi-team-root", "team_id": "team", "execution_control_version": 1,
                    "controller_workflow_id": controller.id, "generation": 7, "execution_control_revision": 0},
                    id="pi-nested-root-" + uuid4().hex, task_queue=QUEUE)
                await controller.execute_update("register_execution", {"generation": 7,
                    "workflow_id": handle.id, "first_execution_run_id": handle.first_execution_run_id})
                # Each Pi turn spawns bun through the one driver slot, and a
                # Windows runner has taken over 2.5 s a turn, against about
                # 1 s locally. This wait spans four turns, the first one cold;
                # the wait for the result spans the eighteen after Resume.
                await asyncio.wait_for(STARTED.wait(), 60)
                description = await handle.describe()
                assert 1 <= len(description.raw_description.pending_children) <= 2
                assert COUNTS["max_permits"] == 2 and DRIVERS["max"] == 1
                admitted = asyncio.Event()
                async def acknowledge(_status):
                    admitted.set()
                scope = SimpleNamespace(controller_workflow_id=controller.id,
                    controller_run_id=controller.first_execution_run_id, workflow_id="graph", generation=7, revision=1)
                stop = asyncio.create_task(transition_generation(environment.client, scope, paused=True, on_admission=acknowledge))
                await asyncio.wait_for(admitted.wait(), 20)
                assert not stop.done(), "Central Stop must drain the admitted grandchild model"
                RELEASE.set()
                stopped = await asyncio.wait_for(stop, 30)
                assert stopped["controlled_executions"] >= 4
                assert (await handle.query("execution_control_status"))["state"] == "paused"
                assert DRIVERS["active"] == 0
                business_counts = lambda: {key: value for key, value in COUNTS.items()
                    if key.startswith("llm:") or key in {"queued", "begun"}}
                before_resume = business_counts()
                await asyncio.sleep(0.15)
                assert business_counts() == before_resume, "Paused descendants may not admit new work"
                scope.revision = 2
                await asyncio.wait_for(transition_generation(environment.client, scope, paused=False), 30)
                result = await asyncio.wait_for(handle.result(), 180)
                assert result["success"], result
                assert result["result"]["response"] == "done: root" and result["result"]["usage"]["total_tokens"] == 6
                assert COUNTS["llm:root"] == 2
                assert all(COUNTS["llm:child-" + str(index)] == 2 and
                    COUNTS["llm:grandchild-" + str(index)] == 1 for index in range(5)), COUNTS
                assert COUNTS["queued"] == COUNTS["begun"] == COUNTS["registered"] == COUNTS["finished"] == 10
                assert COUNTS["cancelled"] == 0 and not PERMITS
                assert COUNTS["max_permits"] == 2 and DRIVERS["max"] == 1 and DRIVERS["active"] == 0
                assert len(PREPARED) == 11 and all(context["pi_harness_version"] == 1 for context in PREPARED)
                assert sorted(context.get("delegation_depth", 0) for context in PREPARED) == [0] + [1] * 5 + [2] * 5
                histories = [await handle.fetch_history()]
                # Every actual nested Coding Agent runs Pi, with real histories
                # proving that parents park and free the only SDK driver slot.
                for context in PREPARED[1:]:
                    parent = handle.id if context["delegation_depth"] == 1 else (
                        handle.id + "-delegate-child-" + context["node_id"].removeprefix("grandchild-") + "-1-" +
                        str(int(context["node_id"].removeprefix("grandchild-")) + 1))
                    index = int(context["node_id"].removeprefix("child-")) + 1 if context["delegation_depth"] == 1 else 1
                    child_id = parent + "-delegate-" + context["node_id"] + "-1-" + str(index)
                    child_handle = environment.client.get_workflow_handle(child_id)
                    child_result = await child_handle.result()
                    assert child_result["success"] and child_result["result"]["response"] == "done: " + context["node_id"]
                    assert child_result["result"]["usage"]["total_tokens"] == (6 if context["delegation_depth"] == 1 else 3)
                    histories.append(await child_handle.fetch_history())
                await controller.execute_update("unregister_execution", {"generation": 7,
                    "workflow_id": handle.id, "first_execution_run_id": handle.first_execution_run_id})
                await controller.signal("reset")
                await controller.result()
                histories.append(await controller.fetch_history())
            for history in histories:
                replay = await Replayer(workflows=definitions).replay_workflow(history)
                assert replay.replay_failure is None
            print("One SDK slot completed five children and five grandchildren; central Stop/Resume and all histories replayed", flush=True)


def test_nested_pi_one_driver_slot_bounded_team_and_central_control():
    workspace = Path(__file__).parents[3]
    with tempfile.TemporaryDirectory(prefix=".pi-nested-control-", dir=workspace) as temporary:
        directory = Path(temporary)
        env = {**os.environ, "DEBUG": "false", "DATA_DIR": str(directory / "data")}
        result = subprocess.run([sys.executable, "-m", "tests.temporal.test_pi_nested_control", str(directory)],
            cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            # Above the sum of the gate's own waits, so a slow run reports
            # which one ran out instead of being killed without its output.
            stderr=subprocess.STDOUT, env=env, timeout=420, check=False)
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-15000:]


if __name__ == "__main__":
    asyncio.run(nested_gate(Path(sys.argv[1])))
