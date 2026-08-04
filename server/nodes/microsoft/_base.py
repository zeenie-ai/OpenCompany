"""Shared helpers for Microsoft Graph plugins.

Every Microsoft plugin follows the same pattern:

    ensure a fresh access token -> ctx.connection("microsoft").<verb>(url)
    -> response.json() -> track usage -> return Output

``graph_request`` captures the token-freshness + base-URL + error
handling so each ``@Operation`` shrinks to the Graph-specific call +
argument shaping. Graph is plain bearer REST, so — unlike Google — no
SDK object is built; the :class:`Connection` facade injects the token
and retries once on 401/403.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

from core.logging import get_logger
from services.plugin import NodeUserError
from services.pricing import get_pricing_service

from ._auth_helper import ensure_fresh_microsoft_token

logger = get_logger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


async def graph_request(
    ctx,
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Issue an authed Microsoft Graph request.

    Args:
        ctx: NodeContext (provides the Connection factory + user_id).
        method: HTTP verb.
        path: Graph path beginning with ``/`` (appended to ``GRAPH_BASE_URL``),
            or an absolute URL (used verbatim, e.g. an ``@odata.nextLink``).
        params: Query string params.
        json: JSON request body.

    Returns:
        Parsed JSON dict, or ``None`` for empty-body responses (e.g. 202
        from sendMail, 204 from delete).

    Raises:
        NodeUserError: on a non-2xx Graph response, carrying Graph's own
            error message so the user/LLM can correct the input.
    """
    # Guarantee the STORED access token is fresh before the Connection
    # facade resolves + injects it (get_oauth_tokens does not refresh).
    await ensure_fresh_microsoft_token(getattr(ctx, "user_id", "owner"))

    url = path if path.startswith("http") else f"{GRAPH_BASE_URL}{path}"

    async with ctx.connection("microsoft") as conn:
        response = await conn.request(method, url, params=params, json=json)

    if response.status_code >= 400:
        raise NodeUserError(_format_graph_error(response))

    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except (ValueError, httpx.DecodingError):
        return None


async def graph_get_raw(
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    user_id: str = "owner",
) -> Dict[str, Any]:
    """Authed Graph GET for contexts without a NodeContext / Connection.

    The polling-trigger hooks (``fetch_ids`` / ``fetch_detail``) run inside
    the deployment loop and the Temporal poll activity — neither has a
    ``ctx`` to source the :class:`Connection` factory from. This helper
    resolves a fresh token via :func:`ensure_fresh_microsoft_token` and
    issues a plain httpx GET with the bearer header.

    Raises:
        NodeUserError: on a non-2xx Graph response.
    """
    token = await ensure_fresh_microsoft_token(user_id)
    url = path if path.startswith("http") else f"{GRAPH_BASE_URL}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        raise NodeUserError(_format_graph_error(response))
    if not response.content:
        return {}
    try:
        return response.json()
    except (ValueError, httpx.DecodingError):
        return {}


async def mark_message_read_raw(message_id: str, *, user_id: str = "owner") -> None:
    """PATCH a message's ``isRead`` flag to true (ctx-free; best-effort caller)."""
    token = await ensure_fresh_microsoft_token(user_id)
    url = f"{GRAPH_BASE_URL}/me/messages/{message_id}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.patch(url, json={"isRead": True}, headers={"Authorization": f"Bearer {token}"})


def _format_graph_error(response: httpx.Response) -> str:
    """Extract Microsoft Graph's structured error message for a user-facing warning."""
    try:
        body = response.json()
    except (ValueError, httpx.DecodingError):
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = err.get("code", "")
            message = err.get("message", "")
            detail = f"{code}: {message}".strip(": ") if (code or message) else ""
            if detail:
                return f"Microsoft Graph error ({response.status_code}): {detail}"
    return f"Microsoft Graph request failed with HTTP {response.status_code}"


def write_attachment_bytes(payload, *, ctx, filename, mime_type=None):
    """Write attachment bytes into the workspace; return ``(FileRef, abs_path)``.

    Mirrors ``services.media.workspace.write_audio``'s proven pattern
    (workspace_root -> resolve_media -> atomic_write_bytes) but returns a
    plain ``kind="file"`` :class:`FileRef` rather than an ``AudioRef`` — a PDF
    is not audio and must never claim ``kind="audio"`` (that asserts an
    ``inspect_audio`` probe that never ran). Files land under
    ``<workspace>/attachments/`` with a random-suffixed name so retries and
    repeated runs never collide or overwrite.

    The absolute path is returned alongside the ref because the downstream
    ``documentParser`` node consumes a plain path string (``file_path`` /
    ``input_dir``), not a FileRef.

    Raises:
        NodeUserError: on empty payload.
    """
    import hashlib
    import mimetypes
    from uuid import uuid4

    from nodes.filesystem._backend import atomic_write_bytes
    from services.media.refs import FileRef
    from services.media.workspace import _slugify, resolve_media, workspace_file_url, workspace_root
    from services.plugin import NodeUserError

    if not payload:
        raise NodeUserError("Refusing to write an empty attachment file.")

    root = workspace_root(ctx)
    node_id = str(getattr(ctx, "node_id", "") or "node")
    workflow_id = getattr(ctx, "workflow_id", None)

    # Preserve the real extension (documentParser globs on *.pdf); slugify the stem.
    dot = filename.rfind(".") if filename else -1
    stem = filename[:dot] if dot > 0 else (filename or "attachment")
    ext = filename[dot + 1 :].lower() if dot > 0 else ""
    safe = f"{_slugify(stem)}-{node_id[:8]}-{uuid4().hex[:6]}"
    name = f"{safe}.{ext}" if ext else safe
    rel = f"attachments/{name}"

    target = resolve_media(rel, ctx=ctx)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target, payload, root_dir=root)

    ref = FileRef(
        kind="file",
        path=rel,
        workflow_id=workflow_id,
        filename=filename or name,
        mime_type=mime_type or mimetypes.guess_type(filename or name)[0] or "application/octet-stream",
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        url=workspace_file_url(workflow_id, rel),
    )
    return ref, str(target)


async def track_microsoft_usage(
    node_id: str,
    action: str,
    resource_count: int,
    context: Dict[str, Any],
) -> Dict[str, float]:
    """Record a Microsoft Graph call in ``api_usage_metrics``.

    ``action`` maps through ``pricing.json``'s ``microsoft_graph``
    operation_map (send / read / search / reply / create / update /
    delete / list). Graph is free at our tier — analytics bookkeeping,
    cost is $0.
    """
    from services.plugin.deps import get_database

    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost("microsoft_graph", action, resource_count)

    db = get_database()
    await db.save_api_usage_metric(
        {
            "session_id": context.get("session_id", "default"),
            "node_id": node_id,
            "workflow_id": context.get("workflow_id"),
            "service": "microsoft_graph",
            "operation": cost_data.get("operation", action),
            "endpoint": action,
            "resource_count": resource_count,
            "cost": cost_data.get("total_cost", 0.0),
        }
    )
    return cost_data
