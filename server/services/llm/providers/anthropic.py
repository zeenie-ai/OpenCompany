"""Anthropic native provider using the official `anthropic` Python SDK."""

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


class AnthropicProvider:
    provider_name = "anthropic"

    def __init__(self, api_key: str, *, proxy_url: Optional[str] = None):
        import anthropic

        kwargs: Dict[str, Any] = {"api_key": api_key}
        if proxy_url:
            kwargs["base_url"] = proxy_url
            kwargs["api_key"] = "ollama"
        self._client = anthropic.AsyncAnthropic(**kwargs)

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
        system, api_msgs = self._split_system(messages)

        params: Dict[str, Any] = {
            "model": model,
            "messages": api_msgs,
            "max_tokens": max_tokens,
        }
        if system:
            params["system"] = system

        # Thinking / extended thinking
        if thinking and thinking.enabled:
            budget = thinking.budget or 2048
            if max_tokens <= budget:
                params["max_tokens"] = budget + 1024
            params["thinking"] = {"type": "enabled", "budget_tokens": budget}
            params["temperature"] = 1  # required by Anthropic when thinking
        else:
            params["temperature"] = temperature

        # Tools
        if tools:
            params["tools"] = [self._to_api_tool(t) for t in tools]

        resp = await self._client.messages.create(**params)
        return self._normalize(resp, model)

    # ------------------------------------------------------------------
    # fetch_models
    # ------------------------------------------------------------------

    async def fetch_models(self, api_key: str) -> List[str]:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                timeout=15.0,
            )
            r.raise_for_status()
            data = r.json()
        return sorted([m["id"] for m in data.get("data", [])])

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _split_system(self, messages: List[Message]):
        """Extract system message (Anthropic takes it as a top-level param)."""
        system_parts = []
        api_msgs = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
            else:
                api_msgs.append(self._to_api_message(m))
        return "\n\n".join(system_parts) if system_parts else None, api_msgs

    def _to_api_message(self, m: Message) -> Dict[str, Any]:
        role = "assistant" if m.role == "assistant" else "user"

        if m.role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id or "",
                        "content": m.content,
                    }
                ],
            }

        if m.role == "assistant" and m.tool_calls:
            content: List[Dict[str, Any]] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.args,
                    }
                )
            return {"role": "assistant", "content": content}

        return {"role": role, "content": m.content}

    @staticmethod
    def _to_api_tool(tool: ToolDef) -> Dict[str, Any]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        text_parts = []
        thinking_parts = []
        tool_calls = []

        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "thinking":
                thinking_parts.append(block.thinking)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        args=block.input if isinstance(block.input, dict) else json.loads(block.input),
                    )
                )

        usage = Usage(
            input_tokens=getattr(resp.usage, "input_tokens", 0),
            output_tokens=getattr(resp.usage, "output_tokens", 0),
            total_tokens=getattr(resp.usage, "input_tokens", 0) + getattr(resp.usage, "output_tokens", 0),
            cache_creation_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0),
            cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0),
        )

        return LLMResponse(
            content="\n".join(text_parts),
            thinking="\n\n".join(thinking_parts) if thinking_parts else None,
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=resp.stop_reason or "stop",
            raw=resp,
        )


# ---------------------------------------------------------------------------
# Plugin self-registration
# ---------------------------------------------------------------------------
# Module load triggers registration into the global ProviderRegistry.
# The typed ``APIError`` class is declared as a lazy "module:Class" ref
# (resolved via ``pkgutil.resolve_name`` at except/read time) so that
# registration never imports the SDK — the eager import here used to
# cost seconds of boot time (docs-internal/performance.md).

from services.llm.registry import ProviderSpec, register_provider

register_provider(
    ProviderSpec(
        name="anthropic",
        factory=AnthropicProvider,
        sdk_exception_refs=("anthropic:APIError",),
    )
)
