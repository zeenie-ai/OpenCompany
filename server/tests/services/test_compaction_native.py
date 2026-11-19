from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.compaction import CompactionService
from services.llm.protocol import LLMError, LLMErrorCategory, LLMResponse, Usage


class _Database:
    get_or_create_session_token_state = AsyncMock(
        return_value={"cumulative_total": 123, "compaction_count": 0}
    )
    save_compaction_event = AsyncMock()
    update_session_token_state = AsyncMock()


@pytest.mark.asyncio
async def test_compaction_uses_chat_unifier_with_native_messages(monkeypatch, llm_usage):
    _Database.update_session_token_state.reset_mock()
    registry = SimpleNamespace(get_max_output_tokens=lambda *_args: 2048)
    monkeypatch.setattr(
        "services.model_registry.get_model_registry",
        lambda: registry,
    )
    unifier = SimpleNamespace(
        chat=AsyncMock(
            return_value=LLMResponse(
                content="compact summary",
                usage=Usage(
                    input_tokens=40,
                    output_tokens=5,
                    total_tokens=45,
                    cache_read_tokens=2,
                ),
            )
        )
    )
    service = CompactionService(
        _Database(),
        SimpleNamespace(compaction_enabled=True),
    )
    service.set_ai_service(SimpleNamespace(chat_unifier=unifier))

    result = await service.compact_context(
        session_id="session-1",
        node_id="agent-1",
        memory_content="long history " * 20,
        provider="openai",
        api_key="secret",
        model="gpt-test",
    )

    assert result["success"] is True
    assert result["usage"] == {
        "input_tokens": 40,
        "output_tokens": 5,
        "total_tokens": 45,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 2,
        "reasoning_tokens": 0,
    }
    booked = llm_usage.await_args.kwargs
    assert booked["iteration"] == 0
    assert booked["usage"]["total_tokens"] == 45
    # Metric-only recording must not add the summarizer tokens back into the
    # active context counter after compaction resets it.
    reset_updates = [
        call.args[1]
        for call in _Database.update_session_token_state.await_args_list
    ]
    assert any(update.get("cumulative_total") == 0 for update in reset_updates)
    assert all(update.get("cumulative_total") != 45 for update in reset_updates)
    request = unifier.chat.await_args.kwargs
    assert request["provider"] == "openai"
    assert request["messages"][0].role == "user"
    assert "long history" in request["messages"][0].content
    assert request["sdk_max_retries"] == 0
    assert request["translate_errors"] is False


_REQUEST = dict(session_id="session-1", node_id="agent-1", memory_content="long history " * 20,
                provider="openai", api_key="secret", model="gpt-test")


def _service(monkeypatch, chat):
    for mock in (_Database.get_or_create_session_token_state, _Database.save_compaction_event,
                 _Database.update_session_token_state):
        mock.reset_mock()
    registry = SimpleNamespace(get_max_output_tokens=lambda *_args: 2048)
    monkeypatch.setattr("services.model_registry.get_model_registry", lambda: registry)
    service = CompactionService(_Database(), SimpleNamespace(compaction_enabled=True))
    service.set_ai_service(SimpleNamespace(chat_unifier=SimpleNamespace(chat=chat)))
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("content", ["", "  \n"])
async def test_an_empty_summary_fails_and_replaces_nothing(monkeypatch, llm_usage, content):
    # 1b.27: saved behind its header, an empty reply replaced the conversation.
    reply = LLMResponse(content=content, usage=Usage(input_tokens=40, output_tokens=0, total_tokens=40))
    service = _service(monkeypatch, AsyncMock(return_value=reply))

    result = await service.compact_context(**_REQUEST)

    assert result["success"] is False and result["error"] == "empty summary"
    assert result["usage"]["total_tokens"] == 40  # the call was billed
    llm_usage.assert_awaited_once()
    _Database.save_compaction_event.assert_not_awaited()
    assert all(call.args[1].get("cumulative_total") != 0 for call in _Database.update_session_token_state.await_args_list)


def _summary_reply():
    return LLMResponse(content="compact summary", usage=Usage(input_tokens=40, output_tokens=5, total_tokens=45))


@pytest.mark.asyncio
async def test_the_usage_row_names_the_employee_and_the_chat_run(monkeypatch, llm_usage):
    service = _service(monkeypatch, AsyncMock(return_value=_summary_reply()))

    await service.compact_context(**_REQUEST, workflow_id="wf", chat_run_id="r_1")

    booked = llm_usage.await_args.kwargs
    assert booked["workflow_id"] == "wf"
    assert booked["chat_run_id"] == "r_1"


@pytest.mark.asyncio
async def test_a_usage_row_outside_a_chat_run_names_none(monkeypatch, llm_usage):
    service = _service(monkeypatch, AsyncMock(return_value=_summary_reply()))

    await service.compact_context(**_REQUEST)

    booked = llm_usage.await_args.kwargs
    assert booked["workflow_id"] is None
    assert booked["chat_run_id"] is None


@pytest.mark.asyncio
async def test_the_summarizer_books_in_the_services_own_database(monkeypatch, llm_usage):
    service = _service(monkeypatch, AsyncMock(return_value=_summary_reply()))

    await service.compact_context(**_REQUEST)

    assert llm_usage.await_args.kwargs["database"] is service._db


@pytest.mark.asyncio
async def test_an_llm_error_is_raised_only_when_asked(monkeypatch):
    error = LLMError("private body", "openai", category=LLMErrorCategory.RATE_LIMIT, retryable=True)
    service = _service(monkeypatch, AsyncMock(side_effect=error))

    result = await service.compact_context(**_REQUEST, explicit_max_retries=0)
    assert result["success"] is False and result["error"] == error.user_message

    with pytest.raises(LLMError) as raised:
        await service.compact_context(**_REQUEST, explicit_max_retries=0, raise_llm_errors=True)
    assert raised.value.category == LLMErrorCategory.RATE_LIMIT
