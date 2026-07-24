"""Temporal compatibility tests for the native agent LLM cutover."""

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


def _payload(*, native: bool) -> dict:
    value = {
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
    if native:
        value.update({"llm_engine": "native", "message_wire_version": 2})
    else:
        value["api_key"] = "test-key"
    return value


def _emergency_langchain_payload() -> dict:
    value = _payload(native=False)
    value.pop("api_key")
    value.update({"llm_engine": "langchain", "message_wire_version": 1})
    return value


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


class TestEngineSelection:
    def test_new_runs_default_to_native(self, monkeypatch):
        from services.temporal.agent_activities import _configured_llm_engine

        monkeypatch.delenv("AGENT_LLM_ENGINE", raising=False)
        assert _configured_llm_engine() == "native"

    def test_emergency_switch_is_explicit_and_validated(self, monkeypatch):
        from services.temporal.agent_activities import _configured_llm_engine

        monkeypatch.setenv("AGENT_LLM_ENGINE", " LangChain ")
        assert _configured_llm_engine() == "langchain"

        monkeypatch.setenv("AGENT_LLM_ENGINE", "surprise")
        with pytest.raises(ValueError, match="AGENT_LLM_ENGINE"):
            _configured_llm_engine()


class TestWorkflowBranchContract:
    @pytest.mark.asyncio
    async def test_native_history_uses_v2_messages_and_heartbeat(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        llm_calls: list[tuple[dict, dict]] = []

        async def fake_execute_activity(name, *, args, **kwargs):
            if name == "agent.prepare_payload.v1":
                return _payload(native=True)
            if name == "agent.broadcast_progress.v1":
                return {"emitted": True}
            if name == "agent.execute_llm_step.v1":
                llm_calls.append((args[0], kwargs))
                return {
                    "kind": "final",
                    "content": "done",
                    "thinking": None,
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                }
            if name == "agent.store_output.v1":
                return {"stored": True}
            if name == "agent.skill.clear.v1":
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
        assert llm_payload["llm_engine"] == "native"
        assert llm_payload["message_wire_version"] == 2
        assert "api_key" not in llm_payload
        assert "tool_data" not in llm_payload
        assert llm_payload["tools"] == [_tool()["definition"]]
        assert [(m["version"], m["role"]) for m in llm_payload["messages"]] == [
            (2, "system"),
            (2, "user"),
        ]
        assert options["heartbeat_timeout"] == timedelta(minutes=1)
        assert options["retry_policy"].maximum_attempts == 1

    @pytest.mark.asyncio
    async def test_compaction_preserves_cumulative_and_summarizer_usage(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        prepared = _payload(native=True)
        prepared.update(
            {
                "memory_content": "prior history",
                "compaction_threshold": 5,
            }
        )
        llm_turn = 0
        compaction_calls = 0

        async def fake_execute_activity(name, *, args, **_kwargs):
            nonlocal llm_turn, compaction_calls
            if name == "agent.prepare_payload.v1":
                return prepared
            if name == "agent.broadcast_progress.v1":
                return {"emitted": True}
            if name == "agent.execute_llm_step.v1":
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
            if name == "agent.compact_memory.v1":
                compaction_calls += 1
                return {
                    "success": True,
                    "summary": "compacted history",
                    "tokens_before": 10,
                    "tokens_after": 0,
                    "usage": {
                        "input_tokens": 4,
                        "output_tokens": 2,
                        "total_tokens": 6,
                    },
                }
            if name == "agent.store_output.v1":
                return {"stored": True}
            if name == "agent.skill.clear.v1":
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
        assert compaction_calls == 1
        assert result["result"]["usage"] == {
            "input_tokens": 16,
            "output_tokens": 6,
            "total_tokens": 22,
        }

    @pytest.mark.asyncio
    async def test_markerless_compaction_preserves_legacy_payload_and_usage(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        prepared = _payload(native=False)
        prepared.update(
            {
                "memory_content": "prior history",
                "compaction_threshold": 5,
            }
        )
        llm_turn = 0
        compact_payloads = []

        async def fake_execute_activity(name, *, args, **_kwargs):
            nonlocal llm_turn
            if name == "agent.prepare_payload.v1":
                return prepared
            if name == "agent.broadcast_progress.v1":
                return {"emitted": True}
            if name == "agent.execute_llm_step.v1":
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
                            "input_tokens": 3,
                            "output_tokens": 3,
                            "total_tokens": 100,
                            "cache_read_tokens": 50,
                        },
                    }
                return {
                    "kind": "final",
                    "content": "done",
                    "thinking": None,
                    "usage": {
                        "input_tokens": 2,
                        "output_tokens": 1,
                        "total_tokens": 3,
                    },
                }
            if name == "agent.compact_memory.v1":
                compact_payloads.append(args[0])
                return {
                    "success": True,
                    "summary": "legacy compacted history",
                    "tokens_before": 6,
                    "tokens_after": 0,
                    "usage": {
                        "input_tokens": 400,
                        "output_tokens": 100,
                        "total_tokens": 500,
                    },
                }
            if name == "agent.store_output.v1":
                return {"stored": True}
            if name == "agent.skill.clear.v1":
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

        assert list(compact_payloads[0]) == [
            "session_id",
            "node_id",
            "memory_content",
            "provider",
            "api_key",
            "model",
        ]
        assert compact_payloads[0]["api_key"] == "test-key"
        # Historical semantics reset pre-compaction usage and did not include
        # the summarizer call in the final workflow result.
        assert result["result"]["usage"] == {
            "input_tokens": 2,
            "output_tokens": 1,
            "total_tokens": 3,
        }

    @pytest.mark.asyncio
    async def test_markerless_compaction_threshold_ignores_total_and_cache(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        prepared = _payload(native=False)
        prepared.update(
            {
                "memory_content": "prior history",
                "compaction_threshold": 7,
                "max_iterations": 1,
            }
        )

        async def fake_execute_activity(name, *, args, **_kwargs):
            if name == "agent.prepare_payload.v1":
                return prepared
            if name == "agent.broadcast_progress.v1":
                return {"emitted": True}
            if name == "agent.execute_llm_step.v1":
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
                        "input_tokens": 3,
                        "output_tokens": 3,
                        "total_tokens": 100,
                        "cache_read_tokens": 50,
                    },
                }
            if name == "agent.compact_memory.v1":
                raise AssertionError(
                    "markerless threshold must use input + output only"
                )
            if name == "agent.store_output.v1":
                return {"stored": True}
            if name == "agent.skill.clear.v1":
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
        assert result["result"]["usage"]["total_tokens"] == 100

    @pytest.mark.asyncio
    async def test_markerless_error_result_keeps_historical_raw_detail(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        async def fake_execute_activity(name, *, args, **_kwargs):
            if name == "agent.prepare_payload.v1":
                return _payload(native=False)
            if name == "agent.broadcast_progress.v1":
                return {"emitted": True}
            if name == "agent.execute_llm_step.v1":
                failure = RuntimeError("outer activity failure")
                failure.cause = RuntimeError("legacy raw provider detail")
                raise failure
            if name == "agent.skill.clear.v1":
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
        assert result["error"] == (
            "LLM step failed: legacy raw provider detail"
        )

    @pytest.mark.asyncio
    async def test_missing_engine_preserves_legacy_activity_input(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        llm_calls: list[tuple[dict, dict]] = []

        async def fake_execute_activity(name, *, args, **kwargs):
            if name == "agent.prepare_payload.v1":
                return _payload(native=False)
            if name == "agent.broadcast_progress.v1":
                return {"emitted": True}
            if name == "agent.execute_llm_step.v1":
                llm_calls.append((args[0], kwargs))
                return {
                    "kind": "final",
                    "content": "done",
                    "thinking": None,
                    "usage": {},
                }
            if name == "agent.store_output.v1":
                return {"stored": True}
            if name == "agent.skill.clear.v1":
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
        llm_payload, options = llm_calls[0]
        assert "llm_engine" not in llm_payload
        assert "message_wire_version" not in llm_payload
        assert "tools" not in llm_payload
        assert llm_payload["tool_data"] == [_tool()["tool_info"]]
        assert list(llm_payload) == [
            "provider",
            "model",
            "api_key",
            "messages",
            "tool_data",
            "system_message",
            "temperature",
            "max_tokens",
            "thinking_config",
        ]
        assert llm_payload["messages"] == [
            {"type": "system", "data": {"content": "Be useful"}},
            {"type": "human", "data": {"content": "do the work"}},
        ]
        assert "heartbeat_timeout" not in options

    @pytest.mark.asyncio
    async def test_explicit_langchain_marker_keeps_credentials_out_of_history(
        self,
        monkeypatch,
        patched_workflow,
    ):
        from services.temporal.agent_workflow import AgentWorkflow

        prepared_payload = _emergency_langchain_payload()
        llm_calls: list[tuple[dict, dict]] = []

        async def fake_execute_activity(name, *, args, **kwargs):
            if name == "agent.prepare_payload.v1":
                assert "api_key" not in prepared_payload
                return prepared_payload
            if name == "agent.broadcast_progress.v1":
                return {"emitted": True}
            if name == "agent.execute_llm_step.v1":
                llm_calls.append((args[0], kwargs))
                return {
                    "kind": "final",
                    "content": "done",
                    "thinking": None,
                    "usage": {},
                }
            if name == "agent.store_output.v1":
                return {"stored": True}
            if name == "agent.skill.clear.v1":
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
        llm_payload, options = llm_calls[0]
        assert list(llm_payload) == [
            "node_id",
            "provider",
            "model",
            "messages",
            "tool_data",
            "system_message",
            "temperature",
            "max_tokens",
            "thinking_config",
            "llm_engine",
            "message_wire_version",
        ]
        assert llm_payload["llm_engine"] == "langchain"
        assert llm_payload["message_wire_version"] == 1
        assert "api_key" not in llm_payload
        assert llm_payload["tool_data"] == [_tool()["tool_info"]]
        assert options["heartbeat_timeout"] == timedelta(minutes=1)

    def test_tool_results_follow_the_history_pinned_wire_format(self):
        from services.temporal.agent_workflow import (
            _append_tool_result_message,
        )

        native_messages: list[dict] = []
        _append_tool_result_message(
            native_messages,
            llm_engine="native",
            content='{"ok": true}',
            tool_call_id="call-1",
            name="write_todos",
        )
        assert native_messages[0]["version"] == 2
        assert native_messages[0]["role"] == "tool"
        assert native_messages[0]["tool_call_id"] == "call-1"
        assert native_messages[0]["blocks"][0]["type"] == "tool_result"

        legacy_messages: list[dict] = []
        _append_tool_result_message(
            legacy_messages,
            llm_engine="langchain",
            content='{"ok": true}',
            tool_call_id="call-1",
            name="write_todos",
        )
        assert legacy_messages == [
            {
                "type": "tool",
                "data": {
                    "content": '{"ok": true}',
                    "tool_call_id": "call-1",
                    "name": "write_todos",
                },
            }
        ]

    def test_native_tool_turn_reasoning_is_read_from_canonical_message(self):
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


class TestNativeActivityBranch:
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
    async def test_buffered_native_call_heartbeats_while_waiting(
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
    async def test_native_step_disables_sdk_retries_and_keeps_v1_result_keys(
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
        assert result["assistant_message"]["version"] == 2
        assert result["calls"][0]["raw_arguments"] == "{not-json"
        assert result["calls"][0]["parse_error"]
        assert result["usage"]["total_tokens"] == 7

        assert run_step.await_args.args == (unifier,)
        kwargs = run_step.await_args.kwargs
        assert kwargs["sdk_max_retries"] == 0
        assert kwargs["translate_errors"] is False
        assert kwargs["tools"][0].name == "write_todos"
