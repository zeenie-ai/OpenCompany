"""Real Temporal durability/replay gates using both the packaged Bun Pi SDK
and an independent protocol peer. Model/tool leaves stay deterministic while
the coordinator, SDK process, scoped transport and control Updates run fully.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
import subprocess
import sys
import os
import tempfile
from uuid import uuid4

from temporalio import activity, workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.llm.protocol import Message, ToolCall, message_to_wire
from services.temporal.pi_protocol import DRIVER_QUEUE, canonical_digest, tool_operation_id
from services.temporal.pi_workflow import PiCodingAgentWorkflow
from services.temporal.agent_workflow import AgentWorkflow

QUEUE = "pi-coordinator-integration"
COUNTS = Counter()
STARTED = asyncio.Event()
RELEASE = asyncio.Event()
DRIVERS = Counter()
FAULTS = Counter()
CHECKPOINT_STARTED = asyncio.Event()
CHECKPOINT_RELEASE = asyncio.Event()
PARK_RETIRE_STARTED = asyncio.Event()
PARK_RETIRE_RELEASE = asyncio.Event()
PARKED_CHILD = {}


async def _hold_parked_child(payload, result):
    if (payload["scope"]["node_id"] == "child" and
            "delegate-stop" in activity.info().workflow_id and result.get("status") == "parked"):
        # Hold the Activity result, and therefore the only driver slot, until
        # the child's admitted model receipt is durable. The coordinator must
        # encounter the still-live parked driver during turn completion.
        info = activity.info()
        PARKED_CHILD.update(workflow_id=info.workflow_id, run_id=info.workflow_run_id)
        PARK_RETIRE_STARTED.set()
        await PARK_RETIRE_RELEASE.wait()
    return result


@workflow.defn(sandboxed=False, name="PiCodingAgentWorkflow")
class ObservedPiCodingAgentWorkflow(PiCodingAgentWorkflow):
    @workflow.run
    async def run(self, context: dict | None = None) -> dict:
        return await super().run(context)

    @workflow.query
    def receipt_checkpoint_status(self):
        # Queries add no history commands: replay below uses the production
        # class. Observe the ledger without changing its parked epoch fence.
        return {"checkpointing": self._pi_checkpointing,
            "model_receipt_completed": (self._pi.operations.get("turn:0:model") or {}).get("status") == "completed",
            "driver_active": self._pi_driver is not None and not self._pi_driver.done()}


async def _wait_for_child_receipt_checkpoint(client):
    child = client.get_workflow_handle(PARKED_CHILD["workflow_id"], run_id=PARKED_CHILD["run_id"])
    while True:
        status = await child.query("receipt_checkpoint_status")
        if status["checkpointing"] and status["model_receipt_completed"] and status["driver_active"]:
            return
        await asyncio.sleep(0.01)


def _binding(name, node_type="pi_test_tool", node_id=None):
    return {"name": name, "node_type": node_type, "version": 1, "tool_node_id": node_id or name,
        "parameters": {}, "tool_info": {}, "definition": {"name": name, "description": name,
            "parameters": {"type": "object", "properties": {}}},
        "activity_policy": {"start_to_close_seconds": 60, "heartbeat_seconds": 10, "task_queue": QUEUE,
            "retry_policy": {"initial_interval_seconds": 1, "maximum_interval_seconds": 5,
                "backoff_coefficient": 2, "maximum_attempts": 1, "non_retryable_error_types": []}}}


@activity.defn(name="agent.prepare_payload")
async def _prepare(context):
    COUNTS["prepare"] += 1
    node = context["node_id"]
    mode = context.get("mode", "child")
    tools = ([_binding("delegate_to_child", "coding_agent", "child")] if mode.startswith("delegate") else
             [_binding("A"), _binding("T", "taskManager")] if mode == "mixed" else
             [_binding("delegate_to_child", "coding_agent", "child")] if mode == "reject-delegate" else
             [_binding("T", "taskManager")] if mode == "reject-task" else
             [_binding("T", "taskManager"), _binding("B")] if mode == "task-wait" else
             [] if mode == "child" else [_binding("A"), _binding("B")])
    if mode == "invalid-calls":
        tools = [_binding("A"), _binding("T", "taskManager"), _binding("delegate_to_child", "coding_agent", "child")]
        tools[0]["definition"]["parameters"] = {"type": "object", "properties": {
            "value": {"type": "integer", "minimum": 0}}, "required": ["value"]}
    return {"node_id": node, "node_type": "coding_agent", "provider": "openai", "model": "stub",
        "workflow_id": "graph", "session_id": "session", "system_message": "s" * 300_000 if mode.startswith("large") else "Keep working",
        "user_prompt": mode, "tools": tools, "max_iterations": 1 if mode == "exhausted" else 3, "memory_node_id": "",
        "compaction_threshold": None, "pi_harness_version": 1}


@activity.defn(name="agent.execute_llm_step")
async def _llm(payload):
    node = payload["node_id"]
    COUNTS[f"llm:{node}"] += 1
    mode = next(message["content"] for message in payload["messages"] if message["role"] == "user")
    if payload["iteration"] == 0 and (mode == "model-stop" or node == "child" and "delegate-stop" in activity.info().workflow_id):
        STARTED.set()
        await RELEASE.wait()
    tools = payload["tools"]
    if (payload["iteration"] == 0 or mode == "rollover" and payload["iteration"] == 1) and tools:
        calls = [{"id": f"call-{index}", "name": tool["name"],
            "args": ({"task": ""} if mode == "reject-delegate" else {"task": "do child work"}) if tool["name"].startswith("delegate_to_") else
                {"operation": "wait_for_task" if mode == "task-wait" else "assign_task"} if tool["name"] == "T" else {}}
            for index, tool in enumerate(tools)]
        if mode == "invalid-calls":
            calls = [{"id": "unknown", "name": "missing_tool", "args": {}},
                {"id": "malformed", "name": "A", "args": {}, "raw_arguments": "{",
                    "parse_error": "Invalid JSON tool arguments"},
                {"id": "negative", "name": "A", "args": {"value": -1}},
                {"id": "malformed-delegate", "name": "delegate_to_child", "args": ["invalid"]},
                {"id": "malformed-task", "name": "T", "args": ["invalid"]}]
        return {"kind": "tool_calls", "calls": calls, "assistant_message": message_to_wire(
            Message(role="assistant", tool_calls=[ToolCall(**call) for call in calls],
                provider_state={"opaque": "p" * 300_000} if mode.startswith("large") else {})), "usage": {"total_tokens": 3}}
    return {"kind": "final", "content": "done", "assistant_message": message_to_wire(
        Message(role="assistant", content="done", provider_state={"signature": "exact"})), "usage": {"total_tokens": 3}}


@activity.defn(name="node.pi_test_tool.v1")
async def _tool(payload):
    COUNTS[payload["node_id"]] += 1
    if payload["node_id"] == "A" and "pi-stop-" in activity.info().workflow_id:
        STARTED.set()
        await RELEASE.wait()
    large = "pi-large-" in activity.info().workflow_id and (payload["node_id"] == "A" or
        "pi-large-overflow-" in activity.info().workflow_id)
    return {"result": "x" * 300_000 if large else payload["node_id"]}


@activity.defn(name="node.taskManager.v1")
async def _task(payload):
    COUNTS["T"] += 1
    if (payload.get("tool_args") or {}).get("operation") == "wait_for_task":
        STARTED.set()
        await RELEASE.wait()
    return {"status": "queued", "result": "recorded task"}


@activity.defn(name="agent.broadcast_progress")
async def _broadcast(payload):
    if payload.get("phase") == "failed":
        COUNTS["failed_phase"] += 1
    return {}


@activity.defn(name="agent.store_output")
async def _store(payload):
    return {}


@activity.defn(name="agent.skill.clear")
async def _clear(payload):
    COUNTS["clear"] += 1
    return {}


class ProtocolPeer:
    def __init__(self, client):
        self.client = client

    @activity.defn(name="coding.pi_drive_turn")
    async def drive(self, payload):
        DRIVERS["active"] += 1
        DRIVERS["max"] = max(DRIVERS["max"], DRIVERS["active"])
        DRIVERS[f"started:{payload['scope']['node_id']}"] += 1
        handle = self.client.get_workflow_handle(activity.info().workflow_id,
            run_id=activity.info().workflow_run_id)
        try:
            admission = await handle.execute_update("register_driver", {"turn": payload["turn"]})
            epoch = admission["epoch"]
            base = {key: payload[key] for key in ("scope", "turn", "context_digest", "config_digest", "binding_digest")}

            async def operation(identity, kind, fields):
                await handle.execute_update("submit_operation", {"id": identity, "kind": kind,
                    "payload": {**base, "epoch": epoch, **fields}})
                if "fault-submit" in activity.info().workflow_id and activity.info().attempt == 1:
                    raise ApplicationError("Lost submit acknowledgement", type="PiTransportFailure")
                while True:
                    status = await handle.query("operation_status", {"id": identity, "epoch": epoch})
                    if status["status"] == "park":
                        return None
                    if status["status"] == "completed":
                        if "fault-result" in activity.info().workflow_id and activity.info().attempt == 1:
                            raise ApplicationError("Lost result response", type="PiTransportFailure")
                        if "fault-leaf" in activity.info().workflow_id and activity.info().attempt == 1 and kind == "tool":
                            raise ApplicationError("Driver died after completed leaf", type="PiTransportFailure")
                        return status["result"]
                    activity.heartbeat()
                    await asyncio.sleep(0.01)

            model_id = f"turn:{payload['turn']}:model"
            step = await operation(model_id, "model", {"purpose": "assistant"})
            if step is None:
                return await _hold_parked_child(payload, {"status": "parked"})
            await handle.execute_update("assistant_ready", {"turn": payload["turn"], "epoch": epoch,
                "context_digest": payload["context_digest"], "binding_digest": payload["binding_digest"],
                "calls": [{"call_index": index, "call_id": call["id"],
                    "receipt_only": "reject-" in activity.info().workflow_id}
                    for index, call in enumerate(step.get("calls") or [])]})
            operations = [model_id]
            results = []
            for index, call in enumerate(step.get("calls") or []):
                identity = tool_operation_id(payload["turn"], index, call)
                result = await operation(identity, "tool", {"call_index": index, "call_id": call["id"]})
                if result is None:
                    return await _hold_parked_child(payload, {"status": "parked"})
                operations.append(identity)
                results.append(result)
            projection = [*payload["messages"], step["assistant_message"], *results]
            return {"status": "completed", "epoch": epoch, "snapshot": {"pi_blob": "fixture"},
                "consumed_operations": operations, "projection_digest": canonical_digest(projection),
                "scope": payload["scope"], "turn": payload["turn"], "config_digest": payload["config_digest"],
                "binding_digest": payload["binding_digest"]}
        finally:
            DRIVERS["active"] -= 1


class LostResponseClient:
    """Drop a delivered Temporal response and force actual SDK reconstruction."""

    def __init__(self, client, mode):
        self.client, self.mode = client, mode

    def get_workflow_handle(self, *args, **kwargs):
        return LostResponseHandle(self.client.get_workflow_handle(*args, **kwargs), self.mode)


class LostResponseHandle:
    def __init__(self, handle, mode):
        self.handle, self.mode = handle, mode

    async def execute_update(self, name, *args, **kwargs):
        result = await self.handle.execute_update(name, *args, **kwargs)
        key = self.handle.id + ":" + self.mode
        if name == "submit_operation" and self.mode == "fault-submit" and not FAULTS[key]:
            FAULTS[key] += 1
            raise ApplicationError("Lost delivered submit response", type="PiTransportFailure")
        return result

    async def query(self, name, payload):
        result = await self.handle.query(name, payload)
        key = self.handle.id + ":" + self.mode
        if result.get("status") == "completed" and not FAULTS[key] and (
                self.mode == "fault-result" or self.mode == "fault-leaf" and ":tool:" in payload.get("id", "")):
            FAULTS[key] += 1
            raise ApplicationError("Lost completed operation response", type="PiTransportFailure")
        return result


async def _scenario(environment, mode, *, actual=False):
    print(f"Pi scenario {mode}: {'packaged SDK' if actual else 'protocol peer'}", flush=True)
    COUNTS.clear()
    DRIVERS.clear()
    STARTED.clear()
    RELEASE.clear()
    CHECKPOINT_STARTED.clear()
    CHECKPOINT_RELEASE.clear()
    PARK_RETIRE_STARTED.clear()
    PARK_RETIRE_RELEASE.clear()
    PARKED_CHILD.clear()
    workflows = [ObservedPiCodingAgentWorkflow, AgentWorkflow]
    if actual:
        from services.temporal.pi_activities import PiDriverActivities
        class RetiringPiDriverActivities(PiDriverActivities):
            @activity.defn(name="coding.pi_drive_turn")
            async def drive_turn(self, payload):
                return await _hold_parked_child(payload, await super().drive_turn(payload))
        client = LostResponseClient(environment.client, mode) if mode.startswith("fault-") else environment.client
        peer = RetiringPiDriverActivities(client).drive_turn
    else:
        peer = ProtocolPeer(environment.client).drive
    from services.temporal.pi_activities import PiControlActivities, pi_put_blob
    async with Worker(environment.client, task_queue=QUEUE, workflows=workflows,
            activities=[_prepare, _llm, _tool, _task, _broadcast, _store, _clear, pi_put_blob,
                *PiControlActivities(environment.client).activities()], max_cached_workflows=0), \
            Worker(environment.client, task_queue=DRIVER_QUEUE, activities=[peer],
                max_concurrent_activities=1):
        handle = await environment.client.start_workflow("PiCodingAgentWorkflow", {"node_id": "agent",
            "node_type": "coding_agent", "pi_harness_version": 1, "mode": mode,
            "pi_local_control_version": 1, "pi_local_control_revision": 0},
            id=f"pi-{mode}-{uuid4().hex}", task_queue=QUEUE)
        if mode in {"stop", "model-stop", "delegate-stop", "task-wait"}:
            checkpoint = None
            try:
                try:
                    await asyncio.wait_for(STARTED.wait(), 30)
                except TimeoutError:
                    print(f"Pi start timeout: {(await handle.query('execution_control_status'))}", flush=True)
                    print(f"Pi workflow status: {(await handle.describe()).status}", flush=True)
                    raise
                stop = {"state": "paused", "revision": 1}
                if mode == "task-wait":
                    description = await handle.describe()
                    assert all(item.activity_type.name != "coding.pi_drive_turn"
                        for item in description.raw_description.pending_activities), "Durable wait must release the driver slot"
                await handle.execute_update("set_control_state", stop)
                checkpoint = asyncio.create_task(handle.execute_update("wait_for_checkpoint", stop))
                if mode == "delegate-stop":
                    await asyncio.wait_for(PARK_RETIRE_STARTED.wait(), 15)
                    assert COUNTS["llm:child"] == 1
                    if not actual:
                        assert DRIVERS["active"] == 1, "The parked child must still occupy the sole driver slot"
                    RELEASE.set()
                    await asyncio.wait_for(_wait_for_child_receipt_checkpoint(environment.client), 15)
                    assert not checkpoint.done(), "Stop must await the still-live parked driver"
                    paused_effects = {name: COUNTS[name] for name in ("llm:agent", "llm:child", "A", "B", "T")}
                    assert paused_effects == {"llm:agent": 1, "llm:child": 1, "A": 0, "B": 0, "T": 0}
                    PARK_RETIRE_RELEASE.set()
                else:
                    RELEASE.set()
                stopped = await asyncio.wait_for(checkpoint, 15)
                assert stopped["checkpoint"] and stopped["pi_driver_checkpoint"]
                assert (await handle.query("operation_status", {"epoch": stopped["pi_driver_epoch"] - 1}))["status"] == "park"
                if not actual:
                    assert DRIVERS["active"] == 0
                await asyncio.sleep(0.1)
                assert COUNTS["A"] == (1 if mode == "stop" else 0) and COUNTS["B"] == 0
                if mode == "delegate-stop":
                    stopped_effects = {name: COUNTS[name] for name in paused_effects}
                    assert stopped_effects == paused_effects, (
                        "Receipt reconstruction must admit no new model or tool effects while paused",
                        {"before": paused_effects, "after": stopped_effects, "all_counters": dict(COUNTS)})
                    assert COUNTS["llm:child"] == 1
                await handle.execute_update("set_control_state", {"state": "running", "revision": 2})
            finally:
                RELEASE.set()
                PARK_RETIRE_RELEASE.set()
                if checkpoint is not None and not checkpoint.done():
                    checkpoint.cancel()
                    await asyncio.gather(checkpoint, return_exceptions=True)
        elif mode == "checkpoint-stop":
            await asyncio.wait_for(CHECKPOINT_STARTED.wait(), 15)
            stop = {"state": "paused", "revision": 1}
            await handle.execute_update("set_control_state", stop)
            checkpoint = asyncio.create_task(handle.execute_update("wait_for_checkpoint", stop))
            await asyncio.sleep(0.1)
            assert not checkpoint.done(), "Stop must await the interrupted snapshot acceptance"
            CHECKPOINT_RELEASE.set()
            stopped = await asyncio.wait_for(checkpoint, 15)
            assert stopped["checkpoint"] and stopped["pi_driver_checkpoint"]
            assert COUNTS["llm:agent"] == COUNTS["A"] == COUNTS["B"] == 1
            await handle.execute_update("set_control_state", {"state": "running", "revision": 2})
        result = await asyncio.wait_for(handle.result(), 25)
        histories = []
        child_histories = []
        run_id = handle.first_execution_run_id
        while run_id:
            history = await environment.client.get_workflow_handle(handle.id, run_id=run_id).fetch_history()
            histories.append(history)
            run_id = next((event.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
                for event in history.events if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW), None)
        if mode.startswith("delegate"):
            for history in histories:
                for event in history.events:
                    if event.event_type == EventType.EVENT_TYPE_CHILD_WORKFLOW_EXECUTION_STARTED:
                        child = event.child_workflow_execution_started_event_attributes.workflow_execution
                        child_histories.append(await environment.client.get_workflow_handle(
                            child.workflow_id, run_id=child.run_id).fetch_history())
            assert child_histories, "Replay must include the delegated child's checkpoint history"
    if mode == "large-overflow":
        assert result["success"] is False and result["error_type"] == "AgentContinuationTooLarge", result
        assert COUNTS["clear"] == COUNTS["failed_phase"] == COUNTS["llm:agent"] == 1
        assert COUNTS["A"] == COUNTS["B"] == 1
    elif mode == "exhausted":
        assert result["success"] is False and result["error_type"] == "PiIterationExhausted"
        assert result["result"] == {"iterations": 1, "usage": {"total_tokens": 3}}
        assert COUNTS["clear"] == COUNTS["failed_phase"] == COUNTS["llm:agent"] == 1
        assert COUNTS["A"] == COUNTS["B"] == 1
    else:
        assert result["success"], result
        assert COUNTS["llm:agent"] == (3 if mode == "rollover" else 2)
    if not actual:
        assert DRIVERS["max"] == 1 and DRIVERS["active"] == 0
    if mode in {"stop", "model-stop", "large", "snapshot-retry", "checkpoint-stop"} or mode.startswith("fault-"):
        assert COUNTS["A"] == COUNTS["B"] == 1
        assert result["result"]["usage"]["total_tokens"] == 6
    elif mode.startswith("delegate"):
        assert COUNTS["llm:child"] == 1
        if not actual:
            assert DRIVERS["started:child"] >= 1
    elif mode == "mixed":
        assert COUNTS["A"] == COUNTS["T"] == 1
    elif mode == "reject-delegate":
        assert COUNTS["llm:child"] == 0
    elif mode == "reject-task":
        assert COUNTS["T"] == 0
    elif mode == "invalid-calls":
        assert COUNTS["A"] == COUNTS["B"] == COUNTS["T"] == COUNTS["llm:child"] == 0
        assert result["result"]["usage"]["total_tokens"] == 6
    elif mode == "task-wait":
        assert COUNTS["T"] == COUNTS["B"] == 1 and COUNTS["A"] == 0
        assert result["result"]["usage"]["total_tokens"] == 6
    elif mode == "rollover":
        assert len(histories) == 3 and COUNTS["prepare"] == 1
        assert COUNTS["A"] == COUNTS["B"] == 2
        assert result["result"]["usage"]["total_tokens"] == 9
        for history in histories[1:]:
            opening = history.events[0].workflow_execution_started_event_attributes
            [carried] = await environment.client.data_converter.decode(opening.input.payloads)
            assert carried["_pi_checkpoint"]["config_digest"]
            assert carried["_agent_resume"]["prepared_payload"]["pi_harness_version"] == 1
            assert carried["pi_local_control_revision"] == 0
    for history in [*histories, *child_histories]:
        replay = await Replayer(workflows=[PiCodingAgentWorkflow, AgentWorkflow]).replay_workflow(
            WorkflowHistory.from_json(history.workflow_id, history.to_json()))
        assert replay.replay_failure is None


async def _main():
    from services import pi_blobs
    blobs = {}
    async def put(scope, kind, value):
        if kind == "snapshot" and "checkpoint-stop" in scope["workflow_id"] and FAULTS[scope["workflow_id"]] == 0:
            FAULTS[scope["workflow_id"]] += 1
            CHECKPOINT_STARTED.set()
            await CHECKPOINT_RELEASE.wait()
        if kind == "snapshot" and "snapshot-retry" in scope["workflow_id"] and FAULTS[scope["workflow_id"]] == 0:
            FAULTS[scope["workflow_id"]] += 1
            from sqlalchemy.exc import OperationalError
            raise OperationalError("checkpoint write", {}, Exception("interrupted write"))
        identity = canonical_digest({"scope": scope, "kind": kind, "value": value})
        blobs[identity] = (scope, value)
        return {"pi_blob": identity, "scope_digest": canonical_digest(scope), "kind": kind, "digest": canonical_digest(value)}
    async def get(scope, reference):
        stored_scope, value = blobs[reference["pi_blob"]]
        assert stored_scope == scope
        return value
    pi_blobs.put_blob = put
    pi_blobs.get_blob = get
    # The time-skipping server is unavailable on Windows. An explicitly
    # supplied cached CLI exercises the same workflow and replay assertions.
    cli = os.environ.get("TEMPORAL_TEST_CLI")
    environment = (await WorkflowEnvironment.start_local(dev_server_existing_path=cli)
                   if cli else await WorkflowEnvironment.start_time_skipping())
    async with environment:
        with environment.auto_time_skipping_disabled():
            focused = os.environ.get("PI_TEST_ACTUAL_SCENARIO")
            if focused:
                from services.temporal import agent_workflow
                previous = agent_workflow._AGENT_HISTORY_SOFT_CAP
                if focused == "large-overflow":
                    agent_workflow._AGENT_HISTORY_SOFT_CAP = 1
                try:
                    await _scenario(environment, focused, actual=True)
                finally:
                    agent_workflow._AGENT_HISTORY_SOFT_CAP = previous
                return
            await _scenario(environment, "stop")
            await _scenario(environment, "delegate")
            for mode in ("model-stop", "mixed", "reject-delegate", "reject-task", "fault-submit", "fault-result", "fault-leaf", "delegate-stop", "exhausted", "task-wait"):
                await _scenario(environment, mode)
            from services.temporal import agent_workflow
            previous_cap = agent_workflow._AGENT_HISTORY_SOFT_CAP
            agent_workflow._AGENT_HISTORY_SOFT_CAP = 1
            try:
                await _scenario(environment, "rollover")
            finally:
                agent_workflow._AGENT_HISTORY_SOFT_CAP = previous_cap
            if os.environ.get("PI_TEST_PROTOCOL_ONLY") == "1":
                return
            from services.pi_runtime import resolve_pi_host
            print(f"Real Pi host: {resolve_pi_host()}", flush=True)
            await _scenario(environment, "stop", actual=True)
            await _scenario(environment, "delegate", actual=True)
            await _scenario(environment, "model-stop", actual=True)
            await _scenario(environment, "large", actual=True)
            await _scenario(environment, "snapshot-retry", actual=True)
            await _scenario(environment, "delegate-stop", actual=True)
            await _scenario(environment, "checkpoint-stop", actual=True)
            await _scenario(environment, "invalid-calls", actual=True)
            await _scenario(environment, "exhausted", actual=True)
            await _scenario(environment, "task-wait", actual=True)
            for mode in ("fault-submit", "fault-result", "fault-leaf"):
                await _scenario(environment, mode, actual=True)
            agent_workflow._AGENT_HISTORY_SOFT_CAP = 1
            try:
                await _scenario(environment, "rollover", actual=True)
                await _scenario(environment, "large-overflow", actual=True)
            finally:
                agent_workflow._AGENT_HISTORY_SOFT_CAP = previous_cap


def test_stop_resume_and_nested_pi_with_one_driver_slot_replay():
    workspace = Path(__file__).parents[3]
    directory = (nullcontext(str(workspace)) if os.environ.get("PI_TEST_PROTOCOL_ONLY") == "1" else
        tempfile.TemporaryDirectory(prefix=".pi-workflow-test-", dir=workspace))
    with directory as data_dir:
        env = {**os.environ, "DEBUG": "false", "DATA_DIR": data_dir}
        result = subprocess.run([sys.executable, "-m", "tests.temporal.test_pi_workflow_integration"],
            cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=240, check=False, env=env)
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-12000:]


if __name__ == "__main__":
    asyncio.run(_main())
