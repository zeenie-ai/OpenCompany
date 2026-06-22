"""WebSocket handlers for the writeTodos plugin.

Owned by the plugin folder; the package ``__init__`` registers the
:data:`WS_HANDLERS` dict into ``services.ws_handler_registry`` so the
core router needs no edit (same self-containment pattern as
``nodes/telegram/_handlers.py``).

These give the parameter-panel Current Todos editor a read/write path
into the live ``TodoService`` state, which is keyed by ``workflow_id``
(``node_id`` fallback) — the same precedence the ``write`` op uses, so
the panel sees exactly what the agent wrote at runtime.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket

from services.plugin.ws import ws_response

from ._events import dispatch_todos_updated


def _session_key(data: Dict[str, Any]) -> str:
    """Mirror the write op's keying precedence
    (``__init__.py``: ``ctx.workflow_id or ctx.node_id or "default"``)."""
    return data.get("workflow_id") or data.get("node_id") or "default"


@ws_response
async def handle_get_todos(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Return the current todo list for the open workflow (node_id fallback)."""
    from services.todo_service import get_todo_service

    session_key = _session_key(data)
    return {
        "success": True,
        "todos": get_todo_service().get(session_key),
        "session_key": session_key,
    }


@ws_response
async def handle_set_todos(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Replace the workflow's todo list (panel edits) and broadcast the change.

    ``TodoService.write`` validates + normalises each item (drops anything
    but ``content``/``status``, coerces unknown statuses to ``pending``),
    so the returned ``stored`` list is the canonical state.
    """
    from services.todo_service import get_todo_service

    session_key = _session_key(data)
    stored = get_todo_service().write(session_key, data.get("todos", []) or [])

    await dispatch_todos_updated(
        session_key=session_key,
        todos=stored,
        node_id=data.get("node_id"),
        workflow_id=data.get("workflow_id"),
    )
    return {"success": True, "todos": stored, "session_key": session_key}


WS_HANDLERS = {
    "get_todos": handle_get_todos,
    "set_todos": handle_set_todos,
}


__all__ = ["WS_HANDLERS", "handle_get_todos", "handle_set_todos"]
