"""Phase A1 contract — every shipped provider self-registers on import.

Locks the plugin shape: importing ``services.llm`` (and transitively
``services.llm.providers``) must populate the global registry with the
full set of native + OpenAI-compat providers. If any provider drops its
``register_provider(...)`` call, this test catches it.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import services.llm  # noqa: F401 — side-effect import populates the registry
from services.llm.protocol import ToolDef
from services.llm.registry import all_providers, get_provider, has_provider


EXPECTED_REGISTERED_PROVIDERS = {
    # Dedicated SDKs
    "anthropic",
    "openai",
    "gemini",
    "openrouter",
    # OpenAI-compatible providers (registered via _compat.py)
    "xai",
    "deepseek",
    "kimi",
    "mistral",
    "ollama",
    "lmstudio",
    # Migrated from LangChain fallback in Phase D — both expose
    # OpenAI-compatible /v1 endpoints so they share OpenAIProvider.
    "groq",
    "cerebras",
}
COMPAT_PROVIDERS = (
    "xai",
    "deepseek",
    "kimi",
    "mistral",
    "ollama",
    "lmstudio",
    "groq",
    "cerebras",
)


def test_all_expected_providers_registered():
    """Every native + compat provider populates the registry on import."""
    assert set(all_providers()) >= EXPECTED_REGISTERED_PROVIDERS


def test_all_twelve_providers_route_to_the_expected_native_class():
    from services.llm.providers.anthropic import AnthropicProvider
    from services.llm.providers.gemini import GeminiProvider
    from services.llm.providers.openai import OpenAIProvider
    from services.llm.providers.openrouter import OpenRouterProvider

    assert get_provider("anthropic").factory is AnthropicProvider
    assert get_provider("gemini").factory is GeminiProvider
    assert get_provider("openai").factory is OpenAIProvider
    assert get_provider("openrouter").factory is OpenRouterProvider
    for name in COMPAT_PROVIDERS:
        spec = get_provider(name)
        assert spec.factory is OpenAIProvider
        assert spec.client_kwargs["provider_name"] == name


def test_dedicated_providers_have_typed_sdk_exceptions():
    """The four dedicated SDKs declare their typed exception classes."""
    import anthropic
    import openai
    from google.genai import errors as google_genai_errors

    assert get_provider("anthropic").sdk_exception_types == (anthropic.APIError,)
    assert get_provider("openai").sdk_exception_types == (openai.OpenAIError,)
    assert get_provider("gemini").sdk_exception_types == (google_genai_errors.APIError,)
    assert get_provider("openrouter").sdk_exception_types == (openai.OpenAIError,)


def test_compat_providers_carry_base_url_in_client_kwargs():
    """Every OpenAI-compat provider pins its base_url declaratively."""
    expected_base_urls = {
        "xai": "https://api.x.ai/v1",
        "deepseek": "https://api.deepseek.com",
        "kimi": "https://api.moonshot.ai/v1",
        "mistral": "https://api.mistral.ai/v1",
        "ollama": "http://localhost:11434/v1",
        "lmstudio": "http://localhost:1234/v1",
        "groq": "https://api.groq.com/openai/v1",
        "cerebras": "https://api.cerebras.ai/v1",
    }
    for name, url in expected_base_urls.items():
        spec = get_provider(name)
        assert spec.client_kwargs.get("base_url") == url, (
            f"{name}: expected base_url={url!r}, got {spec.client_kwargs!r}"
        )


@pytest.mark.parametrize("provider_name", COMPAT_PROVIDERS)
def test_every_compat_provider_inherits_shared_response_and_tool_contract(
    provider_name,
):
    """One matrix gate covers normalization/schema behavior for all 8."""

    from services.llm.providers.openai import OpenAIProvider

    spec = get_provider(provider_name)
    with patch("openai.AsyncOpenAI"):
        provider = OpenAIProvider(
            "key",
            provider_name=provider_name,
            base_url=spec.client_kwargs["base_url"],
        )

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="answer",
                    reasoning="reasoning",
                    reasoning_content=None,
                    reasoning_details=None,
                    refusal=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(
                                name="lookup",
                                arguments="{not-json",
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=4,
            total_tokens=14,
            prompt_tokens_details=SimpleNamespace(cached_tokens=2),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )
    normalized = provider._normalize(response, "provider-model")

    assert normalized.content == "answer"
    assert normalized.thinking == "reasoning"
    assert normalized.finish_reason == "tool_calls"
    assert normalized.usage.total_tokens == 14
    assert normalized.usage.cache_read_tokens == 2
    assert normalized.usage.reasoning_tokens == 3
    assert normalized.tool_calls[0].raw_arguments == "{not-json"
    assert normalized.tool_calls[0].parse_error

    tool = provider._to_api_tool(
        ToolDef(
            name="lookup",
            description="Lookup",
            parameters={
                "type": "object",
                "properties": {
                    "mode": {"enum": ["fast", "deep"]},
                    "query": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["mode"],
            },
        )
    )
    assert tool["function"]["parameters"]["properties"]["mode"]["enum"] == [
        "fast",
        "deep",
    ]
    assert spec.sdk_exception_refs == ("openai:OpenAIError",)


def test_unknown_provider_raises_node_user_error():
    """Unknown provider lookups surface as NodeUserError (user-correctable)."""
    from services.plugin import NodeUserError

    with pytest.raises(NodeUserError, match="Unknown LLM provider"):
        get_provider("definitely-not-real")


def test_has_provider_does_not_raise():
    """``has_provider`` is the cheap membership probe used by ai.py."""
    assert has_provider("anthropic") is True
    assert has_provider("definitely-not-real") is False
