"""Real SDK checkpoint, restart, rollover and replay gates with stub tools."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowFailureError, WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.llm.protocol import Message, ToolCall, message_to_wire
from services.temporal import agent_workflow as module
from services.temporal.agent_workflow import AgentWorkflow, DelegatedTaskWorkflow


QUEUE = "agent-control-sdk-gate"
COUNTS: Counter = Counter()
STARTED: dict[str, asyncio.Event] = {}
RELEASE: dict[str, asyncio.Event] = {}
LLM_INPUTS: list[dict] = []


def _event(name: str, events: dict[str, asyncio.Event]) -> asyncio.Event:
    return events.setdefault(name, asyncio.Event())


def _tool(name: str, node_type: str = "control_test_tool") -> dict:
    return {
        "name": name, "node_type": node_type, "version": 1,
        "tool_node_id": name, "parameters": {}, "tool_info": {},
        "definition": {"name": name, "description": name, "parameters": {"type": "object", "properties": {}}},
        "activity_policy": {
            "start_to_close_seconds": 60, "heartbeat_seconds": 10,
            "task_queue": QUEUE,
            "retry_policy": {"initial_interval_seconds": 1, "maximum_interval_seconds": 5,
                "backoff_coefficient": 2, "maximum_attempts": 1, "non_retryable_error_types": []},
        },
    }


@activity.defn(name="agent.prepare_payload")
async def _prepare(context: dict) -> dict:
    COUNTS["prepare"] += 1
    mode = context.get("mode", "tools")
    return {
        "node_id": "agent", "node_type": "aiAgent", "provider": "openai",
        "model": "stub", "workflow_id": "graph", "session_id": "session",
        "system_message": ("s" * 450_000 if mode == "capacity" else "Keep working"), "user_prompt": mode,
        "tools": ([_tool("A", "taskManager"), _tool("B", "taskManager")] if mode == "parallel"
                  else [] if mode == "delegate-child" else [_tool("A"), _tool("B")]),
        "max_iterations": 4, "memory_node_id": "", "compaction_threshold": None,
    }


@activity.defn(name="agent.execute_llm_step")
async def _llm(payload: dict) -> dict:
    mode = next(message["content"] for message in payload["messages"] if message["role"] == "user")
    iteration = payload["iteration"]
    COUNTS["llm"] += 1
    LLM_INPUTS.append(payload)
    if mode == "llm-stop" and iteration == 0:
        _event("llm", STARTED).set()
        await _event("llm", RELEASE).wait()
    if mode == "rollover" and iteration == 1:
        _event("llm-after-rollover", STARTED).set()
        await _event("llm-after-rollover", RELEASE).wait()
    if mode == "delegate-child" or iteration >= (2 if mode == "rollover" else 1):
        return {"kind": "final", "content": "done", "thinking": f"thought-{iteration}",
                "assistant_message": message_to_wire(Message(role="assistant", content="done")),
                "usage": {"total_tokens": 3}}
    names = (["A"] if iteration == 0 else ["B"]) if mode == "rollover" else ["A", "B"]
    calls = [{"id": f"{iteration}-{name}", "name": name,
              "args": {"operation": "assign_task"} if mode == "parallel" else {}} for name in names]
    return {"kind": "tool_calls", "calls": calls, "thinking": f"thought-{iteration}",
            "assistant_message": message_to_wire(Message(role="assistant", tool_calls=[ToolCall(**call) for call in calls])),
            "usage": {"total_tokens": 3}}


@activity.defn(name="node.control_test_tool.v1")
async def _tool_activity(payload: dict) -> dict:
    name = payload["node_id"]
    COUNTS[name] += 1
    assert activity.info().heartbeat_timeout == timedelta(seconds=10)
    _event(name, STARTED).set()
    if not any(mode in activity.info().workflow_id for mode in ("rollover", "capacity")):
        await _event(name, RELEASE).wait()
    return {"result": name, **({"operations": [{"type": "add_node"}]} if name == "A" else {})}


@activity.defn(name="node.taskManager.v1")
async def _preflight(payload: dict) -> dict:
    name = payload["node_id"]
    COUNTS[name] += 1
    _event(name, STARTED).set()
    await _event(name, RELEASE).wait()
    return {"status": "queued", "result": name}


@activity.defn(name="agent.refresh_tools")
async def _refresh(payload: dict) -> dict:
    COUNTS["refresh"] += 1
    return {"tools": []}


@activity.defn(name="agent.broadcast_progress")
async def _broadcast(payload: dict) -> dict:
    return {}


@activity.defn(name="agent.store_output")
async def _store(payload: dict) -> dict:
    return {}


@activity.defn(name="agent.skill.clear")
async def _clear(payload: dict) -> dict:
    return {}


@activity.defn(name="execution_control.register")
async def _register(payload: dict) -> dict:
    COUNTS["register"] += 1
    return {"participant_state": "running", "participant_revision": 0}


@activity.defn(name="execution_control.unregister")
async def _unregister(payload: dict) -> dict:
    COUNTS["unregister"] += 1
    return {}


@activity.defn(name="agent.register_task_execution")
async def _register_task(payload: dict) -> dict:
    return {}


@activity.defn(name="agent.acquire_subagent_permit")
async def _acquire(payload: dict) -> dict:
    _event("permit", STARTED).set()
    await _event("permit", RELEASE).wait()
    return {"lease_id": "permit"}


@activity.defn(name="agent.begin_delegation")
async def _begin(payload: dict) -> dict:
    COUNTS["begin"] += 1
    return {}


@activity.defn(name="agent.finish_delegation")
async def _finish(payload: dict) -> dict:
    return {"success": True}


@activity.defn(name="agent.release_subagent_permit")
async def _release_permit(payload: dict) -> dict:
    COUNTS["release-permit"] += 1
    return {}


ACTIVITIES = [_prepare, _llm, _tool_activity, _preflight, _refresh, _broadcast, _store, _clear,
              _register, _unregister, _register_task, _acquire, _begin, _finish, _release_permit]
WORKFLOWS = [AgentWorkflow, DelegatedTaskWorkflow]


def _input(mode: str) -> dict:
    return {"node_id": "agent", "mode": mode, "execution_control_version": 1,
            "controller_workflow_id": "controller", "generation": 1, "execution_control_revision": 0}


def _reset() -> None:
    COUNTS.clear()
    STARTED.clear()
    RELEASE.clear()
    LLM_INPUTS.clear()


async def _replay(history: WorkflowHistory) -> None:
    for event in history.events:
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED:
            scheduled = event.activity_task_scheduled_event_attributes
            if scheduled.activity_type.name == "node.control_test_tool.v1":
                assert scheduled.start_to_close_timeout.ToTimedelta() == timedelta(seconds=60)
                assert scheduled.retry_policy.maximum_attempts == 1
    result = await Replayer(workflows=WORKFLOWS).replay_workflow(
        WorkflowHistory.from_json(history.workflow_id, history.to_json()))
    assert result.replay_failure is None


async def _stop_tools(environment: WorkflowEnvironment, *, mode: str, restart: bool = False) -> None:
    _reset()
    async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
        handle = await environment.client.start_workflow("AgentWorkflow", _input(mode), id=f"{mode}-{uuid4()}", task_queue=QUEUE)
        await asyncio.wait_for(_event("llm" if mode == "llm-stop" else "A", STARTED).wait(), 10)
        if mode == "parallel":
            await asyncio.wait_for(_event("B", STARTED).wait(), 10)
        stop = {"state": "paused", "revision": 1, "generation": 1}
        admission = await handle.execute_update("set_control_state", stop)
        assert admission["state"] == "paused"
        checkpoint = asyncio.create_task(handle.execute_update("wait_for_checkpoint", stop))
        await asyncio.sleep(0.05)
        assert not checkpoint.done()
        if mode == "llm-stop":
            _event("llm", RELEASE).set()
        else:
            _event("A", RELEASE).set()
        if mode == "parallel":
            await asyncio.sleep(0.05)
            assert not checkpoint.done()  # B is independently in flight.
            _event("B", RELEASE).set()
        drained = await asyncio.wait_for(checkpoint, 10)
        assert drained["checkpoint"] and drained["active_actions"] == 0
        assert COUNTS["llm"] == 1 and COUNTS["refresh"] == 0
        assert COUNTS["B"] == (1 if mode == "parallel" else 0)
        await handle.execute_update("set_control_state", {"state": "running", "revision": 0, "generation": 1})
        assert (await handle.query("execution_control_status"))["state"] == "paused"
        if not restart:
            await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": False})
            late = await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": True})
            assert late["producers_held"] is False  # A delayed admission cannot undo release.
            _event("A", RELEASE).set()
            _event("B", RELEASE).set()
            result = await asyncio.wait_for(handle.result(), 10)
            history = await handle.fetch_history()
    if restart:
        async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
            assert (await handle.query("execution_control_status"))["state"] == "paused"
            await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": False})
            late = await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": True})
            assert late["producers_held"] is False
            _event("B", RELEASE).set()
            result = await asyncio.wait_for(handle.result(), 10)
            history = await handle.fetch_history()
    assert result["success"] is True
    assert COUNTS["A"] == COUNTS["B"] == 1 and COUNTS["llm"] == 2
    assert COUNTS["prepare"] == 1
    tool_messages = [message for message in LLM_INPUTS[-1]["messages"] if message["role"] == "tool"]
    assert [message["name"] for message in tool_messages] == ["A", "B"]
    await _replay(history)


async def _permit_checkpoint(environment: WorkflowEnvironment) -> None:
    _reset()
    request = {"lifecycle": {"team_task_id": "task", "root_execution_id": "root"},
               "child_context": _input("delegate-child"), "child_workflow_id": "delegate-" + uuid4().hex}
    async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
        handle = await environment.client.start_workflow("DelegatedTaskWorkflow", request, id="runner-" + uuid4().hex, task_queue=QUEUE)
        await asyncio.wait_for(_event("permit", STARTED).wait(), 10)
        stop = {"state": "paused", "revision": 1, "generation": 1}
        await handle.execute_update("set_control_state", stop)
        drained = await asyncio.wait_for(handle.execute_update("wait_for_checkpoint", stop), 10)
        assert drained["checkpoint"]
        _event("permit", RELEASE).set()
        await asyncio.sleep(0.1)
        assert COUNTS["begin"] == 0
        await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": False})
        assert (await asyncio.wait_for(handle.result(), 10))["success"]
        history = await handle.fetch_history()
    assert COUNTS["register"] == COUNTS["unregister"] == COUNTS["release-permit"] == 1
    await _replay(history)


async def _stop_before_first_workflow_task(environment: WorkflowEnvironment) -> None:
    """An Update delivered before run initialization must fence preparation."""
    _reset()
    handle = await environment.client.start_workflow("AgentWorkflow", _input("tools"), id="cold-stop-" + uuid4().hex, task_queue=QUEUE)
    pending_stop = asyncio.create_task(handle.execute_update("set_control_state", {"state": "paused", "revision": 1, "generation": 1}))
    await asyncio.sleep(0.05)  # Submit control while no worker can execute the run.
    async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
        assert (await asyncio.wait_for(pending_stop, 10))["state"] == "paused"
        await handle.execute_update("wait_for_checkpoint", {"state": "paused", "revision": 1, "generation": 1})
        assert COUNTS["prepare"] == COUNTS["llm"] == COUNTS["A"] == COUNTS["B"] == 0
        _event("A", RELEASE).set()
        _event("B", RELEASE).set()
        await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": False})
        assert (await asyncio.wait_for(handle.result(), 10))["success"]
        history = await handle.fetch_history()
    assert COUNTS["prepare"] == COUNTS["A"] == COUNTS["B"] == 1
    await _replay(history)


async def _rollover(environment: WorkflowEnvironment) -> None:
    _reset()
    original_cap = module._AGENT_HISTORY_SOFT_CAP
    module._AGENT_HISTORY_SOFT_CAP = 1
    try:
        async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
            handle = await environment.client.start_workflow("AgentWorkflow", _input("rollover"), id="rollover-" + uuid4().hex, task_queue=QUEUE)
            await asyncio.wait_for(_event("llm-after-rollover", STARTED).wait(), 10)
            stop = {"state": "paused", "revision": 1, "generation": 1}
            await handle.execute_update("set_control_state", stop)
            checkpoint = asyncio.create_task(handle.execute_update("wait_for_checkpoint", stop))
            _event("llm-after-rollover", RELEASE).set()
            assert (await asyncio.wait_for(checkpoint, 10))["checkpoint"]
            assert COUNTS["prepare"] == COUNTS["A"] == 1 and COUNTS["B"] == 0
            await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": False})
            late = await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": True})
            assert late["producers_held"] is False
            result = await asyncio.wait_for(handle.result(), 15)
            run_id = handle.first_execution_run_id
            histories = []
            while run_id:
                history = await environment.client.get_workflow_handle(handle.id, run_id=run_id).fetch_history()
                histories.append(history)
                next_events = [event for event in history.events if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW]
                run_id = next_events[0].workflow_execution_continued_as_new_event_attributes.new_execution_run_id if next_events else None
        assert len(histories) == 3  # Two actual server-side continue-as-new transitions.
        [final_input] = await environment.client.data_converter.decode(histories[-1].events[0].workflow_execution_started_event_attributes.input.payloads)
        assert final_input["execution_control_revision"] == 2
        assert COUNTS["prepare"] == COUNTS["A"] == COUNTS["B"] == 1
        assert COUNTS["llm"] == 3
        assert result["result"]["usage"]["total_tokens"] == 9
        assert all(f"thought-{index}" in result["result"]["thinking"] for index in range(3))
        assert [message["name"] for message in LLM_INPUTS[-1]["messages"] if message["role"] == "tool"] == ["A", "B"]
        for history in histories:
            await _replay(history)
    finally:
        module._AGENT_HISTORY_SOFT_CAP = original_cap


async def _capacity(environment: WorkflowEnvironment) -> None:
    """A small transcript plus large graph/bindings must not escape the guard."""
    _reset()
    original_cap = module._AGENT_HISTORY_SOFT_CAP
    module._AGENT_HISTORY_SOFT_CAP = 1
    try:
        async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
            handle = await environment.client.start_workflow("AgentWorkflow", {**_input("capacity"), "graph_data": "g" * 1_100_000},
                id="capacity-" + uuid4().hex, task_queue=QUEUE)
            try:
                await asyncio.wait_for(handle.result(), 15)
            except WorkflowFailureError as error:
                assert error.cause.type == "AgentContinuationTooLarge"
            else:
                raise AssertionError("An oversized continuation must fail explicitly")
            history = await handle.fetch_history()
        assert COUNTS["llm"] == COUNTS["prepare"] == COUNTS["A"] == COUNTS["B"] == 1
        assert not any(event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW for event in history.events)
        await _replay(history)
    finally:
        module._AGENT_HISTORY_SOFT_CAP = original_cap


async def _main() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        with environment.auto_time_skipping_disabled():
            await _stop_tools(environment, mode="tools", restart=True)
            await _stop_tools(environment, mode="llm-stop")
            await _stop_tools(environment, mode="parallel")
            await _permit_checkpoint(environment)
            await _stop_before_first_workflow_task(environment)
            await _rollover(environment)
            await _capacity(environment)


def test_real_sdk_agent_stop_resume_restart_rollover_and_replay() -> None:
    result = subprocess.run([sys.executable, "-m", "tests.temporal.test_agent_execution_control_integration"],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=120, check=False)
    assert result.returncode == 0, result.stdout.decode(errors="replace")[-12000:]


if __name__ == "__main__":
    asyncio.run(_main())
