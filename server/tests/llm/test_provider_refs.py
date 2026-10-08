"""RFC-0003 D6/D7/D13 at call time: provider references and credentials.

A named endpoint is the provider reference ``openai_compatible:<slug>``.
Its credential rows are keyed by the full reference; everything read from
``llm_defaults.json`` resolves through the shared ``openai_compatible``
block (AG16). No path may hand an SDK an empty key (AG8).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx

import services.llm  # noqa: F401 — populate the registry
from services.llm.config import (
    ENDPOINT_PROVIDER,
    curated_models,
    endpoint_ref,
    get_default_model,
    is_model_valid_for_provider,
    resolve_credential,
    split_provider_ref,
    supports_model_listing,
)
from services.llm.protocol import LLMResponse, Message
from services.llm.registry import ProviderSpec, all_providers
from services.llm.unifier import ChatUnifier
from services.plugin import NodeUserError

REF = "openai_compatible:home"
SERVER_DIR = Path(__file__).resolve().parents[2]


def _unifier(get_api_key) -> ChatUnifier:
    auth = MagicMock()
    auth.resolve_api_key = get_api_key
    return ChatUnifier(defaults={"providers": {}}, auth_service=auth)


async def _chat(unifier: ChatUnifier, provider: str, api_key: str = "k"):
    return await unifier.chat(
        provider=provider,
        api_key=api_key,
        messages=[Message(role="user", content="hi")],
        model="m",
    )


class TestReferences:
    def test_split_and_build_round_trip(self):
        assert split_provider_ref("ollama") == ("ollama", "")
        assert split_provider_ref(REF) == (ENDPOINT_PROVIDER, "home")
        assert endpoint_ref("home") == REF
        assert split_provider_ref("") == ("", "")


class TestResolveCredential:
    def test_a_stored_key_wins(self):
        assert resolve_credential("ollama", "real-key") == "real-key"

    @pytest.mark.parametrize(
        ("provider", "placeholder"),
        [("ollama", "ollama"), ("lmstudio", "lm-studio"), (REF, "sk-no-key-required")],
    )
    def test_keyless_servers_get_the_placeholder_their_vendor_documents(self, provider, placeholder):
        assert resolve_credential(provider, None) == placeholder
        assert resolve_credential(provider, "") == placeholder

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "deepseek"])
    def test_no_key_and_no_placeholder_is_an_error_not_none(self, provider):
        with pytest.raises(ValueError):
            resolve_credential(provider, None)


class TestReferencesReadTheSharedBlock:
    def test_default_model_is_not_a_cloud_fallback(self):
        # An unknown provider would get the "gpt-5.2" literal.
        assert get_default_model(REF) == ""

    def test_listing_is_supported_and_nothing_is_curated(self):
        assert supports_model_listing(REF) is True
        assert curated_models(REF) == []

    @pytest.mark.parametrize("provider", [REF, "ollama", "lmstudio", "openrouter", "groq"])
    def test_open_world_providers_accept_any_model_id(self, provider):
        assert is_model_valid_for_provider("owner/any-model:7b", provider) is True

    def test_closed_world_providers_still_pattern_check(self):
        assert is_model_valid_for_provider("gpt-5.6-sol", "openai") is True
        assert is_model_valid_for_provider("llama3", "openai") is False


class TestRegistryFallbacks:
    @pytest.fixture
    def registry(self, tmp_path, monkeypatch):
        import services.model_registry as mr

        monkeypatch.setattr(mr, "_local_models_path", lambda: tmp_path / "local_models.json")
        svc = mr.ModelRegistryService()
        svc._load_llm_defaults()
        return svc

    def test_an_unsized_endpoint_model_gets_the_conservative_defaults(self, registry):
        assert registry.get_context_length("unknown-model", REF) == 8192
        assert registry.get_max_output_tokens("unknown-model", REF) == 2048
        assert registry.get_temperature_range("unknown-model", REF) == (0.0, 2.0)

    def test_a_model_registered_at_save_time_wins(self, registry):
        registry.register_local_model(REF, "qwen3", {"context_length": 32768})

        assert registry.get_context_length("qwen3", REF) == 32768
        # Another endpoint serving the same id is a different server.
        assert registry.get_context_length("qwen3", "openai_compatible:other") == 8192


class TestPricing:
    @pytest.fixture
    def pricing(self):
        # tests/conftest.py stubs services.pricing; load the real module.
        spec = importlib.util.spec_from_file_location("_real_pricing", SERVER_DIR / "services" / "pricing.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.PricingService()

    def test_an_endpoint_is_free_by_default(self, pricing):
        price = pricing.get_pricing(REF, "qwen3")
        assert (price.input_per_mtok, price.output_per_mtok) == (0.0, 0.0)

    def test_an_endpoint_uses_the_price_recorded_when_it_was_saved(self, pricing):
        info = MagicMock(input_price_per_mtok=3.0, output_price_per_mtok=15.0)
        registry = MagicMock()
        registry.get_model_info.return_value = info
        with patch("services.model_registry.get_model_registry", return_value=registry):
            price = pricing.get_pricing(REF, "gpt-4o")
        assert (price.input_per_mtok, price.output_per_mtok) == (3.0, 15.0)
        registry.get_model_info.assert_called_once_with("gpt-4o", REF)

    def test_other_providers_keep_their_curated_prices(self, pricing):
        price = pricing.get_pricing("openai", "gpt-6-astra")
        assert (price.input_per_mtok, price.output_per_mtok) == (10.0, 50.0)


class TestUnifierGuards:
    async def test_no_request_leaves_without_a_key(self, monkeypatch):
        """AG8: the SDK would read OPENAI_API_KEY and send it to the base URL."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-operator-secret")
        unifier = _unifier(AsyncMock(return_value=None))
        keyless = {name for name in all_providers() if name in ("ollama", "lmstudio", ENDPOINT_PROVIDER)}

        with respx.mock(assert_all_called=False) as mock:
            for provider in sorted(set(all_providers()) - keyless):
                with pytest.raises(NodeUserError, match="No API key"):
                    await _chat(unifier, provider, api_key="")
            assert mock.calls.call_count == 0

    async def test_an_endpoint_without_its_url_row_is_not_configured(self):
        factory = MagicMock()
        spec = ProviderSpec(name=ENDPOINT_PROVIDER, factory=factory, sdk_exception_refs=("openai:OpenAIError",))
        unifier = _unifier(AsyncMock(return_value=None))

        with patch("services.llm.unifier.get_provider", return_value=spec):
            with pytest.raises(NodeUserError, match="'home' is not configured"):
                await _chat(unifier, REF)
        factory.assert_not_called()

    async def test_an_endpoint_reads_its_own_url_row_and_placeholder(self):
        looked_up = []

        async def get_api_key(name, session_id="default"):
            looked_up.append(name)
            return "http://host:8080/v1" if name == f"{REF}_proxy" else None

        client = MagicMock()
        client.chat = AsyncMock(return_value=LLMResponse(content="ok"))
        factory = MagicMock(return_value=client)
        spec = ProviderSpec(
            name=ENDPOINT_PROVIDER,
            factory=factory,
            sdk_exception_refs=("openai:OpenAIError",),
            client_kwargs={"provider_name": ENDPOINT_PROVIDER},
        )
        unifier = _unifier(get_api_key)

        with patch("services.llm.unifier.get_provider", return_value=spec) as lookup:
            await _chat(unifier, REF, api_key="")

        lookup.assert_called_once_with(ENDPOINT_PROVIDER)
        assert f"{REF}_proxy" in looked_up
        kwargs = factory.call_args.kwargs
        assert kwargs["proxy_url"] == "http://host:8080/v1"
        assert kwargs["api_key"] == "sk-no-key-required"
        assert kwargs["provider_name"] == ENDPOINT_PROVIDER
