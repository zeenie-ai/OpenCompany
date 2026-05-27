"""Database operations routes (replaces frontend storage)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Any, Dict

from core.container import container
from core.database import Database
from core.logging import get_logger
from services.example_loader import import_examples_for_user
from services.workflow_storage.handlers import handle_save_workflow

logger = get_logger(__name__)
router = APIRouter(prefix="/api/database", tags=["database"])


class NodeParameterRequest(BaseModel):
    node_id: str
    parameters: Dict[str, Any]


class WorkflowSaveRequest(BaseModel):
    workflow_id: str
    name: str
    data: Dict[str, Any]


@router.post("/node-parameters")
async def save_node_parameters(request: NodeParameterRequest, database: Database = Depends(lambda: container.database())):
    """Save node parameters (replaces frontend Dexie)."""
    try:
        success = await database.save_node_parameters(request.node_id, request.parameters)
        return {"success": success}
    except Exception as e:
        logger.error("Failed to save node parameters", error=str(e), exc_info=True)
        return {"success": False, "error": "Failed to save node parameters"}


@router.get("/node-parameters/{node_id}")
async def get_node_parameters(node_id: str, database: Database = Depends(lambda: container.database())):
    """Get node parameters (replaces frontend Dexie)."""
    try:
        parameters = await database.get_node_parameters(node_id)
        return {"success": True, "parameters": parameters}
    except Exception as e:
        logger.error("Failed to get node parameters", error=str(e), exc_info=True)
        return {"success": False, "error": "Failed to get node parameters"}


@router.delete("/node-parameters/{node_id}")
async def delete_node_parameters(node_id: str, database: Database = Depends(lambda: container.database())):
    """Delete node parameters (replaces frontend Dexie)."""
    try:
        success = await database.delete_node_parameters(node_id)
        return {"success": success}
    except Exception as e:
        logger.error("Failed to delete node parameters", error=str(e), exc_info=True)
        return {"success": False, "error": "Failed to delete node parameters"}


# ============================================================================
# Workflow Operations
# ============================================================================


@router.post("/workflows")
async def save_workflow(request: WorkflowSaveRequest):
    """Save workflow — REST passthrough to the WS handler.

    Single source of truth lives in
    :func:`services.workflow_storage.handlers.handle_save_workflow` —
    it owns slug allocation, the workspace-dir rename on name change,
    and the CloudEvents ``workflow.renamed`` broadcast.
    """
    try:
        return await handle_save_workflow(
            {"workflow_id": request.workflow_id, "name": request.name, "data": request.data},
            websocket=None,  # type: ignore[arg-type]  # unused by the handler
        )
    except Exception as e:
        logger.error("Failed to save workflow", error=str(e), exc_info=True)
        return {"success": False, "error": "Failed to save workflow"}


@router.get("/workflows")
async def get_all_workflows(database: Database = Depends(lambda: container.database())):
    """Get all workflows."""
    try:
        # Auto-load example workflows on first fetch
        user_id = "default"
        settings = await database.get_user_settings(user_id)

        if not settings or not settings.get("examples_loaded", False):
            # First time - import examples
            count = await import_examples_for_user(database)
            if count > 0:
                logger.info(f"Auto-loaded {count} example workflows")

            # Mark as loaded using existing save_user_settings
            current = settings or {}
            current["examples_loaded"] = True
            await database.save_user_settings(current, user_id)

        workflows = await database.get_all_workflows()
        return {
            "success": True,
            "workflows": [
                {
                    "id": w.id,
                    "name": w.name,
                    "slug": w.slug,
                    "nodeCount": len(w.data.get("nodes", [])) if w.data else 0,
                    "createdAt": w.created_at.isoformat() if w.created_at else None,
                    "lastModified": w.updated_at.isoformat() if w.updated_at else None,
                }
                for w in workflows
            ],
        }
    except Exception as e:
        logger.error("Failed to get workflows", error=str(e), exc_info=True)
        return {"success": False, "error": "Failed to get workflows"}


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, database: Database = Depends(lambda: container.database())):
    """Get workflow by ID."""
    try:
        workflow = await database.get_workflow(workflow_id)
        if workflow:
            return {
                "success": True,
                "workflow": {
                    "id": workflow.id,
                    "name": workflow.name,
                    "slug": workflow.slug,
                    "data": workflow.data,
                    "createdAt": workflow.created_at.isoformat() if workflow.created_at else None,
                    "lastModified": workflow.updated_at.isoformat() if workflow.updated_at else None,
                },
            }
        return {"success": False, "error": "Workflow not found"}
    except Exception as e:
        logger.error("Failed to get workflow", error=str(e), exc_info=True)
        return {"success": False, "error": "Failed to get workflow"}


@router.delete("/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, database: Database = Depends(lambda: container.database())):
    """Delete workflow."""
    try:
        success = await database.delete_workflow(workflow_id)
        return {"success": success, "workflow_id": workflow_id}
    except Exception as e:
        logger.error("Failed to delete workflow", error=str(e), exc_info=True)
        return {"success": False, "error": "Failed to delete workflow"}
