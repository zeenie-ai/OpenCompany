"""The agent answering a chat run waits in its turn for a held call
(RFC-0007 §4.6), on the real SDK with stub activities: Allow runs the call
once under its claim and the model's next step reads the real result;
Decline gives it the owner's note; Stop completes while the call waits, and
a call allowed while stopped runs only after Resume. Every history
replays."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import uuid4

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.llm.protocol import Message, ToolCall, message_to_wire
from services.temporal.agent_workflow import AgentWorkflow
from services.temporal.approval_workflow import ApprovalWorkflow

QUEUE = "agent-approval-wait-gate"
COUNTS: Counter = Counter()
LLM_INPUTS: list[dict] = []
SENT: list[dict] = []
READS: list[dict] = []
ROWS: dict[str, dict] = {}


def _tool() -> dict:
    return {
        "name": "send",
        "node_type": "fake_send",
        "version": 1,
        "tool_node_id": "send",
        "parameters": {},
        "tool_info": {},
        "definition": {"name": "send", "description": "send", "parameters": {"type": "object", "properties": {}}},
        "activity_policy": {
            "start_to_close_seconds": 60,
            "heartbeat_seconds": 10,
            "task_queue": QUEUE,
            "retry_policy": {
                "initial_interval_seconds": 1,
                "maximum_interval_seconds": 5,
                "backoff_coefficient": 2,
                "maximum_attempts": 3,
                "non_retryable_error_types": [],
            },
        },
    }


@activity.defn(name="agent.prepare_payload")
async def _prepare(context: dict) -> dict:
    return {
        "node_id": "agent",
        "node_type": "aiAgent",
        "provider": "openai",
        "model": "stub",
        "workflow_id": "wf",
        "session_id": "wf",
        "system_message": "Keep working",
        "user_prompt": "Tell Ana",
        "tools": [_tool()],
        "max_iterations": 4,
        "memory_node_id": "",
        "compaction_threshold": None,
        "chat_run_id": "r1",
        "chat_stream": {"run_id": "r1", "session_id": "wf", "workflow_id": "wf"},
    }


@activity.defn(name="agent.execute_llm_step")
async def _llm(payload: dict) -> dict:
    LLM_INPUTS.append(payload)
    if payload["iteration"] >= 1:
        return {
            "kind": "final",
            "content": "done",
            "assistant_message": message_to_wire(Message(role="assistant", content="done")),
            "usage": {"total_tokens": 3},
        }
    calls = [{"id": "call_1", "name": "send", "args": {"message": "On my way."}}]
    return {
        "kind": "tool_calls",
        "calls": calls,
        "assistant_message": message_to_wire(Message(role="assistant", tool_calls=[ToolCall(**call) for call in calls])),
        "usage": {"total_tokens": 3},
    }


@activity.defn(name="node.fake_send.v1")
async def _send(payload: dict) -> dict:
    if payload.get("approval_execution"):
        SENT.append(payload)
        return {"success": True, "result": {"sent_to": payload["tool_args"].get("to")}}
    COUNTS["held"] += 1
    assert payload.get("approval_wait") is True
    return {
        "success": True,
        "approval_id": "ap1",
        "result": {"status": "waiting_for_owner", "approval_id": "ap1", "message": "Waiting for the owner."},
    }


@activity.defn(name="approvals.read")
async def _read(payload: dict) -> dict:
    READS.append(payload)
    row = ROWS[payload["approval_id"]]
    if row["status"] == "pending":
        return {"status": "pending", "revision": row["revision"], "expires_at": "2100-01-01T00:00:00+00:00"}
    return {key: row[key] for key in ("status", "revision", "note") if key in row}


@activity.defn(name="approvals.claim_send")
async def _claim(payload: dict) -> dict:
    COUNTS["claim"] += 1
    assert payload["revision"] == ROWS[payload["approval_id"]]["revision"]
    return {
        "claimed": True,
        "activity": "node.fake_send.v1",
        "context": {
            "node_data": {"message": "On my way!"},
            "tool_args": {"message": "On my way!", "to": "Ana"},
            "approval_execution": {"approval_id": payload["approval_id"], "claim_token": payload["claim_token"]},
        },
    }


@activity.defn(name="approvals.record_outcome")
async def _record(payload: dict) -> dict:
    COUNTS["record"] += 1
    return {"recorded": True, "status": "sent", "outcome": "sent"}


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
    return {"participant_state": "running", "participant_revision": 0}


@activity.defn(name="execution_control.unregister")
async def _unregister(payload: dict) -> dict:
    return {}


ACTIVITIES = [_prepare, _llm, _send, _read, _claim, _record, _broadcast, _store, _clear, _register, _unregister]
WORKFLOWS = [AgentWorkflow, ApprovalWorkflow]


def _input() -> dict:
    return {
        "node_id": "agent",
        "execution_control_version": 1,
        "controller_workflow_id": "controller",
        "generation": 1,
        "execution_control_revision": 0,
        "approval_wait_version": 1,
    }


def _reset() -> None:
    for bucket in (COUNTS, ROWS):
        bucket.clear()
    for items in (LLM_INPUTS, SENT, READS):
        items.clear()
    ROWS["ap1"] = {"status": "pending", "revision": 1}


async def _waiting(environment: WorkflowEnvironment) -> None:
    """Until the wait has read its row once."""
    for _ in range(500):
        if READS:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the turn never waited on the held call")


def _tool_messages() -> list[dict]:
    return [message for message in LLM_INPUTS[-1]["messages"] if message["role"] == "tool"]


async def _replay(history: WorkflowHistory) -> None:
    result = await Replayer(workflows=WORKFLOWS).replay_workflow(WorkflowHistory.from_json(history.workflow_id, history.to_json()))
    assert result.replay_failure is None


async def _allow(environment: WorkflowEnvironment) -> None:
    _reset()
    async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
        handle = await environment.client.start_workflow("AgentWorkflow", _input(), id=f"allow-{uuid4()}", task_queue=QUEUE)
        await _waiting(environment)
        assert COUNTS["claim"] == 0 and len(LLM_INPUTS) == 1
        ROWS["ap1"].update(status="approved", revision=2)
        await environment.client.get_workflow_handle("approval-ap1").signal("nudge")
        result = await asyncio.wait_for(handle.result(), 10)
        history = await handle.fetch_history()
    assert result["success"] is True
    assert COUNTS["held"] == COUNTS["claim"] == COUNTS["record"] == 1 and len(SENT) == 1
    # The call ran as the owner allowed it, under its claim.
    assert SENT[0]["tool_args"] == {"message": "On my way!", "to": "Ana"}
    assert SENT[0]["approval_execution"]["approval_id"] == "ap1"
    # The model's next step reads the real result.
    assert [message["content"] for message in _tool_messages()] == ['{"sent_to": "Ana"}']
    scheduled = {
        event.activity_task_scheduled_event_attributes.activity_id
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    }
    assert {"approval-claim-ap1", "approval-send-ap1", "approval-record-ap1"} <= scheduled
    await _replay(history)


async def _decline(environment: WorkflowEnvironment) -> None:
    _reset()
    async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
        handle = await environment.client.start_workflow("AgentWorkflow", _input(), id=f"decline-{uuid4()}", task_queue=QUEUE)
        await _waiting(environment)
        ROWS["ap1"].update(status="discarded", revision=2, note="Call her instead.")
        await environment.client.get_workflow_handle("approval-ap1").signal("nudge")
        result = await asyncio.wait_for(handle.result(), 10)
        history = await handle.fetch_history()
    assert result["success"] is True and COUNTS["claim"] == 0 and SENT == []
    [message] = _tool_messages()
    assert "The owner declined this; nothing happened. Their note: Call her instead." in message["content"]
    await _replay(history)


async def _stop_while_waiting(environment: WorkflowEnvironment) -> None:
    _reset()
    async with Worker(environment.client, task_queue=QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES, max_cached_workflows=0):
        handle = await environment.client.start_workflow("AgentWorkflow", _input(), id=f"stop-{uuid4()}", task_queue=QUEUE)
        await _waiting(environment)
        stop = {"state": "paused", "revision": 1, "generation": 1}
        assert (await handle.execute_update("set_control_state", stop))["state"] == "paused"
        # Stop completes while the call waits: the wait is no action.
        drained = await asyncio.wait_for(handle.execute_update("wait_for_checkpoint", stop), 10)
        assert drained["checkpoint"] and drained["active_actions"] == 0
        # Allowed while stopped: it runs only after Resume.
        ROWS["ap1"].update(status="approved", revision=2)
        await environment.client.get_workflow_handle("approval-ap1").signal("nudge")
        await asyncio.sleep(0.3)
        assert COUNTS["claim"] == 0 and SENT == []
        await handle.execute_update("set_control_state", {"state": "running", "revision": 2, "generation": 1, "producers_held": False})
        result = await asyncio.wait_for(handle.result(), 10)
        history = await handle.fetch_history()
    assert result["success"] is True and COUNTS["claim"] == 1 and len(SENT) == 1
    await _replay(history)


async def _main() -> None:
    async with await WorkflowEnvironment.start_time_skipping() as environment:
        with environment.auto_time_skipping_disabled():
            await _allow(environment)
            await _decline(environment)
            await _stop_while_waiting(environment)


def test_the_flag_rides_a_generation_from_start_to_its_tool_calls() -> None:
    from services.coding_agent_runtime import freeze_generation_harness
    from services.temporal.agent_workflow import _INHERITED_SCOPE_KEYS
    from services.temporal.workflow import TEMPORAL_ROUTING_INPUT_KEY, TEMPORAL_ROUTING_INPUT_VERSION, _frozen_routing_from_input

    payload = {TEMPORAL_ROUTING_INPUT_KEY: {"version": TEMPORAL_ROUTING_INPUT_VERSION}}
    freeze_generation_harness(payload, {"execution_control_version": 1, "approval_wait_version": 1})
    assert payload["approval_wait_version"] == payload[TEMPORAL_ROUTING_INPUT_KEY]["approval_wait_version"] == 1
    assert _frozen_routing_from_input(payload)["approval_wait_version"] == 1
    # A generation admitted without it keeps today's drafts.
    freeze_generation_harness(payload, {"execution_control_version": 1})
    assert "approval_wait_version" not in payload and "approval_wait_version" not in _frozen_routing_from_input(payload)
    assert "approval_wait_version" in _INHERITED_SCOPE_KEYS


def test_the_turn_reads_a_message_the_owner_sent_instead_of_answering() -> None:
    # RFC-0007 D3: it may ask for something else, so nothing says don't retry.
    from services.temporal.agent_workflow import _owner_answer

    answer = _owner_answer({"status": "discarded", "note": "Call her instead.", "by_message": True})
    assert answer["error"] == "The owner sent a message instead of answering; nothing happened. Their message: Call her instead."
    assert answer["status"] == "declined" and answer["success"] is False


def test_a_turn_waits_for_the_owner_and_runs_the_allowed_call() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "tests.temporal.test_agent_approval_wait_integration"],
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
