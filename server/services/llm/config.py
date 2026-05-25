"""Provider configuration and model resolution.

Loads provider metadata from config/llm_defaults.json.
No LangChain imports -- pure config and resolution logic.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Provider config dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProviderConfig:
    """Metadata for a single LLM provider."""

    name: str
    default_model: str
    detection_patterns: Tuple[str, ...]
    models_endpoint: str
    api_key_header: str  # e.g. "Authorization", "x-api-key"
    api_key_format: str = "Bearer {key}"  # how the header value is built
    extra_headers: Dict[str, str] = field(default_factory=dict)
    base_url: str = ""  # OpenAI-compatible base URL (e.g. "https://api.deepseek.com")


# ---------------------------------------------------------------------------
# Load config/llm_defaults.json once at import time
# ---------------------------------------------------------------------------


def _load_llm_defaults() -> Dict[str, Any]:
    config_path = Path(__file__).parent.parent.parent / "config" / "llm_defaults.json"
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load llm_defaults.json: {e}")
        return {"providers": {}}


LLM_DEFAULTS: Dict[str, Any] = _load_llm_defaults()


def reload_defaults() -> None:
    """Reload llm_defaults.json (e.g. after model registry refresh)."""
    global LLM_DEFAULTS
    LLM_DEFAULTS = _load_llm_defaults()


# ---------------------------------------------------------------------------
# Provider-specific auth overrides (most providers use Bearer auth)
# ---------------------------------------------------------------------------

_AUTH_OVERRIDES: Dict[str, Dict[str, str]] = {
    "anthropic": {"api_key_header": "x-api-key", "api_key_format": "{key}"},
    "gemini": {"api_key_header": "", "api_key_format": ""},  # API key in URL query param
}


# ---------------------------------------------------------------------------
# Provider registry -- built dynamically from llm_defaults.json
# ---------------------------------------------------------------------------


def _build_provider_configs() -> Dict[str, ProviderConfig]:
    """Build ProviderConfig entries from llm_defaults.json."""
    providers = LLM_DEFAULTS.get("providers", {})
    configs: Dict[str, ProviderConfig] = {}

    for name, prov in providers.items():
        auth = _AUTH_OVERRIDES.get(name, {})
        configs[name] = ProviderConfig(
            name=name,
            default_model=prov.get("default_model", ""),
            detection_patterns=tuple(prov.get("detection_patterns", [name])),
            models_endpoint=prov.get("models_endpoint", ""),
            api_key_header=auth.get("api_key_header", "Authorization"),
            api_key_format=auth.get("api_key_format", "Bearer {key}"),
            extra_headers=prov.get("extra_headers", {}),
            base_url=prov.get("base_url", ""),
        )

    return configs


PROVIDER_CONFIGS: Dict[str, ProviderConfig] = _build_provider_configs()


def get_provider_config(provider: str) -> Optional[ProviderConfig]:
    return PROVIDER_CONFIGS.get(provider)


# ---------------------------------------------------------------------------
# Provider detection from model name
# ---------------------------------------------------------------------------


def detect_provider_from_model(model: str) -> str:
    model_lower = model.lower()
    for name, cfg in PROVIDER_CONFIGS.items():
        if any(p in model_lower for p in cfg.detection_patterns):
            return name
    return "openai"


def is_model_valid_for_provider(model: str, provider: str) -> bool:
    # Open-world providers — OpenRouter is a multi-vendor proxy, ollama
    # and lmstudio serve user-installed local models whose names don't
    # match any "lmstudio"/"ollama" substring. Treat as always-valid;
    # the upstream API will 404 a genuinely missing model. See the
    # mirror in services/ai.py for the full rationale.
    if provider in ("openrouter", "ollama", "lmstudio"):
        return True
    cfg = PROVIDER_CONFIGS.get(provider)
    if not cfg:
        return True
    model_lower = model.lower()
    return any(p in model_lower for p in cfg.detection_patterns)


# ---------------------------------------------------------------------------
# Default model helpers
# ---------------------------------------------------------------------------


def get_default_model(provider: str) -> str:
    cfg = PROVIDER_CONFIGS.get(provider)
    return cfg.default_model if cfg else "gpt-5.2"


async def get_default_model_async(provider: str, database) -> str:
    """DB user setting > JSON config > fallback."""
    if database:
        try:
            db_defaults = await database.get_provider_defaults(provider)
            if db_defaults and db_defaults.get("default_model"):
                return db_defaults["default_model"]
        except Exception as e:
            logger.warning(f"Failed to get DB defaults for {provider}: {e}")
    return get_default_model(provider)


# ---------------------------------------------------------------------------
# Max-tokens / temperature resolution
# ---------------------------------------------------------------------------


def resolve_max_tokens(params: dict, model: str, provider: str) -> int:
    """Resolve max_tokens: user param -> model registry -> llm_defaults -> 4096."""
    from services.model_registry import get_model_registry

    registry = get_model_registry()
    model_max = registry.get_max_output_tokens(model, provider)

    user_val = params.get("max_tokens")
    if user_val:
        user_int = int(user_val)
        if user_int > model_max:
            logger.info(f"[AI] Clamping max_tokens {user_int} -> {model_max} for {provider}/{model}")
            return model_max
        return user_int
    return model_max


def resolve_temperature(params: dict, model: str, provider: str, thinking_enabled: bool) -> float:
    """Resolve temperature with model-specific constraints."""
    from services.model_registry import get_model_registry

    registry = get_model_registry()

    user_temp = float(params.get("temperature", 0.7))

    if registry.is_reasoning_model(model, provider):
        return 1.0

    if thinking_enabled and provider == "anthropic":
        return 1.0

    # Fixed temperature per model from llm_defaults.json (e.g. kimi-k2.5 = 0.6)
    prov_json = LLM_DEFAULTS.get("providers", {}).get(provider, {})
    fixed_temps = prov_json.get("fixed_temperature", {})
    for prefix, fixed_temp in fixed_temps.items():
        if model.startswith(prefix):
            return float(fixed_temp)

    lo, hi = registry.get_temperature_range(model, provider)
    return max(lo, min(hi, user_temp))


def build_headers(provider: str, api_key: str) -> Dict[str, str]:
    """Build HTTP headers for a provider (used by fetch_models)."""
    cfg = PROVIDER_CONFIGS.get(provider)
    if not cfg:
        return {"Authorization": f"Bearer {api_key}"}
    headers = dict(cfg.extra_headers)
    if cfg.api_key_header:
        headers[cfg.api_key_header] = cfg.api_key_format.format(key=api_key)
    return headers
