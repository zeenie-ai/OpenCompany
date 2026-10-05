"""AI service backed by the native provider SDK layer.

Every in-process and durable agent execution goes through
:mod:`services.agent_runtime` and :mod:`services.llm`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, List, Optional, Type, TYPE_CHECKING

import json

# ``NodeUserError`` is the framework's "user-correctable, no-traceback"
# sentinel used to surface typed SDK errors cleanly through
# ``BaseNode.execute()``.
from services.plugin import NodeUserError
from services.agent_bindings import delegation_roster, is_runtime_tool, unique_node_bindings, rebind_allowed, extend_runtime_graph
from services.tool_identity import DuplicateToolNameError, ensure_unique_tool_names

if TYPE_CHECKING:
    from pydantic import BaseModel, Field, create_model  # noqa: F401


# Backwards-compat shim (PEP 562 module-level ``__getattr__``) for code that
# historically read ``PROVIDER_CONFIGS`` as a module-level constant here. It
# resolves to the native config map.
def __getattr__(name: str):
    if name == "PROVIDER_CONFIGS":
        return NATIVE_PROVIDER_CONFIGS
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from core.config import Settings
from core.logging import get_logger, log_execution_time, log_api_call
from services.auth import AuthService

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Native LLM provider/runtime imports for every new chat and agent execution
# ---------------------------------------------------------------------------
# ``ChatUnifier`` (injected via DI) owns provider dispatch. The legacy
# ``services.llm.factory`` module (``create_provider`` /
# ``is_native_provider``) was removed after the unifier cutover.
from services.llm.protocol import (
    Message as NativeMessage,
    ThinkingConfig as NativeThinkingConfig,
    LLMResponse,
    ToolDef,
    Usage,
)
from services.llm.config import (
    PROVIDER_CONFIGS as NATIVE_PROVIDER_CONFIGS,
    detect_provider_from_model as native_detect_provider_from_model,
    get_default_model as native_get_default_model,
    get_default_model_async as native_get_default_model_async,
    is_model_valid_for_provider as native_is_model_valid_for_provider,
    resolve_max_tokens as native_resolve_max_tokens,
    resolve_temperature as native_resolve_temperature,
)
from services.agent_runtime import AgentToolSpec, run_native_agent_loop
from services.tool_output import resolve_tool_output_limit


# =============================================================================
# MARKDOWN MEMORY HELPERS - Parse/append/trim conversation markdown
# =============================================================================


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "reasoning_tokens",
)


def _coerce_native_usage(value: Any) -> Usage:
    if isinstance(value, Usage):
        return value
    if isinstance(value, dict):
        return Usage(
            **{
                key: value.get(key, 0)
                for key in _USAGE_FIELDS
            }
        )
    return Usage()


def _accumulate_compaction_usage(
    final_state: Dict[str, Any],
    compaction_result: Optional[Dict[str, Any]],
) -> None:
    """Add summarizer billing to execution usage, not context thresholds."""

    if not compaction_result or not compaction_result.get("usage"):
        return
    final_state["usage"] = _coerce_native_usage(
        final_state.get("usage")
    ) + _coerce_native_usage(compaction_result["usage"])


def _parse_memory_markdown(content: str) -> List[NativeMessage]:
    """Compatibility wrapper for the native Markdown memory parser."""

    from services.memory.markdown import parse_memory_markdown

    return parse_memory_markdown(content)


# Compatibility alias for callers that clear the historical cache directly.
# The implementation and ownership now live in ``services.memory.vector_store``.
from services.memory.vector_store import (
    _memory_vector_stores as _memory_vector_stores,
    get_memory_vector_store as _get_memory_vector_store,
)


async def _get_configured_memory_vector_store(
    session_id: str,
    memory_data: Dict[str, Any],
    auth_service: Any,
):
    """Resolve one memory node's async native embedding store."""

    return await _get_memory_vector_store(
        session_id,
        provider=memory_data.get("embedding_provider", "huggingface"),
        model=memory_data.get("embedding_model"),
        endpoint=memory_data.get("embedding_endpoint"),
        auth_service=auth_service,
    )


# =============================================================================
# AI PROVIDER REGISTRY - Single source of truth for provider configurations
# =============================================================================


# Compatibility export. Native configuration is the single source of truth.
ThinkingConfig = NativeThinkingConfig


# =============================================================================
# LLM DEFAULTS CONFIGURATION - Load from config/llm_defaults.json
# =============================================================================


def _load_llm_defaults() -> Dict[str, Any]:
    """Load LLM provider defaults from config/llm_defaults.json."""
    from pathlib import Path

    config_path = Path(__file__).parent.parent / "config" / "llm_defaults.json"
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load llm_defaults.json: {e}")
        return {"providers": {}}


_LLM_DEFAULTS = _load_llm_defaults()


def _get_default_model(provider: str, fallback: str) -> str:
    """Get default model for a provider from config, with fallback."""
    providers = _LLM_DEFAULTS.get("providers", {})
    return providers.get(provider, {}).get("default_model", fallback)


def _require_saved_endpoint(provider: str) -> None:
    """Called when no key was injected: a named endpoint is unsaved, not keyless.

    Saving an endpoint always stores a key (the user's or the declared
    placeholder), so a missing key means the endpoint was removed or never
    chosen. Say that, as a user-correctable error, instead of "API key is
    required". Any other provider falls through to the caller's own error.
    """
    from services.llm.endpoints import unconfigured_endpoint_message

    message = unconfigured_endpoint_message(provider)
    if message:
        raise NodeUserError(message)


def _resolve_max_tokens(flattened: dict, model: str, provider: str) -> int:
    """Resolve max_tokens: user param (clamped to model max) -> model max.

    Delegates to ``services/llm/config.py::resolve_max_tokens`` so the
    agent paths (execute_agent / execute_chat_agent / F4.B
    prepare_agent_payload) and the native chat path agree: when the user
    doesn't set max_tokens, the model's full supported output budget is
    used (registry -> llm_defaults fallback), never an artificial
    provider-wide floor.
    """
    return native_resolve_max_tokens(flattened, model, provider)


def _resolve_temperature(flattened: dict, model: str, provider: str, thinking_enabled: bool) -> float:
    """Resolve temperature through the native provider configuration."""
    return native_resolve_temperature(
        flattened,
        model,
        provider,
        thinking_enabled,
    )


def detect_provider_from_model(model: str) -> str:
    """Detect AI provider from model name using registry patterns."""
    return native_detect_provider_from_model(model)


def is_model_valid_for_provider(model: str, provider: str) -> bool:
    """Check if model name matches the provider's patterns.

    Pattern-matching is meaningful for cloud providers — `gpt-*` is OpenAI,
    `claude-*` is Anthropic, etc. — and the check guards against picking
    a model from one provider's dropdown after switching to another.

    For "open-world" providers (OpenRouter proxy, local Ollama / LM Studio
    servers) the model namespace is whatever the user has installed:
    `llama-3.2`, `qwen2.5-coder`, `phi-3-mini`, custom GGUF files, etc.
    None of those contain the literal substrings `ollama` or `lmstudio`,
    so applying the cloud-style filter produces a false negative on every
    valid local model — the call site then "uses default" which for a
    local provider is the SAME model name, emitting a confusing
    "invalid ... using default: <same name>" log line. Treat all three
    as always-valid; the local-server SDK will reject genuinely missing
    models at request time with a clear 404.
    """
    return native_is_model_valid_for_provider(model, provider)


def get_default_model(provider: str) -> str:
    """Get default model for a provider from JSON config."""
    return native_get_default_model(provider)


async def get_default_model_async(provider: str, database) -> str:
    """Get default model for a provider, checking database first then JSON config.

    Priority: database user setting > JSON config file > fallback
    """
    return await native_get_default_model_async(provider, database)


# =============================================================================
# MESSAGE FILTERING UTILITIES - Standardized for all providers
# =============================================================================


def is_valid_message_content(content: Any) -> bool:
    """Check if message content is valid (non-empty) for API calls.

    This is a standardized utility for validating message content before:
    - Saving to conversation memory
    - Including in API requests
    - Building message history

    Args:
        content: The message content to validate (str, list, or other)

    Returns:
        True if content is valid and non-empty, False otherwise
    """
    if content is None:
        return False

    # Handle list content format (Gemini returns [{"type": "text", "text": "..."}])
    if isinstance(content, list):
        return any(
            (isinstance(block, dict) and block.get("text", "").strip()) or (isinstance(block, str) and block.strip()) for block in content
        )

    # Handle string content (most common)
    if isinstance(content, str):
        return bool(content.strip())

    # Other truthy content types
    return bool(content)


def _build_skill_system_prompt(skill_data: List[Dict[str, Any]], log_prefix: str = "[Agent]") -> tuple:
    """Build skill injection text for the system message.

    Personality skills (names ending in '-personality') get their FULL SKILL.md
    instructions injected. All other skills get brief registry descriptions only.

    Args:
        skill_data: List of skill entries from _collect_agent_connections.
        log_prefix: Log prefix for debug messages.

    Returns:
        Tuple of (prompt_text, has_personality). prompt_text is the string to
        append to system_message. has_personality indicates whether any
        personality skills were found (used to drop the default system message).
    """
    # Kept as a compatibility entry point for callers and tests; the shared
    # builder is the sole progressive-disclosure implementation.
    from services.skill_prompt import build_skill_system_prompt

    return build_skill_system_prompt(skill_data, log_prefix)


@dataclass
class _AgentContextRuntime:
    """Plain conversation binding for one Context-connected agent.

    The key ``(workflow_id, generation, agent_node_id)`` addresses one row in
    the ``agent_conversations`` store; ``history`` is that row's transcript
    replayed as native messages, and ``save`` writes the loop's live message
    list back per turn (called via the agent loop's ``conversation_saver``,
    which already treats failures as best-effort).
    """

    workflow_id: str
    generation: int
    agent_node_id: str
    database: Any
    history: List[NativeMessage]

    async def save(self, messages: List[NativeMessage]) -> None:
        from services.agent_context import save_conversation
        from services.llm.protocol import message_to_wire

        await save_conversation(
            self.database,
            workflow_id=self.workflow_id,
            generation=self.generation,
            agent_node_id=self.agent_node_id,
            messages=[dict(message_to_wire(message)) for message in messages],
        )


class AIService:
    """AI model service backed by the native provider SDK layer."""

    def __init__(
        self,
        auth_service: AuthService,
        database,
        cache,
        settings: Settings,
        chat_unifier=None,
    ):
        self.auth = auth_service
        self.database = database
        self.cache = cache
        self.settings = settings
        # ``ChatUnifier`` is the single facade for chat-model dispatch +
        # typed-exception translation + JSON-driven incompatible_models
        # filter. Injected by the DI container; the legacy ``None``
        # default exists only so tests and ad-hoc constructions without
        # the DI container can still instantiate ``AIService`` (the chat
        # paths will fall back to direct factory calls).
        self.chat_unifier = chat_unifier
        # RLM service (lazy import to avoid circular deps)
        from services.rlm import RLMService

        self.rlm_service = RLMService(auth=self.auth)

    def detect_provider(self, model: str) -> str:
        """Detect AI provider from model name."""
        return detect_provider_from_model(model)

    async def _prepare_context(
        self,
        *,
        node_id: str,
        context_data: Optional[Dict[str, Any]],
        execution_context: Optional[Dict[str, Any]],
        workflow_id: Optional[str],
        database: Any,
    ) -> Optional["_AgentContextRuntime"]:
        """Load the agent's stored conversation for this generation.

        Returns ``None`` when no Context node is connected or the run carries
        no admitted generation (manual canvas runs persist nothing by
        design). A failing load raises instead of silently starting an
        amnesiac run that burns tokens on an empty prompt.
        """

        if not context_data or context_data.get("kind") != "context":
            return None

        from services.agent_context import load_conversation
        from services.llm.protocol import message_from_wire

        raw_context = execution_context or {}
        admitted_workflow_id = str(
            workflow_id or raw_context.get("workflow_id") or ""
        )
        descriptor_workflow_id = str(context_data.get("workflow_id") or "")
        if (
            admitted_workflow_id
            and descriptor_workflow_id
            and admitted_workflow_id != descriptor_workflow_id
        ):
            raise ValueError("Context workflow scope mismatch")
        resolved_workflow_id = admitted_workflow_id or descriptor_workflow_id
        if not resolved_workflow_id:
            return None
        admitted_generation = int(
            raw_context.get("generation")
            or raw_context.get("workflow_generation")
            or 0
        )
        descriptor_generation = int(context_data.get("generation") or 0)
        if (
            admitted_generation
            and descriptor_generation
            and admitted_generation != descriptor_generation
        ):
            raise ValueError("Context generation scope mismatch")
        generation = admitted_generation or descriptor_generation
        if generation <= 0:
            return None

        try:
            wires = await load_conversation(
                database,
                workflow_id=resolved_workflow_id,
                generation=generation,
                agent_node_id=node_id,
            )
        except Exception as exc:
            raise ValueError(
                "Conversation load failed for workflow "
                f"{resolved_workflow_id} generation {generation} agent "
                f"{node_id}: {exc}"
            ) from exc
        history = [
            message_from_wire(wire)
            for wire in wires
            if isinstance(wire, dict)
        ]
        return _AgentContextRuntime(
            workflow_id=resolved_workflow_id,
            generation=generation,
            agent_node_id=node_id,
            database=database,
            history=history,
        )

    def _extract_text_content(self, content, ai_response=None) -> str:
        """Extract text content from various response formats.

        Handles:
        - String content (OpenAI, Anthropic)
        - List of content blocks (Gemini 3+ models)
        - Empty/None content with error details from metadata

        Args:
            content: The raw content from response (str, list, or None)
            ai_response: The full AIMessage for metadata inspection

        Returns:
            Extracted text string

        Raises:
            ValueError: If content is empty with details about why
        """
        # Handle list content (Gemini format: [{"type": "text", "text": "..."}])
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text" and block.get("text"):
                        text_parts.append(block["text"])
                    elif "text" in block:
                        text_parts.append(str(block["text"]))
                elif isinstance(block, str):
                    text_parts.append(block)
            extracted = "\n".join(text_parts)
            if extracted.strip():
                return extracted
            # List was present but no text extracted
            logger.warning(f"[Agent] Content was list but no text extracted: {content}")

        # Handle string content
        if isinstance(content, str) and content.strip():
            return content

        # Use the content_blocks property for typed content extraction
        if ai_response and hasattr(ai_response, "content_blocks") and ai_response.content_blocks:
            text_parts = []
            for block in ai_response.content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            extracted = "\n".join(filter(None, text_parts))
            if extracted.strip():
                return extracted

        # Content is empty - try to get error details from metadata
        error_details = []
        if ai_response and hasattr(ai_response, "response_metadata"):
            meta = ai_response.response_metadata
            finish_reason = meta.get("finish_reason", "")

            if finish_reason == "SAFETY":
                error_details.append("Content blocked by safety filters")
                # Try to get specific blocked categories
                safety_ratings = meta.get("safety_ratings", [])
                blocked = [r.get("category") for r in safety_ratings if r.get("blocked")]
                if blocked:
                    error_details.append(f"Blocked categories: {', '.join(blocked)}")

            elif finish_reason in ("MAX_TOKENS", "length"):
                # Check if reasoning consumed all tokens (OpenAI o-series models)
                # OpenAI path: token_usage.completion_tokens_details.reasoning_tokens
                token_usage = meta.get("token_usage", {})
                completion_details = token_usage.get("completion_tokens_details", {})
                reasoning_tokens = completion_details.get("reasoning_tokens", 0)
                completion_tokens = token_usage.get("completion_tokens", 0)

                # Also check Gemini path: output_token_details
                if not reasoning_tokens:
                    token_details = meta.get("output_token_details", {})
                    reasoning_tokens = token_details.get("reasoning", 0)

                if reasoning_tokens > 0 and reasoning_tokens >= completion_tokens:
                    error_details.append(
                        f"Model used all {reasoning_tokens} tokens for reasoning, none left for response. Increase max_tokens (current response used {completion_tokens} total)."
                    )
                else:
                    error_details.append("Response truncated due to max_tokens limit")

            elif finish_reason == "MALFORMED_FUNCTION_CALL":
                error_details.append("Model returned malformed function call. Tool schema may be incompatible.")

            if meta.get("block_reason"):
                error_details.append(f"Block reason: {meta.get('block_reason')}")

        if error_details:
            raise ValueError(f"AI returned empty response. {'; '.join(error_details)}")

        # Generic empty response - log full response details for debugging
        if ai_response:
            logger.warning(f"[Agent] Empty response debug - content_blocks: {getattr(ai_response, 'content_blocks', None)}")
            logger.warning(f"[Agent] Empty response debug - additional_kwargs: {getattr(ai_response, 'additional_kwargs', {})}")
            if hasattr(ai_response, "response_metadata"):
                meta = ai_response.response_metadata
                logger.warning(f"[Agent] Empty response debug - finish_reason: {meta.get('finish_reason')}")
                logger.warning(f"[Agent] Empty response debug - token_usage: {meta.get('token_usage')}")
        logger.warning(f"[Agent] Empty response with no error details. Content type: {type(content)}, value: {repr(content)}")
        raise ValueError("AI generated empty response. Try rephrasing your prompt or using a different model.")

    async def _track_token_usage(
        self,
        session_id: str,
        node_id: str,
        provider: str,
        model: str,
        ai_response,
        all_messages: list,
        broadcaster=None,
        workflow_id: Optional[str] = None,
        memory_content: Optional[str] = None,
        api_key: Optional[str] = None,
        memory_node_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Track token usage and trigger compaction if threshold exceeded.

        Extracts usage_metadata from the assistant message and tracks it
        in the compaction service for session token monitoring.

        Args:
            session_id: Memory session ID
            node_id: Agent node ID
            provider: AI provider name
            model: Model name
            ai_response: The AIMessage response
            all_messages: All messages in conversation (for aggregation)
            broadcaster: Optional status broadcaster
            workflow_id: Optional workflow ID for scoped broadcasts
            memory_content: Current memory content (for compaction)
            api_key: API key (for compaction summarization)
            memory_node_id: Memory node ID (for parameter updates)

        Returns:
            Compaction result if triggered, None otherwise
        """
        from services.compaction import get_compaction_service

        svc = get_compaction_service()
        if not svc:
            return

        # Native responses expose a normalized ``Usage`` object. The
        # attribute probes below stay because callers also hand this method
        # raw provider payloads, which carry usage under either name.
        usage = None
        if isinstance(ai_response, Usage):
            usage = ai_response
        elif isinstance(ai_response, LLMResponse):
            usage = ai_response.usage
        elif hasattr(ai_response, "usage_metadata") and ai_response.usage_metadata:
            usage = ai_response.usage_metadata
        elif hasattr(ai_response, "response_metadata"):
            # Fallback: check response_metadata for usage info
            meta = ai_response.response_metadata
            if "usage" in meta:
                usage = meta["usage"]
            elif "token_usage" in meta:
                usage = meta["token_usage"]

        if not usage and all_messages:
            # Aggregate usage from all AI messages if single message has no usage
            total_input = 0
            total_output = 0
            for msg in all_messages:
                if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                    total_input += msg.usage_metadata.get("input_tokens", 0)
                    total_output += msg.usage_metadata.get("output_tokens", 0)
            if total_input > 0 or total_output > 0:
                usage = {"input_tokens": total_input, "output_tokens": total_output, "total_tokens": total_input + total_output}

        if not usage:
            logger.debug(f"[TokenTracking] No usage_metadata available for {provider}/{model}")
            return

        # Normalize usage dict (handle both dict and TypedDict formats)
        usage_dict = {
            "input_tokens": usage.get("input_tokens", 0) if isinstance(usage, dict) else getattr(usage, "input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0) if isinstance(usage, dict) else getattr(usage, "output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0) if isinstance(usage, dict) else getattr(usage, "total_tokens", 0),
            "cache_creation_tokens": usage.get("cache_creation_tokens", 0) if isinstance(usage, dict) else getattr(usage, "cache_creation_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_tokens", 0) if isinstance(usage, dict) else getattr(usage, "cache_read_tokens", 0),
            "reasoning_tokens": usage.get("reasoning_tokens", 0) if isinstance(usage, dict) else getattr(usage, "reasoning_tokens", 0),
        }

        try:
            tracking = await svc.track(session_id=session_id, node_id=node_id, provider=provider, model=model, usage=usage_dict)
            logger.debug(f"[TokenTracking] Tracked {usage_dict['total_tokens']} tokens for session {session_id}, total={tracking['total']}")

            # Broadcast token update
            if broadcaster:
                await broadcaster.broadcast(
                    {"type": "token_usage_update", "session_id": session_id, "workflow_id": workflow_id, "data": tracking}
                )

            # Trigger compaction if threshold exceeded
            if tracking.get("needs_compaction") and memory_content and api_key:
                logger.info(f"[Compaction] Threshold exceeded for session {session_id}, triggering compaction")

                if broadcaster:
                    await broadcaster.broadcast({"type": "compaction_starting", "session_id": session_id, "node_id": node_id})

                result = await svc.compact_context(
                    session_id=session_id, node_id=node_id, memory_content=memory_content, provider=provider, api_key=api_key, model=model
                )

                if broadcaster:
                    await broadcaster.broadcast(
                        {
                            "type": "compaction_completed",
                            "session_id": session_id,
                            "success": result.get("success", False),
                            "tokens_before": result.get("tokens_before", 0),
                            "tokens_after": result.get("tokens_after", 0),
                            "error": result.get("error"),
                        }
                    )

                return result

        except Exception as e:
            logger.warning(f"[TokenTracking] Failed to track tokens: {e}")

        return None


    def _get_curated_models(self, provider: str) -> List[str]:
        """Get curated model list from llm_defaults.json for a provider.

        Delegates to ``services.llm.config.curated_models`` — the same
        list backs ``OpenAIProvider.fetch_models`` for providers that
        declare no model-list route, and the two must not drift.
        """
        from services.llm.config import curated_models

        return curated_models(provider)

    async def fetch_models(self, provider: str, api_key: str) -> List[str]:
        """Fetch available models from a provider via ``ChatUnifier``.

        The unifier handles ``{provider}_proxy`` resolution, typed-SDK
        exception translation, and the JSON-driven ``incompatible_models``
        filter. All 12 providers (4 dedicated + 8 OpenAI-compat
        including groq + cerebras) route through here — Phase D removed
        the legacy per-provider fallback path.

        On API failure, falls back to the curated list from
        ``llm_defaults.json``. When the provider has no ``default_model``
        declared (intentional for local servers like ollama / lmstudio),
        returns an empty list so the frontend dropdown shows a real
        "no models" empty state instead of a placeholder name.
        """
        if self.chat_unifier is None:
            raise NodeUserError(
                "ChatUnifier is not injected. AIService must be constructed via "
                "the DI container (core.container.Container)."
            )
        try:
            return await self.chat_unifier.fetch_models(provider=provider, api_key=api_key)
        except NodeUserError:
            raise
        except Exception as e:
            logger.warning(f"[AI] Failed to fetch models from {provider} API: {e}")

        # JSON-curated fallback
        curated = self._get_curated_models(provider)
        if curated:
            return curated
        provider_cfg = _LLM_DEFAULTS.get("providers", {}).get(provider, {})
        default_model = provider_cfg.get("default_model", "")
        return [default_model] if default_model else []

    async def execute_chat(self, node_id: str, node_type: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute AI chat model."""
        start_time = time.time()

        try:
            # Flatten options collection from frontend
            options = parameters.get("options", {})
            flattened = {**parameters, **options}

            # Extract the schema-canonical chat-model parameters.
            api_key = flattened.get("api_key")
            model = flattened.get("model", "gpt-3.5-turbo")
            # Strip [FREE] prefix if present (added by OpenRouter model list for display)
            if model.startswith("[FREE] "):
                model = model[7:]
            prompt = flattened.get("prompt", "Hello")

            # Schema-canonical name on chat-model `_base.py` is
            # `system_prompt`; agent paths use `system_message` and are
            # handled separately in execute_agent / execute_chat_agent.
            system_prompt = flattened.get("system_prompt", "")

            # Determine provider from node_type (more reliable than model name detection)
            from constants import detect_ai_provider

            provider = detect_ai_provider(node_type, flattened)

            if not api_key:
                _require_saved_endpoint(provider)
                raise ValueError("API key is required")

            # Validate prompt is not empty (prevents wasted API calls for all providers)
            if not is_valid_message_content(prompt):
                raise ValueError("Prompt cannot be empty")

            # Build thinking config from parameters
            thinking_config = None
            if flattened.get("thinking_enabled"):
                thinking_config = NativeThinkingConfig(
                    enabled=True,
                    budget=int(flattened.get("thinking_budget", 2048)),
                    effort=flattened.get("reasoning_effort", "medium"),
                    # No fabricated default — only forward thinking_level
                    # when the node actually configured it (Vertex rejects
                    # an unsolicited thinking_level on 2.5-era models).
                    level=flattened.get("thinking_level"),
                    format=flattened.get("reasoning_format", "parsed"),
                )

            # --- Unifier path (every provider) ---
            #
            # ``ChatUnifier`` owns proxy_url resolution + provider
            # instantiation + typed-SDK exception translation (raises
            # ``NodeUserError`` for both unknown providers and typed SDK
            # errors so ``BaseNode.execute()`` logs one WARN line with
            # no traceback). No per-provider Python lives here.
            #
            # groq + cerebras register through the OpenAI-compat path in
            # ``services.llm.providers._compat``.
            if self.chat_unifier is None:
                raise NodeUserError(
                    "ChatUnifier is not injected. AIService must be constructed via "
                    "the DI container (core.container.Container)."
                )

            max_tokens = native_resolve_max_tokens(flattened, model, provider)
            temperature = native_resolve_temperature(
                flattened,
                model,
                provider,
                bool(thinking_config and thinking_config.enabled),
            )

            native_msgs: List[NativeMessage] = []
            if system_prompt and is_valid_message_content(system_prompt):
                native_msgs.append(NativeMessage(role="system", content=system_prompt))
            native_msgs.append(NativeMessage(role="user", content=prompt))

            llm_resp: LLMResponse = await self.chat_unifier.chat(
                provider=provider,
                api_key=api_key,
                messages=native_msgs,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking_config,
            )

            response_text = llm_resp.content
            thinking_content = llm_resp.thinking
            finish_reason = llm_resp.finish_reason

            result = {
                "response": response_text,
                "thinking": thinking_content,
                "thinking_enabled": thinking_config.enabled if thinking_config else False,
                "model": model,
                "provider": provider,
                "finish_reason": finish_reason,
                "timestamp": datetime.now().isoformat(),
                "input": {
                    "prompt": prompt,
                    "system_prompt": system_prompt,
                },
            }

            log_execution_time(logger, "ai_chat", start_time, time.time())
            log_api_call(logger, provider, model, "chat", True)

            return {
                "success": True,
                "node_id": node_id,
                "node_type": node_type,
                "result": result,
                "execution_time": time.time() - start_time,
            }

        except NodeUserError:
            # Re-raise without wrapping. ``ChatUnifier`` already
            # translated the typed SDK exception (openai / anthropic /
            # google APIError, …) into ``NodeUserError`` at the single
            # delegation site. ``BaseNode.execute()`` catches this and
            # logs one WARN line with no traceback. Every chat-path
            # call goes through the unifier post-Phase-A3, so the
            # previous per-provider catch blocks are gone.
            raise

        except Exception as e:
            logger.error("AI execution failed", node_id=node_id, error=str(e))
            log_api_call(
                logger,
                provider if "provider" in locals() else "unknown",
                model if "model" in locals() else "unknown",
                "chat",
                False,
                error=str(e),
            )

            return {
                "success": False,
                "node_id": node_id,
                "node_type": node_type,
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    async def execute_agent(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        memory_data: Optional[Dict[str, Any]] = None,
        skill_data: Optional[List[Dict[str, Any]]] = None,
        tool_data: Optional[List[Dict[str, Any]]] = None,
        broadcaster=None,
        workflow_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        database=None,
    ) -> Dict[str, Any]:
        """Execute AI Agent via the shared native SDK loop.

        Each iteration invokes ``ChatUnifier``, appends the exact replayable
        assistant message, dispatches tool calls, appends native tool-result
        messages, and continues until a final response or the configured
        recursion limit.

        Features:
        - Provider-neutral tool calling through ``AgentToolSpec``
        - Real-time status broadcasts for UI animations

        Args:
            node_id: The node identifier
            parameters: Node parameters including prompt, model, etc.
            memory_data: Optional memory data from connected simpleMemory node
                        containing session_id, window_size for conversation history
            skill_data: Optional skill configurations from connected skill nodes
            tool_data: Optional list of tool configurations from connected tool nodes
            broadcaster: Optional StatusBroadcaster for real-time UI updates
            workflow_id: Optional workflow ID for scoped status broadcasts
            context: Optional execution context with nodes, edges for nested agent delegation
        """
        start_time = time.time()
        provider = "unknown"
        model = "unknown"

        # EARLY LOG: Entry point for debugging
        logger.debug(
            f"[AIAgent] execute_agent called: node_id={node_id}, workflow_id={workflow_id}, skill_count={len(skill_data) if skill_data else 0}, tool_count={len(tool_data) if tool_data else 0}"
        )
        if skill_data:
            for i, sd in enumerate(skill_data):
                logger.debug(f"[AIAgent] Skill {i}: type={sd.get('node_type')}, label={sd.get('label')}")
        if tool_data:
            for i, td in enumerate(tool_data):
                logger.debug(f"[AIAgent] Tool {i}: type={td.get('node_type')}, node_id={td.get('node_id')}")

        # Helper to broadcast status updates with workflow_id for proper scoping
        async def broadcast_status(phase: str, details: Dict[str, Any] = None):
            if broadcaster:
                await broadcaster.update_node_status(
                    node_id, "executing", {"phase": phase, "agent_type": "loop", **(details or {})}, workflow_id=workflow_id
                )

        try:
            # Extract top-level parameters (always visible in UI)
            prompt = parameters.get("prompt", "Hello")
            system_message = parameters.get("system_message", "You are a helpful assistant")

            # Inject skills: personality skills get full instructions, others get brief descriptions
            skill_prompt, has_personality = _build_skill_system_prompt(skill_data, log_prefix="[AIAgent]")
            if skill_prompt:
                if has_personality:
                    system_message = skill_prompt
                else:
                    system_message = f"{system_message}\n\n{skill_prompt}"

            # Flatten options collection from frontend
            options = parameters.get("options", {})
            flattened = {**parameters, **options}

            api_key = flattened.get("api_key")
            provider = parameters.get("provider", "openai")
            model = parameters.get("model", "")
            # Strip [FREE] prefix if present (added by OpenRouter model list for display)
            if model.startswith("[FREE] "):
                model = model[7:]

            logger.debug(f"[Agent] Agent: {provider}/{model}, tools={len(tool_data) if tool_data else 0}")

            # If no model specified or model doesn't match provider, use default (DB > config)
            if not model or not is_model_valid_for_provider(model, provider):
                old_model = model
                model = await get_default_model_async(provider, database)
                if old_model:
                    logger.warning(f"Model '{old_model}' invalid for provider '{provider}', using default: {model}")
                else:
                    logger.info(f"No model specified, using default: {model}")

            if not api_key:
                _require_saved_endpoint(provider)
                raise ValueError("API key is required for AI Agent")

            # Resolve max_tokens and temperature via model registry
            max_tokens = _resolve_max_tokens(flattened, model, provider)

            # Build thinking config from parameters
            thinking_config = None
            if flattened.get("thinking_enabled"):
                thinking_config = ThinkingConfig(
                    enabled=True,
                    budget=int(flattened.get("thinking_budget", 2048)),
                    effort=flattened.get("reasoning_effort", "medium"),
                    level=flattened.get("thinking_level"),
                    format=flattened.get("reasoning_format", "parsed"),
                )
                logger.debug(f"[Agent] Thinking enabled: budget={thinking_config.budget}, effort={thinking_config.effort}")

            temperature = _resolve_temperature(flattened, model, provider, bool(thinking_config and thinking_config.enabled))

            # Broadcast: Initializing model
            await broadcast_status("initializing", {"message": f"Initializing {provider} model...", "provider": provider, "model": model, "active_skills": [], "last_skills": [], "last_tool_name": None, "last_capability": None})

            context_runtime = await self._prepare_context(
                node_id=node_id,
                context_data=memory_data,
                execution_context=context,
                workflow_id=workflow_id,
                database=database or self.database,
            )
            if context_runtime is not None:
                # Simple Memory is an explicit tool when a Context node is
                # connected; legacy automatic recall/persistence remains only
                # for graphs recorded with an input-memory edge.
                memory_data = None

            # Build initial messages for state. The SystemMessage is
            # PREPENDED after tool building (below), because the
            # delegation-guidance block at the end of the tool-building
            # section grows ``system_message`` to include the
            # ``Available agents: delegate_to_*`` list and the
            # ``task``/``context`` schema description. Appending the
            # SystemMessage here would lock in the pre-update string
            # and the LLM would never see the delegation contract,
            # which is exactly what previously broke ``aiAgent``-driven
            # delegation through the ``input-tools`` handle.
            initial_messages: List[NativeMessage] = (
                [
                    message
                    for message in context_runtime.history
                    if message.role != "system"
                ]
                if context_runtime is not None
                else []
            )

            # Add memory history from connected simpleMemory node (markdown-based)
            session_id = None
            history_count = 0
            if memory_data and memory_data.get("session_id"):
                session_id = memory_data["session_id"]
                memory_content = memory_data.get("memory_content", "")

                # Broadcast: Loading memory
                await broadcast_status(
                    "loading_memory", {"message": "Loading conversation history...", "session_id": session_id, "has_memory": True}
                )

                # Parse short-term memory from markdown
                history_messages = _parse_memory_markdown(memory_content)
                history_count = len(history_messages)

                # If long-term memory enabled, retrieve relevant context
                if memory_data.get("long_term_enabled"):
                    try:
                        store = await _get_configured_memory_vector_store(
                            session_id,
                            memory_data,
                            self.auth,
                        )
                        if store:
                            k = memory_data.get("retrieval_count", 3)
                            docs = await store.similarity_search(prompt, k=k)
                            if docs:
                                retrieved_context = "\n---\n".join(
                                    d.page_content for d in docs
                                )
                                initial_messages.append(
                                    NativeMessage(
                                        role="system",
                                        content=(
                                            "Relevant past context:\n"
                                            f"{retrieved_context}"
                                        ),
                                    )
                                )
                                logger.info(f"[Agent Memory] Retrieved {len(docs)} relevant memories from long-term store")
                    except Exception as e:
                        logger.debug(f"[Agent Memory] Long-term retrieval skipped: {e}")

                # Add parsed history messages
                initial_messages.extend(history_messages)

                logger.info(f"[Agent Memory] Loaded {history_count} messages from markdown")

                # Broadcast: Memory loaded
                await broadcast_status(
                    "memory_loaded",
                    {"message": f"Loaded {history_count} messages from memory", "session_id": session_id, "history_count": history_count},
                )

            # Add current user prompt
            initial_messages.append(NativeMessage(role="user", content=prompt))

            # Build tools if provided
            tools = []
            tool_configs = {}
            tool_identities: List[Dict[str, str]] = []
            tool_bindings: List[tuple[Any, Dict[str, Any]]] = []

            # Standard skills are progressively disclosed through one scoped
            # provider-neutral tool. Personality skills were injected above.
            from services.skill_runtime import skill_tool_info

            skill_info = skill_tool_info(skill_data or [], node_id)
            effective_tool_data = unique_node_bindings(tool_data or [])
            if skill_info:
                effective_tool_data.append(skill_info)

            if effective_tool_data:
                await broadcast_status("building_tools", {"message": f"Building {len(effective_tool_data)} tool(s)...", "tool_count": len(effective_tool_data)})

                for tool_info in effective_tool_data:
                    tool, config = await self._build_tool_from_node(tool_info)
                    if tool:
                        tools.append(tool)
                        tool_bindings.append((tool, config))
                        tool_identities.append(
                            {
                                "name": tool.name,
                                "node_id": str(config.get("node_id") or tool_info.get("node_id") or ""),
                                "label": str(config.get("label") or tool_info.get("label") or tool_info.get("node_type") or "tool"),
                            }
                        )
                        logger.debug(f"[Agent] Registered tool: name={tool.name}, node_id={config.get('node_id')}")

                logger.debug(f"[Agent] Built {len(tools)} tools")

                # Auto-inject check_delegated_tasks tool when delegation tools present
                if any(tool.name.startswith("delegate_to_") for tool in tools):
                    check_info = {
                        "node_type": "_builtin_check_delegated_tasks",
                        "node_id": f"{node_id}_check_tasks",
                        "parameters": {},
                        "label": "Check Delegated Tasks",
                    }
                    check_tool, check_config = await self._build_tool_from_node(check_info)
                    if check_tool:
                        tools.append(check_tool)
                        tool_bindings.append((check_tool, check_config))
                        tool_identities.append(
                            {
                                "name": check_tool.name,
                                "node_id": str(check_config.get("node_id") or check_info["node_id"]),
                                "label": str(check_config.get("label") or check_info["label"]),
                            }
                        )
                        logger.debug("[Agent] Auto-injected check_delegated_tasks tool")

                    # Add delegation guidance to system message
                    delegate_names = [tool.name for tool in tools if tool.name.startswith("delegate_to_")]
                    system_message += (
                        "\n\n## Agent Delegation\n"
                        "When delegating to sub-agents, use 'task' for the mission directive "
                        "(role and goal) and 'context' for input data the agent needs to work with.\n"
                        f"Available agents: {', '.join(delegate_names)}"
                    )

                # Name-based dispatch is only safe after validating the full
                # surface.  Build the lookup map after this check so a later
                # duplicate can never silently replace an earlier node.
                ensure_unique_tool_names(tool_identities)
                tool_configs.update({tool.name: config for tool, config in tool_bindings})

            # Now that ``system_message`` is final (skill prompt + delegation
            # guidance both folded in), prepend it as the first message.
            # See the comment at the original ``initial_messages`` declaration
            # for why this can't happen earlier.
            if system_message:
                initial_messages.insert(
                    0,
                    NativeMessage(role="system", content=system_message),
                )

            # Create tool executor callback. Tool-node status lifecycle
            # (executing/success/error) is owned by ``handlers.tools.execute_tool``
            # — this closure only emits the *parent agent's* phase
            # broadcasts (``executing_tool`` / ``tool_completed``).
            async def tool_executor(tool_name: str, tool_args: Dict) -> Any:
                """Execute a tool by name."""
                from services.handlers.tools import execute_tool

                config = tool_configs.get(tool_name, {})
                tool_node_id = config.get("node_id")

                logger.debug(f"[Agent] Executing tool: {tool_name} (args={tool_args})")
                logger.debug(f"[Agent] Tool node_id={tool_node_id}, workflow_id={workflow_id}")

                # Parent-agent phase broadcast (does not touch tool node).
                await broadcast_status(
                    "executing_tool", {"message": f"Executing tool: {tool_name}", "tool_name": tool_name}
                )

                if broadcaster and tool_name and tool_name != "Skill":
                    await broadcaster.broadcast_agent_capability(
                        node_id,
                        capability_kind="tool",
                        capability_name=tool_name,
                        state="started",
                        workflow_id=workflow_id,
                        execution_id=str((context or {}).get("execution_id") or "") or None,
                        root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                        target_node_id=str(tool_node_id or "") or None,
                        provider=provider,
                        invocation_source="native",
                    )

                # Inject services + graph context so execute_tool can scope its
                # broadcasts and nested agents can execute with their own tools.
                config["workflow_id"] = workflow_id
                config["ai_service"] = self
                config["database"] = self.database
                config["parent_node_id"] = node_id
                config["provider"] = provider
                # Surface the auto-rebind toggle so agentBuilder's summary
                # text reflects the user's current preference.
                config["auto_rebind_tools"] = auto_rebind_enabled
                if context:
                    config["run_scope"] = context.get("run_scope")
                    config["generation"] = context.get("generation")
                    config["parameter_snapshot"] = context.get("parameter_snapshot", {})
                    config["employee_job_id"] = context.get("employee_job_id")
                    config["nodes"] = context.get("nodes", [])
                    config["edges"] = context.get("edges", [])
                    config["workspace_dir"] = context.get("workspace_dir", "")
                    config["user_id"] = context.get("user_id", "owner")
                    # Stable per-run id so session-keyed tools (browser)
                    # reuse one instance across the agent loop.
                    config["execution_id"] = context.get("execution_id")
                    config["root_execution_id"] = context.get("root_execution_id")
                    config["delegation_depth"] = context.get("delegation_depth", 0)
                    config["team_id"] = context.get("team_id")
                    config["max_concurrent_subagents"] = context.get("max_concurrent_subagents", 3)
                    config["max_delegation_depth"] = context.get("max_delegation_depth", 2)

                try:
                    from services.employees.permissions import assert_runtime_access
                    await assert_runtime_access(self.database, workflow_id, str(tool_node_id or ""), str(config.get("node_type") or ""), config)
                    result = await execute_tool(tool_name, tool_args, config)

                    await broadcast_status(
                        "tool_completed",
                        {"message": f"Tool completed: {tool_name}", "tool_name": tool_name},
                    )

                    if broadcaster and tool_name and tool_name != "Skill":
                        await broadcaster.broadcast_agent_capability(
                            node_id,
                            capability_kind="tool",
                            capability_name=tool_name,
                            state="completed",
                            workflow_id=workflow_id,
                            execution_id=str((context or {}).get("execution_id") or "") or None,
                            root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                            target_node_id=str(tool_node_id or "") or None,
                            provider=provider,
                            invocation_source="native",
                        )

                    return result

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"[Agent] Tool execution failed: {tool_name}", error=error_msg)

                    # Keep normal AI agents on the same observable contract as
                    # specialized/team-lead agents.  Without this terminal
                    # phase a failed fast tool call leaves only the preceding
                    # generic executing phase, so the canvas cannot retain
                    # ``tool <name>`` or mark the capability as failed.
                    await broadcast_status(
                        "tool_completed",
                        {
                            "message": f"Tool failed: {tool_name}",
                            "tool_name": tool_name,
                            "tool_failed": True,
                        },
                    )
                    if broadcaster and tool_name and tool_name != "Skill":
                        await broadcaster.broadcast_agent_capability(
                            node_id,
                            capability_kind="tool",
                            capability_name=tool_name,
                            state="failed",
                            workflow_id=workflow_id,
                            execution_id=str((context or {}).get("execution_id") or "") or None,
                            root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                            target_node_id=str(tool_node_id or "") or None,
                            provider=provider,
                            invocation_source="native",
                            error_code=type(e).__name__,
                        )

                    # Re-raise so the native loop can surface the error to the LLM.
                    raise

            # Auto-rebind toggle: when the user enables "Auto-Rebind Tools
            # After Canvas Changes" in Settings, canvas-mutating tools
            # (agentBuilder) extend the LLM's bound surface mid-loop. The
            # default is True; lookup failures fall back to True so the
            # feature doesn't silently disable itself on a transient DB hiccup.
            auto_rebind_enabled = True
            user_recursion_limit: Optional[int] = None
            tool_output_limit = resolve_tool_output_limit(None, self.settings)
            try:
                user_settings = await self.database.get_user_settings()
                if user_settings is not None:
                    auto_rebind_enabled = bool(
                        user_settings.get("auto_rebind_tools_after_canvas_change", True)
                    )
                    _raw_limit = user_settings.get("agent_recursion_limit")
                    if isinstance(_raw_limit, int) and _raw_limit > 0:
                        user_recursion_limit = _raw_limit
                    tool_output_limit = resolve_tool_output_limit(
                        user_settings, self.settings
                    )
            except Exception as exc:  # noqa: BLE001 — defensive read
                logger.debug("[Agent] user_settings read failed: %s", exc)

            async def _rebind_from_operations(operations: List[Dict[str, Any]]) -> List[Any]:
                """Translate workflow_ops add_node ops (component_kind='tool')
                into fresh :class:`AgentToolSpec` values via the canonical
                :meth:`_build_tool_from_node` helper. The returned list is
                appended to the native loop's current tool surface so the LLM
                can invoke the new tool in
                its NEXT iteration without a Run-stop-Run cycle.

                Tool configs are folded into the existing ``tool_configs``
                closure so ``tool_executor`` can route the LLM's eventual
                tool_call to the right node. A node already bound (Agent
                Builder hands back a saved tool this run may hold) is
                skipped rather than bound twice under one name.
                """
                from services.node_registry import get_node_class

                saved_workflow = await self.database.get_workflow(workflow_id) if workflow_id else None
                saved_graph = getattr(saved_workflow, "data", None) or {}
                if context is not None and saved_graph:
                    admitted_graph = extend_runtime_graph(context, saved_graph, operations)
                    context["nodes"] = admitted_graph["nodes"]
                    context["edges"] = admitted_graph["edges"]
                bound = {identity["node_id"] for identity in tool_identities}
                new_bindings: List[tuple[Any, Dict[str, Any]]] = []
                for op in operations:
                    if op.get("type") != "add_node":
                        continue
                    if not rebind_allowed(op, node_id, nodes=(context or {}).get("nodes", []), edges=(context or {}).get("edges", []), operations=() if saved_graph else operations):
                        continue
                    node_type = op.get("node_type")
                    if not node_type:
                        continue
                    cls = get_node_class(node_type)
                    if cls is None:
                        continue
                    # Match the catalogue filter — pure ToolNode OR
                    # dual-purpose ActionNode (usable_as_tool=True),
                    # excluding chat models. Without this the rebind
                    # silently drops twitterSearch / googleGmail /
                    # pythonExecutor etc. and the LLM tries to call
                    # tools it never got bound to. A Skills node
                    # (masterSkill) is tool-kind but feeds input-skill;
                    # it is never an LLM tool.
                    _kind = getattr(cls, "component_kind", "")
                    if not is_runtime_tool(cls):
                        continue
                    if (getattr(cls, "ui_hints", None) or {}).get("isMasterSkillEditor"):
                        continue
                    tool_info = {
                        "node_id": op.get("minted_id") or op.get("client_ref") or f"new_{node_type}",
                        "node_type": node_type,
                        "parameters": op.get("parameters") or {},
                        "label": op.get("label") or node_type,
                    }
                    if tool_info["node_id"] in bound:
                        continue
                    try:
                        tool, tool_config = await self._build_tool_from_node(tool_info)
                    except Exception as exc:  # noqa: BLE001 — log + skip one tool
                        logger.warning(
                            "[Agent] rebind: _build_tool_from_node raised for %s: %s",
                            node_type,
                            exc,
                        )
                        continue
                    if tool is None:
                        continue
                    new_bindings.append((tool, tool_config or tool_info))
                    bound.add(tool_info["node_id"])

                new_identities = [
                    {
                        "name": tool.name,
                        "node_id": str(config.get("node_id") or ""),
                        "label": str(config.get("label") or config.get("node_type") or "tool"),
                    }
                    for tool, config in new_bindings
                ]
                ensure_unique_tool_names([*tool_identities, *new_identities])
                for tool, tool_config in new_bindings:
                    tool_configs[tool.name] = tool_config
                tool_identities.extend(new_identities)
                return [tool for tool, _ in new_bindings]

            # Broadcast: Building agent
            await broadcast_status(
                "building_graph",
                {
                    "message": "Building agent...",
                    "message_count": len(initial_messages),
                    "has_memory": bool(session_id),
                    "history_count": history_count,
                    "tool_count": len(tools),
                },
            )

            # Broadcast: Invoking LLM
            await broadcast_status(
                "invoking_llm",
                {
                    "message": f"Invoking {provider} LLM...",
                    "provider": provider,
                    "model": model,
                    "iteration": 1,
                    "has_memory": bool(session_id),
                    "history_count": history_count,
                },
            )

            # Run the agent loop. ``recursion_limit`` precedence:
            #   1. ``UserSettings.agent_recursion_limit`` (per-user)
            #   2. ``Settings.agent_recursion_limit`` env var (global default)
            #   3. ``llm_defaults.json:agent.recursion_limit`` (legacy fallback)
            # The real termination signal is the LLM returning a final
            # response without tool_calls; this cap is the safety backstop.
            # On hit, the loop appends a synthetic terminal AIMessage so
            # downstream extraction returns a usable partial response.
            from services.model_registry import get_model_registry

            if user_recursion_limit is not None:
                recursion_limit = user_recursion_limit
            else:
                recursion_limit = int(get_model_registry().get_agent_defaults()["recursion_limit"])

            async def _emit_progress(iter_count: int) -> None:
                if broadcaster:
                    await broadcaster.broadcast_agent_progress(
                        node_id,
                        workflow_id=workflow_id,
                        iteration=iter_count,
                        max_iterations=recursion_limit,
                    )

            final_state = await run_native_agent_loop(
                self.chat_unifier,
                provider=provider,
                api_key=api_key,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking_config,
                initial_messages=initial_messages,
                tools=tools if tools else None,
                tool_executor=tool_executor if tools else None,
                max_iterations=recursion_limit,
                progress_callback=_emit_progress if broadcaster else None,
                rebind_from_operations=_rebind_from_operations if auto_rebind_enabled else None,
                roster_after_rebind=lambda: delegation_roster(tool_identities),
                conversation_saver=(
                    context_runtime.save
                    if context_runtime is not None
                    else None
                ),
                tool_output_limit=tool_output_limit,
            )

            # Extract the AI response (last message in the accumulated messages)
            all_messages = final_state["messages"]
            ai_response = all_messages[-1] if all_messages else None

            if not ai_response or not hasattr(ai_response, "content"):
                raise ValueError("No response generated from agent")

            # Handle different content formats (Gemini can return list of content blocks)
            response_content = self._extract_text_content(ai_response.content, ai_response)
            iterations = final_state.get("iteration", 1)

            # Get accumulated thinking content from state
            thinking_content = final_state.get("thinking_content")

            logger.info(f"[Agent] Agent completed in {iterations} iteration(s), thinking={'yes' if thinking_content else 'no'}")

            # Track token usage if memory connected (for compaction service)
            # Also triggers compaction if threshold exceeded
            compaction_result = None
            if session_id and ai_response:
                compaction_result = await self._track_token_usage(
                    session_id=session_id,
                    node_id=node_id,
                    provider=provider,
                    model=model,
                    ai_response=final_state.get("usage") or ai_response,
                    all_messages=all_messages,
                    broadcaster=broadcaster,
                    workflow_id=workflow_id,
                    memory_content=memory_data.get("memory_content", "") if memory_data else None,
                    api_key=api_key,
                    memory_node_id=memory_data.get("node_id") if memory_data else None,
                )
                _accumulate_compaction_usage(
                    final_state, compaction_result
                )

            # Save to memory if connected (markdown-based with optional vector DB)
            # Only save non-empty messages using standardized validation
            if (
                memory_data
                and memory_data.get("node_id")
                and is_valid_message_content(prompt)
                and is_valid_message_content(response_content)
            ):
                # Broadcast: Saving to memory
                await broadcast_status(
                    "saving_memory",
                    {
                        "message": "Saving to conversation memory...",
                        "session_id": session_id,
                        "has_memory": True,
                        "history_count": history_count,
                    },
                )

                from services.memory.runtime import append_memory_turns_atomic

                # Compaction may take long enough for another worker to append.
                # The atomic helper installs the summary only if the content it
                # compacted is still current; otherwise it preserves the newer
                # transcript and appends this exchange to it.
                compacted_summary = None
                if compaction_result and compaction_result.get("success") and compaction_result.get("summary"):
                    compacted_summary = compaction_result["summary"]
                    logger.info("[Agent Memory] Using compacted summary as new base")

                memory_node_id = memory_data["node_id"]
                execution_id = (context or {}).get("execution_id")
                mutation_id = (
                    f"ai-memory:{execution_id}:{node_id}:{memory_node_id}"
                    if execution_id
                    else None
                )
                _current_params, removed_texts, _applied = await append_memory_turns_atomic(
                    self.database,
                    memory_node_id,
                    [("human", prompt), ("ai", response_content)],
                    window_size=int(memory_data.get("window_size", 10)),
                    mutation_id=mutation_id,
                    replacement_content=compacted_summary,
                    expected_content=memory_data.get("memory_content", ""),
                )

                # Store removed messages in long-term vector DB
                if removed_texts and memory_data.get("long_term_enabled"):
                    try:
                        store = await _get_configured_memory_vector_store(
                            session_id,
                            memory_data,
                            self.auth,
                        )
                        if store:
                            await store.add_texts(removed_texts)
                            logger.info(f"[Agent Memory] Archived {len(removed_texts)} messages to long-term store")
                    except Exception as e:
                        logger.warning(f"[Agent Memory] Failed to archive to vector store: {e}")

                logger.info(f"[Agent Memory] Saved markdown to memory node '{memory_node_id}'")

            result = {
                "response": response_content,
                "thinking": thinking_content,
                "thinking_enabled": thinking_config.enabled if thinking_config else False,
                "model": model,
                "provider": provider,
                "agent_type": "loop",
                "iterations": iterations,
                "finish_reason": "stop",
                "timestamp": datetime.now().isoformat(),
                "input": {
                    "prompt": prompt,
                    "system_message": system_message,
                },
            }

            # Add memory info if used
            if session_id:
                result["memory"] = {"session_id": session_id, "history_loaded": history_count}
            if context_runtime is not None:
                result["context"] = {
                    "workflow_id": context_runtime.workflow_id,
                    "generation": context_runtime.generation,
                    "agent_node_id": context_runtime.agent_node_id,
                }

            log_execution_time(logger, "ai_agent_loop", start_time, time.time())
            log_api_call(logger, provider, model, "agent", True)

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "aiAgent",
                "result": result,
                "execution_time": time.time() - start_time,
            }

        except DuplicateToolNameError as e:
            logger.warning("[Agent] Duplicate tool surface rejected: %s", e)
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "aiAgent",
                **e.as_dict(),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except NodeUserError as e:
            log_api_call(logger, provider, model, "agent", False, error=str(e))
            raise

        except Exception as e:
            logger.error("[Agent] AI agent execution failed", node_id=node_id, error=str(e))
            log_api_call(logger, provider, model, "agent", False, error=str(e))

            return {
                "success": False,
                "node_id": node_id,
                "node_type": "aiAgent",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        finally:
            from services.skill_runtime import clear_skill_turn

            await clear_skill_turn(workflow_id or "", str((context or {}).get("execution_id") or ""), node_id)

    async def execute_chat_agent(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        memory_data: Optional[Dict[str, Any]] = None,
        skill_data: Optional[List[Dict[str, Any]]] = None,
        tool_data: Optional[List[Dict[str, Any]]] = None,
        broadcaster=None,
        workflow_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        database=None,
    ) -> Dict[str, Any]:
        """Execute Chat Agent - conversational AI with memory, skills, and tool calling.

        Chat Agent supports:
        - Memory (input-memory): Markdown-based conversation history (same as AI Agent)
        - Skills (input-skill): Provide context/instructions via SKILL.md
        - Tools (input-tools): Tool nodes (httpRequest, etc.) for agent tool calling

        Args:
            node_id: The node identifier
            parameters: Node parameters including prompt, model, etc.
            memory_data: Optional memory data from connected SimpleMemory node (markdown-based)
            skill_data: Optional skill configurations from connected skill nodes
            tool_data: Optional tool configurations from connected tool nodes (httpRequest, etc.)
            broadcaster: Optional StatusBroadcaster for real-time UI updates
            workflow_id: Optional workflow ID for scoped status broadcasts
            context: Optional execution context with nodes, edges for nested agent delegation
        """
        start_time = time.time()
        provider = "unknown"
        model = "unknown"

        logger.debug(
            f"[ChatAgent] execute_chat_agent called: node_id={node_id}, workflow_id={workflow_id}, skill_count={len(skill_data) if skill_data else 0}, tool_count={len(tool_data) if tool_data else 0}"
        )

        async def broadcast_status(phase: str, details: Dict[str, Any] = None):
            if broadcaster:
                await broadcaster.update_node_status(
                    node_id,
                    "executing",
                    {"phase": phase, "agent_type": "chat_with_skills" if skill_data else "chat", **(details or {})},
                    workflow_id=workflow_id,
                )

        try:
            # Extract parameters
            prompt = parameters.get("prompt", "Hello")
            system_message = parameters.get("system_message", "You are a helpful assistant")

            # Inject skills: personality skills get full instructions, others get brief descriptions
            skill_prompt, has_personality = _build_skill_system_prompt(skill_data, log_prefix="[ChatAgent]")
            if skill_prompt:
                if has_personality:
                    system_message = skill_prompt
                else:
                    system_message = f"{system_message}\n\n{skill_prompt}"

            # Build tools from tool_data using same method as AI Agent
            # This supports all directly connected tool types.
            all_tools = []
            tool_node_configs = {}  # Map tool name to node config (same as AI Agent's tool_configs)
            tool_identities: List[Dict[str, str]] = []
            tool_bindings: List[tuple[Any, Dict[str, Any]]] = []
            from services.skill_runtime import skill_tool_info

            effective_tool_data = unique_node_bindings(tool_data or [])
            progressive_skill_tool = skill_tool_info(skill_data or [], node_id)
            if progressive_skill_tool:
                effective_tool_data.append(progressive_skill_tool)
            if effective_tool_data:
                await broadcast_status("building_tools", {"message": f"Building {len(effective_tool_data)} tool(s)...", "tool_count": len(effective_tool_data)})

                task_manager_bound = any(info.get("node_type") == "taskManager" for info in effective_tool_data)
                durable_delegates = [info for info in effective_tool_data if info.get("delegate_tool_name")]
                if task_manager_bound and durable_delegates:
                    from services.plugin.edge_walker import format_teammate_roster_line

                    teammate_lines = "\n".join(format_teammate_roster_line(info) for info in durable_delegates)
                    system_message += (
                        "\n\n## Durable Team Delegation\n"
                        "All teammate assignments MUST use task_manager with operation='assign_task'. "
                        "Supply title, bounded mission, relevant context, acceptance criteria, and one "
                        "assignee_node_id from the connected list below. Multiple assign_task calls may "
                        "be issued together for parallel queued execution. Never call delegate_to_* "
                        "directly. Review submitted tasks using list_tasks/get_task and accept, retry, "
                        "modify, reassign, or cancel them before the final report. For mutations, copy "
                        "task.id to task_id and task.revision to expected_revision.\n"
                        f"Connected teammates:\n{teammate_lines}"
                    )

                for tool_info in effective_tool_data:
                    if task_manager_bound and tool_info.get("delegate_tool_name"):
                        continue
                    # Use AI Agent's _build_tool_from_node for all tool types
                    tool, config = await self._build_tool_from_node(tool_info)
                    if tool:
                        all_tools.append(tool)
                        tool_bindings.append((tool, config))
                        tool_identities.append(
                            {
                                "name": tool.name,
                                "node_id": str(config.get("node_id") or tool_info.get("node_id") or ""),
                                "label": str(config.get("label") or tool_info.get("label") or tool_info.get("node_type") or "tool"),
                            }
                        )
                        logger.debug(
                            f"[ChatAgent] Built tool: {tool.name} (type={config.get('node_type')}, node_id={config.get('node_id')})"
                        )

                logger.debug(f"[ChatAgent] Built {len(all_tools)} tools from tool_data")

                # Auto-inject check_delegated_tasks tool when delegation tools present
                if any(tool.name.startswith("delegate_to_") for tool in all_tools):
                    check_info = {
                        "node_type": "_builtin_check_delegated_tasks",
                        "node_id": f"{node_id}_check_tasks",
                        "parameters": {},
                        "label": "Check Delegated Tasks",
                    }
                    check_tool, check_config = await self._build_tool_from_node(check_info)
                    if check_tool:
                        all_tools.append(check_tool)
                        tool_bindings.append((check_tool, check_config))
                        tool_identities.append(
                            {
                                "name": check_tool.name,
                                "node_id": str(check_config.get("node_id") or check_info["node_id"]),
                                "label": str(check_config.get("label") or check_info["label"]),
                            }
                        )
                        logger.debug("[ChatAgent] Auto-injected check_delegated_tasks tool")

                    # Add delegation guidance to system message
                    delegate_names = [tool.name for tool in all_tools if tool.name.startswith("delegate_to_")]
                    system_message += (
                        "\n\n## Agent Delegation\n"
                        "When delegating to sub-agents, use 'task' for the mission directive "
                        "(role and goal) and 'context' for input data the agent needs to work with.\n"
                        f"Available agents: {', '.join(delegate_names)}"
                    )

                ensure_unique_tool_names(tool_identities)
                tool_node_configs.update({tool.name: config for tool, config in tool_bindings})

            logger.debug(f"[ChatAgent] Total tools available: {len(all_tools)}")
            # Debug: log all tool schemas to verify they're correct
            for t in all_tools:
                schema = t.parameters
                logger.debug(f"[ChatAgent] Tool '{t.name}' schema: {schema}")

            # Flatten options collection from frontend
            options = parameters.get("options", {})
            flattened = {**parameters, **options}

            api_key = flattened.get("api_key")
            provider = parameters.get("provider", "openai")
            model = parameters.get("model", "")
            # Strip [FREE] prefix if present (added by OpenRouter model list for display)
            if model.startswith("[FREE] "):
                model = model[7:]

            logger.debug(f"[ChatAgent] Provider: {provider}, Model: {model}")

            # Validate model for provider - use default (DB > config)
            if not model or not is_model_valid_for_provider(model, provider):
                old_model = model
                model = await get_default_model_async(provider, database)
                if old_model:
                    logger.warning(f"Model '{old_model}' invalid for provider '{provider}', using default: {model}")
                else:
                    logger.info(f"No model specified, using default: {model}")

            if not api_key:
                _require_saved_endpoint(provider)
                raise ValueError("API key is required for Zeenie")

            # Resolve max_tokens and temperature via model registry
            max_tokens = _resolve_max_tokens(flattened, model, provider)

            # Build thinking config from parameters
            thinking_config = None
            if flattened.get("thinking_enabled"):
                thinking_config = ThinkingConfig(
                    enabled=True,
                    budget=int(flattened.get("thinking_budget", 2048)),
                    effort=flattened.get("reasoning_effort", "medium"),
                    level=flattened.get("thinking_level"),
                    format=flattened.get("reasoning_format", "parsed"),
                )

            temperature = _resolve_temperature(flattened, model, provider, bool(thinking_config and thinking_config.enabled))

            # Broadcast: Initializing
            await broadcast_status("initializing", {"message": f"Initializing {provider} model...", "provider": provider, "model": model, "active_skills": [], "last_skills": [], "last_tool_name": None, "last_capability": None})

            context_runtime = await self._prepare_context(
                node_id=node_id,
                context_data=memory_data,
                execution_context=context,
                workflow_id=workflow_id,
                database=database or self.database,
            )
            if context_runtime is not None:
                memory_data = None

            # Build messages
            messages: List[NativeMessage] = (
                [
                    message
                    for message in context_runtime.history
                    if message.role != "system"
                ]
                if context_runtime is not None
                else []
            )
            if system_message:
                messages.insert(
                    0,
                    NativeMessage(role="system", content=system_message),
                )

            # Load memory history if connected (markdown-based like AI Agent)
            session_id = None
            history_count = 0
            memory_content = None
            if memory_data and memory_data.get("node_id"):
                session_id = memory_data.get("session_id", "default")
                memory_content = memory_data.get("memory_content", "# Conversation History\n\n*No messages yet.*\n")

                await broadcast_status(
                    "loading_memory", {"message": "Loading conversation history...", "session_id": session_id, "has_memory": True}
                )

                # Parse short-term memory from markdown
                history_messages = _parse_memory_markdown(memory_content)
                history_count = len(history_messages)

                # If long-term memory enabled, retrieve relevant context
                if memory_data.get("long_term_enabled"):
                    try:
                        store = await _get_configured_memory_vector_store(
                            session_id,
                            memory_data,
                            self.auth,
                        )
                        if store:
                            k = memory_data.get("retrieval_count", 3)
                            docs = await store.similarity_search(prompt, k=k)
                            if docs:
                                retrieved_context = "\n---\n".join(
                                    d.page_content for d in docs
                                )
                                messages.append(
                                    NativeMessage(
                                        role="system",
                                        content=(
                                            "Relevant past context:\n"
                                            f"{retrieved_context}"
                                        ),
                                    )
                                )
                                logger.info(f"[ChatAgent Memory] Retrieved {len(docs)} relevant memories from long-term store")
                    except Exception as e:
                        logger.debug(f"[ChatAgent Memory] Long-term retrieval skipped: {e}")

                # Add parsed history messages
                messages.extend(history_messages)

                logger.info(f"[ChatAgent Memory] Loaded {history_count} messages from markdown")

                await broadcast_status(
                    "memory_loaded",
                    {"message": f"Loaded {history_count} messages from memory", "session_id": session_id, "history_count": history_count},
                )

            # Add current prompt
            messages.append(NativeMessage(role="user", content=prompt))

            # Broadcast: Invoking LLM
            await broadcast_status(
                "invoking_llm",
                {
                    "message": "Generating response...",
                    "has_memory": session_id is not None,
                    "history_count": history_count,
                    "skill_count": len(skill_data) if skill_data else 0,
                },
            )

            # Execute with or without tools
            thinking_content = None
            iterations = 1

            if all_tools:
                # Use the agent loop for tool execution (like AI Agent)
                logger.debug(f"[ChatAgent] Using agent loop with {len(all_tools)} tools")

                # Create tool executor callback. Tool-node status lifecycle
                # is owned by ``handlers.tools.execute_tool`` (single source
                # of truth, shared with execute_agent's tool_executor).
                async def chat_tool_executor(tool_name: str, tool_args: Dict) -> Any:
                    """Execute a tool by name using handlers/tools.py (same as AI Agent)."""
                    from services.handlers.tools import execute_tool

                    logger.debug(f"[ChatAgent] Executing tool: {tool_name}, args={tool_args}")

                    config = tool_node_configs.get(tool_name, {})
                    tool_node_id = config.get("node_id")
                    logger.debug(
                        f"[ChatAgent] Tool config: node_id={tool_node_id}, node_type={config.get('node_type')}, workflow_id={workflow_id}"
                    )

                    await broadcast_status(
                        "executing_tool",
                        {"message": f"Executing tool: {tool_name}", "tool_name": tool_name},
                    )
                    if broadcaster and tool_name and tool_name != "Skill":
                        await broadcaster.broadcast_agent_capability(
                            node_id,
                            capability_kind="tool",
                            capability_name=tool_name,
                            state="started",
                            workflow_id=workflow_id,
                            execution_id=str((context or {}).get("execution_id") or "") or None,
                            root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                            target_node_id=str(tool_node_id or "") or None,
                            provider=provider,
                            invocation_source="native",
                        )

                    # Inject services + graph context so execute_tool can scope its
                    # broadcasts and nested agents can execute with their own tools.
                    config["workflow_id"] = workflow_id
                    config["ai_service"] = self
                    config["database"] = self.database
                    config["parent_node_id"] = node_id
                    config["provider"] = provider
                    config["auto_rebind_tools"] = auto_rebind_enabled
                    if context:
                        config["run_scope"] = context.get("run_scope")
                        config["generation"] = context.get("generation")
                        config["parameter_snapshot"] = context.get("parameter_snapshot", {})
                        config["employee_job_id"] = context.get("employee_job_id")
                        config["nodes"] = context.get("nodes", [])
                        config["edges"] = context.get("edges", [])
                        config["workspace_dir"] = context.get("workspace_dir", "")
                        config["user_id"] = context.get("user_id", "owner")
                        # Stable per-run id so session-keyed tools (browser)
                        # reuse one instance across the agent loop.
                        config["execution_id"] = context.get("execution_id")
                        config["root_execution_id"] = context.get("root_execution_id")
                        config["delegation_depth"] = context.get("delegation_depth", 0)
                        config["team_id"] = context.get("team_id")
                        config["max_concurrent_subagents"] = context.get("max_concurrent_subagents", 3)
                        config["max_delegation_depth"] = context.get("max_delegation_depth", 2)

                    try:
                        from services.employees.permissions import assert_runtime_access
                        await assert_runtime_access(self.database, workflow_id, str(tool_node_id or ""), str(config.get("node_type") or ""), config)
                        result = await execute_tool(tool_name, tool_args, config)
                        await broadcast_status(
                            "tool_completed",
                            {"message": f"Tool completed: {tool_name}", "tool_name": tool_name},
                        )
                        if broadcaster and tool_name and tool_name != "Skill":
                            await broadcaster.broadcast_agent_capability(
                                node_id,
                                capability_kind="tool",
                                capability_name=tool_name,
                                state="completed",
                                workflow_id=workflow_id,
                                execution_id=str((context or {}).get("execution_id") or "") or None,
                                root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                                target_node_id=str(tool_node_id or "") or None,
                                provider=provider,
                                invocation_source="native",
                            )
                        logger.debug(f"[ChatAgent] Tool executed successfully: {tool_name}")
                        return result
                    except Exception as e:
                        logger.error(f"[ChatAgent] Tool execution failed: {tool_name}", error=str(e))
                        await broadcast_status(
                            "tool_completed",
                            {"message": f"Tool failed: {tool_name}", "tool_name": tool_name, "tool_failed": True},
                        )
                        if broadcaster and tool_name and tool_name != "Skill":
                            await broadcaster.broadcast_agent_capability(
                                node_id,
                                capability_kind="tool",
                                capability_name=tool_name,
                                state="failed",
                                workflow_id=workflow_id,
                                execution_id=str((context or {}).get("execution_id") or "") or None,
                                root_execution_id=str((context or {}).get("root_execution_id") or "") or None,
                                target_node_id=str(tool_node_id or "") or None,
                                provider=provider,
                                invocation_source="native",
                                error_code=type(e).__name__,
                            )
                        return e.as_dict() if isinstance(e, NodeUserError) else {"error": str(e)}

                # Auto-rebind toggle + recursion_limit override: same
                # machinery as ``execute_agent``.
                auto_rebind_enabled = True
                user_recursion_limit: Optional[int] = None
                tool_output_limit = resolve_tool_output_limit(None, self.settings)
                try:
                    user_settings = await self.database.get_user_settings()
                    if user_settings is not None:
                        auto_rebind_enabled = bool(
                            user_settings.get("auto_rebind_tools_after_canvas_change", True)
                        )
                        _raw_limit = user_settings.get("agent_recursion_limit")
                        if isinstance(_raw_limit, int) and _raw_limit > 0:
                            user_recursion_limit = _raw_limit
                        tool_output_limit = resolve_tool_output_limit(
                            user_settings, self.settings
                        )
                except Exception as exc:  # noqa: BLE001 — defensive read
                    logger.debug("[ChatAgent] user_settings read failed: %s", exc)

                async def _rebind_from_operations(operations: List[Dict[str, Any]]) -> List[Any]:
                    """Mirror of execute_agent._rebind_from_operations — see that
                    function for the contract. We can't dedupe cleanly because
                    the two agent paths capture different closure variables
                    (``tool_configs`` vs ``tool_node_configs``)."""
                    from services.node_registry import get_node_class

                    saved_workflow = await self.database.get_workflow(workflow_id) if workflow_id else None
                    saved_graph = getattr(saved_workflow, "data", None) or {}
                    if context is not None and saved_graph:
                        admitted_graph = extend_runtime_graph(context, saved_graph, operations)
                        context["nodes"] = admitted_graph["nodes"]
                        context["edges"] = admitted_graph["edges"]
                    bound = {identity["node_id"] for identity in tool_identities}
                    new_bindings: List[tuple[Any, Dict[str, Any]]] = []
                    for op in operations:
                        if op.get("type") != "add_node":
                            continue
                        if not rebind_allowed(op, node_id, nodes=(context or {}).get("nodes", []), edges=(context or {}).get("edges", []), operations=() if saved_graph else operations):
                            continue
                        node_type = op.get("node_type")
                        if not node_type:
                            continue
                        cls = get_node_class(node_type)
                        if cls is None:
                            continue
                        # Match the catalogue filter — pure ToolNode OR
                        # dual-purpose ActionNode (usable_as_tool=True),
                        # excluding chat models and the Skills node
                        # (masterSkill feeds input-skill, never an LLM
                        # tool). Without this the rebind silently drops
                        # twitterSearch / googleGmail / pythonExecutor etc.
                        _kind = getattr(cls, "component_kind", "")
                        if not is_runtime_tool(cls):
                            continue
                        if (getattr(cls, "ui_hints", None) or {}).get("isMasterSkillEditor"):
                            continue
                        tool_info = {
                            "node_id": op.get("minted_id") or op.get("client_ref") or f"new_{node_type}",
                            "node_type": node_type,
                            "parameters": op.get("parameters") or {},
                            "label": op.get("label") or node_type,
                        }
                        if tool_info["node_id"] in bound:
                            continue
                        try:
                            tool, tool_config = await self._build_tool_from_node(tool_info)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "[ChatAgent] rebind: _build_tool_from_node raised for %s: %s",
                                node_type,
                                exc,
                            )
                            continue
                        if tool is None:
                            continue
                        new_bindings.append((tool, tool_config or tool_info))
                        bound.add(tool_info["node_id"])

                    new_identities = [
                        {
                            "name": tool.name,
                            "node_id": str(config.get("node_id") or ""),
                            "label": str(config.get("label") or config.get("node_type") or "tool"),
                        }
                        for tool, config in new_bindings
                    ]
                    ensure_unique_tool_names([*tool_identities, *new_identities])
                    for tool, tool_config in new_bindings:
                        tool_node_configs[tool.name] = tool_config
                    tool_identities.extend(new_identities)
                    return [tool for tool, _ in new_bindings]

                # Run the agent loop. See ``execute_agent`` for the
                # rationale on ``recursion_limit`` + the truncation
                # behaviour on hit. Precedence: UserSettings > env.
                from services.model_registry import get_model_registry

                if user_recursion_limit is not None:
                    recursion_limit = user_recursion_limit
                else:
                    recursion_limit = int(get_model_registry().get_agent_defaults()["recursion_limit"])

                async def _emit_progress(iter_count: int) -> None:
                    if broadcaster:
                        await broadcaster.broadcast_agent_progress(
                            node_id,
                            workflow_id=workflow_id,
                            iteration=iter_count,
                            max_iterations=recursion_limit,
                        )

                final_state = await run_native_agent_loop(
                    self.chat_unifier,
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    thinking=thinking_config,
                    initial_messages=messages,
                    tools=all_tools,
                    tool_executor=chat_tool_executor,
                    max_iterations=recursion_limit,
                    progress_callback=_emit_progress if broadcaster else None,
                    rebind_from_operations=_rebind_from_operations if auto_rebind_enabled else None,
                    roster_after_rebind=lambda: delegation_roster(tool_identities),
                    conversation_saver=(
                        context_runtime.save
                        if context_runtime is not None
                        else None
                    ),
                    tool_output_limit=tool_output_limit,
                )

                # Extract response
                all_messages = final_state["messages"]
                ai_response = all_messages[-1] if all_messages else None

                if not ai_response or not hasattr(ai_response, "content"):
                    raise ValueError("No response generated from agent")

                response_content = self._extract_text_content(ai_response.content, ai_response)
                iterations = final_state.get("iteration", 1)
                thinking_content = final_state.get("thinking_content")
            else:
                final_state = await run_native_agent_loop(
                    self.chat_unifier,
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    thinking=thinking_config,
                    initial_messages=messages,
                    max_iterations=1,
                    conversation_saver=(
                        context_runtime.save
                        if context_runtime is not None
                        else None
                    ),
                )
                all_messages = final_state["messages"]
                ai_response = all_messages[-1] if all_messages else None
                if ai_response is None:
                    raise ValueError("No response generated from agent")
                response_content = self._extract_text_content(
                    ai_response.content,
                    ai_response,
                )
                thinking_content = final_state.get("thinking_content")

            logger.info(f"[ChatAgent] Response generated, thinking={'yes' if thinking_content else 'no'}, iterations={iterations}")

            # Track token usage if memory connected (for compaction service)
            # Also triggers compaction if threshold exceeded.
            response_msg = final_state.get("usage") or ai_response
            compaction_result = None
            if session_id and response_msg:
                compaction_result = await self._track_token_usage(
                    session_id=session_id,
                    node_id=node_id,
                    provider=provider,
                    model=model,
                    ai_response=response_msg,
                    all_messages=all_messages,
                    broadcaster=broadcaster,
                    workflow_id=workflow_id,
                    memory_content=memory_content,
                    api_key=api_key,
                    memory_node_id=memory_data.get("node_id") if memory_data else None,
                )
                _accumulate_compaction_usage(
                    final_state, compaction_result
                )

            # Save to memory if connected (markdown-based like AI Agent)
            if (
                memory_data
                and memory_data.get("node_id")
                and is_valid_message_content(prompt)
                and is_valid_message_content(response_content)
            ):
                await broadcast_status(
                    "saving_memory", {"message": "Saving to conversation memory...", "session_id": session_id, "has_memory": True}
                )

                from services.memory.runtime import append_memory_turns_atomic

                compacted_summary = None
                if compaction_result and compaction_result.get("success") and compaction_result.get("summary"):
                    compacted_summary = compaction_result["summary"]
                    logger.info("[ChatAgent Memory] Using compacted summary as new base")

                memory_node_id = memory_data["node_id"]
                execution_id = (context or {}).get("execution_id")
                mutation_id = (
                    f"chat-memory:{execution_id}:{node_id}:{memory_node_id}"
                    if execution_id
                    else None
                )
                _current_params, removed_texts, _applied = await append_memory_turns_atomic(
                    self.database,
                    memory_node_id,
                    [("human", prompt), ("ai", response_content)],
                    window_size=int(memory_data.get("window_size", 10)),
                    mutation_id=mutation_id,
                    replacement_content=compacted_summary,
                    expected_content=memory_content,
                )

                # Store removed messages in long-term vector DB
                if removed_texts and memory_data.get("long_term_enabled"):
                    try:
                        store = await _get_configured_memory_vector_store(
                            session_id,
                            memory_data,
                            self.auth,
                        )
                        if store:
                            await store.add_texts(removed_texts)
                            logger.info(f"[ChatAgent Memory] Archived {len(removed_texts)} messages to long-term store")
                    except Exception as e:
                        logger.warning(f"[ChatAgent Memory] Failed to archive to vector store: {e}")

                logger.info(f"[ChatAgent Memory] Saved markdown to memory node '{memory_node_id}'")

            # Determine agent type based on configuration
            agent_type = "chat"
            if skill_data and all_tools:
                agent_type = "chat_with_skills_and_tools"
            elif skill_data:
                agent_type = "chat_with_skills"
            elif all_tools:
                agent_type = "chat_with_tools"

            result = {
                "response": response_content,
                "thinking": thinking_content,
                "thinking_enabled": thinking_config.enabled if thinking_config else False,
                "model": model,
                "provider": provider,
                "agent_type": agent_type,
                "iterations": iterations,
                "finish_reason": "stop",
                "timestamp": datetime.now().isoformat(),
                "input": {
                    "prompt": prompt,
                    "system_message": system_message,
                },
            }

            if session_id:
                result["memory"] = {"session_id": session_id, "history_loaded": history_count}
            if context_runtime is not None:
                result["context"] = {
                    "workflow_id": context_runtime.workflow_id,
                    "generation": context_runtime.generation,
                    "agent_node_id": context_runtime.agent_node_id,
                }

            if skill_data:
                result["skills"] = {
                    "connected": [s.get("skill_name", s.get("node_type", "")) for s in skill_data],
                    "count": len(skill_data),
                }

            if all_tools:
                result["tools"] = {"connected": [t.name for t in all_tools], "count": len(all_tools)}

            log_execution_time(logger, "chat_agent", start_time, time.time())
            log_api_call(logger, provider, model, "chat_agent", True)

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "chatAgent",
                "result": result,
                "execution_time": time.time() - start_time,
            }

        except DuplicateToolNameError as e:
            logger.warning("[ChatAgent] Duplicate tool surface rejected: %s", e)
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "chatAgent",
                **e.as_dict(),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except NodeUserError as e:
            log_api_call(logger, provider, model, "chat_agent", False, error=str(e))
            raise

        except Exception as e:
            logger.error("[ChatAgent] Execution failed", node_id=node_id, error=str(e))
            log_api_call(logger, provider, model, "chat_agent", False, error=str(e))

            return {
                "success": False,
                "node_id": node_id,
                "node_type": "chatAgent",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        finally:
            from services.skill_runtime import clear_skill_turn

            await clear_skill_turn(workflow_id or "", str((context or {}).get("execution_id") or ""), node_id)

    async def _build_tool_from_node(self, tool_info: Dict[str, Any]) -> tuple:
        """Convert a node configuration into a provider-neutral tool spec.

        Uses database-stored schema as source of truth if available, otherwise
        falls back to dynamic schema generation.

        Tool name + description resolution chain (Wave 12 D5):
          1. DB-stored schema (per-node override via UI)
          2. ``cls.tool_name`` / ``cls.tool_description`` ClassVars on the
             plugin class. ``tool_description`` falls back to
             ``cls.description`` when empty.
          3. ``_PSEUDO_TOOL_FALLBACK`` for built-in pseudo-types that have
             no plugin class (``_builtin_check_delegated_tasks``)
          4. ``node_params.get('tool_name')`` / ``...tool_description``
             (per-node override declared as a Pydantic field on the plugin's
             Params model — e.g. brave_search / serper_search / perplexity).
          5. Last-resort default: ``f"tool_{label}"`` / ``f"Execute {label}"``

        Args:
            tool_info: Dict containing node_id, node_type, parameters, and label

        Returns:
            Tuple of (AgentToolSpec, config_dict) or (None, None) on failure
        """
        # Built-in / aggregator pseudo-types — no plugin class, no ClassVar.
        # These must stay as an explicit fallback dict (Wave 12 D5).
        _PSEUDO_TOOL_FALLBACK = {
            "_builtin_check_delegated_tasks": (
                "check_delegated_tasks",
                "Check status and retrieve results of previously delegated tasks.",
            ),
            "_builtin_skill": (
                "Skill",
                "Load a connected skill or read/search one of its declared text resources on demand.",
            ),
        }

        def _resolve_default_tool_name_description(node_type: str) -> tuple:
            """Resolve ``(tool_name, tool_description)`` via the post-D5 chain.

            ``tool_description`` falls back to ``cls.description`` when the
            plugin doesn't override it — only ~15-20 of the 68 plugins need
            an LLM-tuned description distinct from the human-facing one
            (writeTodos, pythonExecutor, the 16 specialized agents,
            stripeAction, ...). The rest share their existing
            ``description``.

            Returns ``(None, None)`` when the node_type matches no entry —
            callers fall through to ``node_params`` then ``f"tool_{label}"``.
            """
            from services.node_registry import get_node_class

            node_cls = get_node_class(node_type)
            if node_cls is not None:
                cv_name = (getattr(node_cls, "tool_name", "") or "").strip()
                cv_desc = (getattr(node_cls, "tool_description", "") or "").strip()
                if cv_name:
                    # tool_description falls back to the plugin's regular
                    # ``description`` ClassVar (avoids duplicating the same
                    # text in two ClassVars on most plugins).
                    return cv_name, cv_desc or (getattr(node_cls, "description", "") or "").strip() or None
            pseudo = _PSEUDO_TOOL_FALLBACK.get(node_type)
            if pseudo is not None:
                return pseudo
            return None, None

        try:
            node_type = tool_info.get("node_type", "")
            node_params = tool_info.get("parameters", {})
            node_label = tool_info.get("label", node_type)
            node_id = tool_info.get("node_id", "")
            connected_services = tool_info.get("connected_services", [])

            default_tool_name, default_tool_description = _resolve_default_tool_name_description(node_type)
            from services.node_registry import get_node_class

            plugin_cls = get_node_class(node_type)
            schema_locked = bool(
                plugin_cls is not None
                and getattr(plugin_cls, "tool_schema_locked", False)
            )
            # Team-handle expansion assigns a per-node identity to custom
            # aiAgent delegates. It intentionally outranks schemas and class
            # defaults because those are type-wide and would collide.
            delegated_name = tool_info.get("delegate_tool_name")

            # Check database for stored schema (source of truth)
            db_schema = await self.database.get_tool_schema(node_id) if node_id else None

            if schema_locked:
                # Security-sensitive first-party tools own both their
                # canonical name and invocation schema. A stale/custom row
                # from the generic Tool editor must not replace that
                # contract.
                tool_name = default_tool_name or node_type
                tool_description = (
                    default_tool_description or f"Execute {node_label} node"
                )
            elif db_schema:
                # Use database schema as source of truth
                logger.debug(f"[Agent] Using DB schema for tool node {node_id}")
                tool_name = delegated_name or db_schema.get("tool_name", default_tool_name or f"tool_{node_label}")
                tool_description = db_schema.get("tool_description", default_tool_description or f"Execute {node_label}")
                # Use stored connected_services if available (for toolkit nodes)
                if db_schema.get("connected_services"):
                    connected_services = db_schema["connected_services"]
            else:
                # Fall back to dynamic generation from node params
                tool_name = delegated_name or (
                    node_params.get("tool_name") or default_tool_name or f"tool_{node_label}".replace(" ", "_").replace("-", "_").lower()
                )
                tool_description = node_params.get("tool_description") or default_tool_description or f"Execute {node_label} node"

            # For AI Agent nodes, enhance description with child agent's tool capabilities
            # This allows parent agent to know what the child agent can do
            from services.workspace_capabilities import is_registered_agent

            if is_registered_agent(node_type):
                child_tools = tool_info.get("child_tools", [])
                if child_tools:
                    # Build capability description from child's connected tools
                    capability_descriptions = []
                    for child_tool in child_tools:
                        child_type = child_tool.get("node_type", "")
                        child_label = child_tool.get("label", child_type)
                        # Resolve via the post-D5 chain (ClassVar → pseudo → legacy)
                        _, child_desc = _resolve_default_tool_name_description(child_type)
                        if not child_desc:
                            child_desc = f"Use {child_label}"
                        capability_descriptions.append(f"- {child_label}: {child_desc}")

                    capabilities_text = "\n".join(capability_descriptions)
                    tool_description = (
                        f"Delegate tasks to '{node_label}' agent. "
                        f"This agent has the following capabilities:\n{capabilities_text}\n"
                        f"Call ONCE per task, returns task_id. Agent works in background."
                    )
                    logger.info(f"[Agent] Enhanced tool description for {node_type} with {len(child_tools)} child tools")

            # Clean tool name (providers require alphanumeric + underscores)
            import re

            tool_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)

            # Build schema based on node type.
            # If DB has schema_config, use it to build custom schema, otherwise use dynamic
            schema_params = dict(node_params)
            if connected_services:
                schema_params["connected_services"] = connected_services
            if (
                not schema_locked
                and db_schema
                and db_schema.get("schema_config")
            ):
                schema_params["db_schema_config"] = db_schema["schema_config"]
            schema = self._get_tool_schema(node_type, schema_params)

            # Build config dict - include connected_services for toolkit nodes
            config = {
                "node_type": node_type,
                "node_id": node_id,
                "parameters": node_params,
                "label": node_label,
                "connected_services": connected_services,  # Pass through for execution
            }

            from services.plugin.tool import inline_schema_refs

            parameters = inline_schema_refs(schema.model_json_schema())
            tool = AgentToolSpec(
                definition=ToolDef(
                    name=tool_name,
                    description=tool_description,
                    parameters=parameters,
                ),
                args_schema=schema,
                execution=config,
            )

            logger.debug(f"[Agent] Built tool '{tool_name}' with node_id={node_id}")
            return tool, config

        except Exception as e:
            logger.error(f"[Agent] Failed to build tool from node: {e}")
            return None, None

    def _get_tool_schema(self, node_type: str, params: Dict[str, Any]) -> Type[BaseModel]:
        """Get Pydantic schema for tool based on node type.

        Uses db_schema_config from database if available (source of truth),
        otherwise falls back to built-in schema definitions.

        Args:
            node_type: The node type (e.g., 'calculatorTool', 'httpRequest')
            params: Node parameters, may include db_schema_config from database

        Returns:
            Pydantic BaseModel class for the tool's arguments
        """
        from pydantic import BaseModel, Field

        # Plugin-locked ToolInput is authoritative even when an older client
        # left a custom ToolSchema row in the database.
        from services.node_registry import get_node_class

        plugin_cls = get_node_class(node_type)
        if plugin_cls is not None and getattr(plugin_cls, "tool_schema_locked", False):
            tool_input_model = getattr(plugin_cls, "tool_input_model", None)
            return tool_input_model() if callable(tool_input_model) else plugin_cls.Params

        # Check if we have a database-stored schema config (source of truth)
        db_schema_config = params.get("db_schema_config")
        if db_schema_config:
            return self._build_schema_from_config(db_schema_config)

        # Agent delegation schema — MUST fire before the plugin fast-path.
        # When an agent is connected to another agent's input-tools handle, we
        # expose a (task, context) schema for delegation rather than the agent's
        # own Params (which would leak provider/model/prompt into the parent LLM).
        _AGENT_DELEGATION_TYPES = (
            "aiAgent",
            "chatAgent",
            "android_agent",
            "coding_agent",
            "web_agent",
            "task_agent",
            "social_agent",
            "travel_agent",
            "tool_agent",
            "productivity_agent",
            "payments_agent",
            "consumer_agent",
            "autonomous_agent",
            "orchestrator_agent",
            "ai_employee",
            "rlm_agent",
            "claude_code_agent",
            "vertex_managed_agent",
        )
        if node_type in _AGENT_DELEGATION_TYPES:
            agent_label = params.get("label", node_type)

            class DelegateToAgentSchema(BaseModel):
                """Delegate a task to another AI Agent (non-blocking).

                The child agent works independently in the background.
                The 'task' becomes the agent's mission directive (system message).
                The 'context' becomes the agent's input data (user prompt).
                Returns a task_id immediately. Use 'check_delegated_tasks'
                tool to check status and retrieve results when ready.
                """

                task: str = Field(
                    description=f"The mission directive for '{agent_label}'. Describe the role and goal clearly, e.g. 'You are a coding assistant. Write a Python script that processes the given CSV data.'"
                )
                context: Optional[str] = Field(
                    default=None,
                    description="Input data or specific details the agent needs to work with, e.g. file contents, user requirements, or parameters",
                )

            return DelegateToAgentSchema

        if node_type == "_builtin_skill":
            from typing import Literal

            class SkillInvocationSchema(BaseModel):
                action: Literal["load", "read_resource", "search_resource"] = Field(
                    default="load", description="Load instructions, read a bounded resource page, or search a resource."
                )
                skill_name: str = Field(description="Exact connected skill name from Available Skills.")
                path: Optional[str] = Field(default=None, description="Declared references/<file> or scripts/<file> path.")
                query: Optional[str] = Field(default=None, description="Case-insensitive search query for search_resource.")
                cursor: int = Field(default=0, ge=0, description="Pagination cursor.")
                limit: int = Field(default=4000, ge=1, le=16000, description="Bounded character or match limit.")

            return SkillInvocationSchema

        # Built-in check tool for delegation result retrieval (no plugin).
        if node_type == "_builtin_check_delegated_tasks":

            class CheckDelegatedTasksSchema(BaseModel):
                """Check on previously delegated tasks and retrieve their results.

                Call this to see if delegated agents have completed their work.
                Returns status and results for each task.
                """

                task_ids: Optional[List[str]] = Field(
                    default=None, description="Specific task IDs to check. Omit to get ALL delegated tasks."
                )

            return CheckDelegatedTasksSchema

        # Wave 11.B.1 plugin fast-path: if this node_type is a registered
        # BaseNode subclass, use its Pydantic Params model directly. Every
        # AI-tool-usable plugin is covered by this lookup (contract invariant
        # test_fast_path_covers_every_plugin_tool). Wave 11.D.13 stripped the
        # per-type ad-hoc schemas that used to live below this gate.
        if plugin_cls is not None and hasattr(plugin_cls, "Params"):
            tool_input_model = getattr(plugin_cls, "tool_input_model", None)
            if callable(tool_input_model):
                return tool_input_model()
            return plugin_cls.Params

        # Generic schema for other nodes
        class GenericToolSchema(BaseModel):
            """Generic schema for tool arguments."""

            input: str = Field(description="Input data for the tool")

        return GenericToolSchema

    def _build_schema_from_config(self, schema_config: Dict[str, Any]) -> Type[BaseModel]:
        """Build a Pydantic schema from database-stored configuration.

        Schema config format:
        {
            "description": "Schema description",
            "fields": {
                "field_name": {
                    "type": "string" | "number" | "boolean" | "object" | "array",
                    "description": "Field description",
                    "required": True | False,
                    "default": <optional default value>,
                    "enum": [<optional enum values>]
                }
            }
        }
        """
        from pydantic import Field, create_model

        fields_config = schema_config.get("fields", {})
        schema_description = schema_config.get("description", "Tool arguments schema")

        # Build field annotations and defaults
        annotations = {}
        field_defaults = {}

        TYPE_MAP = {
            "string": str,
            "number": float,
            "integer": int,
            "boolean": bool,
            "object": Dict[str, Any],
            "array": list,
        }

        for field_name, field_config in fields_config.items():
            field_type_str = field_config.get("type", "string")
            field_type = TYPE_MAP.get(field_type_str, str)
            field_description = field_config.get("description", "")
            is_required = field_config.get("required", True)
            default_value = field_config.get("default")
            enum_values = field_config.get("enum")

            # Handle optional fields
            if not is_required:
                field_type = Optional[field_type]

            annotations[field_name] = field_type

            # Build Field with description and enum if provided
            field_kwargs = {"description": field_description}
            if enum_values:
                # For enums, include in description since Pydantic Field doesn't support enum directly
                field_kwargs["description"] = f"{field_description} Options: {', '.join(str(v) for v in enum_values)}"

            if default_value is not None:
                field_defaults[field_name] = Field(default=default_value, **field_kwargs)
            elif not is_required:
                field_defaults[field_name] = Field(default=None, **field_kwargs)
            else:
                field_defaults[field_name] = Field(**field_kwargs)

        # Create dynamic Pydantic model
        DynamicSchema = create_model(
            "DynamicToolSchema", __doc__=schema_description, **{name: (annotations[name], field_defaults[name]) for name in annotations}
        )

        return DynamicSchema
