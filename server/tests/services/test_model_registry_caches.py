"""RFC-0003 D15 / D16 / AG18: the model registry's three sources.

Models registered from the user's own servers live apart from the OpenRouter
snapshot (so a refresh cannot wipe them, and they never dirty the tracked
``config/model_registry.json``), and the LiteLLM table is trimmed, cached
under DATA_DIR, and matched conservatively.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

import services.model_registry as mr

OPENROUTER = {
    "data": [
        {
            "id": "openai/gpt-test",
            "name": "OpenAI: GPT Test",
            "context_length": 400000,
            "top_provider": {"max_completion_tokens": 128000},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }
    ]
}
LITELLM = {
    "sample_spec": {"mode": "chat", "max_input_tokens": "set to max input tokens"},
    "gpt-4o": {
        "mode": "chat",
        "max_input_tokens": 128000,
        "max_output_tokens": 16384,
        "input_cost_per_token": 2.5e-06,
        "output_cost_per_token": 1e-05,
        "litellm_provider": "openai",
        "supports_vision": True,
    },
    "together_ai/meta-llama/Llama-3.3-70B": {"mode": "chat", "max_input_tokens": 131072},
    "deepinfra/Qwen/Qwen3-8B": {"mode": "chat", "max_input_tokens": 32768},
    "fireworks_ai/Qwen/Qwen3-8B": {"mode": "chat", "max_input_tokens": 40960},
    "text-embedding-3-small": {"mode": "embedding", "max_input_tokens": 8191},
}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    snapshot = tmp_path / "config" / "model_registry.json"
    monkeypatch.setattr(mr, "CACHE_FILE", snapshot)
    monkeypatch.setattr(mr, "_local_models_path", lambda: tmp_path / "data" / "local_models.json")
    monkeypatch.setattr(mr, "_litellm_cache_path", lambda: tmp_path / "data" / "litellm_models.json")
    return tmp_path


@pytest.fixture
def registry(paths):
    svc = mr.ModelRegistryService()
    svc._load_llm_defaults()
    return svc


def _mock_feeds(litellm=LITELLM):
    respx.get(mr.OPENROUTER_MODELS_URL).mock(return_value=httpx.Response(200, json=OPENROUTER))
    return respx.get(mr.LITELLM_MODELS_URL).mock(return_value=httpx.Response(200, json=litellm))


class TestLocalModels:
    @respx.mock
    async def test_a_refresh_does_not_wipe_models_registered_from_the_users_servers(self, registry):
        registry.register_local_model("ollama", "llama3:latest", {"context_length": 4096})
        _mock_feeds()

        await registry.refresh()

        assert registry.get_context_length("llama3:latest", "ollama") == 4096
        assert registry.get_context_length("gpt-test", "openai") == 400000

    @respx.mock
    async def test_they_persist_under_data_dir_never_in_the_tracked_snapshot(self, registry, paths):
        registry.register_local_model("openai_compatible:home", "qwen3", {"context_length": 32768})
        _mock_feeds()
        await registry.refresh()

        local = json.loads((paths / "data" / "local_models.json").read_text(encoding="utf-8"))
        snapshot = json.loads((paths / "config" / "model_registry.json").read_text(encoding="utf-8"))
        assert "openai_compatible:home/qwen3" in local["models"]
        assert not any(key.startswith(("openai_compatible:", "ollama/")) for key in snapshot["models"])

        reloaded = mr.ModelRegistryService()
        reloaded.startup()
        assert reloaded.get_context_length("qwen3", "openai_compatible:home") == 32768

    def test_forgetting_a_provider_drops_only_its_models(self, registry):
        registry.register_local_model("openai_compatible:a", "m", {"context_length": 1000})
        registry.register_local_model("openai_compatible:b", "m", {"context_length": 2000})

        registry.forget_local_models("openai_compatible:a")

        assert registry.get_model_info("m", "openai_compatible:a") is None
        assert registry.get_context_length("m", "openai_compatible:b") == 2000

    def test_a_registered_price_is_kept_for_pricing(self, registry):
        registry.register_local_model(
            "openai_compatible:gw", "gpt-4o", {"context_length": 128000, "input_price_per_mtok": 2.5, "output_price_per_mtok": 10.0}
        )

        info = registry.get_model_info("gpt-4o", "openai_compatible:gw")
        assert (info.input_price_per_mtok, info.output_price_per_mtok) == (2.5, 10.0)


class TestOpenRouterCachePrices:
    """Prompt-cache prices come from OpenRouter's own catalogue, for pricing."""

    def test_cache_prices_are_read_when_the_catalogue_lists_them(self, registry):
        info = registry._parse_openrouter_model(
            {
                "id": "anthropic/claude-test",
                "name": "Anthropic: Claude Test",
                "context_length": 1_000_000,
                "top_provider": {"max_completion_tokens": 128000},
                "pricing": {
                    "prompt": "0.000002",
                    "completion": "0.00001",
                    "input_cache_read": "0.0000001",
                    "input_cache_write": "0.0000025",
                },
            }
        )

        assert (info.cache_read_price_per_mtok, info.cache_write_price_per_mtok) == (0.1, 2.5)
        # The snapshot keeps them.
        assert mr.ModelInfo.from_dict(info.to_dict()) == info

    def test_a_price_the_catalogue_does_not_list_stays_unknown(self, registry):
        info = registry._parse_openrouter_model(OPENROUTER["data"][0])

        assert info.cache_read_price_per_mtok is None
        assert info.cache_write_price_per_mtok is None


class TestLiteLLMTable:
    @respx.mock
    async def test_refresh_keeps_chat_models_and_the_four_fields_read(self, registry, paths):
        _mock_feeds()

        await registry.refresh()

        assert registry.lookup_litellm("gpt-4o") == {
            "max_input_tokens": 128000,
            "max_output_tokens": 16384,
            "input_cost_per_token": 2.5e-06,
            "output_cost_per_token": 1e-05,
        }
        assert registry.lookup_litellm("text-embedding-3-small") is None
        assert registry.lookup_litellm("sample_spec") is None
        assert (paths / "data" / "litellm_models.json").exists()

    @respx.mock
    async def test_a_host_prefixed_key_matches_only_when_unambiguous(self, registry):
        _mock_feeds()
        await registry.refresh()

        assert registry.lookup_litellm("meta-llama/Llama-3.3-70B") == {"max_input_tokens": 131072}
        # Two hosts serve it with different windows: no guess.
        assert registry.lookup_litellm("Qwen/Qwen3-8B") is None
        assert registry.lookup_litellm("never-heard-of-it") is None

    @respx.mock
    async def test_a_failed_fetch_keeps_the_cached_copy(self, registry):
        _mock_feeds()
        await registry.refresh()
        respx.get(mr.LITELLM_MODELS_URL).mock(side_effect=httpx.ConnectError("offline"))

        await registry._refresh_litellm()

        assert registry.lookup_litellm("gpt-4o") is not None

    @respx.mock
    async def test_offline_with_no_cache_simply_matches_nothing(self, registry):
        respx.get(mr.LITELLM_MODELS_URL).mock(side_effect=httpx.ConnectError("offline"))

        await registry.ensure_litellm_table()

        assert registry.lookup_litellm("gpt-4o") is None

    @respx.mock
    async def test_an_on_demand_fetch_that_failed_is_not_repeated_on_every_save(self, registry):
        route = respx.get(mr.LITELLM_MODELS_URL).mock(side_effect=httpx.ConnectError("offline"))

        await registry.ensure_litellm_table()
        await registry.ensure_litellm_table()

        assert route.call_count == 1

    async def test_an_on_demand_fetch_is_bounded_in_total(self, registry, monkeypatch):
        calls = 0

        async def slow_refresh():
            nonlocal calls
            calls += 1
            await asyncio.sleep(5)

        monkeypatch.setattr(registry, "_refresh_litellm", slow_refresh)

        await asyncio.wait_for(registry.ensure_litellm_table(timeout=0.05), 1)
        await registry.ensure_litellm_table(timeout=0.05)

        assert calls == 1
        assert registry.lookup_litellm("gpt-4o") is None

    @respx.mock
    async def test_the_cache_is_reloaded_on_startup(self, registry):
        _mock_feeds()
        await registry.refresh()

        reloaded = mr.ModelRegistryService()
        reloaded.startup()

        assert reloaded.lookup_litellm("gpt-4o")["max_input_tokens"] == 128000
