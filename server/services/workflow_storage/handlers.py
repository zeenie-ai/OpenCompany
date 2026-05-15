"""Workflow storage WS handlers extracted from ``routers/websocket.py`` (Wave 13.7).

5 handlers wrapping the workflow-record CRUD surface:
  - ``save_workflow`` — persist new/updated workflow JSON.
  - ``import_workflow`` — two-step import preview + commit
    (delegates to ``services.workflow_import``).
  - ``get_workflow`` — fetch by ID.
  - ``get_all_workflows`` — sidebar list (minimal projection).
  - ``delete_workflow`` — remove by ID.

Wire shape preserved across the move.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket

from core.container import container
from core.logging import get_logger
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)


async def handle_save_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Save workflow to database."""
    database = container.database()
    success = await database.save_workflow(
        workflow_id=data["workflow_id"],
        name=data["name"],
        data=data.get("data", {}),
    )
    return {"success": success, "workflow_id": data["workflow_id"]}


@ws_handler()
async def handle_import_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Import a workflow JSON. Two-step UX:

    First call with just the workflow object returns a preview if
    confirmations are needed (name conflict, missing credentials). The
    frontend prompts the user, then re-calls with ``name`` set and
    ``force_credentials=True`` to commit.

    Body fields:
        workflow: Raw workflow dict (nodes, edges, optional nodeParameters).
        name: User-confirmed final workflow name; omit on first call to
            let the server report a name conflict.
        force_credentials: Skip the missing-credential preview gate when
            the user has acknowledged the warning.

    See ``services.workflow_import.import_workflow`` for the full
    orchestrator contract.
    """
    from services.workflow_import import import_workflow

    workflow_payload = data.get("workflow")
    if not isinstance(workflow_payload, dict):
        return {"success": False, "error": "workflow payload required"}

    return await import_workflow(
        workflow_payload,
        name=data.get("name"),
        force_credentials=bool(data.get("force_credentials")),
        auth_service=container.auth_service(),
        database=container.database(),
    )


async def handle_get_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get workflow by ID."""
    database = container.database()
    workflow = await database.get_workflow(data["workflow_id"])
    if workflow:
        return {
            "success": True,
            "workflow": {
                "id": workflow.id,
                "name": workflow.name,
                "data": workflow.data,
                "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
                "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
            },
        }
    return {"success": False, "error": "Workflow not found"}


async def handle_get_all_workflows(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get all workflows."""
    database = container.database()
    workflows = await database.get_all_workflows()
    return {
        "success": True,
        "workflows": [
            {
                "id": w.id,
                "name": w.name,
                "nodeCount": len(w.data.get("nodes", [])) if w.data else 0,
                "created_at": w.created_at.isoformat() if w.created_at else None,
                "updated_at": w.updated_at.isoformat() if w.updated_at else None,
            }
            for w in workflows
        ],
    }


async def handle_delete_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Delete workflow."""
    database = container.database()
    success = await database.delete_workflow(data["workflow_id"])
    return {"success": success, "workflow_id": data["workflow_id"]}


WS_HANDLERS: Dict[str, Any] = {
    "save_workflow": handle_save_workflow,
    "import_workflow": handle_import_workflow,
    "get_workflow": handle_get_workflow,
    "get_all_workflows": handle_get_all_workflows,
    "delete_workflow": handle_delete_workflow,
}


__all__ = [
    "WS_HANDLERS",
    "handle_delete_workflow",
    "handle_get_all_workflows",
    "handle_get_workflow",
    "handle_import_workflow",
    "handle_save_workflow",
]
