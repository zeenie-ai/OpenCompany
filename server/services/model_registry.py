"""Model Parameter Management Service.

Centralized registry for model metadata (max_output_tokens, context_length,
temperature constraints, thinking capabilities). Three sources, three files:

- OpenRouter's public model list, cached to ``config/model_registry.json``
  (a tracked, curated snapshot) and refreshed on a 24 h gate.
- Models registered from the user's own servers (Ollama, LM Studio, named
  OpenAI-compatible endpoints) when their credential is saved. These are
  per-install state, so they persist under DATA_DIR, never in the tracked
  snapshot, and an OpenRouter refresh never replaces them.
- LiteLLM's ``model_prices_and_context_window.json``, consulted only when an
  endpoint is saved, to size and price model ids its server does not
  describe (RFC-0003 D15). Cached under DATA_DIR on the same 24 h gate.

Falls back to llm_defaults.json offline.
"""

import asyncio
import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import httpx

from core.logging import get_logger

logger = get_logger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_FILE = Path(__file__).parent.parent / "config" / "model_registry.json"
CACHE_MAX_AGE = timedelta(hours=24)

LITELLM_MODELS_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
# The only LiteLLM fields anything reads. Everything else is dropped at
# fetch time, so the cache holds a few hundred KB, not the full table.
_LITELLM_FIELDS = (
    "max_input_tokens",
    "max_output_tokens",
    "input_cost_per_token",
    "output_cost_per_token",
)
# An on-demand fetch runs inside an endpoint save, so it is bounded in
# total, and a failed one is not repeated on every save: offline, each save
# would otherwise wait out the timeout. The 24 h refresh is not affected.
_LITELLM_ON_DEMAND_TIMEOUT_SECONDS = 8.0
_LITELLM_RETRY_AFTER = timedelta(minutes=10)


def _local_models_path() -> Path:
    """Where models registered from the user's own servers persist."""
    from core.paths import data_path

    return data_path("local_models.json")


def _litellm_cache_path() -> Path:
    """Where the trimmed LiteLLM table is cached."""
    from core.paths import data_path

    return data_path("litellm_models.json")

# Provider normalization: OpenRouter provider -> OpenCompany provider
PROVIDER_MAP = {
    "google": "gemini",
    "meta-llama": "meta",
    "x-ai": "xai",
}

# Known reasoning models (always temperature=1, no user override)
REASONING_MODEL_PATTERNS = re.compile(r"^(o1|o3|o4|o3-mini|o4-mini)(-|$)", re.IGNORECASE)

# Known thinking model patterns -> thinking_type
THINKING_PATTERNS: List[Tuple[str, str, str]] = [
    # (provider, model_pattern, thinking_type)
    ("anthropic", r"claude-(opus|sonnet|haiku)-(4|5|6)", "budget"),
    ("anthropic", r"claude-3[\.\-]5", "budget"),
    ("openai", r"^(o1|o3|o4)", "effort"),
    ("openai", r"^gpt-(5|6)", "effort"),  # GPT-5 / GPT-6 hybrid reasoning
    ("gemini", r"gemini-(2\.5|3)", "budget"),
    ("groq", r"qwen3", "format"),
    ("cerebras", r"qwen", "budget"),
]

# Default temperature ranges per provider
DEFAULT_TEMP_RANGES = {
    "openai": (0.0, 2.0),
    "anthropic": (0.0, 1.0),
    "gemini": (0.0, 2.0),
    "groq": (0.0, 2.0),
    "cerebras": (0.0, 1.5),
    "openrouter": (0.0, 2.0),
    "deepseek": (0.0, 2.0),
    "kimi": (0.0, 1.0),
    "mistral": (0.0, 1.0),
    "sarvam": (0.0, 2.0),
}


# =============================================================================
# DATA STRUCTURES
# =============================================================================


@dataclass
class ModelInfo:
    """Comprehensive model metadata."""

    id: str  # "openai/gpt-5.2"
    name: str  # "OpenAI: GPT-5.2"
    provider: str  # "openai" (OpenCompany-normalized)
    local_id: str  # "gpt-5.2"
    context_length: int = 128000
    max_output_tokens: int = 4096
    input_price_per_mtok: float = 0.0
    output_price_per_mtok: float = 0.0
    supports_thinking: bool = False
    thinking_type: str = "none"  # "budget" | "effort" | "format" | "none"
    temperature_range: tuple = (0.0, 2.0)
    is_reasoning_model: bool = False
    supported_parameters: list = field(default_factory=list)
    # Prompt-cache prices, when the catalogue lists them (None otherwise).
    cache_read_price_per_mtok: Optional[float] = None
    cache_write_price_per_mtok: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["temperature_range"] = list(self.temperature_range)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "ModelInfo":
        data = dict(data)
        if "temperature_range" in data and isinstance(data["temperature_range"], list):
            data["temperature_range"] = tuple(data["temperature_range"])
        if "supported_parameters" not in data:
            data["supported_parameters"] = []
        return cls(**data)


def _price_per_mtok(value: Any) -> Optional[float]:
    """An OpenRouter per-token price string as USD per million tokens,
    or None when the catalogue does not list that price."""
    try:
        return None if value is None else round(float(value) * 1_000_000, 4)
    except (TypeError, ValueError):
        return None


# =============================================================================
# SERVICE
# =============================================================================


class ModelRegistryService:
    """Centralized model parameter management.

    Singleton service that fetches model metadata from OpenRouter's public API
    and caches to a JSON file. Provides lookup methods for max_output_tokens,
    temperature constraints, thinking capabilities, etc.
    """

    def __init__(self):
        self._models: Dict[str, ModelInfo] = {}  # OpenRouter, keyed by "provider/local_id"
        # Registered from the user's own servers, keyed "<provider ref>/<model id>".
        # Kept apart so an OpenRouter refresh cannot replace them.
        self._local_models: Dict[str, ModelInfo] = {}
        self._litellm: Dict[str, Dict[str, Any]] = {}
        self._litellm_failed_at: Optional[datetime] = None
        self._cache_timestamp: Optional[datetime] = None
        self._llm_defaults: Dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def startup(self) -> None:
        """Synchronously load the caches and llm_defaults.json."""
        self._load_llm_defaults()
        self._load_cache()
        self._load_local_models()
        self._load_litellm_cache()
        count = len(self._models)
        if count > 0:
            logger.info(f"Model registry loaded {count} models from cache")
        else:
            logger.info("Model registry: no cache found, will fetch on first refresh")

    def is_stale(self) -> bool:
        """Check if cache is older than 24h or missing."""
        if not self._cache_timestamp:
            return True
        age = datetime.now(timezone.utc) - self._cache_timestamp
        return age > CACHE_MAX_AGE

    async def refresh(self) -> int:
        """Refresh the LiteLLM table, then the OpenRouter snapshot.

        The LiteLLM refresh never raises: it only sizes endpoint models at
        save time, and a stale copy beats none. Returns the number of
        OpenRouter models loaded.
        """
        await self._refresh_litellm()
        return await self._refresh_openrouter()

    async def _refresh_openrouter(self) -> int:
        """Fetch model data from OpenRouter public API and update cache.

        Returns the number of models loaded.
        """
        logger.info("Model registry: fetching from OpenRouter...")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(OPENROUTER_MODELS_URL)
            response.raise_for_status()
            data = response.json()

        raw_models = data.get("data", [])
        if not raw_models:
            logger.warning("Model registry: OpenRouter returned no models")
            return 0

        new_models: Dict[str, ModelInfo] = {}
        for raw in raw_models:
            info = self._parse_openrouter_model(raw)
            if info:
                key = f"{info.provider}/{info.local_id}"
                new_models[key] = info

        self._models = new_models
        self._cache_timestamp = datetime.now(timezone.utc)
        self._save_cache()

        logger.info(f"Model registry: loaded {len(new_models)} models from OpenRouter")
        return len(new_models)

    # -------------------------------------------------------------------------
    # Core lookups
    # -------------------------------------------------------------------------

    @staticmethod
    def _model_variants(model: str) -> List[str]:
        """Generate dot and hyphen variants of a model ID for fuzzy matching.

        Model IDs use inconsistent version separators across sources:
        - OpenRouter/registry: dots (claude-sonnet-4.6)
        - llm_defaults.json:   hyphens (claude-sonnet-4-6)

        Returns the original plus any variants with swapped separators.
        """
        variants = [model]
        # digit-hyphen-digit -> digit.digit  (e.g., claude-sonnet-4-6 -> claude-sonnet-4.6)
        dot_variant = re.sub(r"(\d)-(\d)", r"\1.\2", model)
        if dot_variant != model:
            variants.append(dot_variant)
        # digit.digit -> digit-digit  (e.g., claude-sonnet-4.6 -> claude-sonnet-4-6)
        hyphen_variant = re.sub(r"(\d)\.(\d)", r"\1-\2", model)
        if hyphen_variant != model:
            variants.append(hyphen_variant)
        return variants

    def get_model_info(self, model: str, provider: str) -> Optional[ModelInfo]:
        """Look up model info with waterfall strategy.

        1. Exact match on provider/model (tries dot/hyphen variants)
        2. Prefix match for versioned IDs
        3. Cross-provider check (for OpenRouter models)
        4. Returns None if not found
        """
        # Strip [FREE] prefix if present
        model = model.replace("[FREE] ", "")
        variants = self._model_variants(model)
        # Models registered from the user's own servers go first: they
        # carry the context the server actually loaded.
        sources = (self._local_models, self._models)

        # 1. Exact match (try all dot/hyphen variants)
        for source in sources:
            for variant in variants:
                key = f"{provider}/{variant}"
                if key in source:
                    return source[key]

        # 2. Prefix match (e.g., claude-3-5-sonnet-20241022 -> claude-3-5-sonnet)
        for source in sources:
            for stored_key, info in source.items():
                if info.provider == provider:
                    for variant in variants:
                        if variant.startswith(info.local_id) or info.local_id.startswith(variant):
                            return info

        # 3. Cross-provider lookup (OpenRouter only - same model on different
        #    providers can have different context windows and limits)
        if provider == "openrouter":
            for stored_key, info in self._models.items():
                if info.local_id in variants:
                    return info

        # 4. For OpenRouter, try stripping provider prefix from model
        if provider == "openrouter" and "/" in model:
            _, local = model.split("/", 1)
            local_variants = self._model_variants(local)
            for stored_key, info in self._models.items():
                if info.local_id in local_variants:
                    return info

        return None

    def register_local_model(
        self,
        provider: str,
        model_id: str,
        params: Dict[str, Any],
    ) -> None:
        """Register a model served by the user's own server, with its params.

        Called when an Ollama, LM Studio or named OpenAI-compatible
        endpoint credential is saved (``nodes/model/_local_validator.py``).
        ``params`` carries what the server reported, or what save-time
        enrichment found: ``context_length`` (the n_ctx the server
        enforces), optional ``max_output_tokens``, ``vision`` /
        ``supports_tools`` flags, and optional ``input_price_per_mtok`` /
        ``output_price_per_mtok``. The sync ``get_context_length`` /
        ``get_max_output_tokens`` lookups find this entry first, so chat
        and agent execution honour the real n_ctx — no JSON guess.

        ``provider`` is the provider reference (``ollama``, ``lmstudio``,
        ``openai_compatible:<slug>``); the key shape is the same
        ``{provider}/{local_id}`` as cloud models, so ``get_model_info``
        matches without per-provider branching.

        Idempotent: a re-validation overwrites the prior entry. Persisted
        under DATA_DIR, not in the tracked OpenRouter snapshot, so it
        survives a restart without dirtying the repo, and an OpenRouter
        refresh cannot replace it.
        """
        ctx = int(params.get("context_length") or 0)
        max_out = int(params.get("max_output_tokens") or 0)
        # Default max_output to 25% of context (OpenRouter convention for
        # local models), capped at 4096 — local backends rarely benefit
        # from larger budgets and overcommitting eats into the prompt.
        if not max_out and ctx:
            max_out = min(4096, max(512, ctx // 4))

        # Capability flags + metadata from the SDK probe go into
        # `supported_parameters` so they survive the JSON roundtrip via
        # `ModelInfo.to_dict` / `from_dict`. Cloud models populate this
        # from OpenRouter's listing; for locals we use it as a typed
        # capability bag so downstream code (tool-binding, vision UI
        # gating) can read the same fields regardless of provider.
        supported: List[str] = []
        if params.get("supports_tools"):
            supported.append("tools")
        if params.get("vision"):
            supported.append("vision")

        # Match the user-friendly temperature default for OpenAI-compat
        # local servers (0..2 range — same as the JSON default).
        info = ModelInfo(
            id=f"{provider}/{model_id}",
            name=model_id,
            provider=provider,
            local_id=model_id,
            context_length=ctx,
            max_output_tokens=max_out,
            input_price_per_mtok=float(params.get("input_price_per_mtok") or 0.0),
            output_price_per_mtok=float(params.get("output_price_per_mtok") or 0.0),
            temperature_range=(0.0, 2.0),
            supported_parameters=supported,
        )
        self._local_models[f"{provider}/{model_id}"] = info
        logger.info(
            "[%s] registered model %s (ctx=%s, max_out=%s, tools=%s, vision=%s)",
            provider,
            model_id,
            ctx,
            max_out,
            params.get("supports_tools", False),
            params.get("vision", False),
        )
        # Persist so the entry survives process restart.
        self._save_local_models()

    def forget_local_models(self, provider: str) -> None:
        """Drop every model registered for ``provider`` (a provider reference).

        Called when an endpoint is removed or re-saved, so a model the
        server no longer serves cannot keep answering lookups.
        """
        prefix = f"{provider}/"
        stale = [key for key in self._local_models if key.startswith(prefix)]
        if not stale:
            return
        for key in stale:
            del self._local_models[key]
        self._save_local_models()

    def get_max_output_tokens(self, model: str, provider: str) -> int:
        """Get max output tokens: registry -> llm_defaults -> 4096."""
        info = self.get_model_info(model, provider)
        if info and info.max_output_tokens > 0:
            return info.max_output_tokens

        # Fallback to llm_defaults.json
        return self._get_default_max_output_tokens(provider, model)

    def get_context_length(self, model: str, provider: str) -> int:
        """Get context window length: registry -> llm_defaults -> 128000."""
        info = self.get_model_info(model, provider)
        if info and info.context_length > 0:
            return info.context_length
        return self._get_default_context_length(provider, model)

    def get_temperature_range(self, model: str, provider: str) -> Tuple[float, float]:
        """Get valid temperature range for a model."""
        info = self.get_model_info(model, provider)
        if info:
            return info.temperature_range

        # Fallback to llm_defaults or provider default
        temp_range = self._provider_defaults(provider).get("temperature_range")
        if temp_range and isinstance(temp_range, list) and len(temp_range) == 2:
            return tuple(temp_range)

        return DEFAULT_TEMP_RANGES.get(provider, (0.0, 2.0))

    def is_reasoning_model(self, model: str, provider: str) -> bool:
        """Check if model is a reasoning model (always temp=1)."""
        info = self.get_model_info(model, provider)
        if info:
            return info.is_reasoning_model

        # Fallback: check llm_defaults reasoning_models list
        reasoning_list = self._provider_defaults(provider).get("reasoning_models", [])
        for pattern in reasoning_list:
            if model.startswith(pattern):
                return True

        # Fallback: pattern match
        return bool(REASONING_MODEL_PATTERNS.match(model))

    def supports_thinking(self, model: str, provider: str) -> bool:
        """Check if model supports thinking/reasoning mode."""
        info = self.get_model_info(model, provider)
        if info:
            return info.supports_thinking
        return self.get_thinking_type(model, provider) != "none"

    def get_thinking_type(self, model: str, provider: str) -> str:
        """Get thinking type: 'budget', 'effort', 'format', or 'none'."""
        info = self.get_model_info(model, provider)
        if info:
            return info.thinking_type

        # Fallback: check llm_defaults
        prov_cfg = self._provider_defaults(provider)

        # Check if model matches thinking_models patterns
        thinking_models = prov_cfg.get("thinking_models", [])
        for pattern in thinking_models:
            if model.startswith(pattern) or pattern in model:
                return prov_cfg.get("thinking_type", "none")

        # Check reasoning_models (these use effort type)
        reasoning_list = prov_cfg.get("reasoning_models", [])
        for pattern in reasoning_list:
            if model.startswith(pattern):
                return "effort"

        # Provider-level thinking_type (e.g., anthropic always uses budget)
        if prov_cfg.get("thinking_type") and prov_cfg.get("thinking_type") != "none":
            # Only apply if it's a modern enough model
            return self._infer_thinking_type(model, provider)

        return "none"

    def get_model_constraints(self, model: str, provider: str) -> dict:
        """Get all constraints for a model in one call (for frontend)."""
        info = self.get_model_info(model, provider)
        if info:
            return {
                "found": True,
                "model": model,
                "provider": provider,
                "max_output_tokens": info.max_output_tokens,
                "context_length": info.context_length,
                "temperature_range": list(info.temperature_range),
                "supports_thinking": info.supports_thinking,
                "thinking_type": info.thinking_type,
                "is_reasoning_model": info.is_reasoning_model,
            }

        # Build from fallbacks
        return {
            "found": False,
            "model": model,
            "provider": provider,
            "max_output_tokens": self.get_max_output_tokens(model, provider),
            "context_length": self.get_context_length(model, provider),
            "temperature_range": list(self.get_temperature_range(model, provider)),
            "supports_thinking": self.supports_thinking(model, provider),
            "thinking_type": self.get_thinking_type(model, provider),
            "is_reasoning_model": self.is_reasoning_model(model, provider),
        }

    # -------------------------------------------------------------------------
    # OpenRouter parsing
    # -------------------------------------------------------------------------

    def _parse_openrouter_model(self, raw: dict) -> Optional[ModelInfo]:
        """Parse a single model entry from OpenRouter API response."""
        model_id = raw.get("id", "")
        if not model_id or "/" not in model_id:
            return None

        or_provider, local_id = model_id.split("/", 1)
        # OpenRouter "~provider" alias rows (e.g. ~google/gemini-flash-latest)
        # carry the same metadata as canonical rows — strip the tilde so they
        # key under the canonical provider and local lookups
        # (get_model_info("gemini-flash-latest", "gemini")) match.
        or_provider = or_provider.lstrip("~")
        provider = PROVIDER_MAP.get(or_provider, or_provider)

        # Extract top_provider data
        top_provider = raw.get("top_provider", {}) or {}
        max_completion = top_provider.get("max_completion_tokens") or 0

        # Extract pricing (string values in dollars per token)
        pricing = raw.get("pricing", {}) or {}
        try:
            input_price = float(pricing.get("prompt", "0")) * 1_000_000
            output_price = float(pricing.get("completion", "0")) * 1_000_000
        except (ValueError, TypeError):
            input_price = 0.0
            output_price = 0.0

        # Detect thinking support from supported_parameters
        supported_params = raw.get("supported_parameters", []) or []
        has_reasoning = "include_reasoning" in supported_params or "reasoning" in supported_params

        # Determine thinking type
        thinking_type = self._infer_thinking_type(local_id, provider)

        # If API says reasoning is supported but we didn't detect a type, use provider default
        if has_reasoning and thinking_type == "none":
            if provider == "anthropic":
                thinking_type = "budget"
            elif provider == "openai":
                thinking_type = "effort"
            elif provider in ("groq", "cerebras"):
                thinking_type = "format"

        # Determine if this is a reasoning model (always temp=1)
        is_reasoning = bool(REASONING_MODEL_PATTERNS.match(local_id))

        # Temperature range
        if is_reasoning:
            temp_range = (1.0, 1.0)
        else:
            temp_range = DEFAULT_TEMP_RANGES.get(provider, (0.0, 2.0))

        return ModelInfo(
            id=model_id,
            name=raw.get("name", model_id),
            provider=provider,
            local_id=local_id,
            context_length=raw.get("context_length", 0) or 0,
            max_output_tokens=max_completion,
            input_price_per_mtok=round(input_price, 4),
            output_price_per_mtok=round(output_price, 4),
            supports_thinking=thinking_type != "none",
            thinking_type=thinking_type,
            temperature_range=temp_range,
            is_reasoning_model=is_reasoning,
            supported_parameters=supported_params,
            cache_read_price_per_mtok=_price_per_mtok(pricing.get("input_cache_read")),
            cache_write_price_per_mtok=_price_per_mtok(pricing.get("input_cache_write")),
        )

    def _infer_thinking_type(self, model: str, provider: str) -> str:
        """Infer thinking type from known model patterns."""
        for pat_provider, pat_regex, pat_type in THINKING_PATTERNS:
            if provider == pat_provider and re.search(pat_regex, model, re.IGNORECASE):
                return pat_type
        return "none"

    # -------------------------------------------------------------------------
    # Cache management
    # -------------------------------------------------------------------------

    def _load_cache(self) -> None:
        """Load models from cache file."""
        if not CACHE_FILE.exists():
            return

        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)

            ts_str = data.get("_generated")
            if ts_str:
                self._cache_timestamp = datetime.fromisoformat(ts_str)

            raw_models = data.get("models", {})
            for key, model_data in raw_models.items():
                try:
                    info = ModelInfo.from_dict(model_data)
                except (TypeError, KeyError) as e:
                    logger.debug(f"Skipping invalid model cache entry {key}: {e}")
                    continue
                # Same "~provider" alias normalization as _parse_openrouter_model,
                # for caches written before it landed.
                stripped = info.provider.lstrip("~")
                info.provider = PROVIDER_MAP.get(stripped, stripped)
                self._models[f"{info.provider}/{info.local_id}"] = info

        except Exception as e:
            logger.warning(f"Failed to load model registry cache: {e}")

    def _save_cache(self) -> None:
        """Save models to cache file."""
        try:
            cache_data = {
                "_generated": self._cache_timestamp.isoformat() if self._cache_timestamp else None,
                "_source": "openrouter",
                "_model_count": len(self._models),
                "models": {key: info.to_dict() for key, info in self._models.items()},
            }

            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, default=str)

            logger.debug(f"Model registry cache saved: {len(self._models)} models")

        except Exception as e:
            logger.warning(f"Failed to save model registry cache: {e}")

    def _load_local_models(self) -> None:
        """Load models registered from the user's own servers."""
        path = _local_models_path()
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                raw_models = json.load(f).get("models", {})
            for key, model_data in raw_models.items():
                try:
                    self._local_models[key] = ModelInfo.from_dict(model_data)
                except (TypeError, KeyError) as e:
                    logger.debug(f"Skipping invalid local model entry {key}: {e}")
        except Exception as e:
            logger.warning(f"Failed to load local model registry: {e}")

    def _save_local_models(self) -> None:
        """Persist models registered from the user's own servers. Best effort."""
        path = _local_models_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {"models": {key: info.to_dict() for key, info in self._local_models.items()}},
                    f,
                    indent=2,
                    default=str,
                )
        except Exception as e:  # noqa: BLE001 — the in-memory entry still serves this process
            logger.warning(f"Failed to persist local model registry: {e}")

    # -------------------------------------------------------------------------
    # LiteLLM table (endpoint save-time sizing and pricing, RFC-0003 D15)
    # -------------------------------------------------------------------------

    def lookup_litellm(self, model_id: str) -> Optional[Dict[str, Any]]:
        """LiteLLM's entry for ``model_id``, or ``None``.

        An exact key wins. Otherwise a key whose part after the first ``/``
        (LiteLLM prefixes non-OpenAI ids with their host, e.g.
        ``together_ai/...``) equals the id, but only when exactly one key
        matches: hosts size and price the same model differently, so an
        ambiguous match is no match.
        """
        if not model_id:
            return None
        entry = self._litellm.get(model_id)
        if entry is not None:
            return entry
        matches = [spec for key, spec in self._litellm.items() if key.partition("/")[2] == model_id]
        return matches[0] if len(matches) == 1 else None

    async def ensure_litellm_table(self, timeout: float = _LITELLM_ON_DEMAND_TIMEOUT_SECONDS) -> None:
        """Fetch the LiteLLM table if this process has none yet.

        The 24 h refresh keeps it current; this covers a first endpoint
        save on a machine whose OpenRouter snapshot was still fresh, so
        the refresh has not run yet. It runs inside that save, so it gets
        ``timeout`` seconds in total, and after a failure it waits
        ``_LITELLM_RETRY_AFTER`` before trying again. Never raises.
        """
        if self._litellm:
            return
        if self._litellm_failed_at and datetime.now(timezone.utc) - self._litellm_failed_at < _LITELLM_RETRY_AFTER:
            return
        try:
            await asyncio.wait_for(self._refresh_litellm(), timeout)
        except asyncio.TimeoutError:
            self._litellm_failed_at = datetime.now(timezone.utc)
            logger.warning(f"Model registry: LiteLLM table fetch took longer than {timeout:g}s; not retried for a while")

    async def _refresh_litellm(self) -> None:
        """Fetch and trim the LiteLLM table. Never raises."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(LITELLM_MODELS_URL)
                response.raise_for_status()
                raw = response.json()
        except Exception as e:  # noqa: BLE001 — a stale copy beats none
            self._litellm_failed_at = datetime.now(timezone.utc)
            logger.warning(f"Model registry: LiteLLM table refresh failed, keeping the cached copy: {e}")
            return

        table = self._parse_litellm(raw)
        if not table:
            self._litellm_failed_at = datetime.now(timezone.utc)
            logger.warning("Model registry: LiteLLM table had no chat models, keeping the cached copy")
            return
        self._litellm = table
        self._litellm_failed_at = None
        self._save_litellm_cache()
        logger.info(f"Model registry: loaded {len(table)} chat models from LiteLLM")

    @staticmethod
    def _parse_litellm(raw: Any) -> Dict[str, Dict[str, Any]]:
        """Keep chat-mode entries and the four fields anything reads."""
        table: Dict[str, Dict[str, Any]] = {}
        if not isinstance(raw, dict):
            return table
        for model_id, spec in raw.items():
            if not isinstance(spec, dict) or spec.get("mode") != "chat":
                continue
            entry = {
                name: spec[name]
                for name in _LITELLM_FIELDS
                if isinstance(spec.get(name), (int, float)) and not isinstance(spec.get(name), bool)
            }
            if entry:
                table[model_id] = entry
        return table

    def _load_litellm_cache(self) -> None:
        path = _litellm_cache_path()
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                self._litellm = self._parse_litellm(json.load(f).get("models", {}))
        except Exception as e:
            logger.warning(f"Failed to load LiteLLM table cache: {e}")

    def _save_litellm_cache(self) -> None:
        path = _litellm_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                # Re-stamped with "mode" so the loader's parse applies unchanged.
                json.dump(
                    {
                        "_source": LITELLM_MODELS_URL,
                        "_generated": datetime.now(timezone.utc).isoformat(),
                        "models": {key: {**spec, "mode": "chat"} for key, spec in self._litellm.items()},
                    },
                    f,
                )
        except Exception as e:  # noqa: BLE001 — the in-memory table still serves this process
            logger.warning(f"Failed to persist LiteLLM table cache: {e}")

    # -------------------------------------------------------------------------
    # LLM defaults fallback
    # -------------------------------------------------------------------------

    def _load_llm_defaults(self) -> None:
        """Load llm_defaults.json for offline fallback."""
        defaults_path = Path(__file__).parent.parent / "config" / "llm_defaults.json"
        try:
            with open(defaults_path, encoding="utf-8") as f:
                self._llm_defaults = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load llm_defaults.json: {e}")
            self._llm_defaults = {"providers": {}}

    def get_agent_defaults(self) -> Dict[str, Any]:
        """Return the ``agent`` block defaults (recursion_limit,
        compaction.ratio, default_temperature).

        Source-of-truth order:
          1. Environment via ``core.config.Settings``
             (``AGENT_RECURSION_LIMIT`` / ``COMPACTION_RATIO``) — wins.
          2. ``server/config/llm_defaults.json:agent`` block — fallback
             when Settings can't be instantiated (e.g. one-off CLI
             scripts that bypass it).

        Per-user ``UserSettings.agent_recursion_limit`` /
        ``compaction_ratio`` overrides are applied by the caller at
        request time — this helper returns the GLOBAL defaults only.
        """
        block = dict(self._llm_defaults.get("agent", {}))
        try:
            from core.config import Settings

            settings = Settings()
            block["recursion_limit"] = int(settings.agent_recursion_limit)
            compaction = dict(block.get("compaction") or {})
            compaction["ratio"] = float(settings.compaction_ratio)
            block["compaction"] = compaction
        except Exception:  # noqa: BLE001 — defensive: keep JSON fallback usable
            pass
        return block

    def _provider_defaults(self, provider: str) -> Dict[str, Any]:
        """The llm_defaults.json block for a provider reference.

        A named endpoint (``openai_compatible:<slug>``) reads the shared
        ``openai_compatible`` block.
        """
        from services.llm.config import split_provider_ref

        return self._llm_defaults.get("providers", {}).get(split_provider_ref(provider)[0], {})

    def _get_default_max_output_tokens(self, provider: str, model: str) -> int:
        """Fallback: get max output tokens from llm_defaults.json."""
        token_map = self._provider_defaults(provider).get("max_output_tokens", {})
        variants = self._model_variants(model)

        # Exact match (try dot/hyphen variants)
        for variant in variants:
            if variant in token_map:
                return token_map[variant]

        # Prefix match (try dot/hyphen variants)
        for key, val in token_map.items():
            if key != "_default" and isinstance(val, int):
                for variant in variants:
                    if variant.startswith(key):
                        return val

        return token_map.get("_default", 4096)

    def _get_default_context_length(self, provider: str, model: str) -> int:
        """Fallback: get context length from llm_defaults.json."""
        ctx_map = self._provider_defaults(provider).get("context_length", {})
        variants = self._model_variants(model)

        # Exact match (try dot/hyphen variants)
        for variant in variants:
            if variant in ctx_map:
                return ctx_map[variant]

        # Prefix match (try dot/hyphen variants)
        for key, val in ctx_map.items():
            if key != "_default" and isinstance(val, int):
                for variant in variants:
                    if variant.startswith(key):
                        return val

        return ctx_map.get("_default", 128000)


# =============================================================================
# SINGLETON
# =============================================================================

_service: Optional[ModelRegistryService] = None


def get_model_registry() -> ModelRegistryService:
    """Get or create the model registry singleton."""
    global _service
    if _service is None:
        _service = ModelRegistryService()
    return _service
