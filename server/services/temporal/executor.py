"""Temporal executor for OpenCompany workflow execution.

Provides the same interface as WorkflowExecutor but delegates
execution to Temporal for durable workflow orchestration.
"""

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from temporalio.client import Client
from temporalio.common import (
    SearchAttributeKey,
    SearchAttributePair,
    TypedSearchAttributes,
)

from core.logging import get_logger
from .workflow import (
    MachinaWorkflow,
    TEMPORAL_ROUTING_INPUT_KEY,
    TEMPORAL_ROUTING_INPUT_VERSION,
)

logger = get_logger(__name__)


def capture_temporal_routing_input() -> Dict[str, Any]:
    """Capture command-shaping flags before entering workflow execution."""
    from core.config import Settings

    settings = Settings()
    return {
        TEMPORAL_ROUTING_INPUT_KEY: {
            "version": TEMPORAL_ROUTING_INPUT_VERSION,
            "agent_workflow_enabled": bool(
                getattr(settings, "temporal_agent_workflow_enabled", False)
            ),
            "per_type_dispatch_enabled": bool(
                getattr(settings, "temporal_per_type_dispatch", False)
            ),
            "worker_pool_enabled": bool(
                getattr(settings, "temporal_worker_pool_enabled", False)
            ),
        }
    }


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
        workflow_slug: Optional[str] = None,
        graph_version: int = 0,
        generation: int = 0,
        user_id: str = "owner",
    ) -> Dict[str, Any]:
        """Execute a workflow using Temporal.

        Args:
            workflow_id: Stable UUID system identity (FK target).
            nodes: List of node definitions
            edges: List of edge definitions
            session_id: Session identifier
            enable_caching: Whether to enable result caching (passed to activity)
            workflow_slug: Human-readable slug used in the Temporal Web UI
                listing prefix. Optional — falls back to ``"workflow"`` for
                one-off Runs without a saved DB row.
            graph_version: Normalized graph version for Context V2
            generation: Durable workflow-control generation for Context V2
            user_id: Authenticated server-owned user identity

        Returns:
            Dict with success, outputs, execution_trace, and timing info
        """
        start_time = time.time()
        # Temporal start ID surfaces in the Web UI. Format:
        # ``<workflow_slug>-<uuid8>`` — workflow name leads, short uuid
        # disambiguates per-run uniqueness. No middle kind tag — the
        # Temporal Web UI's "Workflow Type" column already shows
        # MachinaWorkflow.
        slug_prefix = workflow_slug or "workflow"
        execution_id = f"{slug_prefix}-{uuid.uuid4().hex[:8]}"

        logger.info(
            "Starting Temporal workflow execution",
            workflow_id=workflow_id,
            workflow_slug=workflow_slug,
            execution_id=execution_id,
            node_count=len(nodes),
            edge_count=len(edges),
        )

        try:
            # Execute workflow via Temporal
            workflow_payload = {
                "nodes": nodes,
                "edges": edges,
                "session_id": session_id,
                "workflow_id": workflow_id,
                "workflow_slug": workflow_slug,
                "execution_id": execution_id,
                "user_id": str(user_id or "owner"),
                **capture_temporal_routing_input(),
            }
            if int(graph_version or 0) >= 2 and int(generation or 0) > 0:
                workflow_payload.update(
                    {
                        "graphVersion": int(graph_version),
                        "generation": int(generation),
                        "root_execution_id": execution_id,
                    }
                )

            result = await self.client.execute_workflow(
                MachinaWorkflow.run,
                workflow_payload,
                id=execution_id,
                task_queue=self.task_queue,
                search_attributes=TypedSearchAttributes(
                    [
                        SearchAttributePair(
                            SearchAttributeKey.for_keyword("EventWorkflowId"),
                            workflow_id,
                        )
                    ]
                ),
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
                # MachinaWorkflow returns ``errors`` (a list of
                # ``{node_id, error}``), never a singular ``error`` key;
                # reading the wrong key reported every failed run as
                # error-free. Flattened to strings to match the exception
                # branch below.
                "errors": [
                    f"{err.get('node_id', '?')}: {err.get('error', err)}" if isinstance(err, dict) else str(err)
                    for err in (result.get("errors") or [])
                ],
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
