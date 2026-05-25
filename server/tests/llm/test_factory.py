"""Test create_provider() factory and provider detection."""

from unittest.mock import patch

from services.llm.factory import create_provider, is_native_provider, NATIVE_PROVIDERS
from services.llm.config import detect_provider_from_model
from services.llm.protocol import LLMProvider


def test_native_providers_set():
    assert NATIVE_PROVIDERS == {
        "anthropic",
        "openai",
        "gemini",
        "openrouter",
        "xai",
        "deepseek",
        "kimi",
        "mistral",
        "ollama",
        "lmstudio",
    }


def test_is_native_provider():
    assert is_native_provider("openai") is True
    assert is_native_provider("groq") is False


def test_create_anthropic_provider():
    with patch("anthropic.AsyncAnthropic"):
        p = create_provider("anthropic", "sk-test")
        assert p.provider_name == "anthropic"
        assert isinstance(p, LLMProvider)


def test_create_openai_provider():
    with patch("openai.AsyncOpenAI"):
        p = create_provider("openai", "sk-test")
        assert p.provider_name == "openai"
        assert isinstance(p, LLMProvider)


def test_create_gemini_provider():
    with patch("google.genai.Client"):
        p = create_provider("gemini", "key")
        assert p.provider_name == "gemini"
        assert isinstance(p, LLMProvider)


def test_create_openrouter_provider():
    with patch("openai.AsyncOpenAI"):
        p = create_provider("openrouter", "or-key")
        assert p.provider_name == "openrouter"


def test_create_xai_uses_openai_with_base_url():
    with patch("openai.AsyncOpenAI") as mock_cls:
        p = create_provider("xai", "xai-key")
        assert p.provider_name == "openai"  # reuses OpenAI class
        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["base_url"] == "https://api.x.ai/v1"


def test_create_unknown_raises():
    import pytest

    with pytest.raises(ValueError, match="Unknown provider"):
        create_provider("nonexistent", "key")


def test_detect_provider_from_model():
    assert detect_provider_from_model("gpt-5.2") == "openai"
    assert detect_provider_from_model("o3-mini") == "openai"
    assert detect_provider_from_model("claude-sonnet-4-6") == "anthropic"
    assert detect_provider_from_model("gemini-2.5-flash") == "gemini"
    assert detect_provider_from_model("grok-3") == "xai"
    assert detect_provider_from_model("unknown-xyz") == "openai"  # fallback
