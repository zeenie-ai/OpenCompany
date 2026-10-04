"""Shared Graph API plumbing for the WhatsApp Cloud API nodes.

One place owns the version pin, the request helpers and the error-code
translation, so the four node files contain message-shaping logic and
nothing else.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.logging import get_logger
from services.plugin import NodeContext, NodeUserError

logger = get_logger(__name__)


GRAPH_BASE_URL = "https://graph.facebook.com"

# Pinned deliberately, never interpolated per call site. Meta guarantees a
# version at least two years from release, and an *expired* version does not
# error -- calls silently fall through to the next oldest usable version, so
# an unpinned integration changes behaviour without any signal.
#
# v25.0 released 2026-02-18. Pin reviewed 2026-08-04.
# Meta's own docs are internally inconsistent here (Get Started shows v23.0,
# the phone-numbers page shows a stale v15.0, the published OpenAPI spec is
# v23.0), so do not infer the current version from any single sample page.
GRAPH_API_VERSION = "v25.0"

CREDENTIAL_ID = "whatsapp_business"


# --------------------------------------------------------------------------
# Error classification
# --------------------------------------------------------------------------
#
# Meta returns a numeric ``code`` inside a Graph error envelope. The split
# that matters is retryable-vs-terminal: NodeUserError is in the shared
# non-retryable list, so classifying a transient throttle as one would fail
# fast instead of backing off, and classifying a permanent rejection as
# retryable burns three attempts and re-bills nothing but time.
#
# Codes verified against
# developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes

# Token expired / invalidated. Retryable only after the credential refreshes.
_AUTH_CODES = frozenset({0, 190})

# Permission genuinely absent -- retrying cannot help.
_PERMISSION_CODES = frozenset({3, 10})

# Rate limits and throughput ceilings. Meta documents a 4^X second backoff
# for the per-user case.
_THROTTLE_CODES = frozenset({4, 80007, 130429, 131048, 131056, 133008, 133009})

# Server-side or transient; worth another attempt.
_TRANSIENT_CODES = frozenset({131000, 131057, 133004})

# Terminal policy decisions. 131050 is documented "do not retry".
_POLICY_CODES = frozenset({368, 131050, 131064})

# The 24-hour customer service window closed. Actionable, and the remedy is
# a different node, so it gets its own message.
_WINDOW_CLOSED_CODE = 131047


def _is_permission_code(code: int) -> bool:
    return code in _PERMISSION_CODES or 200 <= code <= 299


def classify_error(code: int) -> Tuple[str, bool]:
    """Return ``(category, retryable)`` for a Meta error code."""
    if code in _AUTH_CODES:
        return "auth", True
    if _is_permission_code(code):
        return "permission", False
    if code in _THROTTLE_CODES:
        return "throttle", True
    if code in _TRANSIENT_CODES:
        return "transient", True
    if code in _POLICY_CODES:
        return "policy", False
    if code == _WINDOW_CLOSED_CODE:
        return "window_closed", False
    if 132000 <= code <= 132999:
        return "template", False
    if 133000 <= code <= 133999:
        return "account", False
    return "unknown", False


def _friendly_message(code: int, category: str, detail: str) -> str:
    if category == "window_closed":
        return (
            "This WhatsApp user has not messaged you in the last 24 hours, so "
            "only an approved template message can be delivered. Use the "
            "WhatsApp Cloud Template node instead. "
            f"(Meta error {code}: {detail})"
        )
    if category == "auth":
        return f"WhatsApp access token was rejected. Reconnect the credential. (Meta error {code}: {detail})"
    if category == "permission":
        return (
            "The access token lacks a required permission "
            "(whatsapp_business_messaging / whatsapp_business_management). "
            f"(Meta error {code}: {detail})"
        )
    if category == "template":
        return f"Template rejected by Meta. (error {code}: {detail})"
    return f"WhatsApp API error {code}: {detail}"


def raise_for_graph_error(payload: Dict[str, Any], status_code: int) -> None:
    """Translate a Graph error envelope into the right exception.

    ``error_data.details`` is the field that actually explains the failure;
    ``message`` is frequently generic. ``fbtrace_id`` is always logged because
    it is the identifier Meta support asks for and it cannot be recovered
    afterwards.
    """
    error = (payload or {}).get("error") or {}
    code = int(error.get("code") or 0)
    detail = (error.get("error_data") or {}).get("details") or error.get("message") or "no detail provided"
    fbtrace_id = error.get("fbtrace_id")
    category, retryable = classify_error(code)

    logger.warning(
        "whatsapp cloud api error",
        code=code,
        subcode=error.get("error_subcode"),
        category=category,
        retryable=retryable,
        status_code=status_code,
        fbtrace_id=fbtrace_id,
    )

    if category == "auth":
        # Annotated PermissionError gets the framework's credential envelope
        # and a reconnect affordance in the UI, which a NodeUserError does not.
        exc = PermissionError(_friendly_message(code, category, detail))
        exc.provider = CREDENTIAL_ID  # type: ignore[attr-defined]
        exc.reason = "invalid"  # type: ignore[attr-defined]
        exc.auth = "api_key"  # type: ignore[attr-defined]
        raise exc

    if retryable:
        # Not a NodeUserError: that type is non-retryable in the shared retry
        # policy, so raising one here would defeat the backoff Meta asks for.
        raise RuntimeError(_friendly_message(code, category, detail))

    raise NodeUserError(_friendly_message(code, category, detail))


# --------------------------------------------------------------------------
# Request helpers
# --------------------------------------------------------------------------


async def _request(
    ctx: NodeContext,
    method: str,
    path: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    files: Optional[Any] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call the Graph API with the node's credential and translate failures."""
    url = f"{GRAPH_BASE_URL}/{GRAPH_API_VERSION}/{path.lstrip('/')}"
    async with ctx.connection(CREDENTIAL_ID) as conn:
        response = await conn.request(
            method, url, json=json, params=params, files=files, data=data
        )

    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}

    if response.status_code >= 400:
        raise_for_graph_error(payload, response.status_code)
    return payload


async def graph_post(
    ctx: NodeContext,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    files: Optional[Any] = None,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return await _request(ctx, "POST", path, json=body, files=files, data=data)


async def graph_get(
    ctx: NodeContext, path: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return await _request(ctx, "GET", path, params=params)


async def graph_delete(
    ctx: NodeContext, path: str, params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return await _request(ctx, "DELETE", path, params=params)


async def resolve_phone_number_id(ctx: NodeContext) -> str:
    """The business number to send from — credential-sourced only.

    Deliberately takes no node-level override. This value selects which
    business identity a message is sent *from*. A declared parameter would
    appear in the model's tool schema (a dual-purpose ActionNode's schema is
    its whole Params), even locked with ``server_controlled_fields``; sourcing
    it from the credential keeps it out of the schema and every merge.

    A per-node override belongs with multi-number support, which needs the
    deployed-trigger filtering that is currently deferred.
    """
    from services.plugin.deps import get_auth_service

    stored = await get_auth_service().get_api_key("whatsapp_business_phone_number_id")
    if not stored:
        raise NodeUserError(
            "No WhatsApp business phone number is configured. Add the Phone "
            "Number ID to the WhatsApp Business credential."
        )
    return str(stored).strip()


def normalize_recipient(value: str) -> str:
    """Meta wants digits, tolerating a leading '+'.

    Stripping spaces, dashes and parentheses here means a number pasted from
    a contacts app works instead of failing as error 131009.
    """
    cleaned = "".join(ch for ch in (value or "") if ch.isdigit() or ch == "+")
    if not cleaned.lstrip("+"):
        raise NodeUserError(f"'{value}' is not a usable WhatsApp phone number.")
    return cleaned


__all__ = [
    "CREDENTIAL_ID",
    "GRAPH_API_VERSION",
    "GRAPH_BASE_URL",
    "classify_error",
    "graph_delete",
    "graph_get",
    "graph_post",
    "normalize_recipient",
    "raise_for_graph_error",
    "resolve_phone_number_id",
]
