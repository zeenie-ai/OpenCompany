"""AgentWorkflow tool calls: bounded retries and the failure the model sees.

Before this change a tool activity was scheduled with no retry policy at
all (Temporal's default: unlimited attempts), so a raised failure would
loop forever. Under ``machina-plugin-failure-retries-v1`` the tool
plugin's effective policy is attached; with the gate closed the old
command is reproduced. When a tool activity fails, the model must receive
the plugin's failure envelope as JSON, not the SDK wrapper text.
"""

from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest
from temporalio.exceptions import ActivityError, ApplicationError, RetryState

from services.plugin.scaling import RetryPolicy as PluginRetryPolicy
from services.temporal._retry_policies import DEFAULT_ACTIVITY_RETRY
from services.temporal.workflow import PLUGIN_FAILURE_RETRY_PATCH

pytestmark = pytest.mark.unit


def _tool(node_id: str = "todo-1") -> dict:
    definition = {
        "name": "write_todos",
        "description": "Write a todo list",
        "parameters": {"type": "object", "properties": {"todos": {"type": "array"}}},
    }
    return {
        "name": "write_todos",
        "definition": definition,
        "node_type": "writeTodos",
        "version": 1,
        "task_queue": "write-todos",
        "tool_node_id": node_id,
        "parameters": {},
        "tool_info": {"node_id": node_id, "node_type": "writeTodos", "label": "Todos", "parameters": {}},
    }


def _agent_payload(tools: List[dict]) -> dict:
    return {
        "node_id": "agent-1",
        "node_type": "aiAgent",
        "workflow_id": "graph-1",
        "session_id": "session-1",
        "provider": "openai",
        "model": "test-model",
        "max_tokens": 100,
        "temperature": 0,
        "system_message": "Be useful",
        "user_prompt": "do the work",
        "tools": tools,
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": 2,
        "thinking_config": None,
        "compaction_threshold": None,
    }


def _activity_error(cause: BaseException) -> ActivityError:
    err = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker-1",
        activity_type="node.writeTodos.v1",
        activity_id="tool-todo-1-1-1",
        retry_state=RetryState.NON_RETRYABLE_FAILURE,
    )
    err.__cause__ = cause
    return err


@pytest.fixture
def patched_workflow(monkeypatch):
    import services.temporal.agent_workflow as workflow_module

    temporal_workflow = workflow_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(temporal_workflow, "info", lambda: SimpleNamespace(workflow_id="agent-run-1", run_id="run-id-12345678"))
    monkeypatch.setattr(
        workflow_module,
        "get_node_class",
        lambda _node_type: SimpleNamespace(
            needs_canvas=False,
            effective_retry_policy=lambda: PluginRetryPolicy(maximum_attempts=1),
        ),
    )
    return temporal_workflow


def _fake_execute_activity(tool_commands: List[Tuple[str, dict, dict]], llm_messages: List[list], tool_behaviour=None):
    llm_steps = 0

    async def fake(name, *, args, **kwargs):
        nonlocal llm_steps
        if name == "agent.prepare_payload":
            return _agent_payload([_tool("todo-1")])
        if name == "agent.broadcast_progress":
            return {"emitted": True}
        if name == "agent.execute_llm_step":
            llm_steps += 1
            llm_messages.append(list(args[0].get("messages") or []))
            if llm_steps == 1:
                return {
                    "kind": "tool_calls",
                    "calls": [{"id": "call-a", "name": "write_todos", "args": {"todos": []}}],
                    "usage": {},
                }
            return {"kind": "final", "content": "done", "usage": {}}
        if name == "node.writeTodos.v1":
            tool_commands.append((kwargs.get("activity_id"), args[0], kwargs))
            if tool_behaviour is not None:
                return tool_behaviour()
            return {"success": True, "todos": []}
        if name == "agent.store_output":
            return {"stored": True}
        if name == "agent.skill.clear":
            return {"cleared": True}
        raise AssertionError(f"Unexpected activity {name}")

    return fake


async def _run_agent(patched_workflow, tool_behaviour=None):
    from services.temporal.agent_workflow import AgentWorkflow

    tool_commands: List[Tuple[str, dict, dict]] = []
    llm_messages: List[list] = []
    patched_workflow.execute_activity = _fake_execute_activity(tool_commands, llm_messages, tool_behaviour)
    result = await AgentWorkflow().run({"node_id": "agent-1", "execution_id": "root-run-1"})
    return result, tool_commands, llm_messages


def _tool_message_content(llm_messages: List[list]) -> str:
    """The tool result the second LLM step received."""
    assert len(llm_messages) >= 2, "the agent must call the LLM again after the tool"
    for message in llm_messages[1]:
        if message.get("role") == "tool" or message.get("tool_call_id"):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    raise AssertionError(f"no tool message in {llm_messages[1]!r}")


class TestToolRetryPolicy:
    def test_patch_marker_follows_the_established_naming(self):
        assert PLUGIN_FAILURE_RETRY_PATCH.startswith("machina-")

    async def test_tool_call_carries_the_plugin_effective_policy(self, patched_workflow):
        result, tool_commands, _ = await _run_agent(patched_workflow)

        assert result["success"] is True
        (_, _, kwargs), = tool_commands
        assert kwargs["retry_policy"].maximum_attempts == 1
        assert "OutputValidationError" in kwargs["retry_policy"].non_retryable_error_types

    async def test_unpatched_history_issues_the_old_command_without_a_policy(self, patched_workflow):
        """Determinism guard: pre-patch tool calls were scheduled with no
        retry policy, and the closed gate must reproduce that command."""
        patched_workflow.patched = lambda _pid: False
        _, tool_commands, _ = await _run_agent(patched_workflow)
        (_, _, kwargs), = tool_commands
        assert "retry_policy" not in kwargs

    async def test_pseudo_tool_without_a_node_class_gets_the_agent_default(self, patched_workflow, monkeypatch):
        import services.temporal.agent_workflow as workflow_module

        monkeypatch.setattr(workflow_module, "get_node_class", lambda _node_type: None)
        assert workflow_module._tool_retry_policy("_builtin_skill") is DEFAULT_ACTIVITY_RETRY

    def test_preflight_start_activity_is_gated_the_same_way(self):
        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow.run)
        assert 'preflight_kwargs["retry_policy"] = _tool_retry_policy("taskManager")' in source
        assert source.count("if workflow.patched(PLUGIN_FAILURE_RETRY_PATCH):") == 2, (
            "both the taskManager preflight start_activity and the tool execute_activity must be gated"
        )


class TestToolFailureReachesTheModel:
    async def test_failure_envelope_is_rebuilt_from_the_cause(self, patched_workflow):
        envelope = {
            "success": False,
            "error": 'bad "path"',
            "error_type": "NodeUserError",
            "retryable": False,
            "node_id": "todo-1",
        }

        def _raise():
            raise _activity_error(ApplicationError('bad "path"', envelope, type="NodeUserError", non_retryable=True))

        result, _, llm_messages = await _run_agent(patched_workflow, tool_behaviour=_raise)

        assert result["success"] is True, "the model sees the error and continues"
        content = json.loads(_tool_message_content(llm_messages))
        assert content["error"] == 'bad "path"'
        assert content["error_type"] == "NodeUserError"
        assert content["success"] is False

    async def test_cause_less_exception_still_yields_valid_json(self, patched_workflow):
        def _raise():
            raise RuntimeError('quote " inside')

        _, _, llm_messages = await _run_agent(patched_workflow, tool_behaviour=_raise)
        content = json.loads(_tool_message_content(llm_messages))
        assert content["error_type"] == "RuntimeError"
        assert 'quote " inside' in content["error"]

    async def test_wrapper_text_never_reaches_the_model(self, patched_workflow):
        def _raise():
            raise _activity_error(ApplicationError("credential expired", type="NodeUserError", non_retryable=True))

        _, _, llm_messages = await _run_agent(patched_workflow, tool_behaviour=_raise)
        content = _tool_message_content(llm_messages)
        assert "Activity task failed" not in content
        assert "credential expired" in content
