"""Curated global-model list stays aligned with the router cache."""

import json
from pathlib import Path

from services.ai import AIService


CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def _defaults():
    return json.loads((CONFIG_DIR / "llm_defaults.json").read_text(encoding="utf-8"))


def _registry():
    return json.loads((CONFIG_DIR / "model_registry.json").read_text(encoding="utf-8"))


def test_cloud_defaults_are_present_in_explicit_global_lists() -> None:
    providers = _defaults()["providers"]
    for provider, config in providers.items():
        popular = config.get("popular_models")
        if not popular:
            continue
        assert len(popular) == len(set(popular)), f"{provider} has duplicate popular models"
        assert config["default_model"] in popular, f"{provider} default is absent from global list"


def test_openrouter_global_models_exist_in_latest_router_cache() -> None:
    defaults = _defaults()["providers"]["openrouter"]
    registry_ids = {entry["id"] for entry in _registry()["models"].values()}
    assert set(defaults["popular_models"]).issubset(registry_ids)


def test_latest_router_families_are_exposed() -> None:
    # Global lists carry only >=1M-context models (llm_defaults _description
    # policy, 2026-07-23) — hence grok-4.20 (2M) rather than the 500K grok-4.5.
    providers = _defaults()["providers"]
    assert "gpt-5.6-sol-pro" in providers["openai"]["popular_models"]
    assert "claude-sonnet-5" in providers["anthropic"]["popular_models"]
    assert "gemini-3.5-flash" in providers["gemini"]["popular_models"]
    assert "kimi-k3" in providers["kimi"]["popular_models"]
    assert "grok-4.20" in providers["xai"]["popular_models"]


def test_global_lists_only_carry_1m_context_models() -> None:
    """Every popular_models entry must have a >=1M-token context window.

    Context comes from the provider's own curated context_length map;
    entries without a per-model value (e.g. openrouter's vendor-prefixed
    ids) are exempt because their true windows live in the router cache.
    """
    providers = _defaults()["providers"]
    for provider, config in providers.items():
        context_map = config.get("context_length", {})
        for model in config.get("popular_models", []):
            ctx = context_map.get(model)
            if ctx is None:
                continue
            assert ctx >= 1_000_000, f"{provider}/{model} has {ctx} < 1M context but is in the global list"


def test_ai_service_offline_fallback_uses_explicit_order() -> None:
    expected = _defaults()["providers"]["openrouter"]["popular_models"]
    assert AIService._get_curated_models(None, "openrouter") == expected
