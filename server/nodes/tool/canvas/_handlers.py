"""Human-facing WebSocket API for the Canvas panel and docked sidebar.

Every request resolves its board scope from the persisted workflow graph and
the authenticated WebSocket. Neither the client nor the model can provide a
scope identifier. Item content is returned only on these authorized calls;
the ``canvas_updated`` broadcast stays metadata-only.

Security preamble copied from ``nodes/tool/simple_memory/_handlers.py`` —
the canonical template for panel handlers that read/mutate per-node state.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from fastapi import WebSocket

from services.plugin import NodeUserError
from services.plugin.deps import get_database
from services.plugin.ws import ws_response

from ._events import dispatch_canvas_updated
from ._store import CanvasScope, CanvasStore, CanvasStoreError


def _authenticated_owner(websocket: WebSocket) -> str:
    """Read server-authenticated identity without consulting request data."""
    state = getattr(websocket, "state", None)
    for attribute in ("user_id", "principal_id", "subject"):
        value = getattr(state, attribute, None) if state is not None else None
        if isinstance(value, (str, int)) and str(value).strip():
            return str(value)
    scope = getattr(websocket, "scope", None)
    if isinstance(scope, dict):
        for key in ("user_id", "principal_id", "subject"):
            value = scope.get(key)
            if isinstance(value, (str, int)) and str(value).strip():
                return str(value)
    # The current deployment is single-owner when no auth principal is
    # attached; this mirrors NodeContext.user_id's trusted default.
    return "owner"


def _require_external_socket(websocket: WebSocket) -> None:
    """The internal unauthenticated worker socket may not touch the board.

    Defence in depth behind the allowlist in ``services.authz.ws_surface``
    (deny-by-default for new handlers) — same posture as Memory and Context.
    """
    scope = getattr(websocket, "scope", {}) or {}
    if scope.get("path") == "/ws/internal":
        raise NodeUserError("Canvas access requires an authenticated client")


async def _resolve_store_and_scope(
    data: Dict[str, Any], websocket: WebSocket
) -> tuple[CanvasStore, CanvasScope]:
    _require_external_socket(websocket)
    workflow_id = str(data.get("workflow_id") or "").strip()
    node_id = str(data.get("node_id") or "").strip()
    if not workflow_id:
        raise NodeUserError("workflow_id required")
    if not node_id:
        raise NodeUserError("node_id required")

    database = get_database()
    saved = await database.get_workflow(workflow_id)
    if saved is None:
        raise NodeUserError("Workflow not found")
    graph = saved.data if hasattr(saved, "data") else saved.get("data", saved)
    owner_id = _authenticated_owner(websocket)
    stored_owner = (
        str(graph.get("owner_id") or "") if isinstance(graph, dict) else ""
    )
    if stored_owner and stored_owner != owner_id:
        raise NodeUserError("Workflow access denied")
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    matches = [
        node
        for node in nodes
        if str(node.get("id") or "") == node_id
        and str(node.get("type") or node.get("data", {}).get("type") or "")
        == "canvas"
    ]
    if len(matches) != 1:
        raise NodeUserError(
            "Canvas node does not belong to the requested workflow"
        )
    return CanvasStore(database), CanvasScope(
        owner_id=owner_id,
        workflow_id=workflow_id,
        node_id=node_id,
    )


@ws_response
async def handle_canvas_list(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    store, scope = await _resolve_store_and_scope(data, websocket)
    result = await store.list(scope)
    return {"success": True, **result}


@ws_response
async def handle_canvas_remove(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    store, scope = await _resolve_store_and_scope(data, websocket)
    item_id = str(data.get("item_id") or "").strip()
    if not item_id:
        raise NodeUserError("item_id required")
    try:
        result = await store.remove(scope, item_id)
    except CanvasStoreError as exc:
        raise NodeUserError(str(exc)) from exc
    await dispatch_canvas_updated(
        workflow_id=scope.workflow_id,
        node_id=scope.node_id,
        revision=result["revision"],
    )
    return {"success": True, **result}


@ws_response
async def handle_canvas_clear(
    data: Dict[str, Any], websocket: WebSocket
) -> Dict[str, Any]:
    store, scope = await _resolve_store_and_scope(data, websocket)
    result = await store.clear(scope)
    await dispatch_canvas_updated(
        workflow_id=scope.workflow_id,
        node_id=scope.node_id,
        revision=result["revision"],
    )
    return {"success": True, **result}


@ws_response
async def handle_canvas_add(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Put a file from the workflow's workspace on the board: the owner's own
    action (Screenshot to Canvas on the phone). ``path`` is workspace-relative;
    its reference is rebuilt from the file, as the node's ``display`` does,
    never taken from the client."""
    from types import SimpleNamespace

    from services.workspace_locator import resolve_workspace_root

    from . import _build_ref

    store, scope = await _resolve_store_and_scope(data, websocket)
    path = str(data.get("path") or "").strip()
    if not path:
        raise NodeUserError("path required")
    root = await resolve_workspace_root(scope.workflow_id, get_database(), allow_default=False)
    context = SimpleNamespace(workspace_dir=str(root), workflow_id=scope.workflow_id, node_id=scope.node_id, raw={})
    item = {"kind": "file", "ref": _build_ref(context, path), "source": "owner"}
    try:
        added, revision, total = await store.append(scope, [item], mode="append")
    except CanvasStoreError as exc:
        raise NodeUserError(str(exc)) from exc
    await dispatch_canvas_updated(workflow_id=scope.workflow_id, node_id=scope.node_id, revision=revision)
    return {"success": True, "item": added[0], "revision": revision, "count": total}


@ws_response
async def handle_canvas_version(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """One version of an item, whole."""
    store, scope = await _resolve_store_and_scope(data, websocket)
    item_id = str(data.get("item_id") or "").strip()
    version = data.get("version")
    if not item_id or not isinstance(version, int) or version < 1:
        raise NodeUserError("item_id and version required")
    try:
        item = await store.version(scope, item_id, version)
    except CanvasStoreError as exc:
        raise NodeUserError(str(exc)) from exc
    return {"success": True, "item": item}


WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]
WS_HANDLERS: Dict[str, WSHandler] = {
    "canvas_list": handle_canvas_list,
    "canvas_add": handle_canvas_add,
    "canvas_remove": handle_canvas_remove,
    "canvas_clear": handle_canvas_clear,
    "canvas_version": handle_canvas_version,
}


__all__ = [
    "WS_HANDLERS",
    "handle_canvas_add",
    "handle_canvas_clear",
    "handle_canvas_list",
    "handle_canvas_remove",
    "handle_canvas_version",
]
