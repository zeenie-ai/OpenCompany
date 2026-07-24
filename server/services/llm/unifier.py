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

import asyncio
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from core.logging import get_logger
from services.llm.protocol import (
    LLMError,
    LLMErrorCategory,
    LLMProvider,
    LLMResponse,
    Message,
    ThinkingConfig,
    ToolDef,
)
from services.llm.registry import ProviderSpec, get_provider, has_provider
from services.plugin import NodeUserError

if TYPE_CHECKING:
    from services.auth import AuthService

logger = get_logger(__name__)


@dataclass(eq=False)
class _ClientEntry:
    """One cached SDK client plus its in-flight lease count."""

    client: LLMProvider
    leases: int = 0
    retired: bool = False


class ChatUnifier:
    """Single facade for chat-model execution.

    Construction: one instance lives in the DI container, sharing
    ``llm_defaults.json`` + ``AuthService`` with the rest of the backend.

    Public surface: ``chat()``, ``fetch_models()``, ``is_registered()``.
    Everything else is private.
    """

    def __init__(
        self,
        defaults: Dict[str, Any],
        auth_service: "AuthService",
        *,
        client_cache_size: int = 32,
    ):
        self._defaults = defaults
        self._auth = auth_service
        self._client_cache_size = max(0, int(client_cache_size))
        self._client_cache: OrderedDict[str, _ClientEntry] = OrderedDict()
        self._retired_entries: List[_ClientEntry] = []
        self._cache_lock = asyncio.Lock()

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
        sdk_max_retries: int = 2,
        translate_errors: bool = True,
    ) -> LLMResponse:
        """Execute a chat completion against the named provider.

        Raises ``NodeUserError`` on typed SDK failures (bad key, context
        overflow, missing model, server unreachable, …) — every other
        exception flows through unchanged so genuine server bugs keep
        their full traceback via ``BaseNode.execute()`` 's generic
        ``except Exception``.
        """
        spec = get_provider(provider)
        entry: Optional[_ClientEntry] = None
        try:
            entry = await self._acquire_client(
                spec, api_key, sdk_max_retries=sdk_max_retries
            )
            return await entry.client.chat(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                tools=tools,
            )
        except spec.sdk_exception_types as e:
            error = LLMError.from_exception(provider, e)
            if not translate_errors:
                raise error from e
            logger.warning(
                "LLM provider request failed",
                provider=error.provider,
                category=error.category.value,
                retryable=error.retryable,
                status_code=error.status_code,
                provider_code=error.provider_code,
                request_id=error.request_id,
            )
            raise NodeUserError(error.user_message) from error
        except (ValueError, TypeError, OSError) as e:
            # Only normalize generic configuration/transport failures raised
            # while constructing a provider client. Once a client exists,
            # unexpected exceptions from provider logic remain programming
            # errors and keep their traceback.
            if entry is not None:
                raise
            category = (
                LLMErrorCategory.CONNECTION
                if isinstance(e, OSError)
                else LLMErrorCategory.INVALID_REQUEST
            )
            error = LLMError(
                message=str(e),
                provider=provider,
                category=category,
                retryable=category == LLMErrorCategory.CONNECTION,
            )
            if not translate_errors:
                raise error from e
            logger.warning(
                "LLM client construction failed",
                provider=error.provider,
                category=error.category.value,
                retryable=error.retryable,
            )
            raise NodeUserError(error.user_message) from error
        finally:
            if entry is not None:
                await self._release_client(entry)

    async def fetch_models(self, *, provider: str, api_key: str) -> List[str]:
        """List available models from the named provider.

        Applies the JSON-driven ``incompatible_models`` filter
        (``providers.<name>.incompatible_models`` in ``llm_defaults.json``)
        uniformly. An absent key is a no-op — every provider gets the
        filter for free without per-provider Python.
        """
        spec = get_provider(provider)
        entry: Optional[_ClientEntry] = None
        try:
            entry = await self._acquire_client(
                spec, api_key, sdk_max_retries=2
            )
            models = await entry.client.fetch_models(api_key)
        except spec.sdk_exception_types as e:
            error = LLMError.from_exception(provider, e)
            logger.warning(
                "LLM model-list request failed",
                provider=error.provider,
                category=error.category.value,
                retryable=error.retryable,
                status_code=error.status_code,
                provider_code=error.provider_code,
                request_id=error.request_id,
            )
            raise NodeUserError(error.user_message) from error
        except (ValueError, TypeError, OSError) as e:
            if entry is not None:
                raise
            category = (
                LLMErrorCategory.CONNECTION
                if isinstance(e, OSError)
                else LLMErrorCategory.INVALID_REQUEST
            )
            error = LLMError(
                message=str(e),
                provider=provider,
                category=category,
                retryable=category == LLMErrorCategory.CONNECTION,
            )
            logger.warning(
                "LLM client construction failed",
                provider=error.provider,
                category=error.category.value,
                retryable=error.retryable,
            )
            raise NodeUserError(error.user_message) from error
        finally:
            if entry is not None:
                await self._release_client(entry)
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

    async def aclose(self) -> None:
        """Close and evict all cached SDK clients during worker shutdown."""

        to_close: List[LLMProvider] = []
        async with self._cache_lock:
            entries = list(self._client_cache.values())
            self._client_cache.clear()
            for entry in entries:
                entry.retired = True
                if entry.leases:
                    if entry not in self._retired_entries:
                        self._retired_entries.append(entry)
                else:
                    to_close.append(entry.client)
        for client in to_close:
            await self._close_client(client)

    async def _build_client(
        self,
        spec: ProviderSpec,
        api_key: str,
        *,
        sdk_max_retries: int,
    ) -> LLMProvider:
        """Instantiate the provider implementation.

        Pulls the user-configured ``{provider}_proxy`` URL from the
        encrypted credentials store (matches the legacy ai.py behavior)
        and merges it with the provider's static ``client_kwargs`` (used
        by OpenAI-compatible providers to pin their ``base_url``).
        """
        entry = await self._get_or_create_entry(
            spec,
            api_key,
            sdk_max_retries=sdk_max_retries,
            acquire=False,
        )
        return entry.client

    async def _acquire_client(
        self,
        spec: ProviderSpec,
        api_key: str,
        *,
        sdk_max_retries: int,
    ) -> _ClientEntry:
        """Return an entry leased until ``_release_client`` is called."""

        return await self._get_or_create_entry(
            spec,
            api_key,
            sdk_max_retries=sdk_max_retries,
            acquire=True,
        )

    async def _get_or_create_entry(
        self,
        spec: ProviderSpec,
        api_key: str,
        *,
        sdk_max_retries: int,
        acquire: bool,
    ) -> _ClientEntry:
        proxy_url = await self._auth.get_api_key(f"{spec.name}_proxy")
        factory_kwargs = {
            "api_key": api_key,
            "proxy_url": proxy_url,
            "max_retries": max(0, int(sdk_max_retries)),
            **spec.client_kwargs,
        }
        if not self._client_cache_size:
            return _ClientEntry(
                client=spec.factory(**factory_kwargs),
                leases=1 if acquire else 0,
                retired=True,
            )

        key = self._client_cache_key(
            spec=spec,
            api_key=api_key,
            proxy_url=proxy_url,
            sdk_max_retries=sdk_max_retries,
        )
        evicted: Optional[_ClientEntry] = None
        async with self._cache_lock:
            cached = self._client_cache.get(key)
            if cached is not None:
                self._client_cache.move_to_end(key)
                if acquire:
                    cached.leases += 1
                return cached
            entry = _ClientEntry(
                client=spec.factory(**factory_kwargs),
                leases=1 if acquire else 0,
            )
            self._client_cache[key] = entry
            if len(self._client_cache) > self._client_cache_size:
                _, evicted = self._client_cache.popitem(last=False)
                evicted.retired = True
                if evicted.leases:
                    self._retired_entries.append(evicted)
        if evicted is not None:
            if not evicted.leases:
                await self._close_client(evicted.client)
        return entry

    async def _release_client(self, entry: _ClientEntry) -> None:
        should_close = False
        async with self._cache_lock:
            if entry.leases:
                entry.leases -= 1
            if entry.retired and not entry.leases:
                if entry in self._retired_entries:
                    self._retired_entries.remove(entry)
                should_close = True
        if should_close:
            await self._close_client(entry.client)

    @staticmethod
    def _client_cache_key(
        *,
        spec: ProviderSpec,
        api_key: str,
        proxy_url: Optional[str],
        sdk_max_retries: int,
    ) -> str:
        credential_fingerprint = hashlib.sha256(
            api_key.encode("utf-8")
        ).hexdigest()
        material = json.dumps(
            {
                "provider": spec.name,
                "proxy_url": proxy_url or "",
                "client_kwargs": spec.client_kwargs,
                "credential": credential_fingerprint,
                "max_retries": max(0, int(sdk_max_retries)),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    async def _close_client(client: LLMProvider) -> None:
        close = getattr(client, "aclose", None)
        if close is None:
            return
        try:
            result = close()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            logger.warning("Failed to close cached LLM client", error=str(exc))

    def _incompatible_models(self, provider: str) -> set[str]:
        """Read ``providers.<name>.incompatible_models`` from llm_defaults.json."""
        raw = (
            self._defaults.get("providers", {})
            .get(provider, {})
            .get("incompatible_models")
        )
        return set(raw or ())
