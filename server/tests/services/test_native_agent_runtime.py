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


@pytest.mark.asyncio
@pytest.mark.parametrize("needs_owner", [True, False])
async def test_tool_errors_only_stop_the_loop_when_the_owner_must_fix_them(needs_owner):
    response = LLMResponse(tool_calls=[ToolCall(id="call-1", name="model", args={})])
    unifier = _FakeUnifier([response, LLMResponse(content="fixed")])
    save = AsyncMock()
    async def execute(_name, _args):
        raise NodeUserError("Blocked", hint="Fix the account" if needs_owner else "Supply a value", requires_user_action=needs_owner)

    kwargs = dict(provider="gemini", api_key="test", model="test", temperature=0,
                  max_tokens=100, initial_messages=[Message(role="user", content="go")],
                  tools=[AgentToolSpec(definition=ToolDef(name="model", description="model", parameters={}))],
                  tool_executor=execute, conversation_saver=save)
    if needs_owner:
        with pytest.raises(NodeUserError) as caught:
            await run_native_agent_loop(unifier, **kwargs)
        assert caught.value.requires_user_action
        assert caught.value.hint == "Fix the account"
        assert len(unifier.calls) == 1
        # The failed tool result is saved with its matching call, without
        # spending a second model turn to explain the error.
        assert save.await_args.args[0][-1].role == "tool"
    else:
        result = await run_native_agent_loop(unifier, **kwargs)
        assert result["response"].content == "fixed"
        assert len(unifier.calls) == 2


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
async def test_native_step_retries_only_structured_retryable_errors(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("services.agent_runtime.asyncio.sleep", sleep)
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
    sleep.assert_awaited_once_with(5.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [None, 0, -1, True, float("nan"), float("inf"), 1e300, "30"])
async def test_invalid_provider_delay_uses_backoff_without_masking_error(monkeypatch, delay):
    error = LLMError("limited", "openai", category=LLMErrorCategory.RATE_LIMIT, retryable=True, retry_after=delay)
    unifier = SimpleNamespace(chat=AsyncMock(side_effect=[error, error, LLMResponse(content="done")]))
    sleep = AsyncMock()
    monkeypatch.setattr("services.agent_runtime.asyncio.sleep", sleep)
    result = await run_native_llm_step(
        unifier, provider="openai", api_key="test", messages=[Message(role="user", content="go")],
        model="test", temperature=0, max_tokens=100,
    )
    assert result.content == "done"
    assert [call.args[0] for call in sleep.await_args_list] == [5.0, 10.0]


@pytest.mark.asyncio
async def test_cancel_during_backoff_does_not_make_another_provider_call(monkeypatch):
    error = LLMError("limited", "gemini", category=LLMErrorCategory.RATE_LIMIT, retryable=True)
    unifier = SimpleNamespace(chat=AsyncMock(side_effect=error))
    monkeypatch.setattr("services.agent_runtime.asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await run_native_llm_step(
            unifier, provider="gemini", api_key="test", messages=[Message(role="user", content="go")],
            model="test", temperature=0, max_tokens=100,
        )
    unifier.chat.assert_awaited_once()


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
@pytest.mark.parametrize("daily_quota", [False, True])
async def test_native_gemini_retry_info_is_honored_and_hard_quota_never_retries(monkeypatch, daily_quota):
    from google.genai.errors import ClientError

    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "41.5s"}]
    if daily_quota:
        details.append({"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel"},
        ]})
    error = LLMError.from_exception("gemini", ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded", "details": details,
    }}))
    unifier = SimpleNamespace(chat=AsyncMock(side_effect=[error, LLMResponse(content="recovered")]))
    sleep = AsyncMock()
    monkeypatch.setattr("services.agent_runtime.asyncio.sleep", sleep)
    request = dict(provider="gemini", api_key="test", model="test-model",
                   messages=[Message(role="user", content="go")], temperature=0, max_tokens=100)
    if daily_quota:
        with pytest.raises(NodeUserError) as raised:
            await run_native_llm_step(unifier, **request)
        assert raised.value.requires_user_action
        assert unifier.chat.await_count == 1
        sleep.assert_not_awaited()
    else:
        result = await run_native_llm_step(unifier, **request)
        assert result.content == "recovered"
        assert unifier.chat.await_count == 2
        sleep.assert_awaited_once_with(41.5)


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
async def test_compaction_pause_can_replace_replay_before_next_request():
    compaction_message = Message(
        role="assistant",
        provider_state={
            "provider": "anthropic",
            "payload": {
                "content": [
                    {
                        "type": "compaction",
                        "content": "durable checkpoint",
                    }
                ]
            },
        },
    )
    unifier = _FakeUnifier(
        [
            LLMResponse(
                assistant_message=compaction_message,
                finish_reason="compaction",
            ),
            LLMResponse(content="continued after checkpoint"),
        ]
    )
    pauses: list[int] = []

    async def commit_pause(iteration, _response, _messages):
        pauses.append(iteration)
        return [compaction_message]

    result = await run_native_agent_loop(
        unifier,
        provider="anthropic",
        api_key="test",
        model="claude-sonnet-4-6",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="user", content="large history")],
        compaction_pause_callback=commit_pause,
    )

    assert pauses == [1]
    assert unifier.calls[1]["messages"] == [compaction_message]
    assert result["response"].content == "continued after checkpoint"


@pytest.mark.asyncio
async def test_native_loop_saves_the_conversation_before_tool_execution():
    first_message = Message(
        role="assistant",
        tool_calls=[ToolCall(id="call-1", name="one", args={"value": 3})],
        provider_state={"provider": "openai", "payload": {"opaque": "exact"}},
    )
    unifier = _FakeUnifier(
        [
            LLMResponse(
                tool_calls=first_message.tool_calls,
                assistant_message=first_message,
                usage=Usage(input_tokens=7, output_tokens=2),
            ),
            LLMResponse(
                content="done",
                usage=Usage(input_tokens=11, output_tokens=3),
            ),
        ]
    )
    observed: list[tuple[str, object]] = []
    tool = _tool("one")

    async def save(messages):
        observed.append(("save", [message.role for message in messages]))

    async def execute(_name, args):
        assert tool.execution["tool_call_id"] == "call-1"
        assert (
            tool.execution["operation_id"]
            == "openai:iteration:1:tool:1:call-1"
        )
        observed.append(("tool", str(args["value"])))
        return {"ok": True}

    await run_native_agent_loop(
        unifier,
        provider="openai",
        api_key="test",
        model="gpt-test",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="system", content="resolved"), Message(role="user", content="go")],
        tools=[tool],
        tool_executor=execute,
        conversation_saver=save,
    )

    saves = [value for kind, value in observed if kind == "save"]
    # Saved after the assistant append (billing already happened), again
    # after the tool results, and once more for the final answer.
    assert saves == [
        ["system", "user", "assistant"],
        ["system", "user", "assistant", "tool"],
        ["system", "user", "assistant", "tool", "assistant"],
    ]
    # The assistant turn is durable BEFORE its tool executes.
    assert observed.index(("save", saves[0])) < observed.index(("tool", "3"))


@pytest.mark.asyncio
async def test_native_loop_save_failure_cannot_fail_the_run():
    unifier = _FakeUnifier([LLMResponse(content="done")])

    async def broken_save(messages):
        raise RuntimeError("save exploded")

    result = await run_native_agent_loop(
        unifier,
        provider="openai",
        api_key="test",
        model="gpt-test",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="user", content="go")],
        conversation_saver=broken_save,
    )
    assert result["response"].content == "done"


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
        roster_after_rebind=lambda: "Updated teammates: agent-2: Researcher",
    )

    assert any(message.role == "system" and "agent-2: Researcher" in message.content for message in unifier.calls[1]["messages"])
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
@pytest.mark.parametrize("method_name", ["execute_agent", "execute_chat_agent"])
async def test_an_unsaved_named_endpoint_is_reported_as_not_configured(method_name):
    # No key was injected because the endpoint's rows are gone; the agent
    # must say so, not "API key is required", and send nothing.
    from services.ai import AIService

    unifier = _FakeUnifier([])
    database = _Database()
    service = AIService(
        auth_service=_Auth(),
        database=database,
        cache=None,
        settings=object(),
        chat_unifier=unifier,
    )

    with pytest.raises(NodeUserError, match="endpoint 'gone' is not configured"):
        await getattr(service, method_name)(
            node_id="agent-1",
            parameters={"provider": "openai_compatible:gone", "model": "qwen3", "prompt": "hello"},
            database=database,
        )

    assert unifier.calls == []


@pytest.mark.asyncio
async def test_a_chat_model_with_no_endpoint_chosen_asks_for_one():
    from services.ai import AIService

    unifier = _FakeUnifier([])
    service = AIService(
        auth_service=_Auth(),
        database=_Database(),
        cache=None,
        settings=object(),
        chat_unifier=unifier,
    )

    with pytest.raises(NodeUserError, match="Choose an OpenAI-compatible endpoint"):
        await service.execute_chat("chat-1", "openaiCompatibleChatModel", {"prompt": "hi", "model": "qwen3"})

    assert unifier.calls == []


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


def _typed_tool(name: str, node_type: str) -> AgentToolSpec:
    spec = _tool(name)
    spec.execution["node_type"] = node_type
    return spec


async def _one_tool_call(tool: AgentToolSpec, result: Any, **loop_kwargs):
    """Run one tool call through the loop and return the tool message."""
    call = ToolCall(id="call-1", name=tool.name, args={"value": 1})
    unifier = _FakeUnifier(
        [
            LLMResponse(
                tool_calls=[call],
                assistant_message=Message(role="assistant", tool_calls=[call]),
            ),
            LLMResponse(content="done"),
        ]
    )

    async def execute(_name, _args):
        return result

    loop = await run_native_agent_loop(
        unifier,
        provider="openai",
        api_key="test",
        model="gpt-test",
        temperature=0,
        max_tokens=100,
        initial_messages=[Message(role="user", content="go")],
        tools=[tool],
        tool_executor=execute,
        **loop_kwargs,
    )
    (tool_message,) = [m for m in loop["messages"] if m.role == "tool"]
    return tool_message


@pytest.mark.asyncio
async def test_native_loop_cuts_an_external_tool_result():
    message = await _one_tool_call(
        _typed_tool("scrape", "tikhubAction"),
        {"data": "x" * 5_000},
        tool_output_limit=1_000,
    )

    assert "showing the first 1,000 of 5,012 characters" in message.content
    assert len(message.content) < 1_200
    assert message.blocks[0].text == message.content


@pytest.mark.asyncio
async def test_native_loop_keeps_llm_media_when_the_text_is_cut():
    ref = {
        "kind": "image",
        "path": "images/chart.png",
        "workflow_id": "wf-media",
        "filename": "chart.png",
        "mime_type": "image/png",
        "size_bytes": 1234,
    }
    message = await _one_tool_call(
        _typed_tool("scrape", "tikhubAction"),
        {"data": "x" * 5_000, "llm_media": [{"ref": ref}]},
        tool_output_limit=1_000,
    )

    assert "Tool result truncated" in message.content
    images = [block for block in message.blocks if block.type == "image"]
    assert images and images[0].source["ref"]["path"] == "images/chart.png"


@pytest.mark.asyncio
async def test_native_loop_never_cuts_a_skill_load():
    message = await _one_tool_call(
        _typed_tool("Skill", "_builtin_skill"),
        {"instructions": "i" * 5_000},
        tool_output_limit=1_000,
    )

    assert "Tool result truncated" not in message.content
    assert "i" * 5_000 in message.content


@pytest.mark.asyncio
async def test_native_loop_without_a_limit_keeps_results_whole():
    message = await _one_tool_call(
        _typed_tool("scrape", "tikhubAction"),
        {"data": "x" * 5_000},
    )

    assert "Tool result truncated" not in message.content
    assert "x" * 5_000 in message.content
