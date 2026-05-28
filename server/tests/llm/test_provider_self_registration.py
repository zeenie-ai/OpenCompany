"""Phase A1 contract — every shipped provider self-registers on import.

Locks the plugin shape: importing ``services.llm`` (and transitively
``services.llm.providers``) must populate the global registry with the
full set of native + OpenAI-compat providers. If any provider drops its
``register_provider(...)`` call, this test catches it.
"""

import pytest

import services.llm  # noqa: F401 — side-effect import populates the registry
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


def test_all_expected_providers_registered():
    """Every native + compat provider populates the registry on import."""
    assert set(all_providers()) >= EXPECTED_REGISTERED_PROVIDERS


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


def test_unknown_provider_raises_node_user_error():
    """Unknown provider lookups surface as NodeUserError (user-correctable)."""
    from services.plugin import NodeUserError

    with pytest.raises(NodeUserError, match="Unknown LLM provider"):
        get_provider("definitely-not-real")


def test_has_provider_does_not_raise():
    """``has_provider`` is the cheap membership probe used by ai.py."""
    assert has_provider("anthropic") is True
    assert has_provider("definitely-not-real") is False
