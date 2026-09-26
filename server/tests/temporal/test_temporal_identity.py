"""Focused replay-safe identity tests for Temporal graph and agent commands."""

from __future__ import annotations

import asyncio
import inspect
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _tool(node_id: str, *, name: str = "write_todos", label: str = "Todos") -> dict:
    return {
        "name": name,
        "definition": {
            "name": name,
            "description": "Write a todo list",
            "parameters": {"type": "object", "properties": {}},
        },
        "node_type": "writeTodos",
        "version": 1,
        "task_queue": "write-todos",
        "tool_node_id": node_id,
        "parameters": {},
        "tool_info": {
            "node_id": node_id,
            "node_type": "writeTodos",
            "label": label,
            "parameters": {},
        },
    }


def _agent_payload(tools: list[dict]) -> dict:
    return {
        "node_id": "agent-1",
        "node_type": "aiAgent",
        "workflow_id": "graph-1",
        "session_id": "session-1",
        "provider": "test",
        "model": "test-model",
        "api_key": "test-key",
        "max_tokens": 100,
        "temperature": 0,
        "system_message": "",
        "user_prompt": "do the work",
        "tools": tools,
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": 2,
        "thinking_config": None,
        "compaction_threshold": None,
    }


@pytest.fixture
def patched_workflow(monkeypatch):
    from temporalio import workflow as temporal_workflow

    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(workflow_id="root-run-1", run_id="run-id-12345678"),
    )
    return temporal_workflow


class TestIdentityHelpers:

    def test_root_child_id_uses_run_and_canvas_node(self):
        from services.temporal.workflow import _agent_child_workflow_id

        assert _agent_child_workflow_id("root-a", "agent-1") == "root-a-agent-agent-1"
        assert _agent_child_workflow_id("root-a", "agent-1") != _agent_child_workflow_id("root-a", "agent-2")
        assert _agent_child_workflow_id("root-a", "agent-1") != _agent_child_workflow_id("root-b", "agent-1")

    def test_same_tool_calls_have_distinct_command_ids(self):
        from services.temporal.agent_workflow import (
            _delegation_child_id,
            _refresh_tools_activity_id,
            _tool_activity_id,
        )

        assert _tool_activity_id("tool-1", 0, 0) == "tool-tool-1-1-1"
        assert _tool_activity_id("tool-1", 0, 1) == "tool-tool-1-1-2"
        assert _delegation_child_id("agent-run", "agent-2", 0, 1) == "agent-run-delegate-agent-2-1-2"
        assert _refresh_tools_activity_id("builder-1", 2, 1) == "refresh-tools-builder-1-3-2"

    def test_duplicate_visible_name_error_is_deterministic_and_actionable(self):
        from services.temporal.agent_workflow import _duplicate_visible_tool_name_error

        message = _duplicate_visible_tool_name_error(
            [
                _tool("todo-b", label="Second"),
                _tool("todo-a", label="First"),
                _tool("search-1", name="search", label="Search"),
            ]
        )

        assert message is not None
        assert "'write_todos': First (todo-a), Second (todo-b)" in message
        assert "unique Tool Name" in message
        assert _duplicate_visible_tool_name_error([_tool("todo-a"), _tool("search-1", name="search")]) is None



class TestRootChildIdentity:
    @pytest.mark.asyncio
    async def test_same_label_agents_start_with_distinct_child_ids(self, monkeypatch, patched_workflow):
        from services.temporal.workflow import MachinaWorkflow

        child_ids: list[str] = []
        child_contexts: list[dict] = []

        async def fake_start_child_workflow(_name, *, id, args, **_kwargs):
            child_ids.append(id)
            child_contexts.append(args[0])
            handle = asyncio.get_running_loop().create_future()
            handle.set_result({"success": True, "result": {"response": id}})
            return handle

        monkeypatch.setattr(patched_workflow, "start_child_workflow", fake_start_child_workflow)
        monkeypatch.setattr(
            MachinaWorkflow,
            "_resolve_dispatch",
            lambda _self, _node_type, **_kwargs: {
                "kind": "child_workflow",
                "name": "AgentWorkflow",
                "queue": None,
            },
        )

        result = await MachinaWorkflow().run(
            {
                "nodes": [
                    {"id": "agent-a", "type": "aiAgent", "data": {"label": "Worker"}},
                    {"id": "agent-b", "type": "aiAgent", "data": {"label": "Worker"}},
                ],
                "edges": [],
                "workflow_id": "graph-1",
                "workflow_slug": "Graph",
                "execution_id": "root-run-1",
                "user_id": "account-41",
            }
        )

        assert result["success"] is True
        assert child_ids == ["root-run-1-agent-agent-a", "root-run-1-agent-agent-b"]
        assert [context["user_id"] for context in child_contexts] == [
            "account-41",
            "account-41",
        ]



class TestAgentCallIdentity:
    @pytest.mark.asyncio
    async def test_two_same_tool_calls_get_unique_ids_and_metadata(self, monkeypatch, patched_workflow):
        import services.temporal.agent_workflow as agent_module
        from services.temporal.agent_workflow import AgentWorkflow

        tool_commands: list[tuple[str, dict]] = []
        llm_steps = 0

        async def fake_execute_activity(name, *, args, **kwargs):
            nonlocal llm_steps
            if name == "agent.prepare_payload":
                return _agent_payload([_tool("todo-1")])
            if name == "agent.broadcast_progress":
                return {"emitted": True}
            if name == "agent.execute_llm_step":
                llm_steps += 1
                if llm_steps == 1:
                    return {
                        "kind": "tool_calls",
                        "calls": [
                            {"id": "call-a", "name": "write_todos", "args": {"todos": []}},
                            {"id": "call-b", "name": "write_todos", "args": {"todos": []}},
                        ],
                        "usage": {},
                    }
                return {"kind": "final", "content": "done", "usage": {}}
            if name == "node.writeTodos.v1":
                tool_commands.append((kwargs["activity_id"], args[0]))
                return {"success": True, "todos": []}
            if name == "agent.store_output":
                return {"stored": True}
            if name == "agent.skill.clear":
                return {"cleared": True}
            raise AssertionError(f"Unexpected activity {name}")

        monkeypatch.setattr(patched_workflow, "execute_activity", fake_execute_activity)
        monkeypatch.setattr(agent_module, "get_node_class", lambda _node_type: SimpleNamespace(needs_canvas=False))

        result = await AgentWorkflow().run({"node_id": "agent-1", "execution_id": "root-run-1"})

        assert result["success"] is True
        assert [command_id for command_id, _payload in tool_commands] == [
            "tool-todo-1-1-1",
            "tool-todo-1-1-2",
        ]
        assert [payload["tool_call_id"] for _command_id, payload in tool_commands] == ["call-a", "call-b"]
        assert [payload["tool_call_index"] for _command_id, payload in tool_commands] == [1, 2]
        assert all(payload["invoking_agent_node_id"] == "agent-1" for _command_id, payload in tool_commands)
        assert all(payload["agent_iteration"] == 1 for _command_id, payload in tool_commands)


    @pytest.mark.asyncio
    async def test_duplicate_initial_names_fail_before_llm(self, monkeypatch, patched_workflow):
        from services.temporal.agent_workflow import AgentWorkflow

        activities: list[tuple[str, dict]] = []

        async def fake_execute_activity(name, *, args, **_kwargs):
            activities.append((name, args[0]))
            if name == "agent.prepare_payload":
                return _agent_payload([_tool("todo-a", label="First"), _tool("todo-b", label="Second")])
            if name == "agent.broadcast_progress":
                return {"emitted": True}
            raise AssertionError(f"Duplicate names must fail before {name}")

        monkeypatch.setattr(patched_workflow, "execute_activity", fake_execute_activity)

        result = await AgentWorkflow().run({"node_id": "agent-1", "execution_id": "root-run-1"})

        assert result["success"] is False
        assert result["error_type"] == "DuplicateToolNameError"
        assert "todo-a" in result["error"] and "todo-b" in result["error"]
        assert [entry["node_id"] for entry in result["conflicts"]["write_todos"]] == [
            "todo-a",
            "todo-b",
        ]
        assert [name for name, _payload in activities] == [
            "agent.prepare_payload",
            "agent.broadcast_progress",
        ]
        error_phase = activities[-1][1]
        assert error_phase["status"] == "error"
        assert error_phase["phase"] == "failed"

    @pytest.mark.asyncio
    async def test_hot_refresh_rejects_duplicate_without_overwriting_index(self, monkeypatch, patched_workflow):
        import services.temporal.agent_workflow as agent_module
        from services.temporal.agent_workflow import AgentWorkflow

        llm_payloads: list[dict] = []
        refresh_commands: list[tuple[str, dict]] = []
        phases: list[dict] = []

        async def fake_execute_activity(name, *, args, **kwargs):
            if name == "agent.prepare_payload":
                return _agent_payload([_tool("todo-1", label="Original")])
            if name == "agent.broadcast_progress":
                phases.append(args[0])
                return {"emitted": True}
            if name == "agent.execute_llm_step":
                llm_payloads.append(args[0])
                if len(llm_payloads) == 1:
                    return {
                        "kind": "tool_calls",
                        "calls": [{"id": "call-a", "name": "write_todos", "args": {"todos": []}}],
                        "usage": {},
                    }
                return {"kind": "final", "content": "done", "usage": {}}
            if name == "node.writeTodos.v1":
                return {"success": True, "operations": [{"op": "add_node"}]}
            if name == "agent.refresh_tools":
                refresh_commands.append((kwargs["activity_id"], args[0]))
                return {"tools": [_tool("todo-2", label="Conflicting")]}
            if name == "agent.store_output":
                return {"stored": True}
            if name == "agent.skill.clear":
                return {"cleared": True}
            raise AssertionError(f"Unexpected activity {name}")

        monkeypatch.setattr(patched_workflow, "execute_activity", fake_execute_activity)
        monkeypatch.setattr(agent_module, "get_node_class", lambda _node_type: SimpleNamespace(needs_canvas=False))

        result = await AgentWorkflow().run({"node_id": "agent-1", "execution_id": "root-run-1"})

        assert result["success"] is True
        assert refresh_commands == [
            (
                "refresh-tools-todo-1-1-1",
                {
                    "operations": [{"op": "add_node"}],
                    "agent_node_type": "aiAgent",
                    "invoking_agent_node_id": "agent-1",
                    "agent_iteration": 1,
                    "tool_call_index": 1,
                    "tool_call_id": "call-a",
                },
            )
        ]
        # The conflicting tool never enters the next LLM binding surface.
        assert len(llm_payloads[1]["tools"]) == 1
        tool_messages = [m for m in llm_payloads[1]["messages"] if m.get("role") == "tool"]
        assert len(tool_messages) == 1
        assert "DuplicateToolNameError" in tool_messages[0]["content"]
        assert any(phase.get("phase") == "tool_error" for phase in phases)


class TestLegacyActivityTransportIdentity:
    @pytest.mark.asyncio
    async def test_active_websocket_transport_serializes_run_and_call_identity(self):
        import aiohttp

        from services.temporal.activities import NodeExecutionActivities

        class FakeWebSocket:
            def __init__(self):
                self.sent = None

            async def send_json(self, message):
                self.sent = message

            async def receive(self):
                return SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {
                            "request_id": self.sent["request_id"],
                            "success": True,
                        }
                    ),
                )

        class WebSocketContext:
            def __init__(self, websocket):
                self.websocket = websocket

            async def __aenter__(self):
                return self.websocket

            async def __aexit__(self, *_args):
                return False

        class FakeSession:
            def __init__(self, websocket):
                self.websocket = websocket

            def ws_connect(self, *_args, **_kwargs):
                return WebSocketContext(self.websocket)

        websocket = FakeWebSocket()
        activities = NodeExecutionActivities.__new__(NodeExecutionActivities)
        activities.session = FakeSession(websocket)
        activities.ws_url = "ws://example.invalid/ws/internal"
        activities.ws_headers = {}  # the /ws/internal token, not under test here

        response = await activities._execute_via_websocket(
            {
                "node_id": "todo-1",
                "node_type": "writeTodos",
                "workflow_id": "workflow-1",
                "execution_id": "run-1",
                "invoking_agent_node_id": "agent-1",
                "agent_iteration": 3,
                "tool_call_index": 2,
                "tool_call_id": "provider-call-9",
            }
        )

        assert response["success"] is True
        assert websocket.sent["execution_id"] == "run-1"
        assert websocket.sent["workflow_id"] == "workflow-1"
        assert websocket.sent["invoking_agent_node_id"] == "agent-1"
        assert websocket.sent["agent_iteration"] == 3
        assert websocket.sent["tool_call_index"] == 2
        assert websocket.sent["tool_call_id"] == "provider-call-9"
