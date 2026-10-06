"""Contract tests for the agent workflow loop and LLM step activity.

There is exactly one message standard (the unversioned wire from
``services.llm.protocol``) and one engine. These tests lock the payload
shapes the workflow schedules and the activity's result envelope.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _tool() -> dict:
    definition = {
        "name": "write_todos",
        "description": "Write a todo list",
        "parameters": {
            "type": "object",
            "properties": {"todos": {"type": "array"}},
        },
    }
    return {
        "name": "write_todos",
        "definition": definition,
        "node_type": "writeTodos",
        "version": 1,
        "task_queue": "write-todos",
        "tool_node_id": "todo-1",
        "parameters": {},
        "tool_info": {
            "node_id": "todo-1",
            "node_type": "writeTodos",
            "label": "Todos",
            "parameters": {},
        },
    }


def _payload() -> dict:
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
        "tools": [_tool()],
        "memory_node_id": "",
        "memory_content": "",
        "memory_window_size": 10,
        "max_iterations": 2,
        "thinking_config": None,
        "compaction_threshold": None,
    }


@pytest.fixture
def patched_workflow(monkeypatch):
    import services.temporal.agent_workflow as workflow_module

    temporal_workflow = workflow_module.workflow
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    monkeypatch.setattr(
        temporal_workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id="agent-run-1",
            run_id="run-id-12345678",
        ),
    )
    monkeypatch.setattr(
        workflow_module,
        "get_node_class",
        lambda _node_type: SimpleNamespace(needs_canvas=False),
    )
    return temporal_workflow


class TestWorkflowLoopContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("from_tool", [False, True])
    @pytest.mark.parametrize("category", ["billing", "quota"])
    async def test_owner_action_error_stops_before_another_model_or_compaction(self, monkeypatch, patched_workflow, from_tool, category):
        from services.llm.protocol import LLMError, LLMErrorCategory
        from services.temporal.agent_activities import _as_temporal_llm_error
        from services.temporal.agent_workflow import AgentWorkflow

        error = LLMError("secret provider body", "gemini", category=LLMErrorCategory(category))
        prepared = _payload()
        prepared["compaction_threshold"] = 1
        calls = []
        phases = []
        checkpoints = []
        if from_tool:
            prepared["conversation_key"] = {"workflow_id": "graph-1", "generation": 1, "agent_node_id": "agent-1"}

        async def execute(name, *, args, **_kwargs):
            calls.append(name)
            if name == "agent.prepare_payload":
                return prepared
            if name == "agent.execute_llm_step":
                if from_tool:
                    return {"kind": "tool_calls", "calls": [{"id": "call-1", "name": "write_todos", "args": {}}],
                            "usage": {"input_tokens": 10, "output_tokens": 2}}
                failure = RuntimeError("activity failed")
                failure.cause = _as_temporal_llm_error(error)
                raise failure
            if name == "node.writeTodos.v1":
                return {"success": False, **error.as_node_error().as_dict()}
            if name == "agent.broadcast_progress":
                phases.append(args[0])
                return {}
            if name == "agent.persist_turn":
                checkpoints.append(args[0])
                return {}
            if name in {"agent.skill.clear", "agent.persist_turn"}:
                return {}
            raise AssertionError(f"Unexpected activity: {name}")

        monkeypatch.setattr(patched_workflow, "execute_activity", execute)
        result = await AgentWorkflow().run({"node_id": "agent-1", "execution_id": "root-run-1"})
        assert result["success"] is False
        assert result["requires_user_action"] is True
        assert result["hint"] == error.hint
        assert calls.count("agent.execute_llm_step") == 1
        assert "agent.compact_context" not in calls
        failure = next(p for p in phases if p.get("status") == "error")
        assert failure["hint"] == error.hint
        assert failure["requires_user_action"] is True
        assert failure["retryable"] is False
        assert failure["phase"] == "failed"
        assert "secret" not in str(failure)
        if from_tool:
            assert len(checkpoints) == 1
            assert checkpoints[0]["tool_results"][0]["tool_call_id"] == "call-1"

    @pytest.mark.asyncio
    async def test_llm_step_payload_shape_and_heartbeat(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        llm_calls: list[tuple[dict, dict]] = []

        async def fake_execute_activity(name, *, args, **kwargs):
            if name == "agent.prepare_payload":
                return _payload()
            if name == "agent.broadcast_progress":
                return {"emitted": True}
            if name == "agent.execute_llm_step":
                llm_calls.append((args[0], kwargs))
                return {
                    "kind": "final",
                    "content": "done",
                    "thinking": None,
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                }
            if name == "agent.store_output":
                return {"stored": True}
            if name == "agent.skill.clear":
                return {"cleared": True}
            raise AssertionError(f"Unexpected activity {name}")

        monkeypatch.setattr(
            patched_workflow,
            "execute_activity",
            fake_execute_activity,
        )

        result = await AgentWorkflow().run(
            {"node_id": "agent-1", "execution_id": "root-run-1"}
        )

        assert result["success"] is True
        assert len(llm_calls) == 1
        llm_payload, options = llm_calls[0]
        # One standard: no engine marker, no wire version, no credential.
        assert "llm_engine" not in llm_payload
        assert "message_wire_version" not in llm_payload
        assert "api_key" not in llm_payload
        assert "tool_data" not in llm_payload
        assert llm_payload["workflow_id"] == "graph-1"
        assert llm_payload["iteration"] == 0
        assert llm_payload["max_iterations"] == 2
        assert llm_payload["tools"] == [_tool()["definition"]]
        assert [m["role"] for m in llm_payload["messages"]] == [
            "system",
            "user",
        ]
        assert all("version" not in m for m in llm_payload["messages"])
        assert options["heartbeat_timeout"] == timedelta(minutes=1)
        # Transient provider failures (429/5xx/network) retry indefinitely
        # with backoff; terminal categories fail fast via the
        # ApplicationError non_retryable marker, not this policy.
        policy = options["retry_policy"]
        assert policy.maximum_attempts == 0
        assert policy.initial_interval == timedelta(seconds=5)
        assert policy.maximum_interval == timedelta(minutes=5)

    @pytest.mark.asyncio
    async def test_compaction_failure_is_terminal_for_the_run(
        self,
        monkeypatch,
        patched_workflow,
    ):
        """The pressure-relief valve failing must fail the run loudly.

        Continuing with an ever-growing uncompacted transcript only defers
        the failure to a confusing provider context-overflow error later.
        """
        from services.temporal.agent_workflow import AgentWorkflow

        prepared = _payload()
        prepared["compaction_threshold"] = 5

        async def fake_execute_activity(name, *, args, **_kwargs):
            if name == "agent.prepare_payload":
                return prepared
            if name == "agent.broadcast_progress":
                return {"emitted": True}
            if name == "agent.execute_llm_step":
                return {
                    "kind": "tool_calls",
                    "calls": [
                        {"id": "missing-1", "name": "not_connected", "args": {}}
                    ],
                    "usage": {
                        "input_tokens": 7,
                        "output_tokens": 3,
                        "total_tokens": 10,
                    },
                }
            if name == "agent.compact_context":
                raise RuntimeError("Compaction failed: summarizer down")
            if name == "agent.store_output":
                return {"stored": True}
            if name == "agent.skill.clear":
                return {"cleared": True}
            raise AssertionError(f"Unexpected activity {name}")

        monkeypatch.setattr(
            patched_workflow,
            "execute_activity",
            fake_execute_activity,
        )

        result = await AgentWorkflow().run(
            {"node_id": "agent-1", "execution_id": "root-run-1"}
        )

        assert result["success"] is False
        assert result["error_type"] == "CompactionError"
        assert "Compaction failed" in result["error"]

    @pytest.mark.asyncio
    async def test_compaction_summarizes_live_messages_without_memory(
        self,
        monkeypatch,
        patched_workflow,
    ):
        """Compaction is gated on the token threshold alone.

        The retired design additionally required a memory node's markdown,
        so context-only agents could never compact. The activity now
        receives the live transcript, not memory content.
        """
        from services.temporal.agent_workflow import AgentWorkflow

        prepared = _payload()
        prepared["compaction_threshold"] = 5
        assert prepared["memory_content"] == ""  # no memory node
        llm_turn = 0
        compact_payloads: list[dict] = []

        async def fake_execute_activity(name, *, args, **_kwargs):
            nonlocal llm_turn
            if name == "agent.prepare_payload":
                return prepared
            if name == "agent.broadcast_progress":
                return {"emitted": True}
            if name == "agent.execute_llm_step":
                llm_turn += 1
                if llm_turn == 1:
                    return {
                        "kind": "tool_calls",
                        "calls": [
                            {
                                "id": "missing-1",
                                "name": "not_connected",
                                "args": {},
                            }
                        ],
                        "usage": {
                            "input_tokens": 7,
                            "output_tokens": 3,
                            "total_tokens": 10,
                        },
                    }
                return {
                    "kind": "final",
                    "content": "done",
                    "thinking": None,
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 1,
                        "total_tokens": 6,
                    },
                }
            if name == "agent.compact_context":
                compact_payloads.append(args[0])
                return {
                    "success": True,
                    "summary": "compacted history",
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 2,
                        "total_tokens": 6,
                    },
                }
            if name == "agent.store_output":
                return {"stored": True}
            if name == "agent.skill.clear":
                return {"cleared": True}
            raise AssertionError(f"Unexpected activity {name}")

        monkeypatch.setattr(
            patched_workflow,
            "execute_activity",
            fake_execute_activity,
        )

        result = await AgentWorkflow().run(
            {"node_id": "agent-1", "execution_id": "root-run-1"}
        )

        assert result["success"] is True
        assert len(compact_payloads) == 1
        compact_payload = compact_payloads[0]
        # The live transcript is summarized — not memory markdown.
        assert "messages" in compact_payload
        assert "memory_content" not in compact_payload
        assert compact_payload["provider"] == "openai"
        assert compact_payload["model"] == "test-model"
        # Lifetime usage: turn1 + turn2 + summarizer.
        assert result["result"]["usage"] == {
            "input_tokens": 16,
            "output_tokens": 6,
            "total_tokens": 22,
        }

    def test_tool_result_message_shape(self):
        from services.temporal.agent_workflow import (
            _append_tool_result_message,
        )

        messages: list[dict] = []
        _append_tool_result_message(
            messages,
            content='{"ok": true}',
            tool_call_id="call-1",
            name="write_todos",
        )
        assert "version" not in messages[0]
        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "call-1"
        assert messages[0]["blocks"][0]["type"] == "tool_result"

    def test_tool_turn_reasoning_is_read_from_canonical_message(self):
        from services.llm.protocol import (
            ContentBlock,
            Message,
            message_to_wire,
        )
        from services.temporal.agent_workflow import (
            _native_assistant_thinking,
        )

        wire = message_to_wire(
            Message(
                role="assistant",
                blocks=[
                    ContentBlock(type="reasoning", text="first"),
                    ContentBlock(type="reasoning", text="second"),
                ],
            )
        )
        assert _native_assistant_thinking(wire) == "first\n\nsecond"


class TestLlmStepActivity:
    def test_structured_llm_error_becomes_safe_temporal_failure(self):
        from services.llm.protocol import LLMError, LLMErrorCategory
        from services.temporal.agent_activities import (
            _as_temporal_llm_error,
        )

        provider_error = LLMError(
            message="raw body included secret request details",
            provider="openai",
            category=LLMErrorCategory.RATE_LIMIT,
            retryable=True,
            status_code=429,
            provider_code="capacity",
            request_id="req-123",
            retry_after=1.5,
        )
        temporal_error = _as_temporal_llm_error(provider_error)

        assert "raw body" not in str(temporal_error)
        assert temporal_error.type == "LLMError.rate_limit"
        assert temporal_error.non_retryable is False
        # The provider's Retry-After hint paces the next Temporal attempt.
        assert temporal_error.next_retry_delay == timedelta(seconds=1.5)
        assert temporal_error.details == (
            {
                "provider": "openai",
                "category": "rate_limit",
                "retryable": True,
                "status_code": 429,
                "provider_code": "capacity",
                "request_id": "req-123",
                "retry_after": 1.5,
                "retry_after_raw": None,
            },
        )

    @pytest.mark.asyncio
    async def test_an_unsaved_named_endpoint_fails_the_step_as_not_configured(self, monkeypatch):
        import core.container as container_module
        from services.temporal.agent_activities import _resolve_activity_api_key

        auth = SimpleNamespace(get_api_key=AsyncMock(return_value=None))
        database = SimpleNamespace(get_node_parameters=AsyncMock(return_value={}))
        monkeypatch.setattr(container_module.container, "auth_service", lambda: auth)
        monkeypatch.setattr(container_module.container, "database", lambda: database)

        with pytest.raises(Exception) as raised:
            await _resolve_activity_api_key({"provider": "openai_compatible:gone", "node_id": "agent-1"})

        error = raised.value
        assert error.type == "MissingAgentProviderCredential"
        assert error.non_retryable is True
        # AgentWorkflow shows this type's ``message`` to the user as is.
        assert error.message == "The OpenAI-compatible endpoint 'gone' is not configured. Add it under Credentials."

    @pytest.mark.asyncio
    async def test_buffered_call_heartbeats_while_waiting(
        self,
        monkeypatch,
    ):
        import services.temporal.agent_activities as activity_module

        heartbeat = MagicMock()
        monkeypatch.setattr(activity_module.activity, "heartbeat", heartbeat)

        async def delayed_result():
            await asyncio.sleep(0.02)
            return "done"

        result = await activity_module._await_with_llm_heartbeats(
            delayed_result(),
            detail="waiting",
            interval_seconds=0.001,
        )

        assert result == "done"
        heartbeat.assert_called_with("waiting")

    @pytest.mark.asyncio
    async def test_step_disables_sdk_retries_and_keeps_result_keys(
        self,
        monkeypatch,
    ):
        import core.container as container_module
        import services.agent_runtime as runtime_module
        from services.llm.protocol import (
            LLMResponse,
            Message,
            ToolCall,
            Usage,
            message_to_wire,
        )
        from services.temporal.agent_activities import _execute_native_llm_step

        unifier = object()
        monkeypatch.setattr(
            container_module.container,
            "chat_unifier",
            lambda: unifier,
        )
        call = ToolCall.from_raw(
            id="call-1",
            name="write_todos",
            arguments="{not-json",
        )
        run_step = AsyncMock(
            return_value=LLMResponse(
                tool_calls=[call],
                usage=Usage(input_tokens=5, output_tokens=2),
            )
        )
        monkeypatch.setattr(runtime_module, "run_native_llm_step", run_step)

        result = await _execute_native_llm_step(
            {
                "provider": "openai",
                "model": "test-model",
                "api_key": "secret",
                "messages": [
                    message_to_wire(Message(role="user", content="go"))
                ],
                "tools": [_tool()["definition"]],
                "temperature": 0,
                "max_tokens": 100,
            }
        )

        assert set(result) == {
            "kind",
            "assistant_message",
            "calls",
            "usage",
        }
        assert result["kind"] == "tool_calls"
        assert "version" not in result["assistant_message"]
        assert result["calls"][0]["raw_arguments"] == "{not-json"
        assert result["calls"][0]["parse_error"]
        assert result["usage"]["total_tokens"] == 7

        assert run_step.await_args.args == (unifier,)
        kwargs = run_step.await_args.kwargs
        assert kwargs["sdk_max_retries"] == 0
        assert kwargs["translate_errors"] is False
        assert kwargs["tools"][0].name == "write_todos"
