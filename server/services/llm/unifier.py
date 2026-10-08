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

import httpx

from core.encryption import fingerprint_credential
from core.logging import get_logger
from services.llm.config import resolve_credential, split_provider_ref
from services.llm.endpoints import base_url_key, redact_url, unconfigured_endpoint_message
from services.llm.protocol import (
    LLMError,
    LLMErrorCategory,
    LLMProvider,
    LLMResponse,
    Message,
    StreamEvent,
    StreamSink,
    ThinkingConfig,
    ToolDef,
)
from services.llm.registry import ProviderSpec, get_provider, has_provider
from services.plugin import NodeUserError

if TYPE_CHECKING:
    from services.auth import AuthService

logger = get_logger(__name__)


def _provider_failure(provider: str, exc: BaseException) -> LLMError:
    """Normalize typed transport errors that native SDKs can pass through."""
    error = LLMError.from_exception(provider, exc)
    if isinstance(exc, httpx.TransportError):
        error.category = LLMErrorCategory.TIMEOUT if isinstance(exc, httpx.TimeoutException) else LLMErrorCategory.CONNECTION
        error.retryable = True
    return error


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
        context_management: Optional[Dict[str, Any]] = None,
        sdk_max_retries: int = 2,
        translate_errors: bool = True,
        on_event: Optional[StreamSink] = None,
    ) -> LLMResponse:
        """Execute a chat completion against the named provider.

        Raises ``NodeUserError`` on typed SDK failures (bad key, context
        overflow, missing model, server unreachable, …) — every other
        exception flows through unchanged so genuine server bugs keep
        their full traceback via ``BaseNode.execute()`` 's generic
        ``except Exception``.

        ``provider`` is a provider reference: a registered id, or a named
        endpoint ``openai_compatible:<slug>`` (RFC-0003 D13).

        ``on_event`` receives the response as it is produced. A provider
        that declares ``streaming`` in llm_defaults.json streams it; for any
        other the events come from the finished response, one per kind, so
        a caller handles both the same way. The response is the same either
        way.
        """
        provider_id = split_provider_ref(provider)[0]
        spec = get_provider(provider_id)
        streams = on_event is not None and self._streams(provider_id)
        api_key = self._resolve_credential(provider, api_key)
        entry: Optional[_ClientEntry] = None
        try:
            entry = await self._acquire_client(
                spec, api_key, provider_ref=provider, sdk_max_retries=sdk_max_retries
            )
            kwargs: Dict[str, Any] = {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking": thinking,
                "tools": tools,
                "context_management": context_management,
            }
            if streams:
                kwargs["on_event"] = on_event
            response = await entry.client.chat(messages, **kwargs)
            if on_event is not None and not streams:
                await _replay_response(response, on_event)
            return response
        except LLMError as error:
            # Raised by provider logic rather than the SDK, e.g. a 2xx that
            # carried an error body instead of a completion. Logged whether
            # or not it is translated: agent steps take it untranslated, and
            # only this layer knows where the call went (RFC-0003 D10).
            self._log_failure("LLM provider request failed", error, entry, provider)
            if not translate_errors:
                raise
            raise error.as_node_error() from error
        except spec.sdk_exception_types + (httpx.TransportError, httpx.HTTPStatusError) as e:
            error = _provider_failure(provider, e)
            self._log_failure("LLM provider request failed", error, entry, provider)
            if not translate_errors:
                raise error from e
            raise error.as_node_error() from error
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
            logger.warning(
                "LLM client construction failed",
                provider=error.provider,
                category=error.category.value,
                retryable=error.retryable,
            )
            if not translate_errors:
                raise error from e
            raise error.as_node_error() from error
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
        spec = get_provider(split_provider_ref(provider)[0])
        api_key = self._resolve_credential(provider, api_key)
        entry: Optional[_ClientEntry] = None
        try:
            entry = await self._acquire_client(
                spec, api_key, provider_ref=provider, sdk_max_retries=2
            )
            models = await entry.client.fetch_models(api_key)
        except LLMError as error:
            self._log_failure("LLM model-list request failed", error, entry, provider)
            raise error.as_node_error() from error
        except spec.sdk_exception_types + (httpx.TransportError, httpx.HTTPStatusError) as e:
            error = _provider_failure(provider, e)
            self._log_failure("LLM model-list request failed", error, entry, provider)
            raise error.as_node_error() from error
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
            raise error.as_node_error() from error
        finally:
            if entry is not None:
                await self._release_client(entry)
        blocklist = self._incompatible_models(provider)
        if not blocklist:
            return models
        return [m for m in models if m not in blocklist]

    def _streams(self, provider_id: str) -> bool:
        """Whether the provider streams its responses (``streaming`` in
        llm_defaults.json). Declared per provider once its stream is known
        to report what the finished response does, usage included."""
        config = (self._defaults.get("providers") or {}).get(provider_id) or {}
        return config.get("streaming") is True

    def is_registered(self, provider: str) -> bool:
        """Cheap probe used by callers that want graceful fallback when a
        provider is not wired into the registry.

        Replaces the legacy ``is_native_provider`` function in
        ``services/llm/factory.py`` — the unifier IS the routing layer,
        so registry membership is the source of truth.
        """
        return has_provider(split_provider_ref(provider)[0])

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
        provider_ref: Optional[str] = None,
    ) -> LLMProvider:
        """Instantiate the provider implementation.

        Pulls the user-configured ``{provider}_proxy`` URL from the
        encrypted credentials store and merges it with the provider's
        static ``client_kwargs`` (used by OpenAI-compatible providers to
        pin their ``base_url``).
        """
        entry = await self._get_or_create_entry(
            spec,
            api_key,
            provider_ref=provider_ref,
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
        provider_ref: Optional[str] = None,
    ) -> _ClientEntry:
        """Return an entry leased until ``_release_client`` is called."""

        return await self._get_or_create_entry(
            spec,
            api_key,
            provider_ref=provider_ref,
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
        provider_ref: Optional[str] = None,
    ) -> _ClientEntry:
        # The base-URL row is keyed by the full reference, so each named
        # endpoint reads its own URL while sharing one registration.
        ref = provider_ref or spec.name
        proxy_url = await self._auth.resolve_api_key(base_url_key(ref))
        unconfigured = unconfigured_endpoint_message(ref)
        if unconfigured and not proxy_url:
            # A named endpoint exists only as its credential rows. Without
            # the URL row the SDK would default to api.openai.com.
            raise NodeUserError(unconfigured)
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
        material = json.dumps(
            {
                "provider": spec.name,
                "proxy_url": proxy_url or "",
                "client_kwargs": spec.client_kwargs,
                "max_retries": max(0, int(sdk_max_retries)),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        # The credential component is appended as a PBKDF2 fingerprint rather
        # than folded into the SHA-256 material: credential-derived bytes must
        # never enter a fast hash (CodeQL py/weak-sensitive-data-hashing).
        return (
            hashlib.sha256(material.encode("utf-8")).hexdigest()
            + ":"
            + fingerprint_credential(api_key)
        )

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
            .get(split_provider_ref(provider)[0], {})
            .get("incompatible_models")
        )
        return set(raw or ())

    @staticmethod
    def _resolve_credential(provider: str, api_key: Optional[str]) -> str:
        """The key to send, or a user-facing error before any client exists.

        An empty key must never reach an SDK: the OpenAI SDK would read
        ``OPENAI_API_KEY`` and send it to the configured base URL
        (RFC-0003 D7). Keyless local servers get their declared placeholder.
        """
        try:
            return resolve_credential(provider, api_key)
        except ValueError:
            raise NodeUserError(
                f"No API key is configured for {provider!r}. Add one under Credentials."
            ) from None

    @staticmethod
    def _log_failure(event: str, error: LLMError, entry: Optional[_ClientEntry], provider: str) -> None:
        """One WARN line per provider failure, naming where the call went.

        ``provider`` is the reference the call was made with, so a named
        endpoint keeps its slug even when the provider raised under its
        generic name. ``url`` is redacted (no userinfo, query or fragment)
        and ``url_source`` says which setting produced it, so a routing
        mistake is distinguishable from a model failure (RFC-0003 D10).
        The key is never logged.
        """
        client = entry.client if entry is not None else None
        logger.warning(
            event,
            provider=provider,
            category=error.category.value,
            retryable=error.retryable,
            status_code=error.status_code,
            provider_code=error.provider_code,
            request_id=error.request_id,
            retry_after=error.retry_after,
            url=redact_url(getattr(client, "endpoint_url", None)),
            url_source=getattr(client, "url_source", None),
        )


async def _replay_response(response: LLMResponse, on_event: StreamSink) -> None:
    """Hand a finished response to a sink as if it had streamed: its
    reasoning, then its text, each in one event."""
    if response.thinking:
        await on_event(StreamEvent("reasoning", response.thinking))
    if response.content:
        await on_event(StreamEvent("text", response.content))
