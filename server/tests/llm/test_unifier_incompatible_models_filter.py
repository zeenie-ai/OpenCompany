"""Phase A4 contract — ChatUnifier filters ``incompatible_models`` from fetch_models.

JSON entry: ``providers.<name>.incompatible_models`` in
``llm_defaults.json``. The unifier applies the filter uniformly so
adding an entry is a pure JSON edit — no per-provider Python.

The current motivating case is ``antigravity-preview-05-2026`` on
gemini, which requires the Interactions API and surfaces as an
``APIError`` if selected. Filtering it from the dropdown prevents the
failure at the source.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

import services.llm  # noqa: F401 — populate registry
from services.llm.registry import ProviderSpec, _reset_for_tests
from services.llm.unifier import ChatUnifier
from services.llm.providers.openai import OpenAIProvider


@pytest.fixture
def auth_service_stub():
    stub = MagicMock()
    stub.get_api_key = AsyncMock(return_value=None)
    return stub


def _swap_provider_factory(name: str, models: list[str]):
    """Replace the registered provider's factory so fetch_models returns ``models``."""
    from services.llm import registry as _registry

    original = _registry._REGISTRY[name]
    stub_client = MagicMock()
    stub_client.fetch_models = AsyncMock(return_value=models)
    _registry._REGISTRY[name] = ProviderSpec(
        name=original.name,
        factory=lambda **kwargs: stub_client,
        sdk_exception_refs=original.sdk_exception_refs,
        client_kwargs=original.client_kwargs,
    )
    return original


def _restore(name, original):
    from services.llm import registry as _registry

    _registry._REGISTRY[name] = original


@pytest.mark.asyncio
async def test_gemini_filters_antigravity_preview(auth_service_stub):
    """The motivating case — antigravity model never reaches the dropdown."""
    defaults = {
        "providers": {
            "gemini": {
                "incompatible_models": ["antigravity-preview-05-2026"],
            }
        }
    }
    unifier = ChatUnifier(defaults=defaults, auth_service=auth_service_stub)
    original = _swap_provider_factory(
        "gemini",
        [
            "gemini-3-pro-preview",
            "gemini-flash-latest",
            "antigravity-preview-05-2026",
            "gemini-2.5-pro",
        ],
    )
    try:
        models = await unifier.fetch_models(provider="gemini", api_key="key")
        assert "antigravity-preview-05-2026" not in models
        assert "gemini-3-pro-preview" in models
        assert "gemini-flash-latest" in models
        assert "gemini-2.5-pro" in models
    finally:
        _restore("gemini", original)


@pytest.mark.asyncio
async def test_no_filter_when_json_key_absent(auth_service_stub):
    """A provider without an ``incompatible_models`` JSON key passes through unchanged."""
    defaults = {"providers": {"anthropic": {}}}
    unifier = ChatUnifier(defaults=defaults, auth_service=auth_service_stub)
    original = _swap_provider_factory(
        "anthropic", ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]
    )
    try:
        models = await unifier.fetch_models(provider="anthropic", api_key="sk-test")
        assert models == ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]
    finally:
        _restore("anthropic", original)


@pytest.mark.asyncio
async def test_empty_filter_list_is_noop(auth_service_stub):
    """An empty list is treated the same as a missing key."""
    defaults = {"providers": {"openai": {"incompatible_models": []}}}
    unifier = ChatUnifier(defaults=defaults, auth_service=auth_service_stub)
    original = _swap_provider_factory("openai", ["gpt-5", "gpt-4o"])
    try:
        models = await unifier.fetch_models(provider="openai", api_key="sk-test")
        assert models == ["gpt-5", "gpt-4o"]
    finally:
        _restore("openai", original)
