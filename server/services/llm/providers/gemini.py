"""Google Gemini native provider using the official `google-genai` SDK."""

from __future__ import annotations

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


class GeminiProvider:
    provider_name = "gemini"

    def __init__(self, api_key: str, *, proxy_url: Optional[str] = None):
        from google import genai

        kwargs: Dict[str, Any] = {"api_key": api_key}
        if proxy_url:
            kwargs["http_options"] = {"base_url": proxy_url}
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

        # Thinking -- pass available fields, let the SDK/API resolve per model
        if thinking and thinking.enabled:
            thinking_kwargs: Dict[str, Any] = {}
            if thinking.budget:
                thinking_kwargs["thinking_budget"] = thinking.budget
            if thinking.level:
                thinking_kwargs["thinking_level"] = thinking.level
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
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                timeout=15.0,
            )
            r.raise_for_status()
            data = r.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[7:]
            models.append(name)
        return sorted(models)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _split_system_and_contents(self, messages: List[Message]):
        system_parts = []
        contents = []
        for m in messages:
            if m.role == "system":
                if m.content:
                    system_parts.append(m.content)
                continue

            if m.role == "tool":
                contents.append(
                    {
                        "role": "function",
                        "parts": [
                            {
                                "function_response": {
                                    "name": m.name or "",
                                    "response": {"result": m.content},
                                }
                            }
                        ],
                    }
                )
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
                continue

            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})

        system = "\n\n".join(system_parts) if system_parts else None
        return system, contents

    @staticmethod
    def _to_api_tool(tool: ToolDef) -> Dict[str, Any]:
        return {
            "function_declarations": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            ]
        }

    def _normalize(self, resp: Any, model: str) -> LLMResponse:
        text_parts = []
        thinking_parts = []
        tool_calls = []

        if resp.candidates:
            for part in resp.candidates[0].content.parts:
                if hasattr(part, "thought") and part.thought:
                    thinking_parts.append(part.text)
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    args = dict(fc.args) if fc.args else {}
                    tool_calls.append(
                        ToolCall(
                            id=fc.name,  # Gemini doesn't have separate IDs
                            name=fc.name,
                            args=args,
                        )
                    )
                elif hasattr(part, "text") and part.text:
                    text_parts.append(part.text)

        um = resp.usage_metadata if hasattr(resp, "usage_metadata") and resp.usage_metadata else None
        usage = Usage(
            input_tokens=getattr(um, "prompt_token_count", 0) if um else 0,
            output_tokens=getattr(um, "candidates_token_count", 0) if um else 0,
            total_tokens=getattr(um, "total_token_count", 0) if um else 0,
        )

        finish = "stop"
        if resp.candidates and hasattr(resp.candidates[0], "finish_reason"):
            fr = resp.candidates[0].finish_reason
            if fr:
                finish = str(fr).lower()

        return LLMResponse(
            content="\n".join(text_parts),
            thinking="\n\n".join(thinking_parts) if thinking_parts else None,
            tool_calls=tool_calls,
            usage=usage,
            model=model,
            finish_reason=finish,
            raw=resp,
        )


# ---------------------------------------------------------------------------
# Plugin self-registration
# ---------------------------------------------------------------------------
# ``google.genai.errors`` carries the typed ``APIError`` raised on
# Interactions-API-only models (e.g. ``antigravity-preview-05-2026`` —
# the live failure that motivated the unifier introduction). The unifier
# translates it into ``NodeUserError`` at one catch site so the error
# surfaces as a single WARN line through ``BaseNode.execute()`` instead
# of bubbling up as a bare ``RuntimeError`` traceback.

from google.genai import errors as _google_genai_errors
from services.llm.registry import ProviderSpec, register_provider

register_provider(
    ProviderSpec(
        name="gemini",
        factory=GeminiProvider,
        sdk_exception_types=(_google_genai_errors.APIError,),
    )
)
