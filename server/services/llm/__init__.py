"""LLM service layer -- native provider SDKs, config, protocol types."""

from services.llm.config import (
    ProviderConfig,
    PROVIDER_CONFIGS,
    detect_provider_from_model,
    is_model_valid_for_provider,
    get_default_model,
    get_default_model_async,
    resolve_max_tokens,
    resolve_temperature,
    build_headers,
)
from services.llm.protocol import (
    MESSAGE_WIRE_VERSION,
    NATIVE_MESSAGE_WIRE_VERSIONS,
    SUPPORTED_MESSAGE_WIRE_VERSIONS,
    MessageWireV2,
    ThinkingConfig,
    ToolDef,
    ToolCall,
    ContentBlock,
    Message,
    Usage,
    LLMResponse,
    LLMError,
    LLMErrorCategory,
    LLMProvider,
    message_to_wire,
    message_from_wire,
    messages_to_wire,
    messages_from_wire,
)
from services.llm.messages import is_valid_message_content, filter_empty_messages
from services.llm.schema import compile_tool_schema
from services.llm.registry import (
    ProviderSpec,
    register_provider,
    get_provider,
    all_providers,
    has_provider,
)
from services.llm.unifier import ChatUnifier

# Side-effect import — populates the provider registry by importing
# every provider module (each calls ``register_provider`` at the
# bottom). Must run BEFORE any caller asks the registry for a provider
# or constructs a ``ChatUnifier``.
from services.llm import providers as _providers  # noqa: F401

__all__ = [
    # Config
    "ProviderConfig",
    "PROVIDER_CONFIGS",
    "detect_provider_from_model",
    "is_model_valid_for_provider",
    "get_default_model",
    "get_default_model_async",
    "resolve_max_tokens",
    "resolve_temperature",
    "build_headers",
    # Protocol types
    "MESSAGE_WIRE_VERSION",
    "NATIVE_MESSAGE_WIRE_VERSIONS",
    "SUPPORTED_MESSAGE_WIRE_VERSIONS",
    "MessageWireV2",
    "ThinkingConfig",
    "ToolDef",
    "ToolCall",
    "ContentBlock",
    "Message",
    "Usage",
    "LLMResponse",
    "LLMError",
    "LLMErrorCategory",
    "LLMProvider",
    "message_to_wire",
    "message_from_wire",
    "messages_to_wire",
    "messages_from_wire",
    # Messages
    "is_valid_message_content",
    "filter_empty_messages",
    "compile_tool_schema",
    # Plugin registry + unifier (Phase A — single SERVICE facade)
    "ProviderSpec",
    "register_provider",
    "get_provider",
    "all_providers",
    "has_provider",
    "ChatUnifier",
]
