"""ChatUnifier — the single SERVICE facade for chat-model dispatch.

Reads the ``ProviderRegistry`` to route ``chat`` and ``fetch_models`` calls
to the right provider implementation, translates each provider's typed
SDK exceptions into ``NodeUserError`` at one catch site, and applies the
``incompatible_models`` JSON filter uniformly. ``services/ai.py`` delegates
to this class — there is no per-provider Python anywhere else.

Wired into the DI container once at startup with the parsed
``llm_defaults.json`` + the ``AuthService`` (for ``{provider}_proxy``
credential lookups).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.logging import get_logger
from services.llm.protocol import LLMProvider, LLMResponse, Message, ThinkingConfig, ToolDef
from services.llm.registry import ProviderSpec, get_provider, has_provider
from services.plugin import NodeUserError

if TYPE_CHECKING:
    from services.auth import AuthService

logger = get_logger(__name__)


class ChatUnifier:
    """Single facade for chat-model execution.

    Construction: one instance lives in the DI container, sharing
    ``llm_defaults.json`` + ``AuthService`` with the rest of the backend.

    Public surface: ``chat()``, ``fetch_models()``, ``is_registered()``.
    Everything else is private.
    """

    def __init__(self, defaults: Dict[str, Any], auth_service: "AuthService"):
        self._defaults = defaults
        self._auth = auth_service

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def chat(
        self,
        *,
        provider: str,
        api_key: str,
        messages: List[Message],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking: Optional[ThinkingConfig] = None,
        tools: Optional[List[ToolDef]] = None,
    ) -> LLMResponse:
        """Execute a chat completion against the named provider.

        Raises ``NodeUserError`` on typed SDK failures (bad key, context
        overflow, missing model, server unreachable, …) — every other
        exception flows through unchanged so genuine server bugs keep
        their full traceback via ``BaseNode.execute()`` 's generic
        ``except Exception``.
        """
        spec = get_provider(provider)
        client = await self._build_client(spec, api_key)
        try:
            return await client.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                tools=tools,
            )
        except spec.sdk_exception_types as e:
            raise NodeUserError(str(e)) from e

    async def fetch_models(self, *, provider: str, api_key: str) -> List[str]:
        """List available models from the named provider.

        Applies the JSON-driven ``incompatible_models`` filter
        (``providers.<name>.incompatible_models`` in ``llm_defaults.json``)
        uniformly. An absent key is a no-op — every provider gets the
        filter for free without per-provider Python.
        """
        spec = get_provider(provider)
        client = await self._build_client(spec, api_key)
        try:
            models = await client.fetch_models(api_key)
        except spec.sdk_exception_types as e:
            raise NodeUserError(str(e)) from e
        blocklist = self._incompatible_models(provider)
        if not blocklist:
            return models
        return [m for m in models if m not in blocklist]

    def is_registered(self, provider: str) -> bool:
        """Cheap probe used by callers that want graceful fallback when a
        provider is not wired into the registry.

        Replaces the legacy ``is_native_provider`` function in
        ``services/llm/factory.py`` — the unifier IS the routing layer,
        so registry membership is the source of truth.
        """
        return has_provider(provider)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _build_client(self, spec: ProviderSpec, api_key: str) -> LLMProvider:
        """Instantiate the provider implementation.

        Pulls the user-configured ``{provider}_proxy`` URL from the
        encrypted credentials store (matches the legacy ai.py behavior)
        and merges it with the provider's static ``client_kwargs`` (used
        by OpenAI-compatible providers to pin their ``base_url``).
        """
        proxy_url = await self._auth.get_api_key(f"{spec.name}_proxy")
        return spec.factory(api_key=api_key, proxy_url=proxy_url, **spec.client_kwargs)

    def _incompatible_models(self, provider: str) -> set[str]:
        """Read ``providers.<name>.incompatible_models`` from llm_defaults.json."""
        raw = (
            self._defaults.get("providers", {})
            .get(provider, {})
            .get("incompatible_models")
        )
        return set(raw or ())
