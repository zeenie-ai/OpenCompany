"""Node.js API compatibility routes for seamless migration."""

from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from core.container import container
from core.database import Database
from services.workflow import WorkflowService
from core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["nodejs-compatibility"])


# Pydantic models matching Node.js API
class NodeExecuteRequest(BaseModel):
    nodeId: str
    nodeType: str
    parameters: Optional[Dict[str, Any]] = {}
    nodes: Optional[List[Dict[str, Any]]] = []
    edges: Optional[List[Dict[str, Any]]] = []


class WorkflowSaveRequest(BaseModel):
    name: str
    data: Dict[str, Any]


# Health and status endpoints
@router.get("/")
async def root():
    """Node.js compatible root endpoint."""
    return {
        "message": "React Flow Project API Server",
        "status": "running",
        "version": "2.0.0-python",
        "endpoints": {"health": "/api/health", "workflows": "/api/workflows", "nodes": "/api/nodes", "execute": "/api/nodes/execute"},
        "services": {"main": "http://localhost:3010", "python": "http://localhost:3010"},
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/api/health")
async def health_check():
    """Node.js compatible health check."""
    return {"status": "OK", "message": "React Flow Server is running", "timestamp": datetime.now().isoformat()}


# Workflow management endpoints
@router.get("/api/workflows")
async def get_workflows(database: Database = Depends(lambda: container.database())):
    """Get all workflows (Node.js compatible)."""
    try:
        workflows = await database.get_all_workflows()
        return {"workflows": workflows}
    except Exception as e:
        logger.error("Failed to get workflows", error=str(e))
        return {"workflows": []}


@router.post("/api/workflows")
async def save_workflow(request: WorkflowSaveRequest, database: Database = Depends(lambda: container.database())):
    """Save workflow (Node.js compatible)."""
    try:
        workflow_id = str(int(datetime.now().timestamp() * 1000))  # Node.js style ID
        success = await database.save_workflow(workflow_id=workflow_id, name=request.name, data=request.data)

        if success:
            return {"success": True, "id": workflow_id, "message": "Workflow saved successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to save workflow")

    except Exception as e:
        logger.error("Failed to save workflow", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, database: Database = Depends(lambda: container.database())):
    """Get workflow by ID (Node.js compatible)."""
    try:
        workflow = await database.get_workflow(workflow_id)

        if workflow:
            return {"id": workflow.id, "name": workflow.name, "data": workflow.data}
        else:
            return {"id": workflow_id, "name": "Sample Workflow", "data": {"nodes": [], "edges": []}}

    except Exception as e:
        logger.error("Failed to get workflow", workflow_id=workflow_id, error=str(e))
        return {"id": workflow_id, "name": "Sample Workflow", "data": {"nodes": [], "edges": []}}


# Main execution endpoint - Node.js compatible
@router.post("/api/nodes/execute")
async def execute_node(request: NodeExecuteRequest, workflow_service: WorkflowService = Depends(lambda: container.workflow_service())):
    """Execute a single node (Node.js compatible)."""
    try:
        if not request.nodeId or not request.nodeType:
            raise HTTPException(status_code=400, detail="nodeId and nodeType are required")

        execution_id = str(uuid4())
        logger.info("Executing node", node_id=request.nodeId, execution_id=execution_id)

        # Execute the node using workflow service
        result = await workflow_service.execute_node(
            node_id=request.nodeId,
            node_type=request.nodeType,
            parameters=request.parameters or {},
            nodes=request.nodes or [],
            edges=request.edges or [],
            session_id="default",
        )

        # Transform result to match Node.js format
        if result.get("success"):
            return {
                "success": True,
                "executionId": execution_id,
                "nodeId": request.nodeId,
                "nodeType": request.nodeType,
                "result": result.get("result", {}),
                "executionTime": result.get("execution_time", 0),
                "timestamp": result.get("timestamp", datetime.now().isoformat()),
            }
        else:
            return {
                "success": False,
                "executionId": execution_id,
                "nodeId": request.nodeId,
                "nodeType": request.nodeType,
                "error": result.get("error", "Unknown error"),
                "executionTime": result.get("execution_time", 0),
                "timestamp": result.get("timestamp", datetime.now().isoformat()),
            }

    except Exception as e:
        logger.error("Node execution error", node_id=request.nodeId, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# Execution status and output endpoints
@router.get("/api/executions/{execution_id}")
async def get_execution_status(execution_id: str):
    """Get execution status (Node.js compatible)."""
    # For simplicity, return success status
    # In a full implementation, you'd track execution status
    return {"success": True, "executionId": execution_id, "status": "completed", "timestamp": datetime.now().isoformat()}


@router.get("/api/nodes/{node_id}/output")
async def get_node_output(node_id: str, workflow_service: WorkflowService = Depends(lambda: container.workflow_service())):
    """Get node output data (Node.js compatible)."""
    try:
        result = await workflow_service.get_workflow_node_output(node_id)

        if result.get("success"):
            return {"success": True, "nodeId": node_id, "output": result.get("data", {})}
        else:
            return {"success": False, "nodeId": node_id, "error": result.get("error", "Node output not found")}

    except Exception as e:
        logger.error("Get node output error", node_id=node_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/executions/clear")
async def clear_execution_cache():
    """Clear execution cache (Node.js compatible)."""
    try:
        # Clear workflow service cache if implemented
        return {"success": True, "message": "Execution cache cleared"}
    except Exception as e:
        logger.error("Clear cache error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# Legacy endpoint for backwards compatibility
@router.post("/api/execute/{node_id}")
async def legacy_execute_node(
    node_id: str, request: Dict[str, Any], workflow_service: WorkflowService = Depends(lambda: container.workflow_service())
):
    """Legacy execution endpoint (Node.js compatible)."""
    try:
        node_type = request.get("nodeType")
        if not node_type:
            raise HTTPException(status_code=400, detail="nodeType is required")

        execution_id = str(uuid4())

        result = await workflow_service.execute_node(
            node_id=node_id,
            node_type=node_type,
            parameters=request.get("parameters", {}),
            nodes=request.get("nodes", []),
            edges=request.get("edges", []),
            session_id="default",
        )

        # Return in Node.js format
        if result.get("success"):
            return {
                "success": True,
                "executionId": execution_id,
                "nodeId": node_id,
                "nodeType": node_type,
                "result": result.get("result", {}),
                "executionTime": result.get("execution_time", 0),
                "timestamp": result.get("timestamp", datetime.now().isoformat()),
            }
        else:
            return {
                "success": False,
                "executionId": execution_id,
                "nodeId": node_id,
                "nodeType": node_type,
                "error": result.get("error", "Unknown error"),
                "executionTime": result.get("execution_time", 0),
                "timestamp": result.get("timestamp", datetime.now().isoformat()),
            }

    except Exception as e:
        logger.error("Legacy execution error", node_id=node_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
