"""Phase A1+A3 contract — ChatUnifier translates typed SDK exceptions into NodeUserError.

For each registered provider, simulate the typed SDK error its
``ProviderSpec.sdk_exception_types`` declares and assert the unifier
wraps it into ``NodeUserError``. Replaces the per-provider
``except openai.OpenAIError`` block that previously lived in ai.py.
"""

import pytest
import anthropic
import openai
from google.genai import errors as google_genai_errors
from unittest.mock import AsyncMock, MagicMock

import services.llm  # noqa: F401 — populate registry
from services.llm.registry import ProviderSpec, register_provider, _reset_for_tests, get_provider
from services.llm.unifier import ChatUnifier
from services.llm.protocol import Message
from services.plugin import NodeUserError


@pytest.fixture
def auth_service_stub():
    """Stub auth_service whose get_api_key returns None for ``{provider}_proxy`` lookups."""
    stub = MagicMock()
    stub.get_api_key = AsyncMock(return_value=None)
    return stub


@pytest.fixture
def unifier(auth_service_stub):
    """ChatUnifier wired with empty defaults — no incompatible_models filter."""
    return ChatUnifier(defaults={"providers": {}}, auth_service=auth_service_stub)


def _stub_client_that_raises(exc: Exception):
    """Build a provider client whose chat() / fetch_models() raise ``exc``."""
    client = MagicMock()
    client.chat = AsyncMock(side_effect=exc)
    client.fetch_models = AsyncMock(side_effect=exc)
    return client


def _replace_factory(provider_name: str, exc: Exception):
    """Re-register the named provider with a factory returning the failing client."""
    original = get_provider(provider_name)
    failing_client = _stub_client_that_raises(exc)
    # Build a wrapping spec — same exception refs, factory swapped for a stub
    spec = ProviderSpec(
        name=original.name,
        factory=lambda **kwargs: failing_client,
        sdk_exception_refs=original.sdk_exception_refs,
        client_kwargs=original.client_kwargs,
    )
    # Direct registry write (bypasses register_provider's conflict guard
    # for test purposes). Production code never does this.
    from services.llm import registry as _registry

    _registry._REGISTRY[provider_name] = spec
    return original


@pytest.mark.asyncio
async def test_anthropic_typed_error_becomes_node_user_error(unifier):
    original = _replace_factory("anthropic", anthropic.APIError(message="bad key", request=MagicMock(), body=None))
    try:
        with pytest.raises(NodeUserError, match="bad key"):
            await unifier.chat(
                provider="anthropic",
                api_key="sk-test",
                messages=[Message(role="user", content="hi")],
                model="claude-sonnet-4-6",
            )
    finally:
        from services.llm import registry as _registry

        _registry._REGISTRY["anthropic"] = original


@pytest.mark.asyncio
async def test_openai_typed_error_becomes_node_user_error(unifier):
    original = _replace_factory("openai", openai.AuthenticationError(message="bad key", response=MagicMock(), body=None))
    try:
        with pytest.raises(NodeUserError, match="bad key"):
            await unifier.chat(
                provider="openai",
                api_key="sk-test",
                messages=[Message(role="user", content="hi")],
                model="gpt-5",
            )
    finally:
        from services.llm import registry as _registry

        _registry._REGISTRY["openai"] = original


@pytest.mark.asyncio
async def test_gemini_typed_error_becomes_node_user_error(unifier):
    # The motivating bug — antigravity-preview hits an APIError because
    # it requires the Interactions API. The unifier must surface this as
    # NodeUserError without a traceback.
    original = _replace_factory(
        "gemini",
        google_genai_errors.APIError(code=400, response_json={"message": "This model only supports Interactions API."}),
    )
    try:
        with pytest.raises(NodeUserError, match="Interactions API"):
            await unifier.chat(
                provider="gemini",
                api_key="key",
                messages=[Message(role="user", content="hi")],
                model="antigravity-preview-05-2026",
            )
    finally:
        from services.llm import registry as _registry

        _registry._REGISTRY["gemini"] = original


@pytest.mark.asyncio
async def test_openrouter_typed_error_becomes_node_user_error(unifier):
    original = _replace_factory("openrouter", openai.RateLimitError(message="429", response=MagicMock(), body=None))
    try:
        with pytest.raises(NodeUserError, match="429"):
            await unifier.chat(
                provider="openrouter",
                api_key="or-key",
                messages=[Message(role="user", content="hi")],
                model="anthropic/claude-sonnet-4.6",
            )
    finally:
        from services.llm import registry as _registry

        _registry._REGISTRY["openrouter"] = original


@pytest.mark.asyncio
async def test_unknown_provider_raises_node_user_error_directly(unifier):
    with pytest.raises(NodeUserError, match="Unknown LLM provider"):
        await unifier.chat(
            provider="definitely-not-real",
            api_key="key",
            messages=[Message(role="user", content="hi")],
            model="x",
        )
