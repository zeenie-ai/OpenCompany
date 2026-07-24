"""Google Gemini native provider using the official `google-genai` SDK."""

from __future__ import annotations

import base64
import hashlib
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


class GeminiProvider:
    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        *,
        proxy_url: Optional[str] = None,
        max_retries: Optional[int] = None,
    ):
        from google import genai

        from services.llm.vertex import is_vertex_express_key

        http_options: Dict[str, Any] = {}
        if max_retries is not None:
            # Gemini counts the original request as an attempt.
            http_options["retry_options"] = {
                "attempts": max(1, int(max_retries) + 1)
            }
        self._vertex = is_vertex_express_key(api_key)
        if self._vertex:
            # Agent Platform / Vertex Express key — the SDK routes to
            # aiplatform.googleapis.com and bills the key's GCP project.
            # proxy_url rewrites base_url to a local auth-delegating relay,
            # which is meaningless against the Vertex endpoint.
            if proxy_url:
                logger.warning("gemini: proxy_url ignored in Vertex AI mode")
            kwargs: Dict[str, Any] = {
                "vertexai": True,
                "api_key": api_key,
            }
            if http_options:
                kwargs["http_options"] = http_options
            self._client = genai.Client(**kwargs)
            return

        kwargs: Dict[str, Any] = {"api_key": api_key}
        if proxy_url:
            http_options["base_url"] = proxy_url
        if http_options:
            kwargs["http_options"] = http_options
        self._client = genai.Client(**kwargs)

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
        from google.genai import types

        system_instruction, contents = self._split_system_and_contents(messages)

        config: Dict[str, Any] = {
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }

        if system_instruction:
            config["system_instruction"] = system_instruction

        # Thinking -- forward exactly the fields the caller configured and
        # let the SDK/API validate per model. `level` is None unless the
        # user explicitly set thinking_level (Vertex rejects an unsolicited
        # thinking_level on 2.5-era models with 400 INVALID_ARGUMENT).
        if thinking and thinking.enabled:
            thinking_kwargs: Dict[str, Any] = {"include_thoughts": True}
            if thinking.level:
                thinking_kwargs["thinking_level"] = thinking.level
            elif thinking.budget:
                thinking_kwargs["thinking_budget"] = thinking.budget
            config["thinking_config"] = types.ThinkingConfig(**thinking_kwargs)

        # Tools
        if tools:
            config["tools"] = [self._to_api_tool(t) for t in tools]

        resp = await self._client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**config),
        )
        return self._normalize(resp, model)

    # ------------------------------------------------------------------
    # fetch_models
    # ------------------------------------------------------------------

    async def fetch_models(self, api_key: str) -> List[str]:
        """Validate the key, then return the curated model list.

        The curated list (``max_output_tokens`` keys from the gemini
        block in llm_defaults.json — real model names, no ``-latest``
        aliases) serves both backends; Vertex keys additionally drop the
        ``vertex_incompatible_models`` entries (Gemma is Developer-API
        only — on Vertex it is a Model Garden self-deploy and the plain
        ids 404). Only the key probe differs: Vertex rejects API keys on
        ``models.list`` (401 — requires an OAuth principal), so a free
        ``count_tokens`` call verifies the key there; the Developer API
        uses its models endpoint. Invalid keys raise the typed SDK error
        which the unifier translates into ``NodeUserError``.
        """
        models = self._curated_models()

        if self._vertex:
            vertex_blocked = self._vertex_incompatible_models()
            models = [m for m in models if m not in vertex_blocked]
            probe_model = models[0] if models else "gemini-2.5-flash"
            await self._client.aio.models.count_tokens(
                model=probe_model, contents="hello"
            )
            return models

        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                timeout=15.0,
            )
            r.raise_for_status()
        return models

    @staticmethod
    def _curated_models() -> List[str]:
        from services.llm import config as llm_config

        max_tokens_map = (
            llm_config.LLM_DEFAULTS.get("providers", {})
            .get("gemini", {})
            .get("max_output_tokens", {})
        )
        return [m for m in max_tokens_map if m != "_default"]

    @staticmethod
    def _vertex_incompatible_models() -> frozenset:
        from services.llm import config as llm_config

        return frozenset(
            llm_config.LLM_DEFAULTS.get("providers", {})
            .get("gemini", {})
            .get("vertex_incompatible_models", [])
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _split_system_and_contents(self, messages: List[Message]):
        system_parts = []
        contents = []
        index = 0
        while index < len(messages):
            m = messages[index]
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
                index += 1
                continue

            if m.role == "tool":
                # Gemini expects every response to one parallel function-call
                # batch in a single user turn.  The shared agent runtime keeps
                # one normalized Message per result, so coalesce the
                # consecutive run here at the provider boundary.
                response_parts: List[Dict[str, Any]] = []
                while index < len(messages) and messages[index].role == "tool":
                    tool_message = messages[index]
                    function_response: Dict[str, Any] = {
                        "name": tool_message.name or "",
                        "response": {"result": tool_message.content},
                    }
                    if tool_message.tool_call_id:
                        function_response["id"] = tool_message.tool_call_id
                    response_parts.append(
                        {"function_response": function_response}
                    )
                    index += 1
                contents.append(
                    {
                        "role": "user",
                        "parts": response_parts,
                    }
                )
                continue

            if m.role == "assistant":
                state = m.provider_state
                if state.get("provider") == self.provider_name:
                    payload = state.get("payload")
                    if isinstance(payload, dict) and isinstance(
                        payload.get("parts"), list
                    ):
                        contents.append(
                            {
                                "role": "model",
                                "parts": [
                                    self._state_to_api_part(part)
                                    for part in payload["parts"]
                                    if isinstance(part, dict)
                                ],
                            }
                        )
                        index += 1
                        continue

            if m.role == "assistant" and m.tool_calls:
                parts = []
                if m.content:
                    parts.append({"text": m.content})
                for tc in m.tool_calls:
                    parts.append(
                        {
                            "function_call": {
                                "name": tc.name,
                                "args": tc.args,
                            }
                        }
                    )
                contents.append({"role": "model", "parts": parts})
                index += 1
                continue

            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})
            index += 1

        system = "\n\n".join(system_parts) if system_parts else None
        return system, contents

    @staticmethod
    def _to_api_tool(tool: ToolDef) -> Dict[str, Any]:
        from services.llm.schema import compile_tool_schema

        return {
            "function_declarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": compile_tool_schema(
                        tool.parameters, provider="gemini"
                    ),
                }
            ]
        }

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        text_parts = []
        thinking_parts = []
        tool_calls = []
        blocks: List[ContentBlock] = []
        provider_parts: List[Dict[str, Any]] = []

        if resp.candidates:
            response_identity = getattr(resp, "response_id", None)
            if not isinstance(response_identity, str):
                response_identity = ""
            for index, part in enumerate(resp.candidates[0].content.parts):
                if hasattr(part, "thought") and part.thought:
                    state_part = self._api_part_to_state(part)
                    if state_part:
                        provider_parts.append(state_part)
                    text = getattr(part, "text", "") or ""
                    thinking_parts.append(text)
                    metadata = {}
                    if state_part.get("thought_signature_b64"):
                        metadata["thought_signature_b64"] = state_part[
                            "thought_signature_b64"
                        ]
                    blocks.append(
                        ContentBlock(
                            type="reasoning",
                            text=text,
                            metadata=metadata,
                        )
                    )
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    raw_args = getattr(fc, "args", {})
                    provider_call_id = getattr(fc, "id", None)
                    call_id = (
                        str(provider_call_id)
                        if provider_call_id
                        else self._stable_tool_call_id(
                            response_identity=response_identity,
                            model=model,
                            index=index,
                            name=fc.name,
                            args=raw_args,
                        )
                    )
                    call = ToolCall.from_raw(
                        id=call_id,
                        name=fc.name,
                        arguments=raw_args,
                    )
                    state_part = self._api_part_to_state(
                        part,
                        function_call_id=call_id,
                        tool_call=call,
                    )
                    if state_part:
                        provider_parts.append(state_part)
                    tool_calls.append(call)
                    blocks.append(ContentBlock(type="tool_call", tool_call=call))
                elif hasattr(part, "text") and part.text:
                    state_part = self._api_part_to_state(part)
                    if state_part:
                        provider_parts.append(state_part)
                    text_parts.append(part.text)
                    blocks.append(ContentBlock(type="text", text=part.text))
                else:
                    state_part = self._api_part_to_state(part)
                    if state_part:
                        provider_parts.append(state_part)

        um = resp.usage_metadata if hasattr(resp, "usage_metadata") and resp.usage_metadata else None
        usage = Usage(
            input_tokens=getattr(um, "prompt_token_count", 0) if um else 0,
            output_tokens=getattr(um, "candidates_token_count", 0) if um else 0,
            total_tokens=getattr(um, "total_token_count", 0) if um else 0,
            cache_read_tokens=getattr(um, "cached_content_token_count", 0) if um else 0,
            reasoning_tokens=getattr(um, "thoughts_token_count", 0) if um else 0,
        )

        finish = "stop"
        if resp.candidates and hasattr(resp.candidates[0], "finish_reason"):
            fr = resp.candidates[0].finish_reason
            if fr:
                finish = str(getattr(fr, "value", fr)).lower()

        content = "\n".join(text_parts)
        thinking = "\n\n".join(thinking_parts) if thinking_parts else None
        assistant_message = Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls,
            blocks=blocks,
            provider_state={
                "provider": self.provider_name,
                "payload": {"parts": provider_parts},
            },
        )
        return LLMResponse(
            content=content,
            thinking=thinking,
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=finish,
            raw=resp,
            assistant_message=assistant_message,
        )

    @staticmethod
    def _api_part_to_state(
        part: Any,
        *,
        function_call_id: Optional[str] = None,
        tool_call: Optional[ToolCall] = None,
    ) -> Dict[str, Any]:
        state: Dict[str, Any] = {}
        function_call = getattr(part, "function_call", None)
        if function_call:
            normalized_call = tool_call or ToolCall.from_raw(
                id=str(function_call_id or getattr(function_call, "id", "")),
                name=str(getattr(function_call, "name", "")),
                arguments=getattr(function_call, "args", {}),
            )
            call_state: Dict[str, Any] = {
                "name": normalized_call.name,
                "args": normalized_call.args,
            }
            call_id = normalized_call.id
            if call_id:
                call_state["id"] = str(call_id)
            if normalized_call.raw_arguments is not None:
                call_state["raw_arguments"] = (
                    normalized_call.raw_arguments
                )
            if normalized_call.parse_error is not None:
                call_state["parse_error"] = normalized_call.parse_error
            state["function_call"] = call_state
        else:
            text = getattr(part, "text", None)
            if isinstance(text, str):
                state["text"] = text
            thought = getattr(part, "thought", None)
            if isinstance(thought, bool):
                state["thought"] = thought

        signature = getattr(part, "thought_signature", None)
        if isinstance(signature, bytes):
            state["thought_signature_b64"] = base64.b64encode(signature).decode(
                "ascii"
            )
        elif isinstance(signature, str) and signature:
            # Some SDK versions expose the already-base64 value as str.
            state["thought_signature_b64"] = signature
        return state

    @staticmethod
    def _state_to_api_part(state: Dict[str, Any]) -> Dict[str, Any]:
        part: Dict[str, Any] = {}
        if isinstance(state.get("function_call"), dict):
            call = state["function_call"]
            part["function_call"] = {
                key: call[key]
                for key in ("name", "args", "id")
                if key in call
            }
        elif isinstance(state.get("text"), str):
            part["text"] = state["text"]
        if isinstance(state.get("thought"), bool):
            part["thought"] = state["thought"]
        encoded = state.get("thought_signature_b64")
        if isinstance(encoded, str) and encoded:
            try:
                part["thought_signature"] = base64.b64decode(
                    encoded, validate=True
                )
            except (ValueError, TypeError):
                # The state codec is durable; corrupt continuation metadata
                # should fail loudly instead of silently changing the turn.
                raise ValueError("Invalid Gemini thought_signature_b64")
        return part

    @staticmethod
    def _stable_tool_call_id(
        *,
        response_identity: str,
        model: str,
        index: int,
        name: str,
        args: Any,
    ) -> str:
        material = json.dumps(
            [response_identity, model, index, name, args],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest = hashlib.sha256(material).hexdigest()[:16]
        return f"gemini-{index}-{digest}"

    async def aclose(self) -> None:
        aio = getattr(self._client, "aio", None)
        close = getattr(aio, "aclose", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result
                return
        close = getattr(self._client, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


# ---------------------------------------------------------------------------
# Plugin self-registration
# ---------------------------------------------------------------------------
# ``google.genai.errors`` carries the typed ``APIError`` raised on
# Interactions-API-only models (e.g. ``antigravity-preview-05-2026`` —
# the live failure that motivated the unifier introduction). The unifier
# translates it into ``NodeUserError`` at one catch site so the error
# surfaces as a single WARN line through ``BaseNode.execute()`` instead
# of bubbling up as a bare ``RuntimeError`` traceback.
#
# Declared as a lazy "module:Class" ref — ``google.genai`` is the single
# most expensive SDK import on the boot path (~4s warm / ~15s cold via
# google.auth + api_core + protobuf); the ref defers it to first use.

from services.llm.registry import ProviderSpec, register_provider

register_provider(
    ProviderSpec(
        name="gemini",
        factory=GeminiProvider,
        sdk_exception_refs=("google.genai.errors:APIError",),
    )
)
