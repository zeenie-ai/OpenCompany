"""AI service: LangChain-based chat-model construction + plain async agent loop."""

from __future__ import annotations

import functools
import re
import time
import httpx
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Dict, Any, List, Optional, Callable, Type, TYPE_CHECKING

# ---------------------------------------------------------------------------
# LangChain imports — fully lazy except for ``BaseMessage``.
#
# Cold-start measurement (Windows, fresh disk):
#   ``from langchain_openai import ChatOpenAI``          ~20.8s (pulls openai SDK + tiktoken)
# Deferring the heavy imports until the first agent run trims server-ready time.
#
# ``services/llm/`` (the native LLM SDK layer) handles ``execute_chat()`` and
# ``fetch_models()`` without LangChain. LangChain is only needed on the agent
# execution path (``execute_agent`` / ``execute_chat_agent``) for the per-
# provider ``ChatOpenAI`` / ``ChatAnthropic`` / ``ChatGoogleGenerativeAI``
# instances and ``chat_model.bind_tools()`` provider-unified tool calling.
#
# ``BaseMessage`` stays eager because ``_run_agent_loop`` type-hints it in
# its signature; the import is cheap (no tiktoken / openai SDK pull) and
# keeps type checkers happy. Everything else is lazy via
# ``@functools.cache``'d helpers or local imports inside the methods that
# need them.
# ---------------------------------------------------------------------------

from langchain_core.messages import BaseMessage  # eager: type hint for _run_agent_loop
import json

# Eager imports — both are tiny and read on every chat/agent execution
# path. ``openai`` is the canonical SDK whose typed exception hierarchy
# (``BadRequestError`` / ``AuthenticationError`` / ``RateLimitError`` / …)
# is the contract this module dispatches on; ``NodeUserError`` is the
# framework's "user-correctable, no-traceback" sentinel used to surface
# those typed errors cleanly through ``BaseNode.execute()``.
import openai
from services.plugin import NodeUserError

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI  # noqa: F401
    from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage  # noqa: F401
    from langchain_core.tools import StructuredTool  # noqa: F401
    from pydantic import BaseModel, Field, create_model  # noqa: F401
    from langchain_anthropic import ChatAnthropic  # noqa: F401
    from langchain_groq import ChatGroq  # noqa: F401
    from langchain_cerebras import ChatCerebras  # noqa: F401


@functools.cache
def _get_chat_anthropic() -> Type:
    """Lazy import: ``langchain_anthropic`` is ~800ms cold."""
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic


@functools.cache
def _get_chat_groq() -> Type:
    """Lazy import: ``langchain_groq`` adds ~270ms cold."""
    from langchain_groq import ChatGroq

    return ChatGroq


@functools.cache
def _get_chat_cerebras() -> Optional[Type]:
    """Lazy import: ``langchain_cerebras`` is optional (Python <3.13).

    Returns the class if importable, ``None`` otherwise. Cached so the
    ImportError path runs once.
    """
    try:
        from langchain_cerebras import ChatCerebras

        return ChatCerebras
    except ImportError:
        return None


# Lazy import for Google GenAI (google-generativeai init hangs on Windows/Python 3.13)
@functools.cache
def _get_google_genai_class() -> Type:
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI


def _is_cerebras_available() -> bool:
    """Whether the langchain-cerebras package is importable."""
    return _get_chat_cerebras() is not None


# Backwards-compat shims (PEP 562 module-level __getattr__) for code that
# historically read these as module-level constants. The "import" cost is paid
# only on first access. ``PROVIDER_CONFIGS`` resolves through ``get_provider_configs()``
# so any external `from services.ai import PROVIDER_CONFIGS` keeps working
# without triggering the heavy LangChain imports until first use.
def __getattr__(name: str):
    if name == "ChatAnthropic":
        return _get_chat_anthropic()
    if name == "ChatGroq":
        return _get_chat_groq()
    if name == "ChatCerebras":
        return _get_chat_cerebras()
    if name == "CEREBRAS_AVAILABLE":
        return _is_cerebras_available()
    if name == "CEREBRAS_IMPORT_ERROR":
        # Reproduce the error string without re-raising; helpful for log messages.
        if _get_chat_cerebras() is not None:
            return None
        try:
            __import__("langchain_cerebras")
            return None
        except ImportError as e:
            return str(e)
    if name == "PROVIDER_CONFIGS":
        return get_provider_configs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


from core.config import Settings
from core.logging import get_logger, log_execution_time, log_api_call
from services.auth import AuthService

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Native LLM provider imports (dual-path: native for chat, LangChain for agents)
# ---------------------------------------------------------------------------
# ``create_provider`` / ``is_native_provider`` are no longer called from
# this module — ``ChatUnifier`` (injected via DI) owns provider
# dispatch. The imports stay deleted; Phase D removes the legacy
# factory functions entirely once the LangChain fallback for
# groq/cerebras lands behind the unifier.
from services.llm.protocol import (
    Message as NativeMessage,
    ThinkingConfig as NativeThinkingConfig,
    LLMResponse,
)
from services.llm.config import (
    resolve_max_tokens as native_resolve_max_tokens,
    resolve_temperature as native_resolve_temperature,
)
from services.llm.vertex import is_vertex_express_key


# =============================================================================
# MARKDOWN MEMORY HELPERS - Parse/append/trim conversation markdown
# =============================================================================


def _parse_memory_markdown(content: str) -> List[BaseMessage]:
    """Parse markdown memory content into LangChain messages.

    Markdown format:
    ### **Human** (timestamp)
    message content

    ### **Assistant** (timestamp)
    response content
    """
    from langchain_core.messages import HumanMessage, AIMessage

    messages = []
    pattern = r"### \*\*(Human|Assistant)\*\*[^\n]*\n(.*?)(?=\n### \*\*|$)"
    for role, text in re.findall(pattern, content, re.DOTALL):
        text = text.strip()
        if text:
            msg_class = HumanMessage if role == "Human" else AIMessage
            messages.append(msg_class(content=text))
    return messages


def _append_to_memory_markdown(content: str, role: str, message: str) -> str:
    """Append a message to markdown memory content."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    label = "Human" if role == "human" else "Assistant"
    entry = f"\n### **{label}** ({ts})\n{message}\n"
    # Remove empty state message if present
    return content.replace("*No messages yet.*\n", "") + entry


def _trim_markdown_window(content: str, window_size: int) -> tuple:
    """Keep last N message pairs, return (trimmed_content, removed_texts).

    Args:
        content: Full markdown content
        window_size: Number of message PAIRS to keep (human+assistant)

    Returns:
        Tuple of (trimmed markdown, list of removed message texts for archival)
    """
    pattern = r"(### \*\*(Human|Assistant)\*\*[^\n]*\n.*?)(?=\n### \*\*|$)"
    blocks = [m[0] for m in re.findall(pattern, content, re.DOTALL)]

    if len(blocks) <= window_size * 2:
        return content, []

    keep = blocks[-(window_size * 2) :]
    removed = blocks[: -(window_size * 2)]

    # Extract text from removed blocks for vector storage
    removed_texts = []
    for block in removed:
        match = re.search(r"\n(.*)$", block, re.DOTALL)
        if match:
            removed_texts.append(match.group(1).strip())

    return "# Conversation History\n" + "\n".join(keep), removed_texts


# Global cache for vector stores per session (InMemoryVectorStore)
_memory_vector_stores: Dict[str, Any] = {}


def _get_memory_vector_store(session_id: str):
    """Get or create InMemoryVectorStore for a session."""
    if session_id not in _memory_vector_stores:
        try:
            from langchain_core.vectorstores import InMemoryVectorStore
            from langchain_huggingface import HuggingFaceEmbeddings

            embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
            _memory_vector_stores[session_id] = InMemoryVectorStore(embeddings)
            logger.debug(f"[Memory] Created vector store for session '{session_id}'")
        except ImportError as e:
            logger.warning(f"[Memory] Vector store not available: {e}")
            return None
    return _memory_vector_stores[session_id]


# =============================================================================
# AI PROVIDER REGISTRY - Single source of truth for provider configurations
# =============================================================================


@dataclass
class ProviderConfig:
    """Configuration for an AI provider."""

    name: str
    model_class: Optional[Type]
    api_key_param: str  # Parameter name for API key in model constructor
    max_tokens_param: str  # Parameter name for max tokens
    detection_patterns: tuple  # Patterns to detect this provider from model name
    default_model: str  # Default model when none specified
    models_endpoint: str  # API endpoint to fetch models
    models_header_fn: Callable[[str], dict]  # Function to create headers
    base_url: str = ""  # Custom base URL for OpenAI-compatible providers
    extra_headers: Optional[Dict[str, str]] = None  # Default headers for API calls
    supported_params: Optional[List[str]] = None  # API-supported params (from llm_defaults.json)


@dataclass
class ThinkingConfig:
    """Unified thinking/reasoning configuration across AI providers.

    LangChain parameters per provider (Jan 2026):
    - Claude: thinking={"type": "enabled", "budget_tokens": budget}, temp must be 1
    - OpenAI o-series: reasoning_effort ('minimal', 'low', 'medium', 'high')
    - Gemini 3+: thinking_level ('low', 'medium', 'high')
    - Gemini 2.5: thinking_budget (int tokens)
    - Groq: reasoning_format ('parsed', 'hidden')
    """

    enabled: bool = False
    budget: int = 2048  # Token budget (Claude, Gemini 2.5)
    effort: str = "medium"  # Effort level: 'minimal', 'low', 'medium', 'high' (OpenAI o-series)
    level: str = "medium"  # Thinking level: 'low', 'medium', 'high' (Gemini 3+)
    format: str = "parsed"  # Output format: 'parsed', 'hidden' (Groq)


def _openai_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _anthropic_headers(api_key: str) -> dict:
    return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}


def _gemini_headers(api_key: str) -> dict:
    return {}  # API key in URL for Gemini


def _openrouter_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "HTTP-Referer": "http://localhost:3000", "X-Title": "OpenCompany"}


def _groq_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _cerebras_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _bearer_headers(api_key: str) -> dict:
    """Generic Bearer auth headers for OpenAI-compatible providers."""
    return {"Authorization": f"Bearer {api_key}"}


# Map of provider -> (model_class_factory, header_fn) for providers with custom
# LangChain classes. Factories let us defer the heavy ``langchain_anthropic`` /
# ``langchain_groq`` / ``langchain_cerebras`` imports until an agent actually
# runs. Providers NOT in this map use ChatOpenAI + _bearer_headers
# (OpenAI-compatible). ``model_class_factory`` returns the resolved class
# (or None for gemini, whose lazy resolver lives at the call site).
def _build_provider_class_map() -> Dict[str, tuple]:
    from langchain_openai import ChatOpenAI

    mapping: Dict[str, tuple] = {
        "openai": (ChatOpenAI, _openai_headers),
        "anthropic": (_get_chat_anthropic(), _anthropic_headers),
        "gemini": (None, _gemini_headers),  # resolved via _get_google_genai_class()
        "openrouter": (ChatOpenAI, _openrouter_headers),
        "groq": (_get_chat_groq(), _groq_headers),
    }
    cerebras_class = _get_chat_cerebras()
    if cerebras_class is not None:
        mapping["cerebras"] = (cerebras_class, _cerebras_headers)
    return mapping


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
    """Resolve temperature with model-specific constraints.

    Handles:
    - O-series models: always temp=1
    - Claude with thinking: always temp=1
    - Range clamping per provider
    """
    from services.model_registry import get_model_registry

    registry = get_model_registry()

    user_val = flattened.get("temperature")
    if user_val is None:
        user_val = registry.get_agent_defaults()["default_temperature"]
    user_temp = float(user_val)

    # O-series reasoning models always require temperature=1
    if registry.is_reasoning_model(model, provider):
        if user_temp != 1:
            logger.info(f"[AI] Reasoning model '{model}': forcing temperature to 1 (was {user_temp})")
        return 1.0

    # Claude thinking mode requires temperature=1
    if thinking_enabled and provider == "anthropic":
        if user_temp != 1:
            logger.info(f"[AI] Claude thinking mode: forcing temperature to 1 (was {user_temp})")
        return 1.0

    # Clamp to valid range for provider/model
    temp_range = registry.get_temperature_range(model, provider)
    return max(temp_range[0], min(temp_range[1], user_temp))


# Provider configurations - built from config/llm_defaults.json on first use.
# Building the map triggers the heavy LangChain imports (~1.05s of
# langchain_anthropic + langchain_groq), so we defer it until a caller asks
# for a config (i.e. an AI agent actually runs). The native LLM SDK path
# (services/llm/) bypasses this entirely.
def _build_provider_configs() -> Dict[str, ProviderConfig]:
    """Build PROVIDER_CONFIGS from llm_defaults.json + provider-class map."""
    from langchain_openai import ChatOpenAI

    providers = _LLM_DEFAULTS.get("providers", {})
    configs: Dict[str, ProviderConfig] = {}
    class_map = _build_provider_class_map()
    cerebras_available = _is_cerebras_available()

    for name, prov in providers.items():
        # Skip cerebras if not available
        if name == "cerebras" and not cerebras_available:
            logger.warning("Cerebras provider not available: langchain-cerebras package not installed (Python <3.13 only)")
            continue

        model_class, header_fn = class_map.get(name, (ChatOpenAI, _bearer_headers))
        configs[name] = ProviderConfig(
            name=name,
            model_class=model_class,
            api_key_param=prov.get("api_key_param", "api_key"),
            max_tokens_param=prov.get("max_tokens_param", "max_tokens"),
            detection_patterns=tuple(prov.get("detection_patterns", [name])),
            default_model=prov.get("default_model", ""),
            models_endpoint=prov.get("models_endpoint", ""),
            models_header_fn=header_fn,
            base_url=prov.get("base_url", ""),
            extra_headers=prov.get("extra_headers"),
            supported_params=prov.get("supported_params"),
        )

    return configs


@functools.cache
def get_provider_configs() -> Dict[str, ProviderConfig]:
    """Return PROVIDER_CONFIGS, building on first call (cached thereafter).

    Lazy build defers the LangChain agent-class imports until the first
    AI agent execution — saves ~1.0s of cold-start time on the
    chat-only path used by ``execute_chat()``.
    """
    return _build_provider_configs()


def detect_provider_from_model(model: str) -> str:
    """Detect AI provider from model name using registry patterns."""
    model_lower = model.lower()
    for provider_name, config in get_provider_configs().items():
        if any(pattern in model_lower for pattern in config.detection_patterns):
            return provider_name
    return "openai"  # default


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
    if provider in ("openrouter", "ollama", "lmstudio"):
        return True
    config = get_provider_configs().get(provider)
    if not config:
        return True
    model_lower = model.lower()
    return any(pattern in model_lower for pattern in config.detection_patterns)


def get_default_model(provider: str) -> str:
    """Get default model for a provider from JSON config."""
    config = get_provider_configs().get(provider)
    return config.default_model if config else "gpt-4o-mini"


async def get_default_model_async(provider: str, database) -> str:
    """Get default model for a provider, checking database first then JSON config.

    Priority: database user setting > JSON config file > fallback
    """
    # Check database for user-configured default
    if database:
        try:
            db_defaults = await database.get_provider_defaults(provider)
            if db_defaults and db_defaults.get("default_model"):
                return db_defaults["default_model"]
        except Exception as e:
            logger.warning(f"Failed to get DB defaults for {provider}: {e}")

    # Fall back to JSON config
    return get_default_model(provider)


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


def filter_empty_messages(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Filter out messages with empty content to prevent API errors.

    This is a standardized utility that handles empty message filtering for all
    AI providers (OpenAI, Anthropic/Claude, Google Gemini, and future providers).

    Different providers have different sensitivities:
    - Gemini: Emits "HumanMessage with empty content was removed" warning
    - Claude/Anthropic: Throws errors for empty HumanMessage content
    - OpenAI: Generally tolerant but empty messages waste tokens

    This filter preserves:
    - ToolMessage: Always kept (contains tool execution results)
    - AIMessage with tool_calls: Kept even if content empty (tool calls are content)
    - SystemMessage: Kept only if has non-empty content
    - HumanMessage/others: Filtered if content is empty

    Args:
        messages: Sequence of LangChain BaseMessage objects

    Returns:
        Filtered list of messages with empty content removed
    """
    from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

    filtered = []

    for m in messages:
        # ToolMessage - always keep (contains tool execution results)
        if isinstance(m, ToolMessage):
            filtered.append(m)
            continue

        # AIMessage with tool_calls - keep even if content is empty
        # (the tool calls themselves are the meaningful content)
        if isinstance(m, AIMessage) and hasattr(m, "tool_calls") and m.tool_calls:
            filtered.append(m)
            continue

        # SystemMessage - keep only if has non-empty content
        if isinstance(m, SystemMessage):
            if hasattr(m, "content") and m.content and str(m.content).strip():
                filtered.append(m)
            continue

        # HumanMessage and other message types - filter out empty content
        if hasattr(m, "content"):
            content = m.content

            # Handle list content format (Gemini returns [{"type": "text", "text": "..."}])
            if isinstance(content, list):
                has_content = any(
                    (isinstance(block, dict) and block.get("text", "").strip()) or (isinstance(block, str) and block.strip())
                    for block in content
                )
                if has_content:
                    filtered.append(m)

            # Handle string content (most common)
            elif isinstance(content, str) and content.strip():
                filtered.append(m)

            # Handle other non-empty content types (keep if truthy)
            elif content:
                filtered.append(m)
        else:
            # Message without content attr - keep it (might be special message type)
            filtered.append(m)

    return filtered


def extract_thinking_from_response(response) -> tuple:
    """Extract text and thinking content from LLM response.

    Handles multiple formats:
    - LangChain content_blocks API (Claude, Gemini)
    - OpenAI responses/v1 format (content list with reasoning blocks containing summary)
    - Groq additional_kwargs.reasoning_content
    - Raw string content

    Returns:
        Tuple of (text_content: str, thinking_content: Optional[str])
    """
    text_parts = []
    thinking_parts = []

    logger.debug(f"[extract_thinking] Starting extraction, response type: {type(response).__name__}")
    logger.debug(
        f"[extract_thinking] has content_blocks: {hasattr(response, 'content_blocks')}, value: {getattr(response, 'content_blocks', None)}"
    )
    logger.debug(f"[extract_thinking] has content: {hasattr(response, 'content')}, type: {type(getattr(response, 'content', None))}")
    logger.debug(
        f"[extract_thinking] has additional_kwargs: {hasattr(response, 'additional_kwargs')}, value: {getattr(response, 'additional_kwargs', None)}"
    )
    logger.debug(
        f"[extract_thinking] has response_metadata: {hasattr(response, 'response_metadata')}, keys: {list(getattr(response, 'response_metadata', {}).keys()) if hasattr(response, 'response_metadata') else None}"
    )

    # Use content_blocks API (LangChain 1.0+) for Claude/Gemini
    if hasattr(response, "content_blocks") and response.content_blocks:
        for block in response.content_blocks:
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "reasoning":
                    thinking_parts.append(block.get("reasoning", ""))
                elif block_type == "thinking":
                    thinking_parts.append(block.get("thinking", ""))
                elif block_type == "text":
                    text_parts.append(block.get("text", ""))

    # Check additional_kwargs for reasoning_content (Groq, older OpenAI responses)
    if not thinking_parts and hasattr(response, "additional_kwargs"):
        reasoning = response.additional_kwargs.get("reasoning_content")
        if reasoning:
            thinking_parts.append(reasoning)

    # Check response_metadata for OpenAI o-series reasoning (responses/v1 format)
    # The output array contains reasoning items with summaries
    if not thinking_parts and hasattr(response, "response_metadata"):
        metadata = response.response_metadata
        output = metadata.get("output", [])
        if isinstance(output, list):
            for item in output:
                if isinstance(item, dict) and item.get("type") == "reasoning":
                    summary = item.get("summary", [])
                    if isinstance(summary, list):
                        for s in summary:
                            if isinstance(s, dict):
                                # Handle both summary_text and text types
                                text = s.get("text", "")
                                if text:
                                    thinking_parts.append(text)
                            elif isinstance(s, str):
                                thinking_parts.append(s)

    # Check raw content for OpenAI responses/v1 format and other list formats
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, str):
            if not text_parts:
                text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "text" or block_type == "output_text":
                        # Handle both 'text' and 'output_text' (responses/v1 format)
                        if not text_parts:  # Only add if not already extracted
                            text_parts.append(block.get("text", ""))
                    elif block_type == "reasoning":
                        # OpenAI responses/v1 format: reasoning block with summary array
                        # Format: {"type": "reasoning", "summary": [{"type": "text", "text": "..."}, {"type": "summary_text", "text": "..."}]}
                        summary = block.get("summary", [])
                        if isinstance(summary, list):
                            for s in summary:
                                if isinstance(s, dict):
                                    s_type = s.get("type", "")
                                    if s_type in ("text", "summary_text"):
                                        thinking_parts.append(s.get("text", ""))
                                elif isinstance(s, str):
                                    thinking_parts.append(s)
                        elif isinstance(summary, str):
                            thinking_parts.append(summary)
                        # Also check direct reasoning field
                        if block.get("reasoning"):
                            thinking_parts.append(block.get("reasoning", ""))
                    elif block_type == "thinking":
                        thinking_parts.append(block.get("thinking", ""))
                elif isinstance(block, str) and not text_parts:
                    text_parts.append(block)

    text = "\n".join(filter(None, text_parts))
    thinking = "\n".join(filter(None, thinking_parts)) if thinking_parts else None

    logger.debug(f"[extract_thinking] Final text_parts: {text_parts}")
    logger.debug(f"[extract_thinking] Final thinking_parts: {thinking_parts}")
    logger.debug(
        f"[extract_thinking] Returning text={repr(text[:100] if text else None)}, thinking={repr(thinking[:100] if thinking else None)}"
    )

    return text, thinking


async def _run_agent_loop(
    chat_model,
    initial_messages: List[BaseMessage],
    *,
    tools: Optional[List[Any]] = None,
    tool_executor: Optional[Callable] = None,
    max_iterations: int = 500,
    progress_callback: Optional[Callable[[int], Any]] = None,
    rebind_from_operations: Optional[Callable[[List[Dict[str, Any]]], Awaitable[List[Any]]]] = None,
) -> Dict[str, Any]:
    """Drive an LLM agent loop with optional tool calling.

    Plain-async ``while iteration < max:`` loop. Each iteration:

    1. Invoke ``chat_model.ainvoke(filtered_messages)`` and append the
       returned assistant message verbatim. Appending the message object
       itself preserves Gemini ``thought_signature``, Anthropic cache
       markers, OpenAI ``reasoning_content``, etc.
    2. Extract thinking content; accumulate across iterations with the
       ``--- Iteration N ---`` separator.
    3. If the response carries ``tool_calls``, dispatch each via the
       supplied ``tool_executor`` and append a ``ToolMessage`` per call.
       Executor failures get surfaced to the model as an error JSON so
       the loop can recover rather than abort.
    4. If no tool calls, return.

    On hitting ``max_iterations``, append a terminal ``AIMessage`` with
    a truncation note so downstream extraction has a usable response.

    ``progress_callback(iteration)`` is awaited at the top of each turn
    so callers can drive per-iteration broadcasts (e.g. the FE iteration
    badge).

    Returns ``{messages, iteration, thinking_content, truncated}``.
    ``messages`` is the full accumulated list (system + history +
    interleaved AI / Tool messages); callers extract the last AIMessage
    as the final response.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    # Local mutable tools list so canvas-mutating tools (agentBuilder)
    # can extend the bound surface mid-loop via ``rebind_from_operations``.
    current_tools: List[Any] = list(tools or [])
    model = chat_model.bind_tools(current_tools) if current_tools else chat_model
    messages: List[BaseMessage] = list(initial_messages)
    thinking_accumulated = ""
    iteration = 0

    for iteration in range(1, max_iterations + 1):
        if progress_callback is not None:
            try:
                await progress_callback(iteration)
            except Exception as e:
                logger.debug(f"[Agent loop] progress_callback raised: {e}")

        filtered = filter_empty_messages(messages)
        response = await model.ainvoke(filtered)
        messages.append(response)

        _, new_thinking = extract_thinking_from_response(response)
        if new_thinking:
            if thinking_accumulated:
                thinking_accumulated = f"{thinking_accumulated}\n\n--- Iteration {iteration} ---\n{new_thinking}"
            else:
                thinking_accumulated = new_thinking

        # Gemini occasionally returns a blocked / safety-stopped response;
        # surface the cause so operators can spot it in the logs.
        if hasattr(response, "response_metadata"):
            meta = response.response_metadata or {}
            if meta.get("finish_reason") == "SAFETY":
                logger.warning("[Agent loop] Gemini response blocked by safety filters")
            if meta.get("block_reason"):
                logger.warning(f"[Agent loop] Gemini block reason: {meta.get('block_reason')}")

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            return {
                "messages": messages,
                "iteration": iteration,
                "thinking_content": thinking_accumulated or None,
                "truncated": False,
            }

        if tool_executor is None:
            logger.warning(
                "[Agent loop] LLM emitted %d tool_call(s) but no tool_executor " "configured; treating response as final",
                len(tool_calls),
            )
            return {
                "messages": messages,
                "iteration": iteration,
                "thinking_content": thinking_accumulated or None,
                "truncated": False,
            }

        iteration_new_tools: List[Any] = []
        for call in tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("args", {}) or {}
            tool_id = call.get("id", "")
            try:
                result = await tool_executor(tool_name, tool_args)
            except Exception as e:
                logger.error(f"[Agent loop] Tool {tool_name!r} raised: {e}")
                result = {"error": str(e)}

            # Canvas-mutation rebind: any tool can return an ``operations``
            # field (workflow_ops protocol). When the agent loop has a
            # ``rebind_from_operations`` callback wired AND the toggle is
            # on, build new StructuredTools off the ops list so the LLM
            # can call them in the very next iteration without a restart.
            if (
                rebind_from_operations is not None
                and isinstance(result, dict)
                and result.get("operations")
            ):
                try:
                    added = await rebind_from_operations(result["operations"])
                    if added:
                        iteration_new_tools.extend(added)
                except Exception as exc:  # noqa: BLE001 — defensive; rebind is opt-in
                    logger.warning(
                        "[Agent loop] rebind_from_operations raised: %s", exc, exc_info=True
                    )

            messages.append(
                ToolMessage(
                    content=json.dumps(result, default=str),
                    tool_call_id=tool_id,
                    name=tool_name,
                )
            )

        if iteration_new_tools:
            current_tools.extend(iteration_new_tools)
            model = chat_model.bind_tools(current_tools)
            logger.info(
                "[Agent loop] rebound %d tool(s) after canvas mutation (total bound=%d)",
                len(iteration_new_tools),
                len(current_tools),
            )

    # Loop exited without returning -- hit max_iterations. Append a
    # terminal AIMessage so downstream extraction (and post-loop token
    # tracking / memory persistence) still have a usable state.
    messages.append(
        AIMessage(
            content=(
                f"[Recursion limit reached: {max_iterations} iterations. "
                f"Adjust agent.recursion_limit in llm_defaults.json or "
                f"simplify the task.]"
            )
        )
    )
    logger.warning(f"[Agent loop] max_iterations hit ({max_iterations}); " f"returning partial response with {len(messages)} message(s)")
    return {
        "messages": messages,
        "iteration": iteration,
        "thinking_content": thinking_accumulated or None,
        "truncated": True,
    }


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
    if not skill_data:
        return "", False

    from services.skill_loader import get_skill_loader

    skill_loader = get_skill_loader()
    skill_loader.scan_skills()

    personality_blocks = []
    non_personality_names = []

    for skill_info in skill_data:
        skill_name = skill_info.get("skill_name") or skill_info.get("node_type", "").replace("Skill", "-skill").lower()
        if skill_name.endswith("skill") and "-" not in skill_name:
            skill_name = skill_name[:-5] + "-skill"

        if skill_name.endswith("-personality"):
            instructions = skill_info.get("parameters", {}).get("instructions", "")
            if instructions:
                personality_blocks.append(instructions)
                logger.debug(f"{log_prefix} Personality skill injected (full): {skill_name}")
            else:
                logger.warning(f"{log_prefix} Personality skill {skill_name} has no instructions")
        else:
            non_personality_names.append(skill_name)
            logger.debug(f"{log_prefix} Skill detected: {skill_name}")

    parts = []

    for block in personality_blocks:
        parts.append(block)

    if non_personality_names:
        registry_prompt = skill_loader.get_registry_prompt(non_personality_names)
        if registry_prompt:
            parts.append(registry_prompt)

    if parts:
        logger.debug(
            f"{log_prefix} Enhanced system message: {len(personality_blocks)} personality, {len(non_personality_names)} standard skills"
        )

    return "\n\n".join(parts), len(personality_blocks) > 0


class AIService:
    """AI model service for LangChain operations."""

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

        # LangChain standard: use content_blocks property for typed content extraction
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

        Extracts usage_metadata from LangChain AIMessage and tracks it
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

        # Extract usage_metadata from AIMessage (LangChain standardizes this)
        usage = None
        if hasattr(ai_response, "usage_metadata") and ai_response.usage_metadata:
            usage = ai_response.usage_metadata
        elif hasattr(ai_response, "response_metadata"):
            # Fallback: check response_metadata for usage info
            meta = ai_response.response_metadata
            if "usage" in meta:
                usage = meta["usage"]
            elif "token_usage" in meta:
                usage = meta["token_usage"]

        if not usage:
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

    def create_model(
        self,
        provider: str,
        api_key: str,
        model: str,
        temperature: float,
        max_tokens: int,
        thinking: Optional[ThinkingConfig] = None,
        proxy_url: Optional[str] = None,
    ):
        """Create LangChain model instance using provider registry.

        Args:
            provider: AI provider name (openai, anthropic, gemini, groq, openrouter)
            api_key: Provider API key
            model: Model name/ID
            temperature: Sampling temperature
            max_tokens: Maximum response tokens
            thinking: Optional thinking/reasoning configuration
            proxy_url: Optional proxy URL (Ollama-style auth delegation)

        Returns:
            Configured LangChain chat model instance
        """
        from langchain_openai import ChatOpenAI

        config = get_provider_configs().get(provider)
        if not config:
            # Provide helpful error for Cerebras if import failed
            if provider == "cerebras" and not CEREBRAS_AVAILABLE:
                error_msg = f"Cerebras provider not available: {CEREBRAS_IMPORT_ERROR or 'langchain-cerebras package not installed'}"
                logger.error(error_msg)
                raise ValueError(error_msg)
            raise ValueError(f"Unsupported provider: {provider}")

        # Strip [FREE] prefix if present (added by OpenRouter model list for display)
        if model.startswith("[FREE] "):
            model = model[7:]

        # Strip owner prefix for non-OpenRouter providers (e.g. "openai/gpt-oss-120b" → "gpt-oss-120b")
        if provider != "openrouter" and "/" in model:
            model = model.split("/", 1)[-1]

        # Build kwargs dynamically from registry config
        kwargs = {config.api_key_param: api_key, "model": model, "temperature": temperature, config.max_tokens_param: max_tokens}

        # Agent Platform / Vertex Express keys ("AQ." prefix) route the same
        # ChatGoogleGenerativeAI to the Vertex backend — billed to the key's
        # GCP project instead of personal AI Studio credits. The library
        # handles endpoint construction and key transport.
        if provider == "gemini" and is_vertex_express_key(api_key):
            kwargs["vertexai"] = True
            if proxy_url:
                logger.warning("[AI] gemini: proxy_url ignored in Vertex AI mode")
                proxy_url = None

        # Proxy mode: route through local proxy that handles auth (Ollama pattern)
        # Proxy URL stored as {provider}_proxy credential
        if proxy_url:
            kwargs["base_url"] = proxy_url
            kwargs[config.api_key_param] = "ollama"  # Ollama-style token
            logger.info(f"[AI] Using proxy for {provider}: {proxy_url}")
        # OpenAI-compatible providers: apply base_url + pass only supported params
        elif config.base_url and config.model_class == ChatOpenAI and provider != "openai":
            kwargs["base_url"] = config.base_url
            if config.extra_headers:
                kwargs["default_headers"] = config.extra_headers
            # LangChain ChatOpenAI converts max_tokens -> max_completion_tokens (OpenAI-specific).
            # Non-OpenAI providers reject this, so pass supported params via extra_body.
            del kwargs[config.max_tokens_param]
            extra_body: Dict[str, Any] = {"max_tokens": max_tokens}
            if config.supported_params:
                if "temperature" not in config.supported_params:
                    kwargs.pop("temperature", None)
                if "frequency_penalty" not in config.supported_params:
                    kwargs.pop("frequency_penalty", None)
                if "presence_penalty" not in config.supported_params:
                    kwargs.pop("presence_penalty", None)
            # Handle provider-specific constraints from llm_defaults.json
            prov_json = _LLM_DEFAULTS.get("providers", {}).get(provider, {})

            # Disable thinking for models that have it ON by default (e.g. kimi-k2.5)
            default_on = prov_json.get("thinking_default_on", [])
            if default_on and any(model.startswith(m) for m in default_on):
                if not (thinking and thinking.enabled):
                    extra_body["thinking"] = {"type": "disabled"}

            # Apply fixed temperature per model (e.g. kimi-k2.5 requires exactly 0.6)
            fixed_temps = prov_json.get("fixed_temperature", {})
            for prefix, fixed_temp in fixed_temps.items():
                if model.startswith(prefix):
                    kwargs["temperature"] = fixed_temp
                    break

            kwargs["extra_body"] = extra_body

        # Apply thinking/reasoning configuration using model registry
        # The registry determines thinking_type based on model metadata
        from services.model_registry import get_model_registry

        registry = get_model_registry()

        # Reasoning models (listed in ``reasoning_models`` per provider in
        # llm_defaults.json) do not support the ``temperature`` parameter —
        # the API rejects requests that send any value, including 1. Drop
        # it entirely rather than pass an explicit value.
        if registry.is_reasoning_model(model, provider):
            if "temperature" in kwargs:
                logger.info(f"[AI] Reasoning model '{model}': omitting temperature param")
                kwargs.pop("temperature", None)

        if thinking and thinking.enabled:
            thinking_type = registry.get_thinking_type(model, provider)

            if thinking_type == "budget":
                # Claude, Gemini, Cerebras: budget_tokens approach
                budget = max(1024, thinking.budget)
                if provider == "anthropic":
                    # Claude: max_tokens must be > budget_tokens
                    if max_tokens <= budget:
                        kwargs[config.max_tokens_param] = budget + max(1024, max_tokens)
                        logger.info(
                            f"[AI] Claude thinking: adjusted max_tokens from {max_tokens} to {kwargs[config.max_tokens_param]} (budget={budget})"
                        )
                    kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
                    kwargs["temperature"] = 1
                elif provider == "gemini":
                    kwargs["thinking_budget"] = thinking.budget
                    kwargs["include_thoughts"] = True
                    logger.info(f"[AI] Gemini thinking: budget={thinking.budget}")
                elif provider == "cerebras":
                    kwargs["thinking_budget"] = thinking.budget
                    logger.info(f"[AI] Cerebras thinking: budget={thinking.budget}")
            elif thinking_type == "effort":
                # OpenAI o-series: reasoning_effort parameter
                kwargs["reasoning_effort"] = thinking.effort
                kwargs["temperature"] = 1
                logger.info(f"[AI] OpenAI reasoning: effort={thinking.effort}, temperature=1")
            elif thinking_type == "format":
                # Groq Qwen/QwQ: reasoning_format parameter
                format_val = thinking.format if thinking.format in ("parsed", "hidden") else "parsed"
                kwargs["reasoning_format"] = format_val
            elif thinking_type == "none":
                logger.warning(f"[AI] Model '{model}' ({provider}) may not support thinking mode")

        # Resolve lazy-loaded model class (gemini)
        model_class = config.model_class or _get_google_genai_class()
        return model_class(**kwargs)

    def _get_curated_models(self, provider: str) -> List[str]:
        """Get curated model list from llm_defaults.json for a provider.

        Returns model IDs from max_output_tokens keys (excluding _default),
        preserving config order. Returns empty list if provider has no curated list.
        """
        provider_cfg = _LLM_DEFAULTS.get("providers", {}).get(provider, {})
        max_tokens_map = provider_cfg.get("max_output_tokens", {})
        return [m for m in max_tokens_map if m != "_default"]

    async def fetch_models(self, provider: str, api_key: str) -> List[str]:
        """Fetch available models from a provider via ``ChatUnifier``.

        The unifier handles ``{provider}_proxy`` resolution, typed-SDK
        exception translation, and the JSON-driven ``incompatible_models``
        filter. All 12 providers (4 dedicated + 8 OpenAI-compat
        including groq + cerebras) route through here — Phase D removed
        the legacy LangChain fallback path.

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

            # Extract parameters with camelCase/snake_case support for LangChain
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

            if not api_key:
                raise ValueError("API key is required")

            # Validate prompt is not empty (prevents wasted API calls for all providers)
            if not is_valid_message_content(prompt):
                raise ValueError("Prompt cannot be empty")

            # Determine provider from node_type (more reliable than model name detection)
            from constants import detect_ai_provider

            provider = detect_ai_provider(node_type, flattened)

            # Some APIs return owner-prefixed model IDs (e.g. "openai/gpt-oss-120b")
            # but chat API expects flat IDs — strip prefix for non-OpenRouter providers
            if provider != "openrouter" and "/" in model:
                model = model.split("/", 1)[-1]

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
            # The LangChain fallback for groq + cerebras was deleted in
            # Phase D — both providers now register through the
            # OpenAI-compat path in ``services.llm.providers._compat``.
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
        """Execute AI Agent via the plain-async agent loop.

        Drives :func:`_run_agent_loop` with the LangChain chat model returned by
        :meth:`create_model`. Each iteration: invoke the model, dispatch any
        ``tool_calls`` through the supplied closure, append ``ToolMessage`` results,
        loop until a final response (or the configured recursion limit hits).

        Features:
        - Tool calling via ``chat_model.bind_tools`` + the shared tool dispatcher
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
        from langchain_core.messages import HumanMessage, SystemMessage

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
                    level=flattened.get("thinking_level", "medium"),
                    format=flattened.get("reasoning_format", "parsed"),
                )
                logger.debug(f"[Agent] Thinking enabled: budget={thinking_config.budget}, effort={thinking_config.effort}")

            temperature = _resolve_temperature(flattened, model, provider, bool(thinking_config and thinking_config.enabled))

            # Broadcast: Initializing model
            await broadcast_status("initializing", {"message": f"Initializing {provider} model...", "provider": provider, "model": model})

            # Check for proxy URL (stored as {provider}_proxy credential)
            proxy_url = await self.auth.get_api_key(f"{provider}_proxy")

            # Create LLM using the provider from node configuration
            logger.debug(f"[Agent] Creating {provider} model: {model}")
            chat_model = self.create_model(provider, api_key, model, temperature, max_tokens, thinking_config, proxy_url)

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
            initial_messages: List[BaseMessage] = []

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
                    store = _get_memory_vector_store(session_id)
                    if store:
                        try:
                            k = memory_data.get("retrieval_count", 3)
                            docs = store.similarity_search(prompt, k=k)
                            if docs:
                                context = "\n---\n".join(d.page_content for d in docs)
                                initial_messages.append(SystemMessage(content=f"Relevant past context:\n{context}"))
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
            initial_messages.append(HumanMessage(content=prompt))

            # Build tools if provided
            tools = []
            tool_configs = {}

            if tool_data:
                await broadcast_status("building_tools", {"message": f"Building {len(tool_data)} tool(s)...", "tool_count": len(tool_data)})

                for tool_info in tool_data:
                    tool, config = await self._build_tool_from_node(tool_info)
                    if tool:
                        tools.append(tool)
                        tool_configs[tool.name] = config
                        logger.debug(f"[Agent] Registered tool: name={tool.name}, node_id={config.get('node_id')}")

                logger.debug(f"[Agent] Built {len(tools)} tools")

                # Auto-inject check_delegated_tasks tool when delegation tools present
                if any(name.startswith("delegate_to_") for name in tool_configs):
                    check_info = {
                        "node_type": "_builtin_check_delegated_tasks",
                        "node_id": f"{node_id}_check_tasks",
                        "parameters": {},
                        "label": "Check Delegated Tasks",
                    }
                    check_tool, check_config = await self._build_tool_from_node(check_info)
                    if check_tool:
                        tools.append(check_tool)
                        tool_configs[check_tool.name] = check_config
                        logger.debug("[Agent] Auto-injected check_delegated_tasks tool")

                    # Add delegation guidance to system message
                    delegate_names = [n for n in tool_configs if n.startswith("delegate_to_")]
                    system_message += (
                        "\n\n## Agent Delegation\n"
                        "When delegating to sub-agents, use 'task' for the mission directive "
                        "(role and goal) and 'context' for input data the agent needs to work with.\n"
                        f"Available agents: {', '.join(delegate_names)}"
                    )

            # Now that ``system_message`` is final (skill prompt + delegation
            # guidance both folded in), prepend it as the first message.
            # See the comment at the original ``initial_messages`` declaration
            # for why this can't happen earlier.
            if system_message:
                initial_messages.insert(0, SystemMessage(content=system_message))

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
                    "executing_tool", {"message": f"Executing tool: {tool_name}", "tool_name": tool_name, "tool_args": tool_args}
                )

                # Inject services + graph context so execute_tool can scope its
                # broadcasts and nested agents can execute with their own tools.
                config["workflow_id"] = workflow_id
                config["ai_service"] = self
                config["database"] = self.database
                config["parent_node_id"] = node_id
                # Surface the auto-rebind toggle so agentBuilder's summary
                # text reflects the user's current preference.
                config["auto_rebind_tools"] = auto_rebind_enabled
                if context:
                    config["nodes"] = context.get("nodes", [])
                    config["edges"] = context.get("edges", [])
                    config["workspace_dir"] = context.get("workspace_dir", "")
                    # Stable per-run id so session-keyed tools (browser)
                    # reuse one instance across the agent loop.
                    config["execution_id"] = context.get("execution_id")

                try:
                    result = await execute_tool(tool_name, tool_args, config)

                    await broadcast_status(
                        "tool_completed",
                        {"message": f"Tool completed: {tool_name}", "tool_name": tool_name, "result_preview": str(result)[:100]},
                    )

                    return result

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"[Agent] Tool execution failed: {tool_name}", error=error_msg)

                    # Re-raise to let _run_agent_loop surface the error to the LLM
                    raise

            # Auto-rebind toggle: when the user enables "Auto-Rebind Tools
            # After Canvas Changes" in Settings, canvas-mutating tools
            # (agentBuilder) extend the LLM's bound surface mid-loop. The
            # default is True; lookup failures fall back to True so the
            # feature doesn't silently disable itself on a transient DB hiccup.
            auto_rebind_enabled = True
            user_recursion_limit: Optional[int] = None
            try:
                user_settings = await self.database.get_user_settings()
                if user_settings is not None:
                    auto_rebind_enabled = bool(
                        user_settings.get("auto_rebind_tools_after_canvas_change", True)
                    )
                    _raw_limit = user_settings.get("agent_recursion_limit")
                    if isinstance(_raw_limit, int) and _raw_limit > 0:
                        user_recursion_limit = _raw_limit
            except Exception as exc:  # noqa: BLE001 — defensive read
                logger.debug("[Agent] user_settings read failed: %s", exc)

            async def _rebind_from_operations(operations: List[Dict[str, Any]]) -> List[Any]:
                """Translate workflow_ops add_node ops (component_kind='tool')
                into fresh ``StructuredTool``s via the canonical
                :meth:`_build_tool_from_node` helper. The returned list is
                appended to ``_run_agent_loop``'s ``current_tools`` and
                rebound onto the LLM so the LLM can invoke the new tool in
                its NEXT iteration without a Run-stop-Run cycle.

                Tool configs are folded into the existing ``tool_configs``
                closure so ``tool_executor`` can route the LLM's eventual
                tool_call to the right node.
                """
                from services.node_registry import get_node_class

                new_tools: List[Any] = []
                for op in operations:
                    if op.get("type") != "add_node":
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
                    # tools it never got bound to.
                    _kind = getattr(cls, "component_kind", "")
                    if not (_kind == "tool" or (bool(getattr(cls, "usable_as_tool", False)) and _kind != "model")):
                        continue
                    tool_info = {
                        "node_id": op.get("minted_id") or op.get("client_ref") or f"new_{node_type}",
                        "node_type": node_type,
                        "parameters": op.get("parameters") or {},
                        "label": op.get("label") or node_type,
                    }
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
                    new_tools.append(tool)
                    if tool_config:
                        tool_configs[tool.name] = tool_config
                return new_tools

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

            final_state = await _run_agent_loop(
                chat_model,
                initial_messages,
                tools=tools if tools else None,
                tool_executor=tool_executor if tools else None,
                max_iterations=recursion_limit,
                progress_callback=_emit_progress if broadcaster else None,
                rebind_from_operations=_rebind_from_operations if auto_rebind_enabled else None,
            )

            # Extract the AI response (last message in the accumulated messages)
            all_messages = final_state["messages"]
            ai_response = all_messages[-1] if all_messages else None

            if not ai_response or not hasattr(ai_response, "content"):
                raise ValueError("No response generated from agent")

            # Handle different content formats (Gemini can return list of content blocks)
            raw_content = ai_response.content
            response_content = self._extract_text_content(raw_content, ai_response)
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
                    ai_response=ai_response,
                    all_messages=all_messages,
                    broadcaster=broadcaster,
                    workflow_id=workflow_id,
                    memory_content=memory_data.get("memory_content", "") if memory_data else None,
                    api_key=api_key,
                    memory_node_id=memory_data.get("node_id") if memory_data else None,
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

                # If compaction happened, use compacted summary as base
                if compaction_result and compaction_result.get("success") and compaction_result.get("summary"):
                    # Start fresh with compacted summary, then add current exchange
                    updated_content = compaction_result["summary"]
                    updated_content = _append_to_memory_markdown(updated_content, "human", prompt)
                    updated_content = _append_to_memory_markdown(updated_content, "ai", response_content)
                    logger.info("[Agent Memory] Using compacted summary as new base")
                else:
                    # Normal flow: append to existing memory
                    updated_content = memory_data.get("memory_content", "# Conversation History\n\n*No messages yet.*\n")
                    updated_content = _append_to_memory_markdown(updated_content, "human", prompt)
                    updated_content = _append_to_memory_markdown(updated_content, "ai", response_content)

                # Trim to window size, archive removed to vector DB
                window_size = memory_data.get("window_size", 10)
                updated_content, removed_texts = _trim_markdown_window(updated_content, window_size)

                # Store removed messages in long-term vector DB
                if removed_texts and memory_data.get("long_term_enabled"):
                    store = _get_memory_vector_store(session_id)
                    if store:
                        try:
                            store.add_texts(removed_texts)
                            logger.info(f"[Agent Memory] Archived {len(removed_texts)} messages to long-term store")
                        except Exception as e:
                            logger.warning(f"[Agent Memory] Failed to archive to vector store: {e}")

                # Save updated markdown to node parameters. Schema-
                # canonical key is snake_case; drop any pre-migration
                # camelCase mirror so the saved params stay clean.
                memory_node_id = memory_data["node_id"]
                current_params = await self.database.get_node_parameters(memory_node_id) or {}
                current_params["memory_content"] = updated_content
                current_params.pop("memoryContent", None)
                await self.database.save_node_parameters(memory_node_id, current_params)
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

            log_execution_time(logger, "ai_agent_loop", start_time, time.time())
            log_api_call(logger, provider, model, "agent", True)

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "aiAgent",
                "result": result,
                "execution_time": time.time() - start_time,
            }

        except openai.OpenAIError as e:
            # See execute_chat for the rationale — typed SDK errors are
            # user-correctable and re-raised as NodeUserError so BaseNode
            # logs at WARN without a traceback.
            log_api_call(logger, provider, model, "agent", False, error=str(e))
            raise NodeUserError(str(e)) from e

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
        from langchain_core.messages import HumanMessage, SystemMessage

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
            # This supports ALL tool types: calculatorTool, currentTimeTool, duckduckgoSearch, androidTool, httpRequest
            all_tools = []
            tool_node_configs = {}  # Map tool name to node config (same as AI Agent's tool_configs)
            if tool_data:
                await broadcast_status("building_tools", {"message": f"Building {len(tool_data)} tool(s)...", "tool_count": len(tool_data)})

                for tool_info in tool_data:
                    # Use AI Agent's _build_tool_from_node for all tool types
                    tool, config = await self._build_tool_from_node(tool_info)
                    if tool:
                        all_tools.append(tool)
                        tool_node_configs[tool.name] = config
                        logger.debug(
                            f"[ChatAgent] Built tool: {tool.name} (type={config.get('node_type')}, node_id={config.get('node_id')})"
                        )

                logger.debug(f"[ChatAgent] Built {len(all_tools)} tools from tool_data")

                # Auto-inject check_delegated_tasks tool when delegation tools present
                if any(name.startswith("delegate_to_") for name in tool_node_configs):
                    check_info = {
                        "node_type": "_builtin_check_delegated_tasks",
                        "node_id": f"{node_id}_check_tasks",
                        "parameters": {},
                        "label": "Check Delegated Tasks",
                    }
                    check_tool, check_config = await self._build_tool_from_node(check_info)
                    if check_tool:
                        all_tools.append(check_tool)
                        tool_node_configs[check_tool.name] = check_config
                        logger.debug("[ChatAgent] Auto-injected check_delegated_tasks tool")

                    # Add delegation guidance to system message
                    delegate_names = [n for n in tool_node_configs if n.startswith("delegate_to_")]
                    system_message += (
                        "\n\n## Agent Delegation\n"
                        "When delegating to sub-agents, use 'task' for the mission directive "
                        "(role and goal) and 'context' for input data the agent needs to work with.\n"
                        f"Available agents: {', '.join(delegate_names)}"
                    )

            logger.debug(f"[ChatAgent] Total tools available: {len(all_tools)}")
            # Debug: log all tool schemas to verify they're correct
            for t in all_tools:
                schema = t.get_input_schema().model_json_schema()
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
                    level=flattened.get("thinking_level", "medium"),
                    format=flattened.get("reasoning_format", "parsed"),
                )

            temperature = _resolve_temperature(flattened, model, provider, bool(thinking_config and thinking_config.enabled))

            # Broadcast: Initializing
            await broadcast_status("initializing", {"message": f"Initializing {provider} model...", "provider": provider, "model": model})

            # Check for proxy URL (stored as {provider}_proxy credential)
            proxy_url = await self.auth.get_api_key(f"{provider}_proxy")

            # Create chat model
            chat_model = self.create_model(provider, api_key, model, temperature, max_tokens, thinking_config, proxy_url)

            # Build messages
            messages: List[BaseMessage] = []
            if system_message:
                messages.append(SystemMessage(content=system_message))

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
                    store = _get_memory_vector_store(session_id)
                    if store:
                        try:
                            k = memory_data.get("retrieval_count", 3)
                            docs = store.similarity_search(prompt, k=k)
                            if docs:
                                context = "\n---\n".join(d.page_content for d in docs)
                                messages.append(SystemMessage(content=f"Relevant past context:\n{context}"))
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
            messages.append(HumanMessage(content=prompt))

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

                    # Inject services + graph context so execute_tool can scope its
                    # broadcasts and nested agents can execute with their own tools.
                    config["workflow_id"] = workflow_id
                    config["ai_service"] = self
                    config["database"] = self.database
                    config["parent_node_id"] = node_id
                    config["auto_rebind_tools"] = auto_rebind_enabled
                    if context:
                        config["nodes"] = context.get("nodes", [])
                        config["edges"] = context.get("edges", [])
                        config["workspace_dir"] = context.get("workspace_dir", "")
                        # Stable per-run id so session-keyed tools (browser)
                        # reuse one instance across the agent loop.
                        config["execution_id"] = context.get("execution_id")

                    try:
                        result = await execute_tool(tool_name, tool_args, config)
                        logger.debug(f"[ChatAgent] Tool executed successfully: {tool_name}")
                        return result
                    except Exception as e:
                        logger.error(f"[ChatAgent] Tool execution failed: {tool_name}", error=str(e))
                        return {"error": str(e)}

                # Auto-rebind toggle + recursion_limit override: same
                # machinery as ``execute_agent``.
                auto_rebind_enabled = True
                user_recursion_limit: Optional[int] = None
                try:
                    user_settings = await self.database.get_user_settings()
                    if user_settings is not None:
                        auto_rebind_enabled = bool(
                            user_settings.get("auto_rebind_tools_after_canvas_change", True)
                        )
                        _raw_limit = user_settings.get("agent_recursion_limit")
                        if isinstance(_raw_limit, int) and _raw_limit > 0:
                            user_recursion_limit = _raw_limit
                except Exception as exc:  # noqa: BLE001 — defensive read
                    logger.debug("[ChatAgent] user_settings read failed: %s", exc)

                async def _rebind_from_operations(operations: List[Dict[str, Any]]) -> List[Any]:
                    """Mirror of execute_agent._rebind_from_operations — see that
                    function for the contract. We can't dedupe cleanly because
                    the two agent paths capture different closure variables
                    (``tool_configs`` vs ``tool_node_configs``)."""
                    from services.node_registry import get_node_class

                    new_tools: List[Any] = []
                    for op in operations:
                        if op.get("type") != "add_node":
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
                        # pythonExecutor etc.
                        _kind = getattr(cls, "component_kind", "")
                        if not (_kind == "tool" or (bool(getattr(cls, "usable_as_tool", False)) and _kind != "model")):
                            continue
                        tool_info = {
                            "node_id": op.get("minted_id") or op.get("client_ref") or f"new_{node_type}",
                            "node_type": node_type,
                            "parameters": op.get("parameters") or {},
                            "label": op.get("label") or node_type,
                        }
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
                        new_tools.append(tool)
                        if tool_config:
                            tool_node_configs[tool.name] = tool_config
                    return new_tools

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

                final_state = await _run_agent_loop(
                    chat_model,
                    messages,
                    tools=all_tools,
                    tool_executor=chat_tool_executor,
                    max_iterations=recursion_limit,
                    progress_callback=_emit_progress if broadcaster else None,
                    rebind_from_operations=_rebind_from_operations if auto_rebind_enabled else None,
                )

                # Extract response
                all_messages = final_state["messages"]
                ai_response = all_messages[-1] if all_messages else None

                if not ai_response or not hasattr(ai_response, "content"):
                    raise ValueError("No response generated from agent")

                raw_content = ai_response.content
                response_content = self._extract_text_content(raw_content, ai_response)
                iterations = final_state.get("iteration", 1)
                thinking_content = final_state.get("thinking_content")
            else:
                # Simple invoke without tools
                response = await chat_model.ainvoke(messages)

                # Extract response content
                raw_content = response.content
                response_content = self._extract_text_content(raw_content, response)

                # Extract thinking content if available
                _, thinking_content = extract_thinking_from_response(response)

            logger.info(f"[ChatAgent] Response generated, thinking={'yes' if thinking_content else 'no'}, iterations={iterations}")

            # Track token usage if memory connected (for compaction service)
            # Use ai_response for tools path, response for simple invoke
            # Also triggers compaction if threshold exceeded
            response_msg = ai_response if all_tools else response
            compaction_result = None
            if session_id and response_msg:
                compaction_result = await self._track_token_usage(
                    session_id=session_id,
                    node_id=node_id,
                    provider=provider,
                    model=model,
                    ai_response=response_msg,
                    all_messages=all_messages if all_tools else messages + [response_msg],
                    broadcaster=broadcaster,
                    workflow_id=workflow_id,
                    memory_content=memory_content,
                    api_key=api_key,
                    memory_node_id=memory_data.get("node_id") if memory_data else None,
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

                # If compaction happened, use compacted summary as base
                if compaction_result and compaction_result.get("success") and compaction_result.get("summary"):
                    updated_content = compaction_result["summary"]
                    updated_content = _append_to_memory_markdown(updated_content, "human", prompt)
                    updated_content = _append_to_memory_markdown(updated_content, "ai", response_content)
                    logger.info("[ChatAgent Memory] Using compacted summary as new base")
                else:
                    # Normal flow: append to existing memory
                    updated_content = memory_content or "# Conversation History\n\n*No messages yet.*\n"
                    updated_content = _append_to_memory_markdown(updated_content, "human", prompt)
                    updated_content = _append_to_memory_markdown(updated_content, "ai", response_content)

                # Trim to window size, archive removed to vector DB
                window_size = memory_data.get("window_size", 10)
                updated_content, removed_texts = _trim_markdown_window(updated_content, window_size)

                # Store removed messages in long-term vector DB
                if removed_texts and memory_data.get("long_term_enabled"):
                    store = _get_memory_vector_store(session_id)
                    if store:
                        try:
                            store.add_texts(removed_texts)
                            logger.info(f"[ChatAgent Memory] Archived {len(removed_texts)} messages to long-term store")
                        except Exception as e:
                            logger.warning(f"[ChatAgent Memory] Failed to archive to vector store: {e}")

                # Save updated markdown to node parameters. Schema-
                # canonical key is snake_case; drop any pre-migration
                # camelCase mirror so the saved params stay clean.
                memory_node_id = memory_data["node_id"]
                current_params = await self.database.get_node_parameters(memory_node_id) or {}
                current_params["memory_content"] = updated_content
                current_params.pop("memoryContent", None)
                await self.database.save_node_parameters(memory_node_id, current_params)
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

        except openai.OpenAIError as e:
            # Typed SDK error — see execute_chat for rationale.
            log_api_call(logger, provider, model, "chat_agent", False, error=str(e))
            raise NodeUserError(str(e)) from e

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

    async def _build_tool_from_node(self, tool_info: Dict[str, Any]) -> tuple:
        """Convert a node configuration into a LangChain StructuredTool.

        Uses database-stored schema as source of truth if available, otherwise
        falls back to dynamic schema generation.

        Tool name + description resolution chain (Wave 12 D5):
          1. DB-stored schema (per-node override via UI)
          2. ``cls.tool_name`` / ``cls.tool_description`` ClassVars on the
             plugin class. ``tool_description`` falls back to
             ``cls.description`` when empty.
          3. ``_PSEUDO_TOOL_FALLBACK`` for built-in / aggregator pseudo-types
             that have no plugin class (``_builtin_check_delegated_tasks``,
             ``androidTool``)
          4. ``node_params.get('tool_name')`` / ``...tool_description``
             (per-node override declared as a Pydantic field on the plugin's
             Params model — e.g. brave_search / serper_search / perplexity).
          5. Last-resort default: ``f"tool_{label}"`` / ``f"Execute {label}"``

        Args:
            tool_info: Dict containing node_id, node_type, parameters, label, connected_services (for androidTool)

        Returns:
            Tuple of (StructuredTool, config_dict) or (None, None) on failure
        """
        from langchain_core.tools import StructuredTool

        # Built-in / aggregator pseudo-types — no plugin class, no ClassVar.
        # These must stay as an explicit fallback dict (Wave 12 D5).
        _PSEUDO_TOOL_FALLBACK = {
            "_builtin_check_delegated_tasks": (
                "check_delegated_tasks",
                "Check status and retrieve results of previously delegated tasks.",
            ),
            "androidTool": (
                "android_device",
                "Control Android device. Available services are determined by connected nodes.",
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

            # Check database for stored schema (source of truth)
            db_schema = await self.database.get_tool_schema(node_id) if node_id else None

            if db_schema:
                # Use database schema as source of truth
                logger.debug(f"[Agent] Using DB schema for tool node {node_id}")
                tool_name = db_schema.get("tool_name", default_tool_name or f"tool_{node_label}")
                tool_description = db_schema.get("tool_description", default_tool_description or f"Execute {node_label}")
                # Use stored connected_services if available (for toolkit nodes)
                if db_schema.get("connected_services"):
                    connected_services = db_schema["connected_services"]
            else:
                # Fall back to dynamic generation from node params
                tool_name = (
                    node_params.get("tool_name") or default_tool_name or f"tool_{node_label}".replace(" ", "_").replace("-", "_").lower()
                )
                tool_description = node_params.get("tool_description") or default_tool_description or f"Execute {node_label} node"

            # For androidTool, enhance description with connected services
            if node_type == "androidTool" and connected_services:
                service_names = [s.get("label") or s.get("service_id", "unknown") for s in connected_services]
                tool_description = f"{tool_description} Connected: {', '.join(service_names)}"

            # For AI Agent nodes, enhance description with child agent's tool capabilities
            # This allows parent agent to know what the child agent can do
            from constants import AI_AGENT_TYPES

            if node_type in AI_AGENT_TYPES:
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

            # Clean tool name (LangChain requires alphanumeric + underscores)
            import re

            tool_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool_name)

            # Build schema based on node type - pass connected_services for androidTool
            # If DB has schema_config, use it to build custom schema, otherwise use dynamic
            schema_params = dict(node_params)
            if connected_services:
                schema_params["connected_services"] = connected_services
            if db_schema and db_schema.get("schema_config"):
                schema_params["db_schema_config"] = db_schema["schema_config"]
            schema = self._get_tool_schema(node_type, schema_params)

            # Create StructuredTool - the func is a placeholder, actual execution via tool_executor
            def placeholder_func(**kwargs):
                return kwargs

            tool = StructuredTool.from_function(name=tool_name, description=tool_description, func=placeholder_func, args_schema=schema)

            # Build config dict - include connected_services for toolkit nodes
            config = {
                "node_type": node_type,
                "node_id": node_id,
                "parameters": node_params,
                "label": node_label,
                "connected_services": connected_services,  # Pass through for execution
            }

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
        from services.node_registry import get_node_class

        plugin_cls = get_node_class(node_type)
        if plugin_cls is not None and hasattr(plugin_cls, "Params"):
            return plugin_cls.Params

        # androidTool aggregator — dynamic schema derived from the set of
        # Android service nodes connected to its toolkit handle. No plugin
        # class, so it stays here.
        if node_type == "androidTool":
            connected_services = params.get("connected_services", [])

            if not connected_services:

                class EmptyAndroidSchema(BaseModel):
                    """Android toolkit with no connected services."""

                    query: str = Field(default="status", description="No Android services connected. Connect Android nodes to the toolkit.")

                return EmptyAndroidSchema

            from nodes.android._dispatcher import SERVICE_ACTIONS

            service_info = []
            for svc in connected_services:
                svc_id = svc.get("service_id") or svc.get("node_type", "unknown")
                actions = SERVICE_ACTIONS.get(svc_id, [])
                action_list = [a["value"] for a in actions] if actions else ["status"]
                service_info.append(f"{svc_id}: {'/'.join(action_list)}")

            services_description = "; ".join(service_info)

            class AndroidToolSchema(BaseModel):
                """Schema for Android device control via connected services."""

                service_id: str = Field(description=f"Service to use. Connected: {services_description}")
                action: str = Field(description="Action to perform (see service list for available actions)")
                parameters: Optional[Dict[str, Any]] = Field(
                    default=None,
                    description="Action parameters. Examples: {package_name: 'com.app'} for app_launcher, {volume: 50} for audio",
                )

            return AndroidToolSchema

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
