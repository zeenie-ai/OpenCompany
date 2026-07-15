"""Back-compat shim for tests importing from `services.handlers.todo`.

Wave 11.C moved the write-todos handler body into
`server/nodes/tool/write_todos.py` as `WriteTodosNode.write`. The
contract tests still import the flat `execute_write_todos` function,
so this module re-exposes it with the pre-refactor flat-envelope
signature.
"""

from __future__ import annotations

from typing import Any, Dict

from services.todo_service import get_todo_service, todo_session_key


async def execute_write_todos(
    args: Dict[str, Any],
    config: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Legacy (args, config) -> dict contract.

    Modern calls use a workflow + node composite key. Calls missing node
    identity keep the pre-refactor workflow/default fallback.
    """
    config = config or {}
    session_key = todo_session_key(config.get("workflow_id"), config.get("node_id"))
    todos = args.get("todos", [])
    service = get_todo_service()
    stored = service.write(session_key, todos)

    broadcaster = config.get("broadcaster")
    node_id = config.get("node_id")
    if broadcaster and node_id:
        await broadcaster.update_node_status(
            node_id,
            "executing",
            {"phase": "todo_update", "todos": stored},
            workflow_id=config.get("workflow_id"),
        )

    # Keep compatibility callers on the same event path as the plugin so an
    # open Current Todos panel receives only this workflow+node update.
    from nodes.tool.write_todos._events import dispatch_todos_updated

    await dispatch_todos_updated(
        todos=stored,
        node_id=node_id,
        workflow_id=config.get("workflow_id"),
    )

    return {
        "success": True,
        "message": f"Updated todo list ({len(stored)} items)",
        "count": len(stored),
        # Plain list — matches the plugin's WriteTodosOutput contract
        # (``todos: Optional[list]``); the pre-fix ``format_for_llm()``
        # leaked TodoService's raw JSON string here.
        "todos": stored,
    }


async def handle_write_todos(
    node_id: str,
    node_type: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Pre-refactor node-executor signature wrapper around the plugin."""
    cfg = {
        "workflow_id": context.get("workflow_id"),
        "node_id": node_id,
        "broadcaster": context.get("broadcaster"),
    }
    return await execute_write_todos(parameters, cfg)
