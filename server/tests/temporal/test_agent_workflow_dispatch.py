"""Focused contracts for Context-backed Temporal agent orchestration.

The retired ``_thread_inputs`` session-routing tests are gone with the
journal: the plain conversation store keys on
``(workflow_id, generation, agent_node_id)`` and deliberately ignores
session identity, so every firing of an agent continues one conversation.
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _settings(*, enabled: bool = True):
    return SimpleNamespace(
        temporal_agent_workflow_enabled=enabled,
        temporal_per_type_dispatch=True,
        temporal_worker_pool_enabled=False,
    )


def test_admitted_generation_routes_to_child_workflow_but_missing_metadata_stays_activity():
    from services.temporal.workflow import MachinaWorkflow

    instance = MachinaWorkflow()
    with patch("core.config.Settings", side_effect=lambda: _settings()):
        admitted = instance._resolve_dispatch(
            "aiAgent",
            graph_version=2,
            generation=4,
            context_enabled=True,
        )
        missing_generation = instance._resolve_dispatch(
            "aiAgent",
            graph_version=2,
            generation=0,
            context_enabled=True,
        )
        missing_version = instance._resolve_dispatch(
            "aiAgent",
            graph_version=0,
            generation=4,
            context_enabled=True,
        )

    assert admitted == {
        "kind": "child_workflow",
        "name": "AgentWorkflow",
    }
    assert missing_generation["name"] == "AgentWorkflow"
    assert missing_version["name"] == "AgentWorkflow"


@pytest.mark.asyncio
async def test_task_manager_preflight_rejects_invalid_call_then_starts_one_corrected_task(monkeypatch):
    from nodes.tool.task_manager import TaskManagerNode
    from services import agent_team
    from services.llm.protocol import Message, ToolCall, message_to_wire
    from services.plugin import NodeContext
    from services.plugin import edge_walker
    from services.temporal import agent_workflow as module

    task = {"id": "task-1", "team_id": "team-1", "current_attempt": 0}
    service = SimpleNamespace(assign_durable_task=AsyncMock(return_value=task))
    monkeypatch.setattr(agent_team, "get_agent_team_service", lambda: service)
    monkeypatch.setattr(edge_walker, "build_teammate_descriptors", lambda *_: [{
        "node_id": "worker", "node_type": "aiAgent", "delegate_tool_name": "delegate_to_worker", "label": "Worker",
    }])
    node = TaskManagerNode()
    saved = {"label": "Task Manager", "title": "Saved title", "mission": "Saved mission"}
    prepared = {
        "node_id": "lead", "node_type": "aiAgent", "provider": "openrouter", "model": "test-model",
        "workflow_id": "graph", "session_id": "session", "team_id": "team-1",
        "system_message": "Delegate the research", "user_prompt": "Research", "max_iterations": 3,
        "tools": [
            {"name": "task_manager", "node_type": "taskManager", "version": 1,
             "tool_node_id": "task-manager", "parameters": saved, "definition": node.as_tool_schema()},
            {"name": "delegate_to_worker", "node_type": "aiAgent", "version": 1,
             "tool_node_id": "worker", "parameters": {}, "llm_hidden": True},
        ],
    }
    calls = [
        ToolCall.from_raw(id="invalid", name="task_manager", arguments=json.dumps({
            "operation": "assign_task", 'title” :”Research” }NB:IneedproperJSON.{': "x",
        })),
        ToolCall(id="corrected", name="task_manager", args={
            "operation": "assign_task", "title": "Research", "mission": "Find verified sources", "assignee_node_id": "worker",
        }),
    ]
    inputs, preflight_results, queued = [], [], []
    runner = AsyncMock(return_value=SimpleNamespace(id="runner-1"))

    async def execute_activity(name, *, args, **_options):
        payload = args[0]
        if name == "agent.prepare_payload":
            return prepared
        if name == "agent.execute_llm_step":
            inputs.append(deepcopy(payload))
            iteration = payload["iteration"]
            if iteration < len(calls):
                call = calls[iteration]
                return {"kind": "tool_calls", "calls": [{"id": call.id, "name": call.name, "args": call.args}],
                        "assistant_message": message_to_wire(Message(role="assistant", tool_calls=[call]))}
            return {"kind": "final", "content": "Delegated", "assistant_message": message_to_wire(Message(role="assistant", content="Delegated"))}
        if name == "agent.queue_delegation":
            queued.append(payload)
        return {}

    def start_activity(name, *, args, **_options):
        assert name == "node.taskManager.v1"
        payload = args[0]

        async def preflight():
            assert payload["node_data"]["label"] == "Task Manager"
            assert "label" not in payload["tool_args"]
            result = await node.execute_as_tool(
                payload["tool_args"], saved,
                NodeContext.from_legacy(payload["node_id"], "taskManager", payload),
            )
            preflight_results.append(result)
            return result

        return asyncio.create_task(preflight())

    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="run", run_id="run-id"))
    monkeypatch.setattr(module.workflow, "patched", lambda _: False)
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module.workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(module.workflow, "start_activity", start_activity)
    monkeypatch.setattr(module.workflow, "start_child_workflow", runner)
    result = await module.AgentWorkflow()._run_impl({"execution_id": "execution-1"})

    assert result["success"] is True
    assert preflight_results[0]["error_type"] == "ValidationError"
    assert "delegation_request" not in preflight_results[0]
    assert preflight_results[1]["delegation_request"]["team_task_id"] == "task-1"
    service.assign_durable_task.assert_awaited_once()
    assert service.assign_durable_task.await_args.kwargs["title"] == "Research"
    assert service.assign_durable_task.await_args.kwargs["mission"] == "Find verified sources"
    assert len(queued) == 1 and queued[0]["team_task_id"] == "task-1"
    runner.assert_awaited_once()
    assert runner.await_args.args == ("DelegatedTaskWorkflow",)
    assert runner.await_args.kwargs["args"][0]["lifecycle"]["team_task_id"] == "task-1"
    feedback = [message for message in inputs[1]["messages"] if message["role"] == "tool"]
    assert len(feedback) == 1 and feedback[0]["tool_call_id"] == "invalid"
    assert "Extra inputs are not permitted" in feedback[0]["content"]
