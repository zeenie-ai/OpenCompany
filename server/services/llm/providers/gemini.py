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

        from services.llm.vertex import is_vertex_express_key

        self._vertex = is_vertex_express_key(api_key)
        if self._vertex:
            # Agent Platform / Vertex Express key — the SDK routes to
            # aiplatform.googleapis.com and bills the key's GCP project.
            # proxy_url rewrites base_url to a local auth-delegating relay,
            # which is meaningless against the Vertex endpoint.
            if proxy_url:
                logger.warning("gemini: proxy_url ignored in Vertex AI mode")
            self._client = genai.Client(vertexai=True, api_key=api_key)
            return

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

        # Thinking -- forward exactly the fields the caller configured and
        # let the SDK/API validate per model. `level` is None unless the
        # user explicitly set thinking_level (Vertex rejects an unsolicited
        # thinking_level on 2.5-era models with 400 INVALID_ARGUMENT).
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
        """Validate the key, then return the curated model list.

        The same curated list (``max_output_tokens`` keys from the gemini
        block in llm_defaults.json — real model names, no ``-latest``
        aliases) serves both backends, so the dropdown is identical for
        AI Studio and Vertex keys. Only the key probe differs: Vertex
        rejects API keys on ``models.list`` (401 — requires an OAuth
        principal), so a free ``count_tokens`` call verifies the key
        there; the Developer API uses its models endpoint. Invalid keys
        raise the typed SDK error which the unifier translates into
        ``NodeUserError``.
        """
        models = self._curated_models()

        if self._vertex:
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
