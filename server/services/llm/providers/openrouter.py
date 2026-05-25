"""OpenRouter provider -- wraps OpenAI-compatible API with different base_url."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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

    def __init__(self, api_key: str, *, proxy_url: Optional[str] = None):
        import openai

        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "base_url": proxy_url or "https://openrouter.ai/api/v1",
            "default_headers": {
                "HTTP-Referer": "http://localhost:3000",
                "X-Title": "MachinaOS",
            },
        }
        self._client = openai.AsyncOpenAI(**kwargs)

    async def chat(
        self,
        messages: List[Message],
        *,
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: Optional[ThinkingConfig] = None,
        tools: Optional[List[ToolDef]] = None,
    ) -> LLMResponse:
        # Strip [FREE] prefix from model ID
        clean_model = model.replace("[FREE] ", "")
        return await super().chat(
            messages,
            model=clean_model,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking=thinking,
            tools=tools,
        )

    async def fetch_models(self, api_key: str) -> List[str]:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "HTTP-Referer": "http://localhost:3000",
                    "X-Title": "MachinaOS",
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
