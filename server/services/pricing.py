"""Unified pricing service for LLM tokens and API costs.

Provides cost calculation for:
- LLM providers (OpenAI, Anthropic, Gemini, Groq, Cerebras, OpenRouter)
- API services (Twitter/X, Google Maps, etc.)

Pricing is loaded from config/pricing.json for user editability.
LLM pricing is in USD per million tokens (MTok).
API pricing is per resource/request.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from core.logging import get_logger

logger = get_logger(__name__)

# Path to pricing configuration file
CONFIG_PATH = Path(__file__).parent.parent / "config" / "pricing.json"


@dataclass
class ModelPricing:
    """Pricing for a specific LLM model."""

    input_per_mtok: float  # USD per 1M input tokens
    output_per_mtok: float  # USD per 1M output tokens
    cache_read_per_mtok: Optional[float] = None  # USD per 1M cache read tokens (Anthropic)
    reasoning_per_mtok: Optional[float] = None  # USD per 1M reasoning tokens (OpenAI o-series)


class PricingService:
    """Unified service for calculating LLM token costs and API costs."""

    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._llm_registry: Dict[str, Dict[str, ModelPricing]] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load pricing config from JSON file."""
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                self._build_llm_registry()
                logger.info(f"[Pricing] Loaded pricing config v{self._config.get('version', 'unknown')}")
            except Exception as e:
                logger.error(f"[Pricing] Failed to load config: {e}")
                self._config = {"llm": {}, "api": {}, "operation_map": {}}
                self._llm_registry = {}
        else:
            logger.warning(f"[Pricing] Config not found: {CONFIG_PATH}")
            self._config = {"llm": {}, "api": {}, "operation_map": {}}
            self._llm_registry = {}

    def _build_llm_registry(self) -> None:
        """Build ModelPricing objects from JSON config."""
        self._llm_registry = {}
        llm_config = self._config.get("llm", {})

        for provider, models in llm_config.items():
            self._llm_registry[provider] = {}
            for model_name, pricing_data in models.items():
                if isinstance(pricing_data, dict):
                    self._llm_registry[provider][model_name] = ModelPricing(
                        input_per_mtok=pricing_data.get("input", 1.0),
                        output_per_mtok=pricing_data.get("output", 5.0),
                        cache_read_per_mtok=pricing_data.get("cache_read"),
                        reasoning_per_mtok=pricing_data.get("reasoning"),
                    )

    def reload(self) -> None:
        """Reload pricing config from disk (for live updates)."""
        self._load_config()

    def get_config(self) -> Dict[str, Any]:
        """Get full pricing config for frontend display/editing."""
        return self._config

    def save_config(self, config: Dict[str, Any]) -> bool:
        """Save updated pricing config to JSON file.

        Args:
            config: New pricing configuration dict

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            # Update last_updated timestamp
            config["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d")

            # Ensure config directory exists
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)

            # Reload the config into memory
            self._config = config
            self._build_llm_registry()

            logger.info(f"[Pricing] Saved pricing config v{config.get('version', 'unknown')}")
            return True
        except Exception as e:
            logger.error(f"[Pricing] Failed to save config: {e}")
            return False

    # =========================================================================
    # LLM Token Pricing (existing functionality)
    # =========================================================================

    def get_pricing(self, provider: str, model: str) -> ModelPricing:
        """Get pricing for a specific LLM model.

        Uses partial matching: 'claude-3-5-sonnet-20241022' matches 'claude-3-5-sonnet'.
        Falls back to '_default' if no match found.

        Args:
            provider: Provider name (openai, anthropic, gemini, groq, cerebras, openrouter)
            model: Model name or ID

        Returns:
            ModelPricing with rates per million tokens
        """
        provider_lower = provider.lower()
        model_lower = model.lower() if model else ""

        provider_pricing = self._llm_registry.get(provider_lower, {})

        # Try exact match first
        if model_lower in provider_pricing:
            return provider_pricing[model_lower]

        # Try partial match (model name starts with a known key)
        for model_key, pricing in provider_pricing.items():
            if model_key != "_default" and model_lower.startswith(model_key):
                return pricing

        # Try if any key is contained in the model name
        for model_key, pricing in provider_pricing.items():
            if model_key != "_default" and model_key in model_lower:
                return pricing

        # Fall back to provider default
        default_pricing = provider_pricing.get("_default")
        if default_pricing:
            return default_pricing

        # Ultimate fallback
        logger.warning(f"[Pricing] No pricing found for {provider}/{model}, using global default")
        return ModelPricing(1.00, 5.00)

    def calculate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> Dict[str, float]:
        """Calculate cost for LLM token usage.

        Args:
            provider: LLM provider name
            model: Model name/ID
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            cache_read_tokens: Number of cache read tokens (Anthropic)
            cache_creation_tokens: Number of cache creation tokens (Anthropic)
            reasoning_tokens: Number of reasoning tokens (OpenAI o-series)

        Returns:
            Dict with cost breakdown:
            - input_cost: USD for input tokens
            - output_cost: USD for output tokens
            - cache_cost: USD for cache tokens
            - reasoning_cost: USD for reasoning tokens
            - total_cost: Total USD cost
        """
        pricing = self.get_pricing(provider, model)

        # Calculate costs (prices are per 1M tokens)
        input_cost = (input_tokens / 1_000_000) * pricing.input_per_mtok
        output_cost = (output_tokens / 1_000_000) * pricing.output_per_mtok

        # Cache costs (Anthropic pattern)
        cache_cost = 0.0
        if pricing.cache_read_per_mtok:
            # Cache reads are discounted (typically 10% of input price)
            cache_cost += (cache_read_tokens / 1_000_000) * pricing.cache_read_per_mtok
            # Cache creation is charged at 1.25x output rate
            cache_cost += (cache_creation_tokens / 1_000_000) * pricing.output_per_mtok * 1.25

        # Reasoning costs (OpenAI o-series)
        reasoning_cost = 0.0
        if pricing.reasoning_per_mtok and reasoning_tokens > 0:
            reasoning_cost = (reasoning_tokens / 1_000_000) * pricing.reasoning_per_mtok

        total_cost = input_cost + output_cost + cache_cost + reasoning_cost

        return {
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "cache_cost": round(cache_cost, 6),
            "reasoning_cost": round(reasoning_cost, 6),
            "total_cost": round(total_cost, 6),
        }

    def get_all_pricing(self) -> Dict[str, Dict[str, Dict[str, float]]]:
        """Get all LLM pricing data for frontend display.

        Returns:
            Nested dict: {provider: {model: {input, output, cache_read, reasoning}}}
        """
        result = {}
        for provider, models in self._llm_registry.items():
            result[provider] = {}
            for model, pricing in models.items():
                result[provider][model] = {
                    "input": pricing.input_per_mtok,
                    "output": pricing.output_per_mtok,
                    "cache_read": pricing.cache_read_per_mtok,
                    "reasoning": pricing.reasoning_per_mtok,
                }
        return result

    # =========================================================================
    # API Service Pricing (new functionality)
    # =========================================================================

    def get_api_price(self, service: str, operation: str) -> float:
        """Get price for an API operation.

        Args:
            service: Service name (e.g., 'twitter', 'google_maps')
            operation: Operation key (e.g., 'posts_read', 'content_create')

        Returns:
            USD cost per resource/request
        """
        api_config = self._config.get("api", {})
        service_config = api_config.get(service, {})
        return service_config.get(operation, 0.0)

    def get_api_pricing(self, service: str) -> Dict[str, float]:
        """Get all pricing for an API service.

        Args:
            service: Service name (e.g., 'twitter')

        Returns:
            Dict of {operation: price} for the service
        """
        api_config = self._config.get("api", {})
        service_config = api_config.get(service, {})
        # Filter out metadata keys starting with _
        return {k: v for k, v in service_config.items() if not k.startswith("_") and isinstance(v, (int, float))}

    def map_action_to_operation(self, service: str, action: str) -> Optional[str]:
        """Map handler action to pricing operation.

        Args:
            service: Service name (e.g., 'twitter')
            action: Handler action (e.g., 'tweet', 'search', 'like')

        Returns:
            Pricing operation key or None if no mapping
        """
        operation_map = self._config.get("operation_map", {})
        service_map = operation_map.get(service, {})
        return service_map.get(action)

    def calculate_api_cost(self, service: str, action: str, resource_count: int = 1) -> Dict[str, Any]:
        """Calculate cost for API usage.

        Args:
            service: Service name (e.g., 'twitter')
            action: Handler action (e.g., 'tweet', 'search')
            resource_count: Number of resources fetched or requests made

        Returns:
            Dict with cost breakdown:
            - operation: Pricing operation key
            - unit_cost: USD per resource/request
            - resource_count: Number of resources
            - total_cost: Total USD cost
        """
        operation = self.map_action_to_operation(service, action)
        if not operation:
            return {"operation": action, "unit_cost": 0.0, "resource_count": resource_count, "total_cost": 0.0}

        unit_cost = self.get_api_price(service, operation)
        total_cost = unit_cost * resource_count

        return {"operation": operation, "unit_cost": unit_cost, "resource_count": resource_count, "total_cost": round(total_cost, 6)}


# Singleton instance
_service: Optional[PricingService] = None


def get_pricing_service() -> PricingService:
    """Get the singleton PricingService instance."""
    global _service
    if _service is None:
        _service = PricingService()
    return _service
