"""Base-URL completion for OpenAI-compatible endpoints.

Regression cover for the LM Studio failure mode: a stored proxy URL of
``http://host:port`` (no path) made the OpenAI SDK POST to
``/chat/completions``. LM Studio answers that path with **HTTP 200** and an
``{"error": ...}`` body, so nothing raises and the agent reports an empty
response instead of a routing mistake.

Two invariants:

1. ``normalize_openai_base_url`` completes a path-less URL to ``/v1`` and
   leaves any explicit path alone (a gateway may serve ``/openai``).
2. ``OpenAIProvider`` applies it at the point of use, so a URL already
   persisted without ``/v1`` works without the operator re-entering it.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.llm.config import normalize_openai_base_url
from services.llm.providers.openai import OpenAIProvider


@pytest.mark.parametrize(
    "given,expected",
    [
        # The failure case: bare host:port gets the OpenAI-compat suffix.
        ("http://192.168.4.93:1234", "http://192.168.4.93:1234/v1"),
        ("http://localhost:1234/", "http://localhost:1234/v1"),
        ("  http://localhost:1234  ", "http://localhost:1234/v1"),
        ("https://example.com", "https://example.com/v1"),
        # Already complete: untouched (beyond the trailing-slash trim).
        ("http://localhost:1234/v1", "http://localhost:1234/v1"),
        ("http://localhost:1234/v1/", "http://localhost:1234/v1"),
        # An explicit path is the operator's choice — a gateway may mount the
        # OpenAI surface anywhere. Never rewrite it.
        ("https://gw.example.com/openai", "https://gw.example.com/openai"),
        ("https://gw.example.com/api/v2", "https://gw.example.com/api/v2"),
        # Empty stays empty: the caller decides whether that's an error.
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_openai_base_url(given, expected):
    assert normalize_openai_base_url(given) == expected


@pytest.mark.parametrize("kwarg", ["proxy_url", "base_url"])
def test_provider_completes_path_less_url(kwarg):
    """Whichever way the URL arrives, the SDK receives the ``/v1`` base."""
    with patch("openai.AsyncOpenAI", MagicMock()) as client:
        OpenAIProvider("k", provider_name="lmstudio", **{kwarg: "http://192.168.4.93:1234"})
    assert client.call_args.kwargs["base_url"] == "http://192.168.4.93:1234/v1"


def test_provider_leaves_explicit_path_alone():
    with patch("openai.AsyncOpenAI", MagicMock()) as client:
        OpenAIProvider("k", base_url="https://gw.example.com/openai")
    assert client.call_args.kwargs["base_url"] == "https://gw.example.com/openai"


def test_provider_without_url_passes_no_base_url():
    """Plain OpenAI must keep the SDK's own default endpoint."""
    with patch("openai.AsyncOpenAI", MagicMock()) as client:
        OpenAIProvider("k")
    assert "base_url" not in client.call_args.kwargs
