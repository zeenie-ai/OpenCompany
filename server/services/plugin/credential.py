"""Declarative credential types (n8n + Pipedream pattern).

A :class:`Credential` subclass describes how a provider is authenticated
and how its secret is stored. One Google credential is shared across
gmail / calendar / drive / sheets / tasks / contacts — nodes just
reference the class. The :class:`Connection` facade resolves secrets at
call time; handlers never see tokens.

Two concrete bases:

- :class:`OAuth2Credential` — user-authorised tokens (refresh-capable)
- :class:`ApiKeyCredential` — static secret (header / query injection)

Both are discovered at import time via :data:`CREDENTIAL_REGISTRY`
(populated by ``__init_subclass__``). Nothing else needs to wire them.

Validation is also a base-class concern. Every "validate this key"
flow shares the same wiring (read api_key + session_id from request,
call provider probe, store on success, broadcast status, return
envelope). Only the probe itself varies. That common scaffold lives on
:meth:`Credential.validate`; subclasses override the lighter
:meth:`Credential._probe` to supply the per-provider call. See
:class:`ProbeResult` for the typed return shape.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Literal, Optional, Sequence

import httpx

logger = logging.getLogger(__name__)


CREDENTIAL_REGISTRY: Dict[str, type] = {}


@dataclass
class ProbeResult:
    """Outcome of a per-provider validation probe.

    Carries the common ``valid`` / ``message`` flags every validator
    returns, plus optional fields a few providers extend with
    (``models`` for LLM ``/v1/models`` lists, ``model_params`` for
    Ollama / LM Studio per-model context, ``extra`` for
    provider-specific extensions like Apify's ``username`` / ``email``
    / ``plan``). The base ``Credential.validate`` reads these fields
    when wiring storage / broadcast / response envelope so subclasses
    don't repeat any of that scaffolding.
    """

    valid: bool
    message: str = ""
    models: List[str] = field(default_factory=list)
    model_params: Optional[Dict[str, Dict[str, Any]]] = None
    # Free-form passthrough fields for provider-specific extensions
    # (Apify returns username / email / plan; Twitter returns user id;
    # Maps returns nothing). Merged into the response envelope.
    extra: Dict[str, Any] = field(default_factory=dict)


def classify_credential_error(
    exc: BaseException, *, display_name: str
) -> ProbeResult:
    """Map a transport / SDK exception to a typed ``ProbeResult``.

    Single source of truth for credential-error → user-message mapping.
    Used by the base ``Credential.validate`` after a ``_probe`` call
    raises, and by the local-LLM probe directly. Catches the documented
    ``httpx`` / ``openai`` exception hierarchy so operator logs see
    "HTTP 401" / "connect-refused" / "timeout" instead of an opaque
    repr.

    Returns a ``ProbeResult(valid=False, message=...)`` carrying the
    user-facing string. Operator logs are emitted at WARN by the
    caller — this helper just classifies, doesn't log.
    """
    import openai  # local import: openai is a heavy SDK

    if isinstance(exc, httpx.TimeoutException) or isinstance(exc, openai.APITimeoutError):
        return ProbeResult(
            valid=False,
            message=f"Request to {display_name} timed out — try again or check the network.",
        )

    if isinstance(exc, httpx.ConnectError) or isinstance(exc, openai.APIConnectionError):
        return ProbeResult(
            valid=False,
            message=f"Could not reach {display_name}. Is the server running?",
        )

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return _classify_status(status, display_name)

    if isinstance(exc, openai.AuthenticationError):
        return _classify_status(401, display_name)
    if isinstance(exc, openai.PermissionDeniedError):
        return _classify_status(403, display_name)
    if isinstance(exc, openai.NotFoundError):
        return _classify_status(404, display_name)
    if isinstance(exc, openai.RateLimitError):
        return _classify_status(429, display_name)
    if isinstance(exc, openai.APIStatusError):
        status = getattr(getattr(exc, "response", None), "status_code", 500)
        return _classify_status(status, display_name)

    # Unknown exception: surface the type name so the operator log line
    # is still useful, but don't dump a stacktrace into the user's toast.
    return ProbeResult(
        valid=False,
        message=f"Could not validate {display_name}: {type(exc).__name__}: {exc}",
    )


def _classify_status(status: int, display_name: str) -> ProbeResult:
    """Map an HTTP status code to a user-facing message."""
    if status in (401, 403):
        return ProbeResult(valid=False, message=f"{display_name} rejected the API key.")
    if status == 404:
        return ProbeResult(
            valid=False,
            message=f"{display_name} returned 404 — endpoint not found. Check the URL.",
        )
    if status == 429:
        return ProbeResult(
            valid=False,
            message=f"{display_name} rate-limited the request — try again shortly.",
        )
    if 500 <= status < 600:
        return ProbeResult(
            valid=False,
            message=f"{display_name} returned HTTP {status} — try again later.",
        )
    return ProbeResult(
        valid=False,
        message=f"{display_name} returned HTTP {status}.",
    )


class Credential:
    """Base class for provider credentials.

    Subclasses set class attributes — the class itself is the
    declaration. Never instantiate directly; use
    :class:`OAuth2Credential` or :class:`ApiKeyCredential`.
    """

    id: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    auth: ClassVar[Literal["oauth2", "api_key", "basic", "custom"]] = "custom"
    # Icon key for the credentials modal — follows node icon wire format
    # ("lobehub:gmail", "asset:serper", "🔑", etc.)
    icon: ClassVar[str] = ""
    # UI grouping (e.g. "AI", "Search", "Communication")
    category: ClassVar[str] = "Other"
    # Scopes requested / required — OAuth only, but declared uniformly
    scopes: ClassVar[Sequence[str]] = ()
    # Documentation URL for the user (credentials modal link)
    docs_url: ClassVar[Optional[str]] = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.id:
            if cls.id in CREDENTIAL_REGISTRY and CREDENTIAL_REGISTRY[cls.id] is not cls:
                # Idempotent on re-import — only warn on genuine conflict.
                existing = CREDENTIAL_REGISTRY[cls.id]
                if existing.__qualname__ != cls.__qualname__:
                    raise ValueError(
                        f"Credential id '{cls.id}' registered by "
                        f"{existing.__qualname__} and now by {cls.__qualname__}"
                    )
            CREDENTIAL_REGISTRY[cls.id] = cls

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """Fetch secrets from the auth service.

        Default implementation is abstract — subclasses override.
        """
        raise NotImplementedError

    @classmethod
    def inject(cls, secrets: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
        """Mutate an httpx request dict (headers / params / json / auth)
        to carry authentication. Default is no-op — declarative subclasses
        override to implement their auth scheme.
        """
        return request

    # ---- Validation -------------------------------------------------
    #
    # Every "validate this credential" flow follows the same wiring:
    # read api_key + session_id from the request, run a per-provider
    # probe, store on success, broadcast status, return the standard
    # response envelope. Only the probe call genuinely varies — so the
    # scaffolding lives on the base and subclasses override the lighter
    # :meth:`_probe` hook. The dispatch entry point (called by the WS
    # router) is :meth:`validate`; subclasses with non-standard
    # side-effect ordering (e.g. local-LLM credentials store under TWO
    # keys) override :meth:`validate` directly.

    @classmethod
    async def validate(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Run the credential validation probe and wire side effects.

        The shared scaffold:
          1. Read ``api_key`` + ``session_id`` from the request data.
          2. Guard: missing key → fast-fail envelope.
          3. Call :meth:`_probe` (subclass-supplied; provider-specific).
          4. On exception, classify via :func:`classify_credential_error`.
          5. On valid result, persist via ``auth_service.store_api_key``.
          6. Broadcast the new status via
             ``StatusBroadcaster.update_api_key_status`` (always — invalid
             results clear stale state on connected clients).
          7. Return the standard response envelope.

        Subclasses with non-standard storage / broadcast (e.g. local-LLM
        credentials that store both URL and placeholder key) override
        this method directly.
        """
        from core.container import container
        from services.status_broadcaster import get_status_broadcaster

        api_key = (data.get("api_key") or "").strip()
        session_id = data.get("session_id", "default")
        if not api_key:
            return {
                "success": False,
                "valid": False,
                "error": f"{cls.id} api_key required",
            }

        try:
            result = await cls._probe(api_key)
        except Exception as exc:  # noqa: BLE001 — classified below
            display = cls.display_name or cls.id
            result = classify_credential_error(exc, display_name=display)
            logger.warning(
                "[%s] credential probe failed: %s",
                cls.id,
                result.message,
            )

        broadcaster = get_status_broadcaster()
        auth_service = container.auth_service()

        if result.valid:
            await auth_service.store_api_key(
                provider=cls.id,
                api_key=api_key,
                models=result.models,
                session_id=session_id,
                model_params=result.model_params,
            )

        await broadcaster.update_api_key_status(
            provider=cls.id,
            valid=result.valid,
            message=result.message,
            has_key=result.valid,
            models=result.models,
        )

        return {
            "success": True,
            "provider": cls.id,
            "valid": result.valid,
            "message": result.message,
            "models": result.models,
            "timestamp": time.time(),
            **result.extra,
        }

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        """Run the per-provider validation probe.

        Subclass override point — the probe MUST NOT do storage,
        broadcasts, or registry mutation. It returns a
        :class:`ProbeResult` describing what the upstream returned;
        :meth:`validate` does the rest. Raise ``httpx.*`` /
        ``openai.OpenAIError`` to let the base map them to a typed
        message via :func:`classify_credential_error`.
        """
        raise NotImplementedError(
            f"Credential subclass {cls.__name__} must override _probe()"
        )


class OAuth2Credential(Credential):
    """OAuth 2.0 with refresh-token support.

    Tokens resolved via ``auth_service.get_oauth_tokens(id, user_id)``
    which handles refresh transparently. The :class:`Connection` facade
    retries on 401/403 by re-calling :meth:`resolve`.
    """

    auth: ClassVar[Literal["oauth2"]] = "oauth2"
    authorization_url: ClassVar[str] = ""
    token_url: ClassVar[str] = ""
    # How the access token rides on requests
    token_location: ClassVar[Literal["header", "query"]] = "header"
    token_header: ClassVar[str] = "Authorization"
    token_prefix: ClassVar[str] = "Bearer "
    # Keys the user enters in credentials modal (API-key style rows)
    client_id_api_key: ClassVar[str] = ""       # e.g. "google_client_id"
    client_secret_api_key: ClassVar[str] = ""   # e.g. "google_client_secret"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        from core.container import container

        auth_service = container.auth_service()
        tokens = await auth_service.get_oauth_tokens(cls.id, user_id)
        if not tokens or not tokens.get("access_token"):
            raise PermissionError(
                f"No OAuth tokens for '{cls.id}'. Connect via Credentials modal."
            )
        return tokens

    @classmethod
    def inject(cls, secrets: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
        token = secrets.get("access_token", "")
        if cls.token_location == "header":
            headers = dict(request.get("headers") or {})
            headers[cls.token_header] = f"{cls.token_prefix}{token}"
            request = {**request, "headers": headers}
        else:
            qs = dict(request.get("params") or {})
            qs[cls.token_header] = token
            request = {**request, "params": qs}
        return request


class ApiKeyCredential(Credential):
    """Static API key with declarative injection into headers or query.

    Example::

        class BraveCredential(ApiKeyCredential):
            id = "brave_search"
            display_name = "Brave Search"
            category = "Search"
            key_name = "X-Subscription-Token"
            key_location = "header"
            probe_url = "https://api.search.brave.com/res/v1/web/search"
            probe_params = {"q": "ping", "count": 1}

    Subclasses get a default :meth:`_probe` for free as long as they
    declare a ``probe_url``. Auth attaches via :meth:`inject` (header /
    query / bearer). Subclasses with bespoke validation (URL with
    embedded token, SDK-based probe, custom 200-with-error envelope)
    override :meth:`_probe` directly — see ``TelegramCredential``,
    ``GoogleMapsCredential``, ``ApifyCredential``.
    """

    auth: ClassVar[Literal["api_key"]] = "api_key"
    # Where the key goes
    key_name: ClassVar[str] = ""              # header name or query-string key
    key_location: ClassVar[Literal["header", "query", "bearer"]] = "header"
    # Extra fields stored alongside (e.g. "apify_account_id" for Apify)
    extra_fields: ClassVar[Sequence[str]] = ()

    # ---- Declarative HTTP probe ------------------------------------
    #
    # The default :meth:`_probe` builds a request from these attributes,
    # runs it through :meth:`inject` to attach the key, sends via httpx,
    # and calls :meth:`_handle_probe_response` on a 2xx response.
    # ``httpx.HTTPStatusError`` / ``TimeoutException`` / ``ConnectError``
    # propagate to :meth:`Credential.validate`, where
    # :func:`classify_credential_error` produces the user-facing message.
    probe_url: ClassVar[str] = ""             # set to enable the default probe
    probe_method: ClassVar[str] = "GET"
    probe_params: ClassVar[Dict[str, Any]] = {}
    probe_json: ClassVar[Optional[Dict[str, Any]]] = None
    probe_timeout_seconds: ClassVar[float] = 10.0

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        from core.container import container

        auth_service = container.auth_service()
        api_key = await auth_service.get_api_key(cls.id)
        if not api_key:
            raise PermissionError(
                f"No API key for '{cls.id}'. Add via Credentials modal."
            )
        secrets: Dict[str, Any] = {"api_key": api_key}
        for field in cls.extra_fields:
            value = await auth_service.get_api_key(field)
            if value:
                secrets[field] = value
        return secrets

    @classmethod
    def inject(cls, secrets: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
        api_key = secrets.get("api_key", "")
        name = cls.key_name or "Authorization"
        if cls.key_location == "header":
            headers = dict(request.get("headers") or {})
            headers[name] = api_key
            request = {**request, "headers": headers}
        elif cls.key_location == "bearer":
            headers = dict(request.get("headers") or {})
            headers["Authorization"] = f"Bearer {api_key}"
            request = {**request, "headers": headers}
        else:  # "query"
            qs = dict(request.get("params") or {})
            qs[name] = api_key
            request = {**request, "params": qs}
        return request

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        """Default declarative HTTP probe.

        Subclasses set ``probe_url`` (and optionally ``probe_method``,
        ``probe_params``, ``probe_json``); auth attaches via
        :meth:`inject`. Override :meth:`_handle_probe_response` to
        extract provider-specific metadata or detect API-level failures
        embedded in a 2xx response (e.g. Telegram's ``{ok: false}``
        envelope, Maps' ``status: REQUEST_DENIED``).
        """
        if not cls.probe_url:
            raise NotImplementedError(
                f"Credential subclass {cls.__name__} must override _probe() "
                f"or set the `probe_url` class attribute"
            )

        request = cls.inject(
            {"api_key": api_key},
            {"headers": {}, "params": dict(cls.probe_params)},
        )

        async with httpx.AsyncClient(timeout=cls.probe_timeout_seconds) as client:
            response = await client.request(
                cls.probe_method,
                cls.probe_url,
                headers=request.get("headers") or None,
                params=request.get("params") or None,
                json=cls.probe_json,
            )
        response.raise_for_status()
        return cls._handle_probe_response(response)

    @classmethod
    def _handle_probe_response(cls, response: httpx.Response) -> ProbeResult:
        """Translate a 2xx probe response to a :class:`ProbeResult`.

        Default: success message only. Override to inspect the body —
        for example to surface bot identity (Telegram ``getMe``) or to
        catch wrapped failures that arrive as 200s (Telegram
        ``{ok: false}``).
        """
        return ProbeResult(
            valid=True,
            message=f"{cls.display_name or cls.id} API key is valid",
        )
