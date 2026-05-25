"""Temporal executor for MachinaOs workflow execution.

Provides the same interface as WorkflowExecutor but delegates
execution to Temporal for durable workflow orchestration.
"""

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from temporalio.client import Client

from core.logging import get_logger
from .workflow import MachinaWorkflow

logger = get_logger(__name__)


class TemporalExecutor:
    """Workflow executor that uses Temporal for durable execution.

    Provides a compatible interface with the existing WorkflowExecutor
    so it can be used as a drop-in replacement when Temporal is enabled.
    """

    def __init__(
        self,
        client: Client,
        task_queue: str = "machina-tasks",
        status_callback: Optional[Callable] = None,
    ):
        """Initialize the Temporal executor.

        Args:
            client: Connected Temporal client
            task_queue: Temporal task queue name
            status_callback: Optional callback for node status updates
        """
        self.client = client
        self.task_queue = task_queue
        self.status_callback = status_callback

    async def execute_workflow(
        self,
        workflow_id: str,
        nodes: List[Dict],
        edges: List[Dict],
        session_id: str = "default",
        enable_caching: bool = True,
    ) -> Dict[str, Any]:
        """Execute a workflow using Temporal.

        Args:
            workflow_id: Unique workflow identifier
            nodes: List of node definitions
            edges: List of edge definitions
            session_id: Session identifier
            enable_caching: Whether to enable result caching (passed to activity)

        Returns:
            Dict with success, outputs, execution_trace, and timing info
        """
        start_time = time.time()
        execution_id = f"temporal-{uuid.uuid4().hex[:8]}"

        logger.info(
            "Starting Temporal workflow execution",
            workflow_id=workflow_id,
            execution_id=execution_id,
            node_count=len(nodes),
            edge_count=len(edges),
        )

        try:
            # Execute workflow via Temporal
            result = await self.client.execute_workflow(
                MachinaWorkflow.run,
                {
                    "nodes": nodes,
                    "edges": edges,
                    "session_id": session_id,
                    "workflow_id": workflow_id,
                },
                id=execution_id,
                task_queue=self.task_queue,
            )

            execution_time = time.time() - start_time

            # Notify status callback for completed nodes
            if self.status_callback and result.get("success"):
                for node_id in result.get("execution_trace", []):
                    try:
                        await self.status_callback(
                            node_id,
                            "completed",
                            result.get("outputs", {}).get(node_id, {}),
                        )
                    except Exception as e:
                        logger.warning(f"Status callback error for node {node_id}: {e}")

            logger.info(
                "Temporal workflow completed",
                workflow_id=workflow_id,
                execution_id=execution_id,
                success=result.get("success"),
                nodes_executed=len(result.get("execution_trace", [])),
                execution_time=execution_time,
            )

            return {
                "success": result.get("success", False),
                "execution_id": execution_id,
                "nodes_executed": result.get("execution_trace", []),
                "outputs": result.get("outputs", {}),
                "errors": [result.get("error")] if result.get("error") else [],
                "execution_time": execution_time,
                "temporal_execution": True,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            import traceback

            execution_time = time.time() - start_time
            error_details = f"{type(e).__name__}: {str(e)}"
            tb = traceback.format_exc()
            logger.error(
                f"Temporal workflow failed: {error_details}",
                workflow_id=workflow_id,
                execution_id=execution_id,
                traceback=tb,
            )

            return {
                "success": False,
                "execution_id": execution_id,
                "nodes_executed": [],
                "outputs": {},
                "errors": [error_details],
                "execution_time": execution_time,
                "temporal_execution": True,
                "timestamp": datetime.now().isoformat(),
            }
