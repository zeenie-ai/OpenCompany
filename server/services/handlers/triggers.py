"""Trigger node handlers - Generic event-based triggers."""

import asyncio
import time
from datetime import datetime
from typing import Dict, Any
from core.logging import get_logger
from services import event_waiter

logger = get_logger(__name__)


async def handle_trigger_node(node_id: str, node_type: str, parameters: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Handle trigger node execution (webhook, whatsapp, etc.).

    Uses event_waiter module to register waiter and await matching event.
    Works with any trigger type defined in TRIGGER_REGISTRY.

    Args:
        node_id: The node ID
        node_type: The node type (webhookTrigger, whatsappReceive, etc.)
        parameters: Resolved parameters
        context: Execution context with execution_id for tracing

    Returns:
        Execution result dict with event data
    """
    from services.status_broadcaster import get_status_broadcaster

    start_time = time.time()
    execution_id = context.get("execution_id", "unknown")

    # Get trigger configuration
    config = event_waiter.get_trigger_config(node_type)
    if not config:
        logger.error("Unknown trigger type", node_id=node_id, node_type=node_type, execution_id=execution_id)
        return {
            "success": False,
            "node_id": node_id,
            "node_type": node_type,
            "error": f"Unknown trigger type: {node_type}",
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat(),
        }

    try:
        # Plugin-registered pre-check (e.g. "Telegram bot not connected").
        # Plugins call ``event_waiter.register_trigger_precheck(node_type, fn)``
        # from their package __init__. ``fn(parameters) -> Optional[str]``
        # returns an error message to short-circuit, or None to proceed.
        precheck_error = await event_waiter.run_trigger_precheck(node_type, parameters)
        if precheck_error:
            return {
                "success": False,
                "node_id": node_id,
                "node_type": node_type,
                "error": precheck_error,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        # Register waiter for this trigger (async to pre-fetch LID cache)
        waiter = await event_waiter.register(node_type, node_id, parameters)

        # Get workflow_id from context for per-workflow status scoping (n8n pattern)
        workflow_id = context.get("workflow_id")

        # Broadcast waiting status to frontend
        broadcaster = get_status_broadcaster()
        logger.info("Broadcasting waiting status", node_id=node_id, execution_id=execution_id)
        await broadcaster.update_node_status(
            node_id,
            "waiting",
            {"message": f"Waiting for {config.display_name}...", "event_type": config.event_type, "waiter_id": waiter.id},
            workflow_id=workflow_id,
        )

        logger.info(
            "Trigger waiting for event",
            node_id=node_id,
            node_type=node_type,
            event_type=config.event_type,
            execution_id=execution_id,
            backend_mode=event_waiter.get_backend_mode(),
        )

        # Wait for event indefinitely (user cancels via cancel_event_wait)
        # Uses wait_for_event which handles both Redis Streams and asyncio.Future modes
        try:
            event_data = await event_waiter.wait_for_event(waiter)
            logger.info("Event received", node_id=node_id, node_type=node_type, execution_id=execution_id)

            # Success - event received
            return {
                "success": True,
                "node_id": node_id,
                "node_type": node_type,
                "result": event_data,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except asyncio.CancelledError:
            logger.info("Trigger cancelled by user", node_id=node_id, node_type=node_type, execution_id=execution_id)
            return {
                "success": False,
                "node_id": node_id,
                "node_type": node_type,
                "error": "Cancelled by user",
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    except Exception as e:
        logger.error("Trigger execution failed", node_id=node_id, node_type=node_type, execution_id=execution_id, error=str(e))
        return {
            "success": False,
            "node_id": node_id,
            "node_type": node_type,
            "error": str(e),
            "execution_time": time.time() - start_time,
            "timestamp": datetime.now().isoformat(),
        }
