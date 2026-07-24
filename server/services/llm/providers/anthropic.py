"""Anthropic native provider using the official `anthropic` Python SDK."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services.llm.protocol import (
    ContentBlock,
    decode_binary_state,
    encode_binary_state,
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

    def __init__(
        self,
        api_key: str,
        *,
        proxy_url: Optional[str] = None,
        max_retries: int = 2,
    ):
        import anthropic

        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "max_retries": max(0, int(max_retries)),
        }
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
            budget = max(1024, int(thinking.budget or 2048))
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
        index = 0
        while index < len(messages):
            m = messages[index]
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
                index += 1
                continue

            if m.role == "tool":
                # A parallel Anthropic tool-use turn must be answered by one
                # user message containing every corresponding tool_result
                # block.  Keep normalized messages provider-neutral and
                # coalesce their consecutive run only while compiling the
                # request.
                result_blocks: List[Dict[str, Any]] = []
                while index < len(messages) and messages[index].role == "tool":
                    tool_message = messages[index]
                    result_blocks.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_message.tool_call_id or "",
                            "content": tool_message.content,
                        }
                    )
                    index += 1
                api_msgs.append({"role": "user", "content": result_blocks})
                continue

            api_msgs.append(self._to_api_message(m))
            index += 1
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

        if m.role == "assistant":
            state = m.provider_state
            if state.get("provider") == self.provider_name:
                payload = state.get("payload")
                if isinstance(payload, dict) and isinstance(
                    payload.get("content"), list
                ):
                    return {
                        "role": "assistant",
                        "content": [
                            self._state_block_to_api(block)
                            for block in payload["content"]
                            if isinstance(block, dict)
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
        from services.llm.schema import compile_tool_schema

        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": compile_tool_schema(
                tool.parameters, provider="anthropic"
            ),
        }

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        text_parts = []
        thinking_parts = []
        tool_calls = []
        blocks: List[ContentBlock] = []
        provider_blocks: List[Dict[str, Any]] = []

        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
                blocks.append(ContentBlock(type="text", text=block.text))
                provider_blocks.append({"type": "text", "text": block.text})
            elif block.type == "thinking":
                thinking_parts.append(block.thinking)
                metadata: Dict[str, Any] = {}
                provider_block: Dict[str, Any] = {
                    "type": "thinking",
                    "thinking": block.thinking,
                }
                signature = getattr(block, "signature", None)
                if isinstance(
                    signature, (str, bytes, bytearray, memoryview)
                ):
                    durable_signature = encode_binary_state(signature)
                    metadata["signature"] = durable_signature
                    provider_block["signature"] = durable_signature
                blocks.append(
                    ContentBlock(
                        type="reasoning",
                        text=block.thinking,
                        metadata=metadata,
                    )
                )
                provider_blocks.append(provider_block)
            elif block.type == "redacted_thinking":
                data = getattr(block, "data", "")
                if isinstance(data, (bytes, bytearray, memoryview)):
                    data = encode_binary_state(data)
                elif not isinstance(data, str):
                    data = str(data)
                blocks.append(
                    ContentBlock(
                        type="reasoning",
                        metadata={"redacted": True},
                    )
                )
                provider_blocks.append(
                    {"type": "redacted_thinking", "data": data}
                )
            elif block.type == "tool_use":
                call = ToolCall.from_raw(
                    id=block.id,
                    name=block.name,
                    arguments=block.input,
                )
                tool_calls.append(call)
                blocks.append(ContentBlock(type="tool_call", tool_call=call))
                provider_blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": (
                            block.input
                            if isinstance(
                                block.input,
                                (dict, list, str, int, float, bool, type(None)),
                            )
                            else str(block.input)
                        ),
                    }
                )

        input_tokens = getattr(resp.usage, "input_tokens", 0) or 0
        output_tokens = getattr(resp.usage, "output_tokens", 0) or 0
        cache_creation_tokens = (
            getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        )
        cache_read_tokens = (
            getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        )
        usage = Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=(
                input_tokens
                + cache_creation_tokens
                + cache_read_tokens
                + output_tokens
            ),
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
        )

        content = "\n".join(text_parts)
        thinking = "\n\n".join(thinking_parts) if thinking_parts else None
        assistant_message = Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            blocks=blocks,
            provider_state={
                "provider": self.provider_name,
                "payload": {"content": provider_blocks},
            },
        )
        return LLMResponse(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=resp.stop_reason or "stop",
            raw=resp,
            assistant_message=assistant_message,
        )

    @staticmethod
    def _state_block_to_api(block: Dict[str, Any]) -> Dict[str, Any]:
        """Rehydrate only the binary fields Anthropic returned originally."""

        result = dict(block)
        if result.get("type") == "thinking" and "signature" in result:
            result["signature"] = decode_binary_state(result["signature"])
        elif (
            result.get("type") == "redacted_thinking"
            and "data" in result
        ):
            result["data"] = decode_binary_state(result["data"])
        return result

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


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
