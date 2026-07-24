"""SDK-level replay gates for the native agent-engine cutover.

Unlike the fast unit tests in ``test_native_llm_cutover.py``, these tests run
the real workflow worker against Temporal's time-skipping test server and feed
the recorded event history through ``Replayer``.  Provider calls remain fully
stubbed activities, so the gate needs no credentials or network API access.
"""

from __future__ import annotations

import asyncio
import base64
import subprocess
import sys
import zlib
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
FIXTURES_DIR = Path(__file__).with_name("fixtures")


def _prepared_payload(*, engine: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    }
    if engine == "native":
        payload.update(
            {
                "llm_engine": "native",
                "message_wire_version": 2,
            }
        )
    elif engine == "langchain":
        payload.update(
            {
                "llm_engine": "langchain",
                "message_wire_version": 1,
            }
        )
    elif engine == "legacy":
        # Pre-cutover histories included the resolved secret in prepare
        # output. The compatibility branch must continue accepting that
        # recorded shape, while native histories must never persist it.
        payload["api_key"] = "legacy-recorded-test-key"
    else:
        raise AssertionError(f"Unexpected test engine {engine!r}")
    return payload


@activity.defn(name="agent.prepare_payload.v1")
async def _prepare_payload(context: dict[str, Any]) -> dict[str, Any]:
    return _prepared_payload(engine=str(context["test_engine"]))


@activity.defn(name="agent.broadcast_progress.v1")
async def _broadcast_progress(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"emitted": True}


@activity.defn(name="agent.execute_llm_step.v1")
async def _execute_llm_step(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("llm_engine") == "native":
        assert payload["message_wire_version"] == 2
        assert "api_key" not in payload
        assert "tool_data" not in payload
        assert all(message["version"] == 2 for message in payload["messages"])
        assert activity.info().heartbeat_timeout == timedelta(minutes=1)
        assistant = message_to_wire(
            Message(role="assistant", content="done")
        )
    elif payload.get("llm_engine") == "langchain":
        # New runs under the emergency switch use the legacy adapter without
        # persisting a credential in either activity result or input.
        assert payload["message_wire_version"] == 1
        assert payload["node_id"] == "agent-replay"
        assert "api_key" not in payload
        assert "tools" not in payload
        assert "tool_data" in payload
        assert all("type" in message for message in payload["messages"])
        assert activity.info().heartbeat_timeout == timedelta(minutes=1)
        assistant = {"type": "ai", "data": {"content": "done"}}
    else:
        # This is the pre-cutover contract: no engine marker, LangChain's
        # canonical message dictionaries, and no heartbeat timeout.
        assert "llm_engine" not in payload
        assert "message_wire_version" not in payload
        assert payload["api_key"] == "legacy-recorded-test-key"
        assert "tools" not in payload
        assert "tool_data" in payload
        assert all("type" in message for message in payload["messages"])
        assert activity.info().heartbeat_timeout is None
        assistant = {"type": "ai", "data": {"content": "done"}}

    return {
        "kind": "final",
        "assistant_message": assistant,
        "content": "done",
        "thinking": None,
        "usage": {"input_tokens": 2, "output_tokens": 1},
    }


@activity.defn(name="agent.store_output.v1")
async def _store_output(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"stored": True}


@activity.defn(name="agent.skill.clear.v1")
async def _clear_skills(_payload: dict[str, Any]) -> dict[str, Any]:
    return {"cleared": True}


_TEST_ACTIVITIES = [
    _prepare_payload,
    _broadcast_progress,
    _execute_llm_step,
    _store_output,
    _clear_skills,
]


async def _run_and_capture(
    environment: WorkflowEnvironment,
    *,
    engine: str,
) -> tuple[dict[str, Any], WorkflowHistory]:
    workflow_id = f"agent-{engine}-replay-{uuid4()}"
    handle = await environment.client.start_workflow(
        "AgentWorkflow",
        {"node_id": "agent-replay", "test_engine": engine},
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    result = await handle.result()
    return result, await handle.fetch_history()


def _scheduled_activities(history: WorkflowHistory) -> list[Any]:
    return [
        event.activity_task_scheduled_event_attributes
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    ]


def _load_captured_history(name: str, workflow_id: str) -> WorkflowHistory:
    """Load a frozen Temporal JSON history stored as compressed base64."""

    encoded = (FIXTURES_DIR / name).read_bytes()
    history_json = zlib.decompress(base64.b64decode(encoded)).decode("utf-8")
    return WorkflowHistory.from_json(workflow_id, history_json)


async def _run_replay_gate() -> None:
    """Execute all engine branches and replay their serialized histories."""

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[AgentWorkflow],
            activities=_TEST_ACTIVITIES,
        ):
            native_result, native_history = await _run_and_capture(
                environment,
                engine="native",
            )
            emergency_result, emergency_history = await _run_and_capture(
                environment,
                engine="langchain",
            )
            legacy_result, legacy_history = await _run_and_capture(
                environment,
                engine="legacy",
            )

        assert native_result["success"] is True
        assert native_result["result"]["response"] == "done"
        assert emergency_result["success"] is True
        assert emergency_result["result"]["response"] == "done"
        assert legacy_result["success"] is True
        assert legacy_result["result"]["response"] == "done"

        expected_order = [
            "agent.prepare_payload.v1",
            "agent.broadcast_progress.v1",
            "agent.broadcast_progress.v1",
            "agent.execute_llm_step.v1",
            "agent.store_output.v1",
            "agent.skill.clear.v1",
            "agent.broadcast_progress.v1",
        ]
        native_scheduled = _scheduled_activities(native_history)
        emergency_scheduled = _scheduled_activities(emergency_history)
        legacy_scheduled = _scheduled_activities(legacy_history)
        assert [
            item.activity_type.name for item in native_scheduled
        ] == expected_order
        assert [
            item.activity_type.name for item in emergency_scheduled
        ] == expected_order
        assert [
            item.activity_type.name for item in legacy_scheduled
        ] == expected_order

        native_llm = native_scheduled[3]
        emergency_llm = emergency_scheduled[3]
        legacy_llm = legacy_scheduled[3]
        assert (
            native_llm.heartbeat_timeout.ToTimedelta()
            == timedelta(minutes=1)
        )
        # Temporal records the omitted optional duration as an explicit zero
        # value in history; activity.info() still correctly exposes None.
        assert emergency_llm.heartbeat_timeout.ToTimedelta() == timedelta(
            minutes=1
        )
        assert legacy_llm.heartbeat_timeout.ToTimedelta() == timedelta(0)

        [native_input] = await environment.client.data_converter.decode(
            native_llm.input.payloads
        )
        [emergency_input] = await environment.client.data_converter.decode(
            emergency_llm.input.payloads
        )
        [legacy_input] = await environment.client.data_converter.decode(
            legacy_llm.input.payloads
        )
        assert native_input["llm_engine"] == "native"
        assert native_input["message_wire_version"] == 2
        assert "api_key" not in native_input
        assert "tool_data" not in native_input
        assert emergency_input["llm_engine"] == "langchain"
        assert emergency_input["message_wire_version"] == 1
        assert "api_key" not in emergency_input
        assert "tool_data" in emergency_input
        assert "llm_engine" not in legacy_input
        assert "message_wire_version" not in legacy_input
        assert "tool_data" in legacy_input
        assert legacy_input["api_key"] == "legacy-recorded-test-key"

        prepare_results = []
        for history in (native_history, emergency_history, legacy_history):
            completed = next(
                event.activity_task_completed_event_attributes
                for event in history.events
                if event.event_type
                == EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
            )
            [prepared] = await environment.client.data_converter.decode(
                completed.result.payloads
            )
            prepare_results.append(prepared)

        native_prepared, emergency_prepared, legacy_prepared = prepare_results
        assert native_prepared["llm_engine"] == "native"
        assert "api_key" not in native_prepared
        assert emergency_prepared["llm_engine"] == "langchain"
        assert "api_key" not in emergency_prepared
        assert "llm_engine" not in legacy_prepared
        assert legacy_prepared["api_key"] == "legacy-recorded-test-key"

        replayer = Replayer(workflows=[AgentWorkflow])
        live_histories = (
            native_history,
            emergency_history,
            legacy_history,
        )
        for history in live_histories:
            # JSON round-trip makes this a captured-history gate rather
            # than replaying the live protobuf object in memory.
            captured = WorkflowHistory.from_json(
                history.workflow_id,
                history.to_json(),
            )
            replay = await replayer.replay_workflow(captured)
            assert replay.replay_failure is None

        # This fixture is frozen rather than generated from the workflow
        # implementation under test, protecting already-recorded histories
        # whose prepare result had no engine/version marker.
        frozen_legacy = _load_captured_history(
            "agent_legacy_pre_native_history.json.zlib.b64",
            "agent-legacy-pre-native-captured-v1",
        )
        replay = await replayer.replay_workflow(frozen_legacy)
        assert replay.replay_failure is None


def test_all_cutover_history_shapes_execute_and_replay() -> None:
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
