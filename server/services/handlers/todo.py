"""Back-compat shim for tests importing from `services.handlers.todo`.

Wave 11.C moved the write-todos handler body into
`server/nodes/tool/write_todos.py` as `WriteTodosNode.write`. The
contract tests still import the flat `execute_write_todos` function,
so this module re-exposes it with the pre-refactor flat-envelope
signature.
"""

from __future__ import annotations

from typing import Any, Dict

from services.todo_service import get_todo_service


async def execute_write_todos(
    args: Dict[str, Any],
    config: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Legacy (args, config) -> dict contract.

    session_key picks config.workflow_id > config.node_id > 'default'
    (matches pre-refactor handler precedence).
    """
    config = config or {}
    session_key = config.get("workflow_id") or config.get("node_id") or "default"
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

    return {
        "success": True,
        "message": f"Updated todo list ({len(stored)} items)",
        "count": len(stored),
        "todos": service.format_for_llm(session_key),
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
