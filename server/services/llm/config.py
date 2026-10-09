"""Provider configuration and model resolution.

Loads provider metadata from config/llm_defaults.json.
Pure config and resolution logic.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Mapping, Optional, Tuple

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
    return PROVIDER_CONFIGS.get(split_provider_ref(provider)[0])


# ---------------------------------------------------------------------------
# Provider references (RFC-0003 D13)
# ---------------------------------------------------------------------------

#: The provider whose instances are user-named endpoints.
ENDPOINT_PROVIDER = "openai_compatible"


def split_provider_ref(ref: str) -> Tuple[str, str]:
    """Split a provider reference into ``(provider, endpoint_slug)``.

    A reference is either a registered provider id (``"ollama"``) or a
    named OpenAI-compatible endpoint (``"openai_compatible:home-vllm"``);
    the slug is ``""`` for a plain provider. This is the only parser of
    the format. Every lookup keyed by ``llm_defaults.json`` resolves the
    reference through it, so an endpoint reads the ``openai_compatible``
    block while its credential rows stay keyed by the full reference.
    """
    name, _, slug = (ref or "").partition(":")
    return name, slug


def endpoint_ref(slug: str) -> str:
    """The provider reference of the named endpoint ``slug``."""
    return f"{ENDPOINT_PROVIDER}:{slug}"


def _provider_block(provider: str) -> Dict[str, Any]:
    """The ``llm_defaults.json`` block for a provider reference."""
    return LLM_DEFAULTS.get("providers", {}).get(split_provider_ref(provider)[0], {})


def resolve_credential(provider: str, stored: Optional[str]) -> str:
    """Return the key to send for ``provider``.

    The stored key when there is one, else the placeholder the vendor
    documents for keyless servers, declared as
    ``providers.<name>.auth.placeholder_key`` (Ollama's docs: ``"ollama"``,
    "required but ignored"). Raises ``ValueError`` when neither exists.

    Never returns ``None``: the OpenAI SDK reads ``OPENAI_API_KEY`` when
    handed ``api_key=None`` and would send the operator's OpenAI key to
    whatever ``base_url`` is configured (RFC-0003 D7).
    """
    if stored:
        return stored
    placeholder = (_provider_block(provider).get("auth") or {}).get("placeholder_key")
    if placeholder:
        return placeholder
    raise ValueError(f"no API key configured for provider {provider!r}")


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
    # Open-world providers serve ids no pattern can check: a proxy's
    # catalogue (OpenRouter), a local server's pulls (Ollama, LM Studio),
    # owner-qualified ids (Groq's ``openai/gpt-oss-120b``), or whatever a
    # named endpoint lists. They declare ``open_world_models`` and are
    # treated as provider-selected; the upstream returns a clear 404 for a
    # genuinely missing model.
    if _provider_block(provider).get("open_world_models"):
        return True
    cfg = get_provider_config(provider)
    if not cfg:
        return True
    model_lower = model.lower()
    return any(p in model_lower for p in cfg.detection_patterns)


# ---------------------------------------------------------------------------
# Default model helpers
# ---------------------------------------------------------------------------


def get_default_model(provider: str) -> str:
    cfg = get_provider_config(provider)
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


async def agent_model(parameters: Mapping[str, Any], database) -> Tuple[str, str]:
    """The provider and model an agent node runs on: its saved ``provider``
    and ``model`` (an OpenRouter ``[FREE] `` prefix dropped), or that
    provider's default model when the saved one is missing or not the
    provider's. The run (``prepare_agent_payload``) and the chat's model
    picker (services/chat/choice.py) both read an agent's model here, so
    they never disagree."""
    provider = parameters.get("provider", "openai")
    model = parameters.get("model", "")
    if isinstance(model, str) and model.startswith("[FREE] "):
        model = model[7:]
    if not model or not is_model_valid_for_provider(model, provider):
        model = await get_default_model_async(provider, database)
    return provider, model


# ---------------------------------------------------------------------------
# The chat's model picker (Home's employee chat)
# ---------------------------------------------------------------------------


def chat_models() -> List[Dict[str, Any]]:
    """The models the chat's picker offers after Auto (``chat_models`` in
    llm_defaults.json), in order: ``{id: "provider::model", name, short,
    description}``."""
    block = LLM_DEFAULTS.get("chat_models") or {}
    return [dict(entry) for entry in block.get("models") or []]


def split_chat_model(model_id: str) -> Tuple[str, str]:
    """A picker id ``provider::model`` as ``(provider, model)``."""
    provider, _, model = (model_id or "").partition("::")
    return provider, model


def supports_effort(provider: str, model: str) -> bool:
    """Whether ``model`` takes the chat's effort choice as its own request
    setting: listed (prefix-matched) in its provider's ``effort_models``."""
    prefixes = _provider_block(provider).get("effort_models") or []
    return any(model.startswith(prefix) for prefix in prefixes)


def provider_display_name(provider: str) -> str:
    """The provider's name as the owner sees it (``display_name``)."""
    return str(_provider_block(provider).get("display_name", provider))


def curated_models(provider: str) -> list:
    """JSON-curated model ids for ``provider``.

    Prefers the explicit ``popular_models`` list, preserving its order.
    Older provider blocks (and every block that carries an empty
    ``popular_models`` under the >=1M-context policy) fall back to the
    ``max_output_tokens`` keys, minus the ``_default`` sentinel. Returns
    an empty list when neither source has anything.

    Shared by ``AIService._get_curated_models`` (the API-failure
    fallback) and ``OpenAIProvider.fetch_models`` (the curated list
    served when a provider declares no model-list route).

    ``GeminiProvider._curated_models`` deliberately does NOT use this:
    it reads the ``max_output_tokens`` keys *specifically* because those
    are real model names while gemini's ``popular_models`` carries
    ``-latest`` aliases the Vertex backend rejects.
    """
    provider_cfg = _provider_block(provider)
    explicit = provider_cfg.get("popular_models") or []
    if explicit:
        return list(explicit)
    max_tokens_map = provider_cfg.get("max_output_tokens", {})
    return [m for m in max_tokens_map if m != "_default"]


def supports_model_listing(provider: str) -> bool:
    """Whether ``provider`` exposes an OpenAI-style model-list endpoint.

    Defaults to ``True`` — only a provider that explicitly declares
    ``"supports_model_listing": false`` in llm_defaults.json opts out.
    Sarvam is the first: it serves OpenAI-compatible chat completions but
    ships no model-list route at all (verified against its published
    OpenAPI spec), so calling ``client.models.list()`` there 404s.
    """
    return bool(_provider_block(provider).get("supports_model_listing", True))


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
    """Resolve temperature with model-specific constraints.

    Default sourced from ``llm_defaults.json`` (``agent.default_temperature``)
    when the caller passes ``None`` or omits the field — chat-model Params
    declare ``temperature: Optional[float] = None`` so the merge produced by
    the default tool-execution service can legitimately carry ``None`` here.
    """
    from services.model_registry import get_model_registry

    registry = get_model_registry()

    user_val = params.get("temperature")
    if user_val is None:
        user_val = registry.get_agent_defaults().get(
            "default_temperature",
            LLM_DEFAULTS.get("agent", {}).get(
                "default_temperature", 0.7
            ),
        )
    user_temp = float(user_val)

    if registry.is_reasoning_model(model, provider):
        return 1.0

    if thinking_enabled and provider == "anthropic":
        return 1.0

    # Fixed temperature per model from llm_defaults.json (e.g. kimi-k2.5 = 0.6)
    fixed_temps = _provider_block(provider).get("fixed_temperature", {})
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
