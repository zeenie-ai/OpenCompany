"""OpenAI native provider using the official `openai` Python SDK.

Handles GPT-5.x, GPT-4.x, and o-series reasoning models.
Also used as base for OpenRouter (same API shape, different base_url).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services.llm.protocol import (
    ContentBlock,
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

    def __init__(
        self,
        api_key: str,
        *,
        proxy_url: Optional[str] = None,
        base_url: Optional[str] = None,
        provider_name: Optional[str] = None,
        max_retries: int = 2,
    ):
        import openai

        self.provider_name = provider_name or type(self).provider_name
        kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "max_retries": max(0, int(max_retries)),
        }
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
        model = self._clean_model(model)
        policy = self._model_policy(model, thinking)
        if policy["use_responses"] and (
            tools or self._has_responses_state(messages)
        ):
            return await self._chat_responses(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                thinking=thinking,
                tools=tools,
            )

        params: Dict[str, Any] = {
            "model": model,
            "messages": [self._to_api_message(m) for m in messages],
        }

        # OpenAI reasoning models use max_completion_tokens.  Compatible
        # endpoints continue to receive their documented max_tokens field.
        if policy["max_completion_tokens"]:
            params["max_completion_tokens"] = max_tokens
        else:
            params["max_tokens"] = max_tokens
        if policy["temperature_allowed"]:
            params["temperature"] = (
                policy["fixed_temperature"]
                if policy["fixed_temperature"] is not None
                else temperature
            )

        if thinking and thinking.enabled:
            thinking_type = policy["thinking_type"]
            if thinking_type == "effort":
                params["reasoning_effort"] = thinking.effort or "medium"
            elif thinking_type == "format":
                params.setdefault("extra_body", {})["reasoning_format"] = (
                    thinking.format
                    if thinking.format in {"parsed", "hidden"}
                    else "parsed"
                )
            elif thinking_type == "budget":
                params.setdefault("extra_body", {})["thinking_budget"] = max(
                    0, int(thinking.budget)
                )
        elif policy["thinking_default_on"]:
            params.setdefault("extra_body", {})["thinking"] = {
                "type": "disabled"
            }

        # Tools
        if tools:
            params["tools"] = [self._to_api_tool(t) for t in tools]

        resp = await self._client.chat.completions.create(**params)
        return self._normalize(resp, model)

    async def _chat_responses(
        self,
        *,
        messages: List[Message],
        model: str,
        max_tokens: int,
        thinking: Optional[ThinkingConfig],
        tools: Optional[List[ToolDef]],
    ) -> LLMResponse:
        """Run a self-contained Responses API turn for reasoning tool use."""

        params: Dict[str, Any] = {
            "model": model,
            "input": self._to_responses_input(messages),
            "max_output_tokens": max_tokens,
            # Durable encrypted reasoning state is recorded in our message;
            # do not depend on OpenAI retaining a prior response.
            "store": False,
            "include": ["reasoning.encrypted_content"],
        }
        if thinking and thinking.enabled:
            params["reasoning"] = {
                "effort": thinking.effort or "medium",
            }
        if tools:
            params["tools"] = [
                self._to_responses_tool(tool) for tool in tools
            ]
        response = await self._client.responses.create(**params)
        return self._normalize_responses(response, model)

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

    def _to_api_message(self, m: Message) -> Dict[str, Any]:
        if m.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": m.tool_call_id or "",
                "content": m.content,
            }

        if m.role == "assistant":
            result: Dict[str, Any] = {
                "role": "assistant",
                "content": m.content or None,
            }
            if m.tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": (
                                tc.raw_arguments
                                if tc.raw_arguments is not None
                                else json.dumps(tc.args)
                            ),
                        },
                    }
                    for tc in m.tool_calls
                ]
            state = m.provider_state
            if state.get("provider") == self.provider_name:
                payload = state.get("payload")
                if isinstance(payload, dict):
                    # These are the only continuation fields currently
                    # accepted by OpenAI-compatible chat endpoints.  Never
                    # forward arbitrary provider state into an API request.
                    for key in (
                        "reasoning",
                        "reasoning_content",
                        "reasoning_details",
                        "refusal",
                    ):
                        if payload.get(key) is not None:
                            result[key] = payload[key]
            return result

        return {"role": m.role, "content": m.content}

    def _to_api_tool(self, tool: ToolDef) -> Dict[str, Any]:
        from services.llm.schema import compile_tool_schema

        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": compile_tool_schema(
                    tool.parameters, provider=self.provider_name
                ),
            },
        }

    def _to_responses_tool(self, tool: ToolDef) -> Dict[str, Any]:
        from services.llm.schema import compile_tool_schema

        return {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "parameters": compile_tool_schema(
                tool.parameters, provider=self.provider_name
            ),
            "strict": False,
        }

    def _to_responses_input(
        self, messages: List[Message]
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id or "",
                        "output": message.content,
                    }
                )
                continue

            if message.role == "assistant":
                state = message.provider_state
                payload = state.get("payload")
                if (
                    state.get("provider") == self.provider_name
                    and isinstance(payload, dict)
                    and payload.get("api") == "responses"
                    and isinstance(payload.get("output"), list)
                ):
                    items.extend(
                        dict(item)
                        for item in payload["output"]
                        if isinstance(item, dict)
                    )
                    continue

                if message.content:
                    items.append(
                        {
                            "role": "assistant",
                            "content": message.content,
                        }
                    )
                items.extend(
                    {
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": (
                            call.raw_arguments
                            if call.raw_arguments is not None
                            else json.dumps(call.args)
                        ),
                    }
                    for call in message.tool_calls
                )
                continue

            items.append(
                {"role": message.role, "content": message.content}
            )
        return items

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        choice = resp.choices[0] if resp.choices else None
        if not choice:
            return LLMResponse(model=model)

        msg = choice.message
        content = msg.content or ""
        thinking = None
        tool_calls = []
        blocks: List[ContentBlock] = []

        # OpenAI-compatible providers expose reasoning under several fields:
        # DeepSeek/xAI commonly use reasoning_content, Groq uses reasoning,
        # and OpenRouter needs the lossless reasoning_details array replayed
        # on the next tool turn.
        reasoning_content = getattr(msg, "reasoning_content", None)
        reasoning = getattr(msg, "reasoning", None)
        if isinstance(reasoning_content, str) and reasoning_content:
            thinking = reasoning_content
        elif isinstance(reasoning, str) and reasoning:
            thinking = reasoning
        if thinking:
            blocks.append(ContentBlock(type="reasoning", text=thinking))

        refusal = getattr(msg, "refusal", None)
        if not content and isinstance(refusal, str) and refusal:
            content = refusal

        if content:
            blocks.append(
                ContentBlock(
                    type="text",
                    text=content,
                    metadata=(
                        {"refusal": True}
                        if isinstance(refusal, str) and refusal
                        else {}
                    ),
                )
            )

        # Tool calls
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                call = ToolCall.from_raw(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                )
                tool_calls.append(call)
                blocks.append(ContentBlock(type="tool_call", tool_call=call))

        u = resp.usage
        usage = Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            output_tokens=getattr(u, "completion_tokens", 0) if u else 0,
            total_tokens=getattr(u, "total_tokens", 0) if u else 0,
            cache_read_tokens=self._cache_read_tokens(u),
            reasoning_tokens=getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) if u else 0,
        )

        payload: Dict[str, Any] = {}
        if isinstance(reasoning, str) and reasoning:
            payload["reasoning"] = reasoning
        if isinstance(reasoning_content, str) and reasoning_content:
            payload["reasoning_content"] = reasoning_content
        reasoning_details = getattr(msg, "reasoning_details", None)
        if isinstance(reasoning_details, (list, tuple)):
            encoded_details = [
                self._dump_response_item(detail)
                for detail in reasoning_details
            ]
            encoded_details = [detail for detail in encoded_details if detail]
            if encoded_details:
                payload["reasoning_details"] = encoded_details
        if isinstance(refusal, str) and refusal:
            payload["refusal"] = refusal
        assistant_message = Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            blocks=blocks,
            provider_state=(
                {"provider": self.provider_name, "payload": payload}
                if payload
                else {}
            ),
        )
        return LLMResponse(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=choice.finish_reason or "stop",
            raw=resp,
            assistant_message=assistant_message,
        )

    def _normalize_responses(self, resp: Any, model: str) -> LLMResponse:
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        blocks: List[ContentBlock] = []
        output_state: List[Dict[str, Any]] = []

        for item in getattr(resp, "output", ()) or ():
            dumped = self._dump_response_item(item)
            if dumped:
                output_state.append(dumped)
            item_type = getattr(item, "type", None)
            if item_type == "reasoning":
                for summary in getattr(item, "summary", ()) or ():
                    text = getattr(summary, "text", None)
                    if isinstance(text, str) and text:
                        thinking_parts.append(text)
                        blocks.append(
                            ContentBlock(type="reasoning", text=text)
                        )
            elif item_type == "message":
                for part in getattr(item, "content", ()) or ():
                    part_type = getattr(part, "type", None)
                    text = getattr(part, "text", None)
                    if part_type == "output_text" and isinstance(text, str):
                        text_parts.append(text)
                        blocks.append(ContentBlock(type="text", text=text))
                    elif part_type == "refusal":
                        refusal = getattr(part, "refusal", None)
                        if not isinstance(refusal, str):
                            continue
                        text_parts.append(refusal)
                        blocks.append(
                            ContentBlock(
                                type="text",
                                text=refusal,
                                metadata={"refusal": True},
                            )
                        )
            elif item_type == "function_call":
                call = ToolCall.from_raw(
                    id=str(
                        getattr(item, "call_id", None)
                        or getattr(item, "id", "")
                    ),
                    name=str(getattr(item, "name", "")),
                    arguments=getattr(item, "arguments", "{}"),
                )
                tool_calls.append(call)
                blocks.append(ContentBlock(type="tool_call", tool_call=call))

        usage_obj = getattr(resp, "usage", None)
        input_details = getattr(usage_obj, "input_tokens_details", None)
        output_details = getattr(usage_obj, "output_tokens_details", None)
        usage = Usage(
            input_tokens=(
                getattr(usage_obj, "input_tokens", 0) if usage_obj else 0
            ),
            output_tokens=(
                getattr(usage_obj, "output_tokens", 0) if usage_obj else 0
            ),
            total_tokens=(
                getattr(usage_obj, "total_tokens", 0) if usage_obj else 0
            ),
            cache_read_tokens=(
                getattr(input_details, "cached_tokens", 0)
                if input_details
                else 0
            ),
            reasoning_tokens=(
                getattr(output_details, "reasoning_tokens", 0)
                if output_details
                else 0
            ),
        )
        content = "\n".join(text_parts)
        thinking_text = (
            "\n\n".join(thinking_parts) if thinking_parts else None
        )
        assistant_message = Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            blocks=blocks,
            provider_state={
                "provider": self.provider_name,
                "payload": {
                    "api": "responses",
                    "response_id": str(getattr(resp, "id", "") or ""),
                    "output": output_state,
                },
            },
        )
        return LLMResponse(
            content=content,
            thinking=thinking_text,
            tool_calls=tool_calls,
            usage=usage,
            model=str(getattr(resp, "model", "") or model),
            finish_reason=str(
                getattr(resp, "status", "") or "stop"
            ).lower(),
            raw=resp,
            assistant_message=assistant_message,
        )

    @staticmethod
    def _dump_response_item(item: Any) -> Dict[str, Any]:
        dump = getattr(item, "model_dump", None)
        if dump is not None:
            value = dump(mode="json", exclude_none=True)
            return dict(value) if isinstance(value, dict) else {}
        if isinstance(item, dict):
            return dict(item)

        # Lightweight fallback for SDK-compatible proxy objects and tests.
        item_type = getattr(item, "type", None)
        if not isinstance(item_type, str):
            return {}
        result: Dict[str, Any] = {"type": item_type}
        for key in (
            "id",
            "status",
            "role",
            "call_id",
            "name",
            "arguments",
            "encrypted_content",
        ):
            value = getattr(item, key, None)
            if isinstance(value, (str, int, float, bool)):
                result[key] = value
        for key in ("content", "summary"):
            values = getattr(item, key, None)
            if not values:
                continue
            encoded: List[Dict[str, Any]] = []
            for value in values:
                value_dump = getattr(value, "model_dump", None)
                if value_dump is not None:
                    dumped = value_dump(mode="json", exclude_none=True)
                    if isinstance(dumped, dict):
                        encoded.append(dumped)
                else:
                    entry = {
                        "type": getattr(value, "type", None),
                        "text": getattr(value, "text", None),
                    }
                    encoded.append(
                        {
                            item_key: item_value
                            for item_key, item_value in entry.items()
                            if item_value is not None
                        }
                    )
            result[key] = encoded
        return result

    def _clean_model(self, model: str) -> str:
        # Provider model IDs are opaque. Groq requires owner-qualified IDs
        # such as ``openai/gpt-oss-120b`` and ``qwen/qwen3-32b``; local
        # OpenAI-compatible endpoints may use namespaces too. Only remove the
        # UI-only OpenRouter free-tier decoration.
        return model.removeprefix("[FREE] ")

    def _model_policy(
        self, model: str, thinking: Optional[ThinkingConfig]
    ) -> Dict[str, Any]:
        """Resolve JSON-driven provider quirks in one native layer."""

        from services.llm.config import LLM_DEFAULTS

        config = LLM_DEFAULTS.get("providers", {}).get(self.provider_name, {})
        reasoning_models = tuple(config.get("reasoning_models", ()))
        is_reasoning = any(model.startswith(prefix) for prefix in reasoning_models)
        if self.provider_name == "openai":
            is_reasoning = is_reasoning or model.startswith(("o1", "o3", "o4"))
        thinking_models = tuple(config.get("thinking_models", ()))
        is_configured_thinking_model = any(
            model.startswith(prefix) or prefix in model
            for prefix in thinking_models
        )
        openai_reasoning_capable = (
            self.provider_name == "openai"
            and (is_reasoning or is_configured_thinking_model)
        )

        fixed_temperature = None
        for prefix, value in config.get("fixed_temperature", {}).items():
            if model.startswith(prefix):
                fixed_temperature = float(value)
                break

        supported = set(config.get("supported_params", ()))
        temperature_allowed = not is_reasoning and not openai_reasoning_capable
        if supported and "temperature" not in supported:
            temperature_allowed = False

        default_on = any(
            model.startswith(prefix)
            for prefix in config.get("thinking_default_on", ())
        )
        thinking_type = config.get("thinking_type", "none")
        supports_configured_thinking = (
            not thinking_models
            or is_configured_thinking_model
            or is_reasoning
        )
        if not supports_configured_thinking:
            thinking_type = "none"

        # GPT-5 and o-series models use reasoning effort. Groq's GPT-OSS
        # models also use reasoning_effort; reasoning_format is explicitly
        # unsupported for those models and is reserved for Qwen.
        if self.provider_name == "openai" and (
            model.startswith("gpt-5") or is_reasoning
        ):
            thinking_type = "effort"
        elif self.provider_name == "groq" and (
            model.startswith("openai/gpt-oss-")
            or model.startswith("gpt-oss-")
        ):
            thinking_type = "effort"

        return {
            "max_completion_tokens": (
                openai_reasoning_capable
            ),
            "use_responses": (
                openai_reasoning_capable
            ),
            "temperature_allowed": temperature_allowed
            and not (
                self.provider_name == "openai"
                and thinking is not None
                and thinking.enabled
                and thinking_type == "effort"
            ),
            "fixed_temperature": fixed_temperature,
            "thinking_default_on": default_on,
            "thinking_type": thinking_type,
        }

    def _has_responses_state(self, messages: List[Message]) -> bool:
        return any(
            message.provider_state.get("provider") == self.provider_name
            and isinstance(message.provider_state.get("payload"), dict)
            and message.provider_state["payload"].get("api") == "responses"
            for message in messages
        )

    @staticmethod
    def _cache_read_tokens(usage: Any) -> int:
        if not usage:
            return 0
        details = getattr(usage, "prompt_tokens_details", None)
        return getattr(details, "cached_tokens", 0) if details else 0

    async def aclose(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


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
