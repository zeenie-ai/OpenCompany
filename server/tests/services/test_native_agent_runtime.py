from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from services.agent_runtime import (
    AgentToolSpec,
    run_native_agent_loop,
    run_native_llm_step,
)
from services.llm.protocol import (
    LLMError,
    LLMErrorCategory,
    LLMResponse,
    Message,
    ToolCall,
    ToolDef,
    Usage,
)
from services.plugin import NodeUserError


class _Args(BaseModel):
    value: int


class _FakeUnifier:
    def __init__(self, responses: list[LLMResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class _Auth:
    async def get_api_key(self, _name):
        return None


class _Database:
    async def get_user_settings(self, *_args, **_kwargs):
        return None


def test_in_process_compaction_usage_joins_execution_wide_total():
    from services.ai import _accumulate_compaction_usage

    # Base usage already represents multiple native loop iterations.
    final_state = {
        "usage": Usage(
            input_tokens=30,
            output_tokens=6,
            total_tokens=36,
            cache_read_tokens=4,
        )
    }
    _accumulate_compaction_usage(
        final_state,
        {
            "success": True,
            "usage": {
                "input_tokens": 8,
                "output_tokens": 2,
                "total_tokens": 10,
                "reasoning_tokens": 1,
            },
        },
    )

    assert final_state["usage"] == Usage(
        input_tokens=38,
        output_tokens=8,
        total_tokens=46,
        cache_read_tokens=4,
        reasoning_tokens=1,
    )


def _tool(name: str) -> AgentToolSpec:
    return AgentToolSpec(
        definition=ToolDef(
            name=name,
            description=f"Run {name}",
            parameters=_Args.model_json_schema(),
        ),
        args_schema=_Args,
        execution={"node_id": name},
    )


@pytest.mark.asyncio
async def test_native_step_retries_only_structured_retryable_errors():
    class _RetryUnifier:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise LLMError(
                    message="rate limited",
                    provider="openai",
                    category=LLMErrorCategory.RATE_LIMIT,
                    retryable=True,
                    retry_after=0,
                )
            return LLMResponse(content="recovered")

    unifier = _RetryUnifier()
    response = await run_native_llm_step(
        unifier,
        provider="openai",
        api_key="test",
        messages=[Message(role="user", content="go")],
        model="gpt-test",
        temperature=0,
        max_tokens=100,
    )

    assert response.content == "recovered"
    assert len(unifier.calls) == 2
    assert all(call["sdk_max_retries"] == 0 for call in unifier.calls)
    assert all(call["translate_errors"] is False for call in unifier.calls)


@pytest.mark.asyncio
async def test_native_step_never_surfaces_raw_provider_error_text():
    raw_message = (
        "POST https://private-gateway.internal/v1 payload="
        '{"authorization":"Bearer secret"}'
    )

    class _FailingUnifier:
        async def chat(self, **_kwargs):
            raise LLMError(
                message=raw_message,
                provider="openai",
                category=LLMErrorCategory.INVALID_REQUEST,
            )

    with pytest.raises(NodeUserError) as caught:
        await run_native_llm_step(
            _FailingUnifier(),
            provider="openai",
            api_key="test",
            messages=[Message(role="user", content="go")],
            model="gpt-test",
            temperature=0,
            max_tokens=100,
            explicit_max_retries=0,
        )

    assert str(caught.value) == (
        "OpenAI rejected the model request configuration."
    )
    assert "private-gateway" not in str(caught.value)
    assert "Bearer secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_native_loop_replays_assistant_and_accumulates_usage():
    first_message = Message(
        role="assistant",
        tool_calls=[ToolCall(id="call-1", name="one", args={"value": 3})],
        provider_state={"provider": "gemini", "payload": {"signature": "abc"}},
    )
    unifier = _FakeUnifier(
        [
            LLMResponse(
                tool_calls=first_message.tool_calls,
                assistant_message=first_message,
                thinking="plan",
                usage=Usage(input_tokens=10, output_tokens=2),
            ),
            LLMResponse(
                content="done",
                thinking="answer",
                usage=Usage(input_tokens=20, output_tokens=4),
            ),
        ]
    )
    executed = []

    async def execute(name, args):
        executed.append((name, args))
        return {"ok": args["value"]}

    result = await run_native_agent_loop(
        unifier,
        provider="gemini",
        api_key="test",
        model="gemini-test",
        temperature=0.2,
        max_tokens=100,
        initial_messages=[Message(role="user", content="go")],
        tools=[_tool("one")],
        tool_executor=execute,
    )

    assert executed == [("one", {"value": 3})]
    assert result["messages"][1] is first_message
    assert result["messages"][2].role == "tool"
    assert unifier.calls[1]["messages"][1].provider_state["provider"] == "gemini"
    assert result["usage"] == Usage(
        input_tokens=30,
        output_tokens=6,
        total_tokens=36,
    )
    assert result["thinking_content"] == "plan\n\n--- Iteration 2 ---\nanswer"


@pytest.mark.asyncio
async def test_native_loop_returns_invalid_arguments_to_model_without_execution():
    invalid = ToolCall(
        id="bad",
        name="one",
        args={},
        raw_arguments="{",
        parse_error="Invalid JSON",
    )
    unifier = _FakeUnifier(
        [
            LLMResponse(tool_calls=[invalid]),
            LLMResponse(content="recovered"),
        ]
    )
    executed = False

    async def execute(_name, _args):
        nonlocal executed
        executed = True

    result = await run_native_agent_loop(
        unifier,
        provider="openai",
        api_key="test",
        model="gpt-test",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="user", content="go")],
        tools=[_tool("one")],
        tool_executor=execute,
    )

    assert not executed
    assert '"error": "Invalid tool arguments"' in result["messages"][2].content
    assert result["messages"][-1].content == "recovered"


@pytest.mark.asyncio
async def test_native_loop_returns_unknown_tool_error_without_execution():
    unknown = ToolCall(
        id="unknown-1",
        name="hallucinated_tool",
        args={"value": 3},
    )
    unifier = _FakeUnifier(
        [
            LLMResponse(tool_calls=[unknown]),
            LLMResponse(content="recovered"),
        ]
    )
    executed = False

    async def execute(_name, _args):
        nonlocal executed
        executed = True
        return {"incorrect": "must not run"}

    result = await run_native_agent_loop(
        unifier,
        provider="openai",
        api_key="test",
        model="gpt-test",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="user", content="go")],
        tools=[_tool("one")],
        tool_executor=execute,
    )

    assert executed is False
    assert '"error": "Unknown tool"' in result["messages"][2].content
    assert "hallucinated_tool" in result["messages"][2].content
    assert result["messages"][-1].content == "recovered"


@pytest.mark.asyncio
async def test_native_loop_rebinds_tools_after_canvas_operation():
    unifier = _FakeUnifier(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="create-1", name="one", args={"value": 1})
                ]
            ),
            LLMResponse(
                tool_calls=[
                    ToolCall(id="use-2", name="two", args={"value": 2})
                ]
            ),
            LLMResponse(content="done"),
        ]
    )
    executed: list[str] = []

    async def execute(name, _args):
        executed.append(name)
        if name == "one":
            return {
                "operations": [
                    {"type": "add_node", "node_type": "calculatorTool"}
                ]
            }
        return {"ok": True}

    async def rebind(_operations):
        return [_tool("two")]

    result = await run_native_agent_loop(
        unifier,
        provider="openai",
        api_key="test",
        model="gpt-test",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="user", content="go")],
        tools=[_tool("one")],
        tool_executor=execute,
        rebind_from_operations=rebind,
    )

    assert executed == ["one", "two"]
    assert [tool.name for tool in unifier.calls[1]["tools"]] == ["one", "two"]
    assert result["messages"][-1].content == "done"


@pytest.mark.asyncio
async def test_native_loop_returns_tool_failure_and_iteration_limit():
    unifier = _FakeUnifier(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(id="call-1", name="one", args={"value": 1})
                ]
            )
        ]
    )

    async def execute(_name, _args):
        raise RuntimeError("tool exploded")

    result = await run_native_agent_loop(
        unifier,
        provider="openai",
        api_key="test",
        model="gpt-test",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="user", content="go")],
        tools=[_tool("one")],
        tool_executor=execute,
        max_iterations=1,
    )

    assert '"error": "tool exploded"' in result["messages"][-2].content
    assert result["truncated"] is True
    assert "Recursion limit reached" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_native_loop_propagates_cancellation():
    started = asyncio.Event()

    class _BlockingUnifier:
        async def chat(self, **_kwargs):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        run_native_agent_loop(
            _BlockingUnifier(),
            provider="openai",
            api_key="test",
            model="gpt-test",
            temperature=0,
            max_tokens=100,
            initial_messages=[Message(role="user", content="go")],
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["execute_agent", "execute_chat_agent"])
async def test_ai_service_agent_entrypoints_use_native_unifier(method_name):
    from services.ai import AIService

    unifier = _FakeUnifier(
        [
            LLMResponse(
                content="native answer",
                usage=Usage(input_tokens=3, output_tokens=2),
            )
        ]
    )
    database = _Database()
    service = AIService(
        auth_service=_Auth(),
        database=database,
        cache=None,
        settings=object(),
        chat_unifier=unifier,
    )

    result = await getattr(service, method_name)(
        node_id="agent-1",
        parameters={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "secret",
            "prompt": "hello",
            "system_message": "be useful",
            "temperature": 0.2,
        },
        database=database,
    )

    assert result["success"] is True, result
    assert result["result"]["response"] == "native answer"
    assert unifier.calls[0]["provider"] == "openai"
    assert unifier.calls[0]["messages"][-1].role == "user"


@pytest.mark.asyncio
async def test_chat_agent_connected_tool_uses_agent_tool_spec_schema():
    from services.ai import AIService

    unifier = _FakeUnifier([LLMResponse(content="native answer")])
    database = _Database()
    service = AIService(
        auth_service=_Auth(),
        database=database,
        cache=None,
        settings=object(),
        chat_unifier=unifier,
    )
    service._build_tool_from_node = AsyncMock(
        return_value=(
            _tool("one"),
            {
                "node_id": "tool-1",
                "node_type": "testTool",
                "label": "Test tool",
            },
        )
    )

    result = await service.execute_chat_agent(
        node_id="chat-1",
        parameters={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "secret",
            "prompt": "hello",
        },
        tool_data=[
            {
                "node_id": "tool-1",
                "node_type": "testTool",
                "label": "Test tool",
                "parameters": {},
            }
        ],
        context={"execution_id": "run-1"},
        database=database,
    )

    assert result["success"] is True, result
    assert unifier.calls[0]["tools"][0].parameters == _Args.model_json_schema()


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["execute_agent", "execute_chat_agent"])
async def test_long_term_retrieval_preserves_execution_context_on_save(
    method_name,
    monkeypatch,
):
    import services.ai as ai_module
    import services.memory.runtime as memory_runtime
    from services.ai import AIService

    store = SimpleNamespace(
        similarity_search=AsyncMock(return_value=[
            SimpleNamespace(page_content="remembered detail")
        ])
    )
    monkeypatch.setattr(
        ai_module,
        "_get_memory_vector_store",
        AsyncMock(return_value=store),
    )
    append_turns = AsyncMock(return_value=({}, [], True))
    monkeypatch.setattr(
        memory_runtime,
        "append_memory_turns_atomic",
        append_turns,
    )

    unifier = _FakeUnifier([LLMResponse(content="native answer")])
    database = _Database()
    service = AIService(
        auth_service=_Auth(),
        database=database,
        cache=None,
        settings=object(),
        chat_unifier=unifier,
    )
    service._track_token_usage = AsyncMock(return_value=None)
    memory_data = {
        "node_id": "memory-1",
        "session_id": "session-1",
        "memory_content": "# Conversation History\n\n*No messages yet.*\n",
        "window_size": 10,
        "long_term_enabled": True,
        "retrieval_count": 1,
    }

    result = await getattr(service, method_name)(
        node_id="agent-1",
        parameters={
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "secret",
            "prompt": "hello",
        },
        memory_data=memory_data,
        context={
            "execution_id": "run-1",
            "root_execution_id": "root-1",
        },
        database=database,
    )

    assert result["success"] is True, result
    assert any(
        "remembered detail" in message.content
        for message in unifier.calls[0]["messages"]
    )
    assert append_turns.await_args.kwargs["mutation_id"].startswith(
        "ai-memory:" if method_name == "execute_agent" else "chat-memory:"
    )
