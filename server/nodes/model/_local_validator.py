"""Local-LLM credential validator (Ollama, LM Studio).

Lives next to the chat-model plugins so all per-provider behaviour for
the local servers stays in `nodes/model/`. Registered into
`routers.websocket._SPECIAL_PROVIDER_VALIDATORS` from the same place
the cloud-provider mapping is declared — same shim shape as apify and
google_maps, the function body just lives here instead.

The frontend reuses the standard ``validate_api_key`` WebSocket message
for these providers; the ``api_key`` field carries the user's Base URL,
not a secret. We:

1. Save the URL under ``{provider}_proxy`` — the existing Ollama-style
   auth-delegation key that the runtime path in ``services/ai.py``
   already reads.
2. Probe the user's server via the *official* SDK (``ollama`` for
   Ollama, ``lmstudio`` for LM Studio) — list installed models and
   their actually-loaded ``context_length``. SDK-driven introspection
   beats hand-rolled httpx against ``/api/show`` / ``/api/v0/models``
   because the SDK ships the typed result struct (``ShowResponse``,
   ``LlmInstanceInfo``) and stays compatible with version drift
   upstream.
3. Store the placeholder api_key + discovered model list + per-model
   ``context_length`` under the provider id. ``model_registry`` reads
   the per-model context at runtime so a chat call never assumes a
   bogus 32K when the user has a 4K-loaded model.
4. Return ``valid=True`` only when at least one model was found, so a
   misconfigured URL surfaces as a clear "no models" message instead
   of a silent success.

Connection failures (server down, wrong port, auth refused, timeout)
are caught off the SDK's own exceptions and mapped to specific
user-facing toasts — operators see "is the server running?" for
connect-refused, "wrong path" for 404, etc. The catalogue ``stored``
flag flips to False on every failure path via the broadcaster, so a
failed re-probe clears the previously-green palette dot.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import lmstudio
import ollama
from core.logging import get_logger
from services.plugin.deps import get_auth_service
from services.status_broadcaster import get_status_broadcaster

logger = get_logger(__name__)


def _classify_http_error(provider: str, base_url: str, exc: BaseException) -> Tuple[str, str]:
    """Map an httpx / generic exception to (log_summary, user_message).

    Both SDKs (ollama-python, lmstudio) raise httpx errors underneath.
    Mapping these directly gives clean operator logs ("connect-refused"
    vs "404 vs "timeout") + actionable user toasts without depending
    on the openai SDK exception hierarchy.
    """
    display = "LM Studio" if provider == "lmstudio" else provider.capitalize()

    if isinstance(exc, httpx.TimeoutException):
        return ("timeout", f"Request to {base_url} timed out — server may be overloaded or unreachable")

    if isinstance(exc, httpx.ConnectError):
        return ("connect-refused",
                f"Could not reach {display} at {base_url}. Is the server running?")

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return (f"HTTP {status}", f"{display} rejected the request — server requires auth.")
        if status == 404:
            url = base_url.rstrip("/")
            if url.endswith("/v1"):
                hint = f"the {display} server is reachable but expected endpoints aren't exposed — check the server version."
            else:
                hint = f"the URL likely needs to end with `/v1` (e.g. {url}/v1)."
            return (f"HTTP {status}", f"{display} returned 404 — {hint}")
        if status == 429:
            return (f"HTTP {status}", f"{display} rate-limited the request — try again shortly.")
        return (f"HTTP {status}", f"{display} returned HTTP {status} — check the server logs.")

    if isinstance(exc, httpx.RequestError):
        return ("network-error", f"Network error reaching {display} at {base_url}: {exc.__class__.__name__}")

    # Unknown exception type: surface the class name + message so the
    # operator can tell what they're looking at without a stacktrace.
    return (type(exc).__name__, f"Could not reach {display}: {exc}")


async def _fail(
    provider: str,
    message: str,
    *,
    has_key: bool = False,
) -> Dict[str, Any]:
    """Common rejection path: broadcast invalid status + return envelope.

    `has_key=True` is used after the URL has been persisted so the
    palette doesn't go straight to the "unconfigured" gray dot — the
    user's URL is on file, it just can't reach a working server right
    now. `has_key=False` is used when we abort before persisting (only
    the "Base URL required" early-exit today).
    """
    await get_status_broadcaster().update_api_key_status(
        provider=provider, valid=False, message=message,
        has_key=has_key, models=[],
    )
    return {
        "provider": provider, "success": True, "valid": False,
        "message": message, "models": [], "timestamp": time.time(),
    }


def _strip_v1_path(base_url: str) -> str:
    """Return ``base_url`` with a trailing ``/v1`` segment stripped.

    The user's stored URL is the OpenAI-compatible base
    (``http://host:port/v1``). Both Ollama's REST API and LM Studio's
    SDK want the host:port without the OpenAI-compat suffix.
    """
    u = base_url.rstrip("/")
    if u.endswith("/v1"):
        return u[: -len("/v1")]
    return u


async def _fetch_ollama_models(base_url: str) -> List[Dict[str, Any]]:
    """List currently-loaded Ollama models with their actual params.

    Uses ``ollama.AsyncClient.ps()`` — the official "list running models"
    endpoint. Returns a typed ``ProcessResponse`` whose ``models[]``
    entries already carry every field we need as proper Pydantic
    attributes (no dict-key hunting, no Modelfile parameters parsing):

      - ``model``         — canonical name passed to ``/v1/chat/completions``
      - ``context_length``— live server-side n_ctx (this is the value
                             that produces the 400 overflow when a
                             prompt exceeds it)
      - ``details``       — typed ``ModelDetails`` (family, parameter_size,
                             quantization_level)

    If the user has models *pulled* but none currently loaded, ``ps()``
    returns an empty list — same semantics as LM Studio's
    ``list_loaded()``. The validator surfaces this as the "load a model
    and click Fetch again" message, which is already accurate.
    """
    host = _strip_v1_path(base_url)
    client = ollama.AsyncClient(host=host, timeout=10.0)
    try:
        running = await client.ps()
    finally:
        try:
            await client._client.aclose()
        except Exception:
            pass

    out: List[Dict[str, Any]] = []
    for m in running.models or []:
        mid = m.model or m.name
        if not mid:
            continue
        entry: Dict[str, Any] = {"id": mid}
        if m.context_length:
            entry["context_length"] = int(m.context_length)
        if m.details:
            if m.details.family:
                entry["architecture"] = m.details.family
            if m.details.parameter_size:
                entry["param_size"] = m.details.parameter_size
            if m.details.quantization_level:
                entry["quantization"] = m.details.quantization_level
            if m.details.format:
                entry["format"] = m.details.format
        out.append(entry)
    return out


async def _fetch_lmstudio_models(base_url: str) -> List[Dict[str, Any]]:
    """List currently-loaded LM Studio models with their actual params.

    Uses ``lmstudio.AsyncClient.llm.list_loaded()`` — each handle's
    ``get_info()`` returns a typed ``LlmInstanceInfo``. We read only
    SDK-typed fields (``context_length``, ``max_context_length``,
    ``vision``, ``trained_for_tool_use``, ``architecture``,
    ``params_string``); no string parsing.

    LM Studio's SDK takes ``api_host`` as ``host:port`` (no scheme, no
    path), so the user's stored ``http://host:port/v1`` is stripped
    before construction.
    """
    host = _strip_v1_path(base_url)
    api_host = host.split("://", 1)[-1]

    client = lmstudio.AsyncClient(api_host=api_host)
    out: List[Dict[str, Any]] = []
    async with client:
        loaded = await client.llm.list_loaded()
        for handle in loaded:
            try:
                info = await handle.get_info()
            except Exception as e:
                logger.info("[lmstudio] get_info skipped for %s: %s",
                            getattr(handle, "identifier", "<unknown>"), type(e).__name__)
                continue
            mid = info.identifier or info.model_key
            if not mid:
                continue
            entry: Dict[str, Any] = {"id": mid}
            if info.context_length:
                entry["context_length"] = int(info.context_length)
            if info.max_context_length:
                entry["max_context_length"] = int(info.max_context_length)
            if info.vision is not None:
                entry["vision"] = bool(info.vision)
            if info.trained_for_tool_use is not None:
                entry["supports_tools"] = bool(info.trained_for_tool_use)
            if info.architecture:
                entry["architecture"] = info.architecture
            if info.params_string:
                entry["param_size"] = info.params_string
            if info.format:
                entry["format"] = info.format
            out.append(entry)
    return out


async def _fetch_local_models(provider: str, base_url: str) -> List[Dict[str, Any]]:
    """Dispatch to the per-provider SDK probe."""
    if provider == "ollama":
        return await _fetch_ollama_models(base_url)
    if provider == "lmstudio":
        return await _fetch_lmstudio_models(base_url)
    raise ValueError(f"Unsupported local provider: {provider}")


async def validate_local_llm(data: Dict[str, Any]) -> Dict[str, Any]:
    """Validator for ollama / lmstudio. Returns the standard response envelope.

    Called from :meth:`nodes.model._credentials._LocalLLM.validate` (the
    ``Credential.validate`` hook ``handle_validate_api_key`` dispatches
    to). All side effects (URL persistence, status broadcasts, model
    registry registration) go through the ``StatusBroadcaster`` /
    ``AuthService`` singletons — no per-request WebSocket reference is
    needed.
    """
    provider = data["provider"].lower()
    base_url = data.get("api_key", "").strip()
    session_id = data.get("session_id", "default")

    if not base_url:
        return {"success": False, "valid": False, "error": "Base URL required"}

    auth_service = get_auth_service()

    # Persist the URL first so the runtime path (services/ai.py) can
    # read it via the existing {provider}_proxy lookup even before the
    # probe succeeds. The URL stays persisted on probe failure so the
    # user can re-click "Fetch" without re-entering it; the failure
    # branch broadcasts has_key=True so the palette dot reflects "URL
    # on file but currently unreachable" rather than "unconfigured".
    await auth_service.store_api_key(
        provider=f"{provider}_proxy",
        api_key=base_url,
        models=[],
        session_id=session_id,
    )

    try:
        entries = await _fetch_local_models(provider, base_url)
    except (httpx.HTTPError, lmstudio.LMStudioError) as e:
        log_summary, user_msg = _classify_http_error(provider, base_url, e)
        logger.warning("[%s] model probe failed (%s) at %s", provider, log_summary, base_url)
        return await _fail(provider, user_msg, has_key=True)
    except Exception as e:
        log_summary, user_msg = _classify_http_error(provider, base_url, e)
        logger.warning("[%s] model probe unexpected error (%s) at %s: %s",
                       provider, log_summary, base_url, e)
        return await _fail(provider, user_msg, has_key=True)

    if not entries:
        # Server reachable, responded with empty model list. Different
        # failure mode from the connect-error branches above — keep the
        # original "load a model" hint here since it's now accurate.
        display = "LM Studio" if provider == "lmstudio" else provider.capitalize()
        message = f"Connected to {display} at {base_url}, but no models are loaded. Load a model in {display} and click Fetch again."
        logger.info("[%s] reachable at %s but returned 0 models", provider, base_url)
        return await _fail(provider, message, has_key=True)

    # Pivot the SDK-probed entries into the storage shape: a parallel
    # ``models`` list (for the legacy readers) plus a ``model_params``
    # dict carrying every typed field the SDK exposed
    # (``context_length``, ``vision``, ``supports_tools``,
    # ``architecture``, ``param_size``, ``quantization``,
    # ``max_context_length``). ``model_registry.register_local_model``
    # consumes these directly to populate ``ModelInfo``.
    models = [e["id"] for e in entries]
    model_params: Dict[str, Dict[str, Any]] = {
        e["id"]: {k: v for k, v in e.items() if k != "id"}
        for e in entries
        if any(k != "id" for k in e)
    }

    # Store placeholder api_key + the real model list + per-model params.
    # The placeholder ("ollama") is the documented value the OpenAI-style
    # auth-delegation path expects when no real key is needed; it never
    # leaves the process because the runtime SDK rewrites it when
    # proxy_url is set.
    await auth_service.store_api_key(
        provider=provider,
        api_key="ollama",
        models=models,
        session_id=session_id,
        model_params=model_params,
    )

    # Register each model in the in-memory model registry so the sync
    # ``get_context_length`` / ``get_max_output_tokens`` lookups pick up
    # the real loaded n_ctx without re-querying the DB on every chat
    # call. Also keeps the runtime path branchless — local and cloud
    # models share the same ``provider/model_id`` registry key.
    from services.model_registry import get_model_registry
    registry = get_model_registry()
    for mid, params in model_params.items():
        registry.register_local_model(provider, mid, params)

    await get_status_broadcaster().update_api_key_status(
        provider=provider, valid=True,
        message=f"{len(models)} model(s) discovered at {base_url}",
        has_key=True, models=models,
    )
    ctx_summary = ", ".join(
        f"{mid}={p['context_length']}"
        for mid, p in model_params.items()
    ) or "no per-model context info"
    logger.info("[%s] discovered %d model(s) at %s (%s)",
                provider, len(models), base_url, ctx_summary)
    return {
        "provider": provider, "success": True, "valid": True,
        "models": models, "message": f"Connected to {provider} at {base_url}",
        "timestamp": time.time(),
    }
