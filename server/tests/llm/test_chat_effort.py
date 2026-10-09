"""The effort the owner picks in Home's chat (Quick ``low`` / Thorough
``high``): each provider sends it as its own request setting, and
ChatUnifier passes it only to the models in a provider's ``effort_models``
(llm_defaults.json). Effort never turns thinking on or off."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm.protocol import LLMResponse, Message, ThinkingConfig, ToolDef


def _anthropic():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        return AnthropicProvider("sk-test")


def _anthropic_stream(provider):
    response = MagicMock()
    response.content = [MagicMock(type="text", text="ok")]
    response.usage = MagicMock(input_tokens=5, output_tokens=2, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    response.stop_reason = "end_turn"
    stream = MagicMock()
    stream.get_final_message = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=stream)
    ctx.__aexit__ = AsyncMock(return_value=False)
    provider._client.messages.stream = MagicMock(return_value=ctx)
    return provider._client.messages.stream


@pytest.mark.asyncio
async def test_anthropic_sends_effort_as_output_config():
    provider = _anthropic()
    stream = _anthropic_stream(provider)
    await provider.chat([Message(role="user", content="Hi")], model="claude-sonnet-5-5", effort="high")
    assert stream.call_args.kwargs["output_config"] == {"effort": "high"}
    # Thinking stays the agent's own: effort alone adds none.
    assert "thinking" not in stream.call_args.kwargs

    await provider.chat([Message(role="user", content="Hi")], model="claude-sonnet-5-5")
    assert "output_config" not in stream.call_args.kwargs


def _openai():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        return OpenAIProvider("sk-test")


def _completion():
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content="ok", reasoning_content=None, tool_calls=None), finish_reason="stop")]
    response.usage = MagicMock(prompt_tokens=5, completion_tokens=2, total_tokens=7, completion_tokens_details=None)
    return response


@pytest.mark.asyncio
async def test_openai_sends_reasoning_effort_on_chat_completions():
    provider = _openai()
    provider._client.chat.completions.create = AsyncMock(return_value=_completion())
    await provider.chat([Message(role="user", content="Hi")], model="gpt-6-sol", effort="low")
    assert provider._client.chat.completions.create.call_args.kwargs["reasoning_effort"] == "low"

    await provider.chat([Message(role="user", content="Hi")], model="gpt-6-sol")
    assert "reasoning_effort" not in provider._client.chat.completions.create.call_args.kwargs


@pytest.mark.asyncio
async def test_openai_effort_wins_over_the_agents_own_on_responses():
    provider = _openai()
    response = SimpleNamespace(
        id="response-1",
        model="gpt-6-sol",
        status="completed",
        output=[SimpleNamespace(type="message", id="m-1", role="assistant", status="completed", content=[SimpleNamespace(type="output_text", text="ok", annotations=[])])],
        usage=SimpleNamespace(
            input_tokens=8,
            output_tokens=5,
            total_tokens=13,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )
    provider._client.responses.create = AsyncMock(return_value=response)
    tools = [ToolDef(name="lookup", description="Look up", parameters={"type": "object", "properties": {}})]
    await provider.chat(
        [Message(role="user", content="Hi")],
        model="gpt-6-sol",
        thinking=ThinkingConfig(enabled=True, effort="high"),
        tools=tools,
        effort="low",
    )
    assert provider._client.responses.create.call_args.kwargs["reasoning"] == {"effort": "low"}


def _gemini():
    with patch("google.genai.Client"):
        from services.llm.providers.gemini import GeminiProvider

        return GeminiProvider("key")


async def _gemini_thinking(provider, **kwargs):
    types = MagicMock()
    response = MagicMock()
    response.candidates = []
    response.usage_metadata = None
    provider._client.aio.models.generate_content = AsyncMock(return_value=response)
    with patch("google.genai.types", types):
        await provider.chat([Message(role="user", content="Hi")], model="gemini-3.8-flash", **kwargs)
    return types.ThinkingConfig


@pytest.mark.asyncio
async def test_gemini_sends_effort_as_the_thinking_level():
    provider = _gemini()
    config = await _gemini_thinking(provider, effort="high")
    assert config.call_args.kwargs == {"thinking_level": "high"}

    # Over the agent's own budget: a level and a budget never go together.
    config = await _gemini_thinking(provider, thinking=ThinkingConfig(enabled=True, budget=4096), effort="low")
    assert config.call_args.kwargs == {"include_thoughts": True, "thinking_level": "low"}

    config = await _gemini_thinking(provider)
    config.assert_not_called()


@pytest.mark.asyncio
async def test_the_unifier_passes_effort_only_to_the_effort_models(monkeypatch):
    from services.llm import config, registry
    from services.llm.registry import ProviderSpec
    from services.llm.unifier import ChatUnifier

    client = MagicMock()
    client.chat = AsyncMock(return_value=LLMResponse(content="ok"))
    name = "effort-test"
    registry._REGISTRY[name] = ProviderSpec(name=name, factory=lambda **kwargs: client, sdk_exception_refs=("openai:OpenAIError",))
    monkeypatch.setitem(config.LLM_DEFAULTS["providers"], name, {"effort_models": ["think-"]})
    auth = MagicMock()
    auth.resolve_api_key = AsyncMock(return_value=None)
    unifier = ChatUnifier(defaults={"providers": {}}, auth_service=auth, client_cache_size=0)
    try:
        await unifier.chat(provider=name, api_key="secret", messages=[Message(role="user", content="Hi")], model="think-1", effort="high")
        assert client.chat.call_args.kwargs["effort"] == "high"
        await unifier.chat(provider=name, api_key="secret", messages=[Message(role="user", content="Hi")], model="plain-1", effort="high")
        assert "effort" not in client.chat.call_args.kwargs
        await unifier.chat(provider=name, api_key="secret", messages=[Message(role="user", content="Hi")], model="think-1")
        assert "effort" not in client.chat.call_args.kwargs
    finally:
        registry._REGISTRY.pop(name, None)
        await unifier.aclose()


def test_the_effort_models_are_the_thinking_generations():
    from services.llm.config import supports_effort

    assert supports_effort("anthropic", "claude-sonnet-5-5") and supports_effort("anthropic", "claude-opus-5-5")
    assert not supports_effort("anthropic", "claude-haiku-4-5")
    assert supports_effort("openai", "gpt-6-sol") and not supports_effort("openai", "gpt-4.1")
    assert supports_effort("gemini", "gemini-3.1-pro-preview") and not supports_effort("gemini", "gemini-2.5-pro")
    assert not supports_effort("openrouter", "anthropic/claude-sonnet-5.5")
