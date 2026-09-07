"""SDK-level replay gate for the agent workflow.

Runs the real workflow worker against Temporal's time-skipping test server
and feeds the recorded event history through ``Replayer``. Provider calls
remain fully stubbed activities, so the gate needs no credentials or
network API access.

There is exactly one message standard: the unversioned wire shape from
``services.llm.protocol.message_to_wire``. Histories recorded before the
single-standard cleanup are deliberately non-replayable (dev decision --
deployments are Reset), so this gate covers every history the current
code can produce.

Two scenarios run under one worker:

1. A plain final answer (no tools) -- the original gate.
2. One ``write_todos`` tool call -- records the tool activity command
   with the retry policy the ``machina-plugin-failure-retries-v1`` patch
   attaches (the plugin's effective policy) and replays that history.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import nodes  # noqa: F401 -- the worker process needs the plugin registry
from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.llm.protocol import Message, message_to_wire
from services.node_registry import get_node_class
from services.temporal.agent_workflow import AgentWorkflow


TASK_QUEUE = "agent-native-replay-gate"

# Scenario knobs read by the stub activities. The worker registers one
# activity per name, so the prepare / LLM stubs consult these instead of
# being redefined per scenario.
_SCENARIO: dict[str, Any] = {"tools": [], "max_iterations": 1}
_LLM_CALLS = {"count": 0}


def _write_todos_tool() -> dict[str, Any]:
    return {
        "name": "write_todos",
        "definition": {
            "name": "write_todos",
            "description": "Write a todo list",
            "parameters": {"type": "object", "properties": {"todos": {"type": "array"}}},
        },
        "node_type": "writeTodos",
        "version": 1,
        "task_queue": TASK_QUEUE,
        "tool_node_id": "todo-1",
        "parameters": {},
        "tool_info": {"node_id": "todo-1", "node_type": "writeTodos", "label": "Todos", "parameters": {}},
    }


def _prepared_payload() -> dict[str, Any]:
    return {
        "node_id": "agent-replay",
        "node_type": "aiAgent",
        "workflow_id": "graph-replay",
        "session_id": "session-replay",
        "provider": "openai",
        "model": "test-model",
        "max_tokens": 100,
        "temperature": 0,
        "system_message": "Be useful",
        "user_prompt": "return done",
        "tools": list(_SCENARIO["tools"]),
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": _SCENARIO["max_iterations"],
        "thinking_config": None,
        "compaction_threshold": None,
    }


@activity.defn(name="agent.prepare_payload")
async def _prepare_payload(context: dict[str, Any]) -> dict[str, Any]:
    return _prepared_payload()


@activity.defn(name="agent.broadcast_progress")
async def _broadcast_progress(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"emitted": True}


@activity.defn(name="agent.execute_llm_step")
async def _execute_llm_step(payload: dict[str, Any]) -> dict[str, Any]:
    # One standard: no engine marker, no wire version, no credential in
    # the activity input, provider-neutral tool definitions only.
    assert "llm_engine" not in payload
    assert "message_wire_version" not in payload
    assert "api_key" not in payload
    assert "tool_data" not in payload
    for message in payload["messages"]:
        assert "version" not in message
        assert message.get("role")
    assert activity.info().heartbeat_timeout == timedelta(minutes=1)

    _LLM_CALLS["count"] += 1
    if _SCENARIO["tools"] and _LLM_CALLS["count"] == 1:
        return {
            "kind": "tool_calls",
            "calls": [{"id": "call-a", "name": "write_todos", "args": {"todos": []}}],
            "usage": {"input_tokens": 2, "output_tokens": 1},
        }

    return {
        "kind": "final",
        "assistant_message": message_to_wire(
            Message(role="assistant", content="done")
        ),
        "content": "done",
        "thinking": None,
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }


@activity.defn(name="node.writeTodos.v1")
async def _write_todos(_context: dict[str, Any]) -> dict[str, Any]:
    return {"todos": []}


@activity.defn(name="agent.store_output")
async def _store_output(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"stored": True}


@activity.defn(name="agent.skill.clear")
async def _clear_skills(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"cleared": True}


_TEST_ACTIVITIES = [
    _prepare_payload,
    _broadcast_progress,
    _execute_llm_step,
    _write_todos,
    _store_output,
    _clear_skills,
]


def _scheduled_activities(history: WorkflowHistory) -> list[Any]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def _replay(history: WorkflowHistory) -> None:
    # JSON round-trip makes this a captured-history gate rather than
    # replaying the live protobuf object in memory.
    replayer = Replayer(workflows=[AgentWorkflow])
    captured = WorkflowHistory.from_json(
        history.workflow_id,
        history.to_json(),
    )
    replay = await replayer.replay_workflow(captured)
    assert replay.replay_failure is None


async def _execute(environment: WorkflowEnvironment) -> tuple[dict[str, Any], WorkflowHistory]:
    handle = await environment.client.start_workflow(
        "AgentWorkflow",
        {"node_id": "agent-replay"},
        id=f"agent-replay-{uuid4()}",
        task_queue=TASK_QUEUE,
    )
    result = await handle.result()
    history = await handle.fetch_history()
    return result, history


async def _run_final_answer_gate(environment: WorkflowEnvironment) -> None:
    _SCENARIO.update(tools=[], max_iterations=1)
    _LLM_CALLS["count"] = 0

    result, history = await _execute(environment)

    assert result["success"] is True
    assert result["result"]["response"] == "done"

    scheduled = _scheduled_activities(history)
    assert [item.activity_type.name for item in scheduled] == [
        "agent.prepare_payload",
        "agent.broadcast_progress",
        "agent.broadcast_progress",
        "agent.execute_llm_step",
        "agent.store_output",
        "agent.skill.clear",
        "agent.broadcast_progress",
    ]

    llm = scheduled[3]
    assert llm.heartbeat_timeout.ToTimedelta() == timedelta(minutes=1)
    [llm_input] = await environment.client.data_converter.decode(
        llm.input.payloads
    )
    assert "llm_engine" not in llm_input
    assert "message_wire_version" not in llm_input
    assert "api_key" not in llm_input
    assert "tool_data" not in llm_input

    completed = next(
        event.activity_task_completed_event_attributes
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
    )
    [prepared] = await environment.client.data_converter.decode(
        completed.result.payloads
    )
    assert "llm_engine" not in prepared
    assert "api_key" not in prepared

    await _replay(history)


async def _run_tool_call_gate(environment: WorkflowEnvironment) -> None:
    """A tool call records the plugin's effective retry policy on the
    tool activity command; the captured history must replay."""
    _SCENARIO.update(tools=[_write_todos_tool()], max_iterations=2)
    _LLM_CALLS["count"] = 0

    result, history = await _execute(environment)

    assert result["success"] is True
    assert result["result"]["response"] == "done"

    scheduled = _scheduled_activities(history)
    tool_commands = [item for item in scheduled if item.activity_type.name == "node.writeTodos.v1"]
    assert len(tool_commands) == 1, [item.activity_type.name for item in scheduled]
    tool_command = tool_commands[0]

    expected = get_node_class("writeTodos").effective_retry_policy()
    assert tool_command.retry_policy.maximum_attempts == expected.maximum_attempts
    assert "NodeUserError" in tool_command.retry_policy.non_retryable_error_types
    assert tool_command.heartbeat_timeout.ToTimedelta() == timedelta(minutes=2)

    await _replay(history)


async def _run_replay_gate() -> None:
    """Execute both scenarios under one worker and replay each history."""

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[AgentWorkflow],
            activities=_TEST_ACTIVITIES,
        ):
            await _run_final_answer_gate(environment)
            await _run_tool_call_gate(environment)


def test_generated_history_executes_and_replays() -> None:
    """Run the SDK gate in a clean process with valid Windows I/O handles."""

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.temporal.test_agent_workflow_replay",
        ],
        cwd=Path(__file__).parents[2],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, "Temporal replay subprocess failed"


if __name__ == "__main__":
    asyncio.run(_run_replay_gate())
