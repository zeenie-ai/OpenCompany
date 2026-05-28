"""LLM service layer -- native provider SDKs, config, protocol types, factory."""

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
    ThinkingConfig,
    ToolDef,
    ToolCall,
    Message,
    Usage,
    LLMResponse,
    LLMProvider,
)
from services.llm.messages import is_valid_message_content, filter_empty_messages
from services.llm.factory import create_provider, is_native_provider, NATIVE_PROVIDERS
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
    "ThinkingConfig",
    "ToolDef",
    "ToolCall",
    "Message",
    "Usage",
    "LLMResponse",
    "LLMProvider",
    # Messages
    "is_valid_message_content",
    "filter_empty_messages",
    # Factory (legacy — replaced by unifier in Phase A3; kept for back-compat)
    "create_provider",
    "is_native_provider",
    "NATIVE_PROVIDERS",
    # Plugin registry + unifier (Phase A — single SERVICE facade)
    "ProviderSpec",
    "register_provider",
    "get_provider",
    "all_providers",
    "has_provider",
    "ChatUnifier",
]
