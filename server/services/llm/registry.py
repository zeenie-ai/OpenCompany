"""Provider registry — every LLM provider self-registers here at import time.

Plugin-shape backbone for the chat-model service layer. Each provider module
calls ``register_provider(ProviderSpec(...))`` at top level, and the unifier
reads this registry to dispatch chat / fetch_models calls and to translate
typed SDK exceptions into ``NodeUserError``. No per-provider Python lives
inside ``services/ai.py`` after this layer is wired.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple, Type

from core.logging import get_logger
from services.llm.protocol import LLMProvider
from services.plugin import NodeUserError

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProviderSpec:
    """Declarative spec for a chat-model provider plugin.

    Fields:
        name: registry key — matches the ``provider`` string used by
            ai.py, the chat-model node ``provider`` field, and the
            ``providers.<name>`` block in ``llm_defaults.json``.
        factory: callable returning an ``LLMProvider`` instance.
            Invoked as ``factory(api_key=..., proxy_url=..., **client_kwargs)``.
        sdk_exception_types: tuple of typed SDK error classes raised by
            this provider's underlying SDK. The unifier wraps a single
            ``except spec.sdk_exception_types`` around every call site so
            user-correctable errors surface as ``NodeUserError`` without
            a per-provider branch in ai.py.
        client_kwargs: static keyword arguments passed to ``factory`` on
            every instantiation. Used by OpenAI-compatible providers
            (xai / deepseek / kimi / mistral / ollama / lmstudio / groq /
            cerebras) to pin ``base_url`` from JSON config.
    """

    name: str
    factory: Callable[..., LLMProvider]
    sdk_exception_types: Tuple[Type[Exception], ...]
    client_kwargs: Dict[str, Any] = field(default_factory=dict)


_REGISTRY: Dict[str, ProviderSpec] = {}


def register_provider(spec: ProviderSpec) -> None:
    """Register a provider plugin.

    Called from each provider module's top level on import. The unifier
    expects every registered provider to declare at least one typed SDK
    exception so the wrapper try/except can translate it.

    Re-registration with an identical spec is a no-op (handles uvicorn
    reload cycles). Re-registration with a different spec raises so
    accidental shadowing is loud.
    """
    if not spec.sdk_exception_types:
        raise ValueError(
            f"ProviderSpec({spec.name!r}) declares empty sdk_exception_types. "
            "Every provider must surface its typed SDK error class so the "
            "unifier can translate it into NodeUserError."
        )
    existing = _REGISTRY.get(spec.name)
    if existing is not None and existing != spec:
        raise ValueError(
            f"Provider {spec.name!r} already registered with a different spec. "
            f"Existing: {existing!r}; new: {spec!r}."
        )
    _REGISTRY[spec.name] = spec
    logger.debug("registered LLM provider", provider=spec.name)


def get_provider(name: str) -> ProviderSpec:
    """Look up a provider spec by name.

    Raises ``NodeUserError`` on unknown providers so the failure surfaces
    cleanly through ``BaseNode.execute()`` as a single WARN line — same
    contract as every other user-correctable error in the framework.
    """
    spec = _REGISTRY.get(name)
    if spec is None:
        raise NodeUserError(
            f"Unknown LLM provider: {name!r}. "
            f"Registered providers: {sorted(_REGISTRY)}"
        )
    return spec


def all_providers() -> List[str]:
    """Return sorted list of registered provider names."""
    return sorted(_REGISTRY)


def has_provider(name: str) -> bool:
    """Cheap membership probe — does not raise on miss."""
    return name in _REGISTRY


def _reset_for_tests() -> None:
    """Test-only — drop every registered provider.

    Lets unit tests around the registry control the state instead of
    inheriting the production population. Never call from runtime code.
    """
    _REGISTRY.clear()
