"""Test that execute_chat() and fetch_models() route through ChatUnifier.

Post-Phase-A3: ``AIService`` delegates every native chat / model-list
call to the injected ``chat_unifier``. These tests confirm the
delegation contract — they DO NOT reach into provider classes any more
(see ``test_unifier_typed_errors.py`` for that layer).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm.protocol import LLMResponse, Usage


@pytest.fixture
def ai_service():
    """Create an AIService with a mock chat_unifier injected.

    Stubs the cross-cutting modules that ``services.ai`` imports at the
    top level so test collection doesn't need a real DI container.
    """
    import sys
    from unittest.mock import MagicMock as MM

    _stubs = [
        "core.config",
        "core.container",
        "services.model_registry",
        "services.compaction",
        "services.pricing",
        "services.auth",
        "services.status_broadcaster",
        "constants",
    ]
    for mod in _stubs:
        if mod not in sys.modules:
            sys.modules[mod] = MM()

    sys.modules["core.config"].Settings = MagicMock

    from services.ai import AIService

    settings = MagicMock()
    settings.ai_timeout = 30
    auth = AsyncMock()
    auth.get_api_key = AsyncMock(return_value=None)  # no proxy

    chat_unifier = MagicMock()
    chat_unifier.is_registered = MagicMock(return_value=True)
    chat_unifier.chat = AsyncMock()
    chat_unifier.fetch_models = AsyncMock()

    return AIService(
        auth_service=auth,
        database=MagicMock(),
        cache=MagicMock(),
        settings=settings,
        chat_unifier=chat_unifier,
    )


@pytest.mark.asyncio
async def test_execute_chat_delegates_to_unifier_for_openai(ai_service):
    """``execute_chat`` for openaiChatModel hands off to ``chat_unifier.chat``."""
    fake_resp = LLMResponse(
        content="Hello from native",
        thinking=None,
        usage=Usage(input_tokens=5, output_tokens=3, total_tokens=8),
        model="gpt-5.2",
        finish_reason="stop",
    )
    ai_service.chat_unifier.chat.return_value = fake_resp

    with (
        patch("services.ai.native_resolve_max_tokens", return_value=4096),
        patch("services.ai.native_resolve_temperature", return_value=0.7),
    ):
        result = await ai_service.execute_chat(
            node_id="test-1",
            node_type="openaiChatModel",
            parameters={"api_key": "sk-test", "model": "gpt-5.2", "prompt": "Hi"},
        )

    assert result["success"] is True
    assert result["result"]["response"] == "Hello from native"
    assert result["result"]["provider"] == "openai"
    ai_service.chat_unifier.chat.assert_awaited_once()
    call_kwargs = ai_service.chat_unifier.chat.call_args.kwargs
    assert call_kwargs["provider"] == "openai"
    assert call_kwargs["api_key"] == "sk-test"
    assert call_kwargs["model"] == "gpt-5.2"


@pytest.mark.asyncio
async def test_execute_chat_raises_node_user_error_on_unknown_provider(ai_service):
    """Phase D removed the LangChain fallback. Every provider routes
    through ``ChatUnifier``; unknown providers surface as ``NodeUserError``
    from inside the unifier (``get_provider`` raises) — ``execute_chat``
    catches it via ``except NodeUserError: raise`` so the framework's
    ``BaseNode.execute()`` logs one WARN line with no traceback.
    """
    from services.plugin import NodeUserError

    # Simulate the unifier rejecting an unknown provider.
    ai_service.chat_unifier.chat = AsyncMock(
        side_effect=NodeUserError("Unknown LLM provider: 'unregistered'")
    )

    with (
        patch("services.ai.native_resolve_max_tokens", return_value=4096),
        patch("services.ai.native_resolve_temperature", return_value=0.7),
    ):
        with pytest.raises(NodeUserError, match="Unknown LLM provider"):
            await ai_service.execute_chat(
                node_id="test-2",
                node_type="someChatModel",
                parameters={"api_key": "x", "model": "y", "prompt": "Hi", "provider": "unregistered"},
            )


@pytest.mark.asyncio
async def test_fetch_models_delegates_to_unifier_for_anthropic(ai_service):
    """``fetch_models`` for anthropic delegates to ``chat_unifier.fetch_models``."""
    expected_models = ["claude-sonnet-4-6", "claude-opus-4-6"]
    ai_service.chat_unifier.fetch_models.return_value = expected_models

    models = await ai_service.fetch_models("anthropic", "sk-ant-test")

    assert models == expected_models
    ai_service.chat_unifier.fetch_models.assert_awaited_once_with(
        provider="anthropic", api_key="sk-ant-test"
    )


@pytest.mark.asyncio
async def test_execute_chat_passes_thinking_config_to_unifier(ai_service):
    """``execute_chat`` forwards the thinking config to ``chat_unifier.chat``."""
    fake_resp = LLMResponse(
        content="Answer",
        thinking="Let me think...",
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        model="claude-sonnet-4-6",
        finish_reason="stop",
    )
    ai_service.chat_unifier.chat.return_value = fake_resp

    with (
        patch("services.ai.native_resolve_max_tokens", return_value=4096),
        patch("services.ai.native_resolve_temperature", return_value=1.0),
    ):
        result = await ai_service.execute_chat(
            node_id="test-3",
            node_type="anthropicChatModel",
            parameters={
                "api_key": "sk-ant-test",
                "model": "claude-sonnet-4-6",
                "prompt": "Think about this",
                "thinking_enabled": True,
                "thinking_budget": 4096,
            },
        )

    assert result["success"] is True
    assert result["result"]["thinking"] == "Let me think..."
    assert result["result"]["thinking_enabled"] is True

    call_kwargs = ai_service.chat_unifier.chat.call_args.kwargs
    assert call_kwargs["thinking"].enabled is True
    assert call_kwargs["thinking"].budget == 4096
