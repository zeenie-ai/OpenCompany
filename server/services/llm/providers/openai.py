"""OpenAI native provider using the official `openai` Python SDK.

Handles GPT-5.x, GPT-4.x, and o-series reasoning models.
Also used as base for OpenRouter (same API shape, different base_url).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services.llm.protocol import (
    LLMResponse,
    Message,
    ThinkingConfig,
    ToolCall,
    ToolDef,
    Usage,
)

logger = get_logger(__name__)


class OpenAIProvider:
    provider_name = "openai"

    def __init__(self, api_key: str, *, proxy_url: Optional[str] = None, base_url: Optional[str] = None):
        import openai

        kwargs: Dict[str, Any] = {"api_key": api_key}
        url = proxy_url or base_url
        if url:
            kwargs["base_url"] = url
            if proxy_url:
                kwargs["api_key"] = "ollama"
        self._client = openai.AsyncOpenAI(**kwargs)

    # ------------------------------------------------------------------
    # chat
    # ------------------------------------------------------------------

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
        params: Dict[str, Any] = {
            "model": model,
            "messages": [self._to_api_message(m) for m in messages],
        }

        is_reasoning = model.startswith(("o1", "o3", "o4"))

        # Reasoning models use max_completion_tokens, not max_tokens
        if is_reasoning:
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens
            params["temperature"] = temperature

        # Thinking / reasoning effort
        if thinking and thinking.enabled:
            effort = thinking.effort or "medium"
            if is_reasoning:
                params["reasoning_effort"] = effort
            elif model.startswith("gpt-5"):
                params["reasoning_effort"] = effort

        # Tools
        if tools:
            params["tools"] = [self._to_api_tool(t) for t in tools]

        resp = await self._client.chat.completions.create(**params)
        return self._normalize(resp, model)

    # ------------------------------------------------------------------
    # fetch_models
    # ------------------------------------------------------------------

    async def fetch_models(self, api_key: str) -> List[str]:
        """Fetch models using the client's configured base_url."""
        models = await self._client.models.list()
        return sorted([m.id for m in models.data])

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    @staticmethod
    def _to_api_message(m: Message) -> Dict[str, Any]:
        if m.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content,
            }

        if m.role == "assistant" and m.tool_calls:
            return {
                "role": "assistant",
                "content": m.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.args),
                        },
                    }
                    for tc in m.tool_calls
                ],
            }

        return {"role": m.role, "content": m.content}

    @staticmethod
    def _to_api_tool(tool: ToolDef) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        choice = resp.choices[0] if resp.choices else None
        if not choice:
            return LLMResponse(model=model)

        msg = choice.message
        content = msg.content or ""
        thinking = None
        tool_calls = []

        # Extract reasoning/thinking if present
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            thinking = msg.reasoning_content

        # Tool calls
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        args=json.loads(args) if isinstance(args, str) else args,
                    )
                )

        u = resp.usage
        usage = Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            output_tokens=getattr(u, "completion_tokens", 0) if u else 0,
            total_tokens=getattr(u, "total_tokens", 0) if u else 0,
            reasoning_tokens=getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) if u else 0,
        )

        return LLMResponse(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=choice.finish_reason or "stop",
            raw=resp,
        )


# ---------------------------------------------------------------------------
# Plugin self-registration
# ---------------------------------------------------------------------------
# Registers ``openai`` into the global registry. The typed
# ``OpenAIError`` class is declared as a lazy "module:Class" ref so
# registration never imports the SDK at boot.
#
# OpenAI-compatible providers (xai / deepseek / kimi / mistral / ollama /
# lmstudio) register separately in ``_compat.py`` — they reuse
# ``OpenAIProvider`` as their factory but pin ``base_url`` via
# ``ProviderSpec.client_kwargs`` rather than per-provider Python.

from services.llm.registry import ProviderSpec, register_provider

register_provider(
    ProviderSpec(
        name="openai",
        factory=OpenAIProvider,
        sdk_exception_refs=("openai:OpenAIError",),
    )
)
