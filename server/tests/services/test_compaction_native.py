from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.compaction import CompactionService
from services.llm.protocol import LLMResponse, Usage


class _Database:
    get_or_create_session_token_state = AsyncMock(
        return_value={"cumulative_total": 123, "compaction_count": 0}
    )
    save_token_metric = AsyncMock()
    save_compaction_event = AsyncMock()
    update_session_token_state = AsyncMock()


@pytest.mark.asyncio
async def test_compaction_uses_chat_unifier_with_native_messages(monkeypatch):
    _Database.save_token_metric.reset_mock()
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
    metric = _Database.save_token_metric.await_args.args[0]
    assert metric["iteration"] == 0
    assert metric["total_tokens"] == 45
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
