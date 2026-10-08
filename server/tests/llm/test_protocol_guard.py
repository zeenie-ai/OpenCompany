"""RFC-0003 D6 / D9 / AG10 / AG11 at the provider boundary.

A 2xx that carries an error body instead of a completion is a routing
failure, not an empty answer, and must say which URL answered. The key a
user stored is sent as stored: no provider rewrites it to a placeholder.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import services.llm  # noqa: F401 — populate the registry
from services.llm.protocol import LLMError, LLMErrorCategory, LLMResponse, Message
from services.llm.providers.anthropic import AnthropicProvider
from services.llm.providers.openai import OpenAIProvider
from services.llm.registry import ProviderSpec
from services.llm.unifier import ChatUnifier
from services.plugin import NodeUserError

LEAKY_URL = "http://user:pw@host:1234/?token=t"


def _provider(**kwargs) -> OpenAIProvider:
    with patch("openai.AsyncOpenAI") as client_cls:
        client_cls.return_value.base_url = kwargs.get("proxy_url") or kwargs.get("base_url") or "https://api.openai.com/v1"
        return OpenAIProvider("k", provider_name="lmstudio", **kwargs)


class TestTwoHundredWithoutChoices:
    def test_an_error_body_raises_protocol_naming_the_redacted_url(self):
        provider = _provider(proxy_url=LEAKY_URL)
        response = SimpleNamespace(
            choices=None,
            usage=None,
            model_extra={"error": "Unexpected endpoint or method. (POST /chat/completions)"},
        )

        with pytest.raises(LLMError) as raised:
            provider._normalize(response, "m")

        error = raised.value
        assert error.category is LLMErrorCategory.PROTOCOL
        message = error.user_message
        assert "http://host:1234/" in message
        for secret in ("user", "pw", "token", "Unexpected endpoint", "Bearer"):
            assert secret not in message

    def test_no_choices_and_no_error_is_still_an_empty_response(self):
        response = SimpleNamespace(choices=[], usage=None, model_extra={})

        normalized = _provider()._normalize(response, "m")

        assert isinstance(normalized, LLMResponse)
        assert normalized.content == ""

    def test_a_mock_response_is_not_mistaken_for_an_error_body(self):
        response = MagicMock()
        response.choices = []

        assert _provider()._normalize(response, "m").content == ""

    async def test_the_unifier_turns_it_into_one_user_facing_error(self):
        error = LLMError(
            message="raw",
            provider="lmstudio",
            category=LLMErrorCategory.PROTOCOL,
            public_message="The server at http://host:1234 answered without a completion.",
        )
        client = MagicMock()
        client.chat = AsyncMock(side_effect=error)
        spec = ProviderSpec(name="lmstudio", factory=MagicMock(return_value=client), sdk_exception_refs=("openai:OpenAIError",))
        auth = MagicMock()
        auth.resolve_api_key = AsyncMock(return_value=None)
        unifier = ChatUnifier(defaults={"providers": {}}, auth_service=auth)

        with patch("services.llm.unifier.get_provider", return_value=spec):
            with pytest.raises(NodeUserError, match="answered without a completion"):
                await unifier.chat(
                    provider="lmstudio",
                    api_key="k",
                    messages=[Message(role="user", content="hi")],
                    model="m",
                )

    async def test_an_untranslated_failure_still_logs_where_the_call_went(self):
        # Agent steps take errors untranslated (translate_errors=False); the
        # failure log must not depend on translation (D10).
        error = LLMError(message="raw", provider="openai_compatible", category=LLMErrorCategory.PROTOCOL)
        client = MagicMock()
        client.chat = AsyncMock(side_effect=error)
        client.endpoint_url = LEAKY_URL
        client.url_source = "proxy"
        spec = ProviderSpec(
            name="openai_compatible", factory=MagicMock(return_value=client), sdk_exception_refs=("openai:OpenAIError",)
        )
        auth = MagicMock()
        auth.resolve_api_key = AsyncMock(return_value=LEAKY_URL)
        unifier = ChatUnifier(defaults={"providers": {}}, auth_service=auth)

        with patch("services.llm.unifier.get_provider", return_value=spec), patch("services.llm.unifier.logger") as log:
            with pytest.raises(LLMError):
                await unifier.chat(
                    provider="openai_compatible:home",
                    api_key="k",
                    messages=[Message(role="user", content="hi")],
                    model="m",
                    translate_errors=False,
                )

        logged = log.warning.call_args.kwargs
        assert logged["provider"] == "openai_compatible:home"
        assert logged["url"] == "http://host:1234/"
        assert logged["url_source"] == "proxy"


class TestTheStoredKeyIsSentAsStored:
    def test_openai_compatible_client_keeps_the_key_with_a_proxy(self):
        with patch("openai.AsyncOpenAI") as client_cls:
            provider = OpenAIProvider("real-key", proxy_url="http://host:1234/v1", provider_name="lmstudio")
        assert client_cls.call_args.kwargs["api_key"] == "real-key"
        assert client_cls.call_args.kwargs["base_url"] == "http://host:1234/v1"
        assert provider.url_source == "proxy"

    def test_anthropic_client_keeps_the_key_with_a_proxy(self):
        with patch("anthropic.AsyncAnthropic") as client_cls:
            AnthropicProvider("real-key", proxy_url="http://relay:8080")
        assert client_cls.call_args.kwargs["api_key"] == "real-key"

    @pytest.mark.parametrize(
        ("kwargs", "source"),
        [
            ({"proxy_url": "http://a/v1", "base_url": "http://b/v1"}, "proxy"),
            ({"base_url": "http://b/v1"}, "llm_defaults"),
            ({}, "sdk_default"),
        ],
    )
    def test_url_source_names_the_setting_that_produced_the_url(self, kwargs, source):
        with patch("openai.AsyncOpenAI"):
            provider = OpenAIProvider("k", **kwargs)
        assert provider.url_source == source
