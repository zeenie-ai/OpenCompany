"""SDK-level replay gate for the agent workflow.

Runs the real workflow worker against Temporal's time-skipping test server
and feeds the recorded event history through ``Replayer``. Provider calls
remain fully stubbed activities, so the gate needs no credentials or
network API access.

The prepared payload is a chat run's agent: it names the run, where its text
streams, and an image the owner attached, so the replay covers the opening
user message's image block.

There is exactly one message standard: the unversioned wire shape from
``services.llm.protocol.message_to_wire``. Histories recorded before the
single-standard cleanup are deliberately non-replayable (dev decision —
deployments are Reset), so this gate covers every history the current
code can produce.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from temporalio import activity
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.llm.protocol import Message, message_to_wire
from services.temporal.agent_workflow import AgentWorkflow


TASK_QUEUE = "agent-native-replay-gate"

CHAT_STREAM = {
    "run_id": "r_replay",
    "session_id": "graph-replay",
    "workflow_id": "graph-replay",
    "reply_message_id": "a_r_replay",
}
#: An image the owner attached to the message the run answers (a FileRef).
ATTACHED_IMAGE = {
    "kind": "image",
    "path": "uploads/receipt.png",
    "workflow_id": "graph-replay",
    "filename": "receipt.png",
    "mime_type": "image/png",
    "size_bytes": 2048,
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
        "tools": [],
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": 1,
        "thinking_config": None,
        "compaction_threshold": None,
        "chat_run_id": "r_replay",
        "chat_stream": dict(CHAT_STREAM),
        "user_images": [dict(ATTACHED_IMAGE)],
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

    return {
        "kind": "final",
        "assistant_message": message_to_wire(
            Message(role="assistant", content="done")
        ),
        "content": "done",
        "thinking": None,
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }


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
    _store_output,
    _clear_skills,
]


def _scheduled_activities(history: WorkflowHistory) -> list[Any]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


async def _run_replay_gate() -> None:
    """Execute a run and replay its serialized history."""

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[AgentWorkflow],
            activities=_TEST_ACTIVITIES,
        ):
            handle = await environment.client.start_workflow(
                "AgentWorkflow",
                {"node_id": "agent-replay"},
                id=f"agent-replay-{uuid4()}",
                task_queue=TASK_QUEUE,
            )
            result = await handle.result()
            history = await handle.fetch_history()

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
        # The step knows the run (Stop) and where its text streams.
        assert llm_input["chat_run_id"] == "r_replay"
        assert llm_input["chat_stream"] == CHAT_STREAM
        # The owner's message carries the attached image as a ref, never bytes.
        [opening] = [message for message in llm_input["messages"] if message["role"] == "user"]
        assert opening["content"] == "return done"
        [image] = [block for block in opening["blocks"] if block["type"] == "image"]
        assert image["source"] == {"kind": "file_ref", "ref": ATTACHED_IMAGE, "detail": "auto"}

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

        # JSON round-trip makes this a captured-history gate rather than
        # replaying the live protobuf object in memory.
        replayer = Replayer(workflows=[AgentWorkflow])
        captured = WorkflowHistory.from_json(
            history.workflow_id,
            history.to_json(),
        )
        replay = await replayer.replay_workflow(captured)
        assert replay.replay_failure is None


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
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, "Temporal replay subprocess failed"


if __name__ == "__main__":
    asyncio.run(_run_replay_gate())
