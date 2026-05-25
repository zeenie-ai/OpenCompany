"""Test that execute_chat() and fetch_models() route through native providers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm.protocol import LLMResponse, Usage


@pytest.fixture
def ai_service():
    """Create an AIService with mocked dependencies."""
    import sys
    from unittest.mock import MagicMock as MM

    # Stub all modules that ai.py imports at top level
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

    # Ensure core.config.Settings is a mock class
    sys.modules["core.config"].Settings = MagicMock

    from services.ai import AIService

    settings = MagicMock()
    settings.ai_timeout = 30
    auth = AsyncMock()
    auth.get_api_key = AsyncMock(return_value=None)  # no proxy
    return AIService(auth_service=auth, database=MagicMock(), cache=MagicMock(), settings=settings)


@pytest.mark.asyncio
async def test_execute_chat_uses_native_openai(ai_service):
    """execute_chat with openaiChatModel routes through create_provider, not create_model."""
    fake_resp = LLMResponse(
        content="Hello from native",
        thinking=None,
        usage=Usage(input_tokens=5, output_tokens=3, total_tokens=8),
        model="gpt-5.2",
        finish_reason="stop",
    )

    with (
        patch("services.ai.create_provider") as mock_factory,
        patch("services.ai.native_resolve_max_tokens", return_value=4096),
        patch("services.ai.native_resolve_temperature", return_value=0.7),
        patch("services.ai.is_native_provider", return_value=True),
    ):
        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(return_value=fake_resp)
        mock_factory.return_value = mock_provider

        result = await ai_service.execute_chat(
            node_id="test-1",
            node_type="openaiChatModel",
            parameters={"api_key": "sk-test", "model": "gpt-5.2", "prompt": "Hi"},
        )

    assert result["success"] is True
    assert result["result"]["response"] == "Hello from native"
    assert result["result"]["provider"] == "openai"
    mock_factory.assert_called_once()
    mock_provider.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_chat_uses_langchain_for_groq(ai_service):
    """execute_chat with groqChatModel falls back to LangChain create_model."""
    mock_response = MagicMock()
    mock_response.content = "Hello from groq"

    with (
        patch("services.ai.is_native_provider", return_value=False),
        patch.object(ai_service, "create_model", return_value=MagicMock(invoke=MagicMock(return_value=mock_response))),
        patch("services.ai.extract_thinking_from_response", return_value=("Hello from groq", None)),
        patch("services.ai._resolve_max_tokens", return_value=4096),
        patch("services.ai._resolve_temperature", return_value=0.7),
    ):
        result = await ai_service.execute_chat(
            node_id="test-2",
            node_type="groqChatModel",
            parameters={"api_key": "gsk-test", "model": "llama-4-scout", "prompt": "Hi"},
        )

    assert result["success"] is True
    assert result["result"]["response"] == "Hello from groq"
    assert result["result"]["provider"] == "groq"


@pytest.mark.asyncio
async def test_fetch_models_uses_native_for_anthropic(ai_service):
    """fetch_models for anthropic delegates to native provider."""
    expected_models = ["claude-sonnet-4-6", "claude-opus-4-6"]

    with patch("services.ai.is_native_provider", return_value=True), patch("services.ai.create_provider") as mock_factory:
        mock_provider = AsyncMock()
        mock_provider.fetch_models = AsyncMock(return_value=expected_models)
        mock_factory.return_value = mock_provider

        models = await ai_service.fetch_models("anthropic", "sk-ant-test")

    assert models == expected_models
    # ai.fetch_models forwards `proxy_url` (defaulting to None) to the native
    # factory so the Ollama-pattern proxy override path stays uniform with
    # execute_chat. Asserting the full call signature including the explicit
    # `None` kwarg ensures the proxy path is not silently dropped.
    mock_factory.assert_called_once_with("anthropic", "sk-ant-test", proxy_url=None)


@pytest.mark.asyncio
async def test_execute_chat_native_with_thinking(ai_service):
    """execute_chat passes thinking config to native provider."""
    fake_resp = LLMResponse(
        content="Answer",
        thinking="Let me think...",
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        model="claude-sonnet-4-6",
        finish_reason="stop",
    )

    with (
        patch("services.ai.create_provider") as mock_factory,
        patch("services.ai.native_resolve_max_tokens", return_value=4096),
        patch("services.ai.native_resolve_temperature", return_value=1.0),
        patch("services.ai.is_native_provider", return_value=True),
    ):
        mock_provider = AsyncMock()
        mock_provider.chat = AsyncMock(return_value=fake_resp)
        mock_factory.return_value = mock_provider

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

    # Verify thinking config was passed to provider.chat
    call_kwargs = mock_provider.chat.call_args[1]
    assert call_kwargs["thinking"].enabled is True
    assert call_kwargs["thinking"].budget == 4096
