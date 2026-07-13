"""CloudEvents factory + broadcaster wrapper for agent_builder.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

This is NOT a trigger event — it's a canvas-mutation broadcast consumed
by the frontend's ``useWorkflowOpsListener`` hook. The wire format is
flat (``{workflow_id, caller_node_id, operations}``) for FE back-compat;
the typed envelope is constructed for parity with the other plugin
``_events.py`` modules but not consumed today.

Delivery: direct WS broadcast (no ``dispatch.emit`` — no
:class:`TriggerListenerWorkflow` consumer exists for canvas mutations,
and no legacy ``event_waiter`` waiter listens on ``workflow_ops_apply``
either). Pre-Wave-13 this routed through ``send_custom_event`` which
also called ``event_waiter.dispatch_async`` — that dispatch was dead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.events.envelope import WorkflowEvent


# Flat wire-routing key consumed by ``client/src/hooks/useWorkflowOpsListener``.
_WIRE_ROUTING_KEY = "workflow_ops_apply"


def workflow_ops_applied(
    *,
    workflow_id: Optional[str],
    caller_node_id: str,
    operations: List[Dict[str, Any]],
) -> WorkflowEvent:
    """Canvas-mutation envelope. ``subject`` is the caller node id so
    consumers can route per-builder."""
    return WorkflowEvent(
        source="opencompany://nodes/agent_builder",
        type="com.opencompany.workflow.ops.applied",
        subject=caller_node_id,
        workflow_id=workflow_id,
        data={
            "workflow_id": workflow_id,
            "caller_node_id": caller_node_id,
            "operations": list(operations),
        },
    )


async def broadcast_workflow_ops(
    *,
    workflow_id: Optional[str],
    caller_node_id: str,
    operations: List[Dict[str, Any]],
) -> None:
    """Broadcast a canvas-mutation event for the FE to apply.

    Flat payload (FE back-compat with ``useWorkflowOpsListener``); the
    typed envelope is constructed for audit/future migration but not
    on the wire today.
    """
    if not operations:
        return
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    payload = {
        "workflow_id": workflow_id,
        "caller_node_id": caller_node_id,
        "operations": list(operations),
    }
    # Envelope constructed for parity / future migration; not on the
    # wire today because FE listener reads the flat shape.
    _ = workflow_ops_applied(
        workflow_id=workflow_id,
        caller_node_id=caller_node_id,
        operations=operations,
    )
    await broadcaster.broadcast(
        {
            "type": _WIRE_ROUTING_KEY,
            "data": payload,
        }
    )


__all__ = [
    "broadcast_workflow_ops",
    "workflow_ops_applied",
]
