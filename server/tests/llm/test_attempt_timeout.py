"""Local and OpenAI-compatible servers get a 30-minute LLM attempt (1b.25).

A server that does not stream sends nothing until the whole answer is done,
so the OpenAI SDK's 10-minute read timeout cut slow local answers. Three
provider blocks declare ``attempt_timeout_seconds``; ``OpenAIProvider``
gives its client that timeout, and no other provider's client changes.
"""

from __future__ import annotations

from unittest.mock import patch

import openai
import pytest

import services.llm  # noqa: F401 — side-effect import populates the registry
from services.llm import config
from services.llm.config import attempt_timeout_seconds
from services.llm.providers.openai import OpenAIProvider
from services.llm.providers.openrouter import OpenRouterProvider
from services.llm.registry import all_providers, get_provider


def test_only_local_and_compatible_servers_declare_a_longer_attempt():
    declared = {p: attempt_timeout_seconds(p) for p in all_providers() if attempt_timeout_seconds(p) is not None}
    assert declared == {"ollama": 1800.0, "lmstudio": 1800.0, "openai_compatible": 1800.0}
    # A named endpoint reads the shared block.
    assert attempt_timeout_seconds("openai_compatible:home") == 1800.0
    assert attempt_timeout_seconds("not-a-provider") is None


@pytest.mark.parametrize("name", sorted(p for p in all_providers() if get_provider(p).factory is OpenAIProvider))
def test_the_client_timeout_follows_the_providers_block(name):
    with patch("openai.AsyncOpenAI") as client_cls:
        OpenAIProvider("k", **get_provider(name).client_kwargs)
    kwargs = client_cls.call_args.kwargs
    seconds = attempt_timeout_seconds(name)
    if seconds is None:
        assert "timeout" not in kwargs
    else:
        assert kwargs["timeout"] == openai.Timeout(seconds, connect=openai.DEFAULT_TIMEOUT.connect)


def test_the_value_is_read_from_the_block(monkeypatch):
    monkeypatch.setitem(config.LLM_DEFAULTS["providers"], "ollama", {"attempt_timeout_seconds": 7})
    with patch("openai.AsyncOpenAI") as client_cls:
        OpenAIProvider("k", provider_name="ollama")
    assert client_cls.call_args.kwargs["timeout"] == openai.Timeout(7, connect=openai.DEFAULT_TIMEOUT.connect)


def test_openrouter_keeps_the_sdk_default():
    with patch("openai.AsyncOpenAI") as client_cls:
        OpenRouterProvider("k")
    assert "timeout" not in client_cls.call_args.kwargs
