"""OpenRouter provider -- wraps OpenAI-compatible API with different base_url."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from core.logging import get_logger
from services.llm.protocol import (
    LLMResponse,
    Message,
    ThinkingConfig,
    ToolDef,
)
from services.llm.providers.openai import OpenAIProvider

logger = get_logger(__name__)


class OpenRouterProvider(OpenAIProvider):
    provider_name = "openrouter"

    def __init__(
        self,
        api_key: str,
        *,
        proxy_url: Optional[str] = None,
        max_retries: int = 2,
    ):
        import openai

        self.provider_name = "openrouter"
        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "base_url": proxy_url or "https://openrouter.ai/api/v1",
            "max_retries": max(0, int(max_retries)),
            "default_headers": {
                "HTTP-Referer": "http://localhost:5678",
                "X-Title": "OpenCompany",
            },
        }
        self._client = openai.AsyncOpenAI(**kwargs)

    async def chat(
        self,
        messages: List[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = 4096,
        thinking: Optional[ThinkingConfig] = None,
        tools: Optional[List[ToolDef]] = None,
        context_management: Optional[Dict[str, Any]] = None,
        effort: Optional[str] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        extra_body: Optional[Mapping[str, Any]] = None,
    ) -> LLMResponse:
        # OpenAI-compatible endpoints must prove support independently; the
        # OpenRouter adapter currently uses portable Context checkpoints.
        del context_management
        # Strip [FREE] prefix from model ID
        clean_model = model.replace("[FREE] ", "")
        if effort:
            # OpenRouter takes effort in its reasoning object, which it maps
            # to each vendor's own setting (Anthropic's output_config.effort).
            # Only the effort_models receive one (ChatUnifier).
            extra_body = {**(extra_body or {}), "reasoning": {"effort": effort}}
        return await super().chat(
            messages,
            model=clean_model,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            tools=tools,
            context_management=None,
            extra_headers=extra_headers,
            extra_body=extra_body,
        )

    def _reported_cost(self, usage: Any) -> Optional[float]:
        """What OpenRouter charged for the call, in USD (its credits).

        Every response carries ``usage.cost``, the amount taken from the
        account. On a bring-your-own-key call that is only OpenRouter's fee,
        and the provider's own charge is beside it in
        ``usage.cost_details.upstream_inference_cost``, so the two are added.
        (openrouter.ai/docs/use-cases/usage-accounting)

        The guide says a call paid with credits has no upstream charge, but
        one of its examples lists one, so a call marked ``is_byok: false``
        reports ``cost`` alone and is never counted twice.

        Anything but those shapes reports nothing: this runs after the call
        was paid for, and an error here would make the step send it again.
        """
        cost = getattr(usage, "cost", None)
        if not isinstance(cost, (int, float)):
            return None
        details = getattr(usage, "cost_details", None)
        upstream = details.get("upstream_inference_cost") if isinstance(details, dict) else None
        if getattr(usage, "is_byok", None) is False or not isinstance(upstream, (int, float)):
            return cost
        return cost + upstream

    async def fetch_models(self, api_key: str) -> List[str]:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "http://localhost:5678",
                    "X-Title": "OpenCompany",
                },
                timeout=15.0,
            )
            r.raise_for_status()
            data = r.json()

        models = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            pricing = m.get("pricing", {})
            is_free = pricing.get("prompt") == "0" and pricing.get("completion") == "0"
            display = f"[FREE] {model_id}" if is_free else model_id
            models.append(display)

        return sorted(models, key=lambda x: (not x.startswith("[FREE]"), x))


# ---------------------------------------------------------------------------
# Plugin self-registration
# ---------------------------------------------------------------------------
# OpenRouter rides the OpenAI Python SDK with a different ``base_url`` +
# extra headers, so its typed exceptions are ``openai.OpenAIError``
# subclasses — same lazy exception ref as the openai provider.

from services.llm.registry import ProviderSpec, register_provider

register_provider(
    ProviderSpec(
        name="openrouter",
        factory=OpenRouterProvider,
        sdk_exception_refs=("openai:OpenAIError",),
    )
)
