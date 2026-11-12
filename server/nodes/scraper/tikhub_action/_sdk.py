"""Everything that touches the ``tikhub`` SDK, imported lazily.

The node in ``__init__.py`` never imports ``tikhub`` at module level — the
plugin must register (and the credentials modal must keep working) even when
the SDK is missing. Every function here that needs the SDK imports it inside
its body.

The SDK is auto-generated from TikHub's OpenAPI spec: ``client.<resource>.
<method>(**kwargs)`` is ``GET|POST /api/v1/<platform>/<api>/<action>``, all
parameters are keyword-only, and each method's docstring carries the route
literal on its last line. That convention is what :func:`endpoint_index`
introspects — there is no vendored endpoint list, so an SDK upgrade is the
only thing needed to pick up new routes.
"""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import inspect
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from core.logging import get_logger
from services.plugin import NodeContext, NodeUserError

from .._credentials import TikHubCredential

logger = get_logger(__name__)

# Per-request timeout handed to the SDK's httpx client. TikHub's API reference
# asks for 30 to 60 seconds; the upper end keeps a slow answer from being cut
# off and requested again.
_TIMEOUT = 60.0
# ``RetryPolicy.should_retry`` returns False once ``attempt >= max_retries``,
# so 3 means one initial attempt plus two retries (connection / 5xx / 429
# only). Tests monkeypatch this to 0 to skip the anyio backoff sleeps.
_MAX_RETRIES = 3
# Any non-empty key works for introspection: the throwaway client never
# sends a request, it only exists so ``vars(client)`` lists the resources.
_INTROSPECTION_KEY = "introspection"
_BODY_PREVIEW_CHARS = 500
_MAX_SUGGESTIONS = 5
_ROUTE_RE = re.compile(r"``(GET|POST|PUT|PATCH|DELETE)\s+(/\S+?)``")
_SOURCE_ROUTE_RE = re.compile(r"_request\(\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]\s*,\s*['\"](/[^'\"]+)['\"]")

# Static platform list for the ``platform`` dropdown. Filter rule is
# ``info.platform == platform`` where platform is the first ``_`` segment of
# the SDK resource name (``tiktok_app_v3`` -> ``tiktok``). A test asserts every
# value except ``all`` matches at least one live resource so SDK drift fails
# loudly instead of silently emptying a dropdown.
PLATFORMS: Tuple[str, ...] = (
    "all",
    "tiktok",
    "douyin",
    "instagram",
    "youtube",
    "twitter",
    "xiaohongshu",
    "bilibili",
    "kuaishou",
    "weibo",
    "reddit",
    "threads",
    "linkedin",
    "telegram",
    "zhihu",
    "toutiao",
    "xigua",
    "wechat",
    "lemon8",
    "pipixia",
    "hybrid",
    "tikhub",
)


@dataclass(frozen=True)
class ParamInfo:
    name: str
    type: str
    required: bool


@dataclass(frozen=True)
class EndpointInfo:
    endpoint: str  # "douyin_web.fetch_one_video" — the SDK's own id
    resource: str  # "douyin_web"
    method: str  # "fetch_one_video"
    platform: str  # "douyin"
    http_method: Optional[str]  # "GET" / "POST" (None when undiscoverable)
    path: Optional[str]  # "/api/v1/douyin/web/fetch_one_video"
    summary: str  # English half of the docstring's first line
    params: Tuple[ParamInfo, ...]

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self) | {"params": [asdict(p) for p in self.params]}


_INDEX: Optional[List[EndpointInfo]] = None
_BY_ENDPOINT: Dict[str, EndpointInfo] = {}
_BY_PATH: Dict[str, EndpointInfo] = {}
_INDEX_LOCK = asyncio.Lock()


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


def platform_of(resource: str) -> str:
    """``tiktok_app_v3`` -> ``tiktok``; ``hybrid_parsing`` -> ``hybrid``."""
    return resource.split("_")[0]


def _is_resource(obj: Any) -> bool:
    try:
        from tikhub.resources._base import AsyncResource

        if isinstance(obj, AsyncResource):
            return True
    except ImportError:
        pass
    module = getattr(type(obj), "__module__", "") or ""
    return module.startswith("tikhub.resources")


def _annotation_text(param: inspect.Parameter) -> str:
    ann = param.annotation
    if ann is inspect.Parameter.empty:
        return "Any"
    # Under ``from __future__ import annotations`` these are already strings.
    return ann if isinstance(ann, str) else getattr(ann, "__name__", repr(ann))


def _route_of(fn: Any) -> Tuple[Optional[str], Optional[str]]:
    doc = inspect.getdoc(fn) or ""
    match = _ROUTE_RE.search(doc)
    if match:
        return match.group(1), match.group(2)
    try:
        match = _SOURCE_ROUTE_RE.search(inspect.getsource(fn))
    except (OSError, TypeError):
        match = None
    if match:
        return match.group(1), match.group(2)
    return None, None


def _summary_of(fn: Any) -> str:
    doc = inspect.getdoc(fn) or ""
    first = doc.strip().splitlines()[0].strip() if doc.strip() else ""
    # Generated docstrings read "<Chinese>/<English>"; keep the English half.
    if "/" in first:
        english = first.split("/", 1)[1].strip()
        if english:
            return english
    return first


def _describe(resource_name: str, method_name: str, fn: Any) -> EndpointInfo:
    params: List[ParamInfo] = []
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        for p in signature.parameters.values():
            if p.kind is not inspect.Parameter.KEYWORD_ONLY:
                continue
            params.append(
                ParamInfo(
                    name=p.name,
                    type=_annotation_text(p),
                    required=p.default is inspect.Parameter.empty,
                )
            )
    http_method, path = _route_of(fn)
    return EndpointInfo(
        endpoint=f"{resource_name}.{method_name}",
        resource=resource_name,
        method=method_name,
        platform=platform_of(resource_name),
        http_method=http_method,
        path=path,
        summary=_summary_of(fn),
        params=tuple(params),
    )


def _build_index() -> List[EndpointInfo]:
    """Reflect every resource method on a throwaway client. No network."""
    from tikhub import AsyncTikHub

    client = AsyncTikHub(api_key=_INTROSPECTION_KEY, max_retries=0)
    entries: List[EndpointInfo] = []
    for resource_name, resource in vars(client).items():
        if resource_name.startswith("_") or not _is_resource(resource):
            continue
        for method_name, fn in inspect.getmembers(type(resource), inspect.iscoroutinefunction):
            if method_name.startswith("_"):
                continue
            entries.append(_describe(resource_name, method_name, fn))
    entries.sort(key=lambda e: e.endpoint)
    return entries


async def endpoint_index() -> List[EndpointInfo]:
    """Cached, process-wide endpoint catalogue (built once, ~1000 entries)."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    async with _INDEX_LOCK:
        if _INDEX is None:
            entries = await asyncio.to_thread(_build_index)
            _BY_ENDPOINT.clear()
            _BY_PATH.clear()
            for entry in entries:
                _BY_ENDPOINT[entry.endpoint] = entry
                if entry.path:
                    _BY_PATH.setdefault(entry.path, entry)
            _INDEX = entries
            logger.info("tikhub endpoint index built", endpoints=len(entries))
    return _INDEX


async def list_endpoints(
    platform: str = "all",
    search: str = "",
    limit: Optional[int] = None,
) -> Tuple[List[EndpointInfo], int]:
    """Filter the index by platform and a case-insensitive substring.

    Returns ``(matches, total)`` where ``total`` counts matches before
    ``limit`` is applied, so a caller can tell the list was cut.
    """
    index = await endpoint_index()
    needle = (search or "").strip().lower()
    matches = [
        e
        for e in index
        if (platform in ("", "all") or e.platform == platform)
        and (not needle or needle in e.endpoint.lower() or needle in e.summary.lower() or needle in (e.path or "").lower())
    ]
    total = len(matches)
    if limit is not None and limit >= 0:
        matches = matches[:limit]
    return matches, total


# ---------------------------------------------------------------------------
# Resolution + argument binding
# ---------------------------------------------------------------------------


def _normalise_key(raw: str) -> Tuple[Optional[str], List[str]]:
    """Return ``(dotted_id, candidate_paths)`` for a user-supplied endpoint.

    Accepts ``resource.method``, ``resource/method``, an ``/api/v1/...`` path
    (with or without the leading slash / prefix) or a full URL.
    """
    key = raw.strip()
    if key.startswith(("http://", "https://")):
        key = urlparse(key).path
    key = key.rstrip("/")
    if key.startswith("/"):
        return None, [key, f"/api/v1{key}" if not key.startswith("/api/") else key]
    if key.count("/") == 1:
        return key.replace("/", "."), [f"/{key}", f"/api/v1/{key}"]
    if "/" in key:
        return None, [f"/{key}", f"/api/v1/{key}"]
    return key, []


async def resolve_endpoint(raw: str) -> EndpointInfo:
    """Map any accepted spelling of an endpoint to its :class:`EndpointInfo`."""
    if not (raw or "").strip():
        raise NodeUserError(
            "endpoint is required (e.g. douyin_web.fetch_one_video). "
            "Use operation=list_endpoints to discover endpoint ids."
        )
    await endpoint_index()
    dotted, paths = _normalise_key(raw)
    if dotted and dotted in _BY_ENDPOINT:
        return _BY_ENDPOINT[dotted]
    for path in paths:
        if path in _BY_PATH:
            return _BY_PATH[path]

    probe = dotted or raw.strip()
    suggestions = difflib.get_close_matches(probe, list(_BY_ENDPOINT), n=_MAX_SUGGESTIONS, cutoff=0.5)
    if not suggestions and "." not in probe:
        # A bare method name: offer every resource that has it.
        tail = "." + probe.rsplit("/", 1)[-1]
        suggestions = [e for e in _BY_ENDPOINT if e.endswith(tail)][:_MAX_SUGGESTIONS]
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    raise NodeUserError(
        f"Unknown TikHub endpoint '{raw.strip()}'.{hint} "
        "Use operation=list_endpoints (with platform and search) to discover endpoint ids."
    )


def _accepted_params(info: EndpointInfo) -> str:
    if not info.params:
        return "none (this endpoint takes no parameters)"
    return ", ".join(f"{p.name} ({p.type}, {'required' if p.required else 'optional'})" for p in info.params)


def bind_params(info: EndpointInfo, fn: Any, params: Any) -> Dict[str, Any]:
    """Validate ``params`` against the SDK method signature before any HTTP.

    A wrong or missing keyword is the most common LLM mistake; rejecting it
    here (with the accepted list) beats a 422 from TikHub that was already
    billed as a request.
    """
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise NodeUserError(
            f"params for {info.endpoint} must be a JSON object of keyword arguments. Accepted: {_accepted_params(info)}"
        )
    if any(not isinstance(k, str) for k in params):
        raise NodeUserError(f"params for {info.endpoint} must use string keys. Accepted: {_accepted_params(info)}")
    try:
        bound = inspect.signature(fn).bind(**params)
    except TypeError as exc:
        raise NodeUserError(f"{info.endpoint} rejected params: {exc}. Accepted: {_accepted_params(info)}") from exc
    return dict(bound.arguments)


# ---------------------------------------------------------------------------
# Response + error shaping
# ---------------------------------------------------------------------------


def _preview(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    return text if len(text) <= _BODY_PREVIEW_CHARS else text[:_BODY_PREVIEW_CHARS] + "…"


def _envelope_message(body: Any, fallback: str) -> str:
    if isinstance(body, dict):
        for key in ("message", "error", "detail", "msg"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


def _error_detail(body: Any) -> str:
    """TikHub's own words from the body of a failed request.

    TikHub answers ``{"detail": {"message": "...", "message_zh": "...",
    "support": "...", "docs": "..."}}``; only the English message is of use
    to a model. A 422 is FastAPI's ``{"detail": [{"loc": [...], "msg": ...}]}``.
    The SDK reads neither shape and reports ``HTTP <status>`` for both.
    """
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or "")
    if isinstance(detail, list):
        return "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc') or ())}: {item.get('msg', '')}"
            for item in detail
            if isinstance(item, dict)
        )
    return ""


def to_plain(result: Any) -> Dict[str, Any]:
    """Unwrap TikHub's ``{code, router, params, data}`` envelope.

    The SDK returns ``response.json()`` verbatim and never looks at the
    in-body ``code``, so an API-level failure arrives here as a 2xx dict —
    treat an int ``code >= 400`` as a user error. A body without a ``data``
    key (or a non-JSON body) is passed through whole under ``data``.
    """
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    if not isinstance(result, dict):
        return {"data": result, "code": None, "router": None}
    code = result.get("code")
    if isinstance(code, int) and not isinstance(code, bool) and code >= 400:
        raise NodeUserError(f"TikHub returned code {code}: {_envelope_message(result, 'request failed')}")
    data = result["data"] if "data" in result else result
    return {"data": data, "code": code, "router": result.get("router")}


def raise_user_error(exc: BaseException, endpoint: str) -> None:
    """Translate an SDK exception into a ``NodeUserError``.

    Only ``TikHubError`` subclasses are mapped; anything else propagates so
    genuine bugs keep their traceback. Always raises.
    """
    from tikhub import (
        TikHubAuthError,
        TikHubBadRequestError,
        TikHubConfigError,
        TikHubConnectionError,
        TikHubFeatureRemovedError,
        TikHubHTTPError,
        TikHubNotFoundError,
        TikHubPermissionError,
        TikHubRateLimitError,
        TikHubServerError,
        TikHubUpstreamError,
        TikHubValidationError,
    )

    status = getattr(exc, "status_code", None)
    response_body = getattr(exc, "response_body", None)
    said = _error_detail(response_body)
    detail = str(said or (exc.args[0] if exc.args else exc)).strip().rstrip(".")
    # The raw body is worth showing only when TikHub's words could not be read from it.
    body = "" if said else _preview(response_body)

    if isinstance(exc, TikHubAuthError):
        message = f"TikHub rejected the API key (401) while calling {endpoint}. Update it in Credentials → TikHub."
    elif isinstance(exc, TikHubPermissionError):
        message = (
            f"TikHub refused {endpoint} (403): {detail}. The API key lacks permission for this endpoint, or the "
            "account is disabled or its email is unverified. Enable the endpoint for the key at user.tikhub.io."
        )
    elif isinstance(exc, TikHubRateLimitError):
        retry_after = getattr(exc, "retry_after", None)
        wait = f"; retry after {retry_after:g}s" if isinstance(retry_after, (int, float)) else ""
        message = f"TikHub rate-limited {endpoint} (429){wait}. Each endpoint allows about 10 requests/s."
    elif isinstance(exc, TikHubNotFoundError):
        message = (
            f"TikHub returned 404 for {endpoint}: {detail}. Either the requested item does not exist or TikHub "
            "has retired this route; operation=describe says whether the route is still published."
        )
    elif isinstance(exc, TikHubBadRequestError) and status == 422:
        message = (
            f"TikHub rejected the parameters of {endpoint} (422): {detail}. "
            "operation=describe documents each parameter."
        )
        if body:
            message += f" Response: {body}"
    elif isinstance(exc, TikHubBadRequestError):
        # TikHub documents 400 as a bad parameter OR a failure of its own, and
        # sends the same message for both, so the model must not assume its
        # arguments were wrong.
        message = (
            f"TikHub could not complete {endpoint} ({status}): {detail}. TikHub answers 400 for a wrong parameter "
            "value and also for a failure on its side or the platform's, and bills neither. Check the values with "
            "operation=describe; if they are right, retry once or use another endpoint for the same data."
        )
        if body:
            message += f" Response: {body}"
    elif status == 402:
        message = (
            f"TikHub refused {endpoint} (402): {detail}. The account balance is too low for this endpoint; "
            "top up at user.tikhub.io (operation=account shows the balance)."
        )
    elif isinstance(exc, TikHubValidationError):
        raw = _preview(getattr(exc, "raw", None))
        message = f"TikHub returned an unparseable response for {endpoint}: {detail}."
        if raw:
            message += f" Raw: {raw}"
    elif isinstance(exc, (TikHubUpstreamError, TikHubServerError, TikHubConnectionError)):
        where = f" (HTTP {status})" if status else ""
        message = f"TikHub upstream error while calling {endpoint}{where}: {detail}. Retries were exhausted — try again later."
    elif isinstance(exc, (TikHubFeatureRemovedError, TikHubConfigError)):
        message = str(exc)
    elif isinstance(exc, TikHubHTTPError):
        message = f"TikHub returned HTTP {status} for {endpoint}: {detail}."
        if body:
            message += f" Response: {body}"
    else:
        raise exc

    request_id = getattr(exc, "request_id", None)
    if request_id:
        message += f" [request_id={request_id}]"
    raise NodeUserError(message) from exc


# ---------------------------------------------------------------------------
# Client + dispatch
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def make_client(ctx: NodeContext) -> AsyncIterator[Any]:
    """Yield an authenticated ``AsyncTikHub`` for one operation.

    The key is resolved FIRST so a missing credential raises the annotated
    ``PermissionError`` untouched — ``BaseNode.execute`` turns that into the
    ``PermissionDeniedError`` envelope plus the ``credential.api_key.
    runtime_failed`` broadcast. The key is always passed explicitly so a
    ``$TIKHUB_API_KEY`` in the server environment can never authenticate a
    node implicitly.
    """
    secrets = await TikHubCredential.resolve(user_id=ctx.credential_customer_id)
    try:
        from tikhub import AsyncTikHub
    except ImportError as exc:
        raise NodeUserError("The 'tikhub' package is not installed — run `uv sync` in server/.") from exc
    async with AsyncTikHub(api_key=secrets["api_key"], timeout=_TIMEOUT, max_retries=_MAX_RETRIES) as client:
        yield client


async def call_endpoint(client: Any, info: EndpointInfo, params: Any) -> Dict[str, Any]:
    """``getattr`` dispatch + bind + typed-error mapping + envelope unwrap."""
    from tikhub import TikHubError

    resource = getattr(client, info.resource, None)
    fn = getattr(resource, info.method, None) if resource is not None else None
    if fn is None:
        raise NodeUserError(f"{info.endpoint} is not available in the installed tikhub SDK. Run operation=list_endpoints.")
    kwargs = bind_params(info, fn, params)
    try:
        raw = await fn(**kwargs)
    except TikHubError as exc:
        raise_user_error(exc, info.endpoint)
    return to_plain(raw)


# ---------------------------------------------------------------------------
# Usage tracking + dropdown loader
# ---------------------------------------------------------------------------


async def track_tikhub_usage(ctx: NodeContext, action: str, endpoint_path: Optional[str]) -> float:
    """Record one billed TikHub request in ``api_usage_metrics``.

    Called only after a successful call (non-2xx is not billed by TikHub)
    and never for ``list_endpoints``. Best-effort: a tracking failure logs a
    warning and returns 0.0 rather than failing a call that already cost
    money. Returns the computed USD cost for the ``cost_usd`` output key.
    """
    try:
        from services.plugin.deps import get_database
        from services.pricing import get_pricing_service

        cost_data = get_pricing_service().calculate_api_cost("tikhub", action, 1)
        raw = ctx.raw if isinstance(ctx.raw, dict) else {}
        await get_database().save_api_usage_metric(
            {
                "session_id": ctx.session_id or raw.get("session_id", "default"),
                "node_id": ctx.node_id,
                "workflow_id": ctx.workflow_id or raw.get("workflow_id"),
                "service": "tikhub",
                "operation": cost_data.get("operation", action),
                "endpoint": endpoint_path or action,
                "resource_count": 1,
                "cost": cost_data.get("total_cost", 0.0),
            }
        )
        return float(cost_data.get("total_cost", 0.0) or 0.0)
    except Exception as exc:  # noqa: BLE001 — tracking must never fail a billed call
        logger.warning("tikhub usage tracking failed", action=action, error=str(exc))
        return 0.0


async def load_tikhub_endpoints(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """``loadOptionsMethod`` for the ``endpoint`` dropdown.

    ``all`` deliberately returns no options — ~1000 items is not a usable
    dropdown, and the LLM path accepts any id as free text anyway. The
    panel prompts to pick a platform first. A missing SDK must never break
    discovery, so failures degrade to an empty list with a warning.
    """
    platform = str((params or {}).get("platform") or "all")
    if platform == "all":
        return []
    try:
        matches, _ = await list_endpoints(platform=platform)
    except Exception as exc:  # noqa: BLE001 — SDK missing / introspection failure
        logger.warning("tikhub endpoint loader failed", platform=platform, error=str(exc))
        return []
    return [
        {
            "value": e.endpoint,
            "label": e.endpoint,
            "description": f"{e.summary} — {e.http_method or '?'} {e.path or ''}".strip(),
        }
        for e in matches
    ]


__all__ = [
    "PLATFORMS",
    "EndpointInfo",
    "ParamInfo",
    "bind_params",
    "call_endpoint",
    "endpoint_index",
    "list_endpoints",
    "load_tikhub_endpoints",
    "make_client",
    "platform_of",
    "raise_user_error",
    "resolve_endpoint",
    "to_plain",
    "track_tikhub_usage",
]
