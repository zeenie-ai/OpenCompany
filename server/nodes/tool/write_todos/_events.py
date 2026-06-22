"""CloudEvents factory + centralized dispatch for the writeTodos plugin.

Plugin-scoped per RFC plugin_authoring_rfc.md §6.4 — the typed event
factory lives in the plugin folder, mirroring
``nodes/telegram/_events.py``. Emission goes through the centralized
``services.events.dispatch.emit`` (the same path
``dispatch_telegram_message_received`` uses), NOT a direct
``status_broadcaster.broadcast`` call — ``emit`` fans the envelope out to
any running Temporal consumers AND to in-process WS clients uniformly.

A ``todos_updated`` frame fires whenever the workflow's todo list
changes — both when the agent calls the ``write_todos`` tool mid-run and
when a user edits the list from the parameter panel (``set_todos`` WS
handler). It is deliberately NOT a ``node_status`` "executing" broadcast:
a manual edit must not make the canvas node glow as if it were running.
The frontend routes the ``todos_updated`` wire key to refresh the open
Current Todos panel.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.events.envelope import WorkflowEvent

# Wire-routing key the FE switch dispatches on (outer WS frame ``type``).
_TODOS_UPDATED_WIRE_KEY = "todos_updated"


def todos_updated(
    *,
    session_key: str,
    todos: List[dict],
    node_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> WorkflowEvent:
    """Todo-list-changed envelope. ``subject`` is the session key (the
    workflow the list is shared under) so the FE can route the update to
    the right ``['todos', session_key]`` query."""
    data: Dict[str, Any] = {
        "session_key": session_key,
        "todos": todos,
        "node_id": node_id,
        "workflow_id": workflow_id,
    }
    return WorkflowEvent(
        source="machinaos://nodes/write_todos",
        type="com.machinaos.todos.updated",
        subject=session_key,
        data=data,
    )


async def dispatch_todos_updated(
    *,
    session_key: str,
    todos: List[dict],
    node_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> None:
    """Emit the typed ``todos_updated`` CloudEvent via the centralized
    dispatcher (``services.events.dispatch.emit``)."""
    from services.events.dispatch import emit

    await emit(
        todos_updated(
            session_key=session_key,
            todos=todos,
            node_id=node_id,
            workflow_id=workflow_id,
        ),
        wire_routing_key=_TODOS_UPDATED_WIRE_KEY,
    )


__all__ = ["dispatch_todos_updated", "todos_updated"]
