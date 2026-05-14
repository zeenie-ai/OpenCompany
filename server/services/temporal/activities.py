"""Temporal activities for distributed node execution.

Uses class-based activity pattern recommended by Temporal docs for sharing
resources like aiohttp.ClientSession across activity invocations.

References:
- https://docs.temporal.io/develop/python/python-sdk-sync-vs-async
- https://docs.temporal.io/develop/python/core-application

Architecture:
- NodeExecutionActivities class holds shared aiohttp.ClientSession
- Session is passed via constructor, avoiding recreation per activity
- Each activity call gets its own WebSocket connection from the session pool
"""

from datetime import datetime
from typing import Any, Dict

import aiohttp
from temporalio import activity

from core.logging import get_logger
from core.config import Settings

logger = get_logger(__name__)

# Load settings to get the correct server port
_settings = Settings()
MACHINA_URL = f"http://{_settings.host}:{_settings.port}"
WS_URL = f"ws://{_settings.host}:{_settings.port}/ws/internal"

logger.debug(f"MACHINA_URL configured: {MACHINA_URL}")
logger.debug(f"WS_URL configured: {WS_URL}")


class NodeExecutionActivities:
    """Activity class for node execution with shared aiohttp session.

    Following Temporal's recommended pattern for dependency injection:
    - aiohttp.ClientSession is passed via constructor
    - Session provides connection pooling for concurrent activities
    - Each activity call gets its own WebSocket connection from the pool

    Reference: https://docs.temporal.io/develop/python/python-sdk-sync-vs-async
    """

    def __init__(self, session: aiohttp.ClientSession):
        """Initialize with shared aiohttp session.

        Args:
            session: aiohttp.ClientSession with connection pooling configured
        """
        self.session = session
        self.ws_url = WS_URL
        self.http_url = f"{MACHINA_URL}/api/workflow/node/execute"
        self.broadcast_url = f"{MACHINA_URL}/api/workflow/broadcast-status"

    @activity.defn
    async def execute_node_activity(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single workflow node with isolated context.

        This activity can run on ANY worker in the cluster, enabling
        horizontal scaling and multi-tenant distribution.

        Each node execution is independent - if it fails, Temporal will retry
        on the same or different worker without affecting other nodes.

        Args:
            context: Immutable context containing:
                - node_id: Unique node identifier
                - node_type: Type of node (aiAgent, console, timer, etc.)
                - node_data: Node configuration from React Flow
                - inputs: Outputs from upstream nodes (dependencies)
                - workflow_id: Parent workflow ID for tracking
                - tenant_id: Tenant identifier for multi-tenancy
                - session_id: Session identifier
                - nodes: Full node list (for tool/memory detection by handlers)
                - edges: Full edge list (for tool/memory detection by handlers)

        Returns:
            Dict with success, result, node_id, and metadata
        """
        node_id = context["node_id"]
        node_type = context["node_type"]
        node_data = context.get("node_data", {})
        workflow_id = context.get("workflow_id")
        tenant_id = context.get("tenant_id")

        activity.logger.info(
            f"Executing node activity: {node_id} ({node_type})",
            extra={"tenant_id": tenant_id, "workflow_id": workflow_id},
        )

        # Heartbeat at start to signal activity is alive
        activity.heartbeat(f"Starting {node_type}: {node_id}")

        # Handle pre-executed trigger nodes (already have their output)
        if context.get("pre_executed"):
            activity.logger.debug(f"Node {node_id} is pre-executed, returning cached result")
            result = {
                "success": True,
                "node_id": node_id,
                "node_type": node_type,
                "result": context.get("trigger_output", {}),
                "pre_executed": True,
                "timestamp": datetime.now().isoformat(),
            }
            await self._broadcast_status(node_id, "success", result, workflow_id)
            return result

        # Handle disabled nodes
        if node_data.get("disabled"):
            activity.logger.debug(f"Node {node_id} is disabled, skipping")
            result = {
                "success": True,
                "node_id": node_id,
                "node_type": node_type,
                "skipped": True,
                "reason": "disabled",
                "timestamp": datetime.now().isoformat(),
            }
            await self._broadcast_status(node_id, "skipped", {"disabled": True}, workflow_id)
            return result

        # Broadcast "executing" status for UI updates
        await self._broadcast_status(
            node_id=node_id,
            status="executing",
            data={"node_type": node_type},
            workflow_id=workflow_id,
        )

        try:
            # Heartbeat before potentially long WebSocket operation
            activity.heartbeat(f"Executing via WebSocket: {node_id}")

            # Execute node via WebSocket (each call gets own connection from pool)
            result = await self._execute_via_websocket(context)

            # Add metadata
            result["node_id"] = node_id
            result["node_type"] = node_type
            result["timestamp"] = datetime.now().isoformat()

            # Status is already broadcast by the WebSocket handler (handle_execute_node)
            # which calls both update_node_status and update_node_output.
            # Do NOT duplicate here - late-arriving broadcasts overwrite output data.
            if result.get("success"):
                activity.logger.info(f"Node {node_id} completed successfully")
            else:
                activity.logger.warning(f"Node {node_id} failed: {result.get('error')}")

            # Heartbeat for activity liveness
            activity.heartbeat(f"Node {node_id} completed")

            return result

        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            activity.logger.error(f"Node {node_id} execution failed: {error_msg}")

            # Broadcast error status
            await self._broadcast_status(
                node_id=node_id,
                status="error",
                data={"error": error_msg},
                workflow_id=workflow_id,
            )

            # Raise to trigger Temporal retry mechanism
            raise

    async def _execute_via_websocket(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute node via WebSocket using shared session's connection pool.

        Each call creates a new WebSocket connection from the session's pool,
        avoiding race conditions when multiple activities run concurrently.

        Heartbeats are sent both on incoming non-matching messages AND on a
        30-second timer. This is critical for long-running nodes (browser,
        AI multi-tool, claude_code_agent) where the backend may be processing internally
        without broadcasting any WS messages for minutes. Without the periodic
        timer, the 2-minute heartbeat_timeout would cancel the activity even
        though the node is actively executing.
        """
        import asyncio
        import json
        import uuid

        node_id = context["node_id"]
        node_type = context["node_type"]
        request_id = str(uuid.uuid4())

        message = {
            "type": "execute_node",
            "request_id": request_id,
            "node_id": node_id,
            "node_type": node_type,
            "parameters": context.get("node_data", {}),
            "nodes": context.get("nodes", []),
            "edges": context.get("edges", []),
            "session_id": context.get("session_id", "default"),
            "workflow_id": context.get("workflow_id"),
            # CRITICAL: Pass upstream node outputs for downstream nodes to access
            # This enables taskTrigger -> chatAgent data flow via input-task handle
            "outputs": context.get("inputs", {}),
        }

        activity.logger.debug(f"WebSocket execute for {node_id}")

        try:
            # Each activity gets its own WebSocket connection from the pool
            async with self.session.ws_connect(
                self.ws_url,
                heartbeat=30,
                receive_timeout=None,  # No receive timeout — we handle liveness via heartbeats
            ) as ws:
                await ws.send_json(message)

                # Read loop with periodic heartbeat timer.
                # ws.receive() may block for minutes if the backend is doing
                # heavy processing without broadcasting. The 30s timeout ensures
                # we heartbeat at least every 30s regardless of message traffic.
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # No message in 30s — node is still running, send heartbeat
                        activity.heartbeat(f"Waiting for {node_id} ({node_type})")
                        continue

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        response = json.loads(msg.data)
                        if response.get("request_id") == request_id:
                            activity.logger.debug(f"Got response for {node_id}: success={response.get('success')}")
                            return response
                        # Non-matching message (broadcast to other clients) — heartbeat
                        activity.heartbeat(f"Waiting for {node_id}")
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise Exception(f"WebSocket error: {ws.exception()}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                        raise Exception("WebSocket closed unexpectedly")

        except aiohttp.ClientError as e:
            raise Exception(f"WebSocket connection error: {e}")

    async def _broadcast_status(
        self,
        node_id: str,
        status: str,
        data: dict,
        workflow_id: str = None,
    ) -> None:
        """Broadcast node status for real-time UI updates.

        Non-fatal - execution continues even if broadcast fails.
        """
        try:
            async with self.session.post(
                self.broadcast_url,
                json={
                    "node_id": node_id,
                    "status": status,
                    "data": data or {},
                    "workflow_id": workflow_id,
                },
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                if response.status == 200:
                    logger.debug(f"Broadcast: {node_id} -> {status}")
        except Exception as e:
            # Non-fatal - don't fail execution if broadcast fails
            logger.warning(f"Broadcast failed for {node_id}: {e}")


# =============================================================================
# Factory function for creating activity instance with session
# =============================================================================

def create_node_activities(session: aiohttp.ClientSession) -> NodeExecutionActivities:
    """Factory function to create activity instance with shared session.

    This follows Temporal's recommended pattern for dependency injection.
    The session should be created once when the worker starts and reused.

    Args:
        session: aiohttp.ClientSession with connection pooling

    Returns:
        NodeExecutionActivities instance ready for worker registration
    """
    return NodeExecutionActivities(session)


async def create_shared_session(pool_size: int = 100) -> aiohttp.ClientSession:
    """Create a shared aiohttp session with connection pooling.

    Args:
        pool_size: Maximum number of concurrent connections

    Returns:
        Configured aiohttp.ClientSession
    """
    connector = aiohttp.TCPConnector(
        limit=pool_size,
        limit_per_host=pool_size,
        enable_cleanup_closed=True,
    )
    timeout = aiohttp.ClientTimeout(
        total=300,  # 5 min total
        connect=10,  # 10 sec connect
    )
    session = aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
    )
    logger.info(f"Created shared session with pool_size={pool_size}")
    return session


# ---------------------------------------------------------------------------
# Wave 12 A8: emit_event_activity
#
# Thin Temporal-activity wrapper around ``services.events.dispatch.emit``.
# Used by ``PollingTriggerWorkflow`` (Phase C2) when a poll cycle produces
# new items — the workflow can't directly do I/O (the dispatch helper
# uses asyncio + a network round-trip to Temporal Visibility), so it
# offloads to this activity.
#
# Activity scheduling timeouts (``start_to_close_timeout``, retry policy,
# heartbeat) are set by the CALLING workflow at scheduling time —
# ``workflow.execute_activity(emit_event_activity, ...,
# start_to_close_timeout=timedelta(seconds=2))``. Defaults are not
# hardcoded here.

@activity.defn
async def emit_event_activity(event_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Re-enter :func:`services.events.dispatch.emit` from a workflow context.

    Args:
        event_payload: The serialised :class:`WorkflowEvent` (output of
            ``event.model_dump(mode="json")``). The activity reconstructs
            the envelope and routes it through the dispatch helper.

    Returns:
        Status dict:
            {
              "delivered": True | False,    # whether dispatch completed
              "event_id": str,
              "event_type": str,
            }
    """
    from services.events.envelope import WorkflowEvent
    from services.events.dispatch import emit

    try:
        event = WorkflowEvent.model_validate(event_payload)
    except Exception as exc:  # noqa: BLE001
        activity.logger.warning(
            f"emit_event_activity: envelope rejected: {exc}"
        )
        return {
            "delivered": False,
            "event_id": event_payload.get("id", "?"),
            "event_type": event_payload.get("type", "?"),
            "error": f"{type(exc).__name__}: {exc}",
        }

    await emit(event)
    return {
        "delivered": True,
        "event_id": event.id,
        "event_type": event.type,
    }
