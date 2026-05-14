"""Wave 12 B10: CloudEvents factory + broadcaster wrapper for agent_builder.

Plugin-specific event emission — replaces the inline
``broadcaster.send_custom_event("workflow_ops_apply", {...})`` call in
``agent_builder/__init__.py:_broadcast``.

The agent_builder canvas-mutation broadcast carries a flat shape
(``{workflow_id, caller_node_id, operations}``) consumed by the
frontend's ``useWorkflowOpsListener`` hook directly (NOT via the
typed-envelope path). Per the existing X4-allowlist rationale, the
WIRE format stays flat for FE back-compat; the typed envelope is
constructed for audit/future use only.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live
in the plugin folder.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.events.envelope import WorkflowEvent


# Legacy wire-routing key consumed by ``client/src/hooks/useWorkflowOpsListener``.
_LEGACY_EVENT_TYPE = "workflow_ops_apply"


# ---- Typed factory ---------------------------------------------------------


def workflow_ops_applied(
    *,
    workflow_id: Optional[str],
    caller_node_id: str,
    operations: List[Dict[str, Any]],
) -> WorkflowEvent:
    """Canvas-mutation envelope. ``subject`` is the caller node id so
    consumers can route per-builder."""
    return WorkflowEvent(
        source="machinaos://nodes/agent_builder",
        type="com.machinaos.workflow.ops.applied",
        subject=caller_node_id,
        workflow_id=workflow_id,
        data={
            "workflow_id": workflow_id,
            "caller_node_id": caller_node_id,
            "operations": list(operations),
        },
    )


# ---- Broadcaster wrapper ---------------------------------------------------


async def broadcast_workflow_ops(
    *,
    workflow_id: Optional[str],
    caller_node_id: str,
    operations: List[Dict[str, Any]],
) -> None:
    """Emit a workflow_ops_apply event. Routes through
    ``broadcaster.send_custom_event`` (raw flat payload for FE
    back-compat; envelope constructed for future audit use).
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
    # Typed envelope constructed for future audit; not on the wire today
    # because FE listener consumes the flat payload via send_custom_event.
    _ = workflow_ops_applied(
        workflow_id=workflow_id,
        caller_node_id=caller_node_id,
        operations=operations,
    )
    await broadcaster.send_custom_event(_LEGACY_EVENT_TYPE, payload)


__all__ = [
    "broadcast_workflow_ops",
    "workflow_ops_applied",
]
