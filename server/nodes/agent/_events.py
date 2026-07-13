"""CloudEvents broadcaster wrappers for agent task delegation.

Plugin-folder-owned wrappers around the cross-cutting
``WorkflowEvent.task_completed`` factory. The factory itself stays in
``services/events/envelope.py`` per RFC §6.4 — it's parametrised by
``task_id`` + ``agent`` + ``status`` and uniformly applies to every
delegated agent task, not a single-plugin shape. The broadcaster
wrappers below live with the agent plugin folder so the 3 callsites in
``services/handlers/tools.py`` (the fire-and-forget delegation
dispatcher) import from ``nodes.agent._events`` rather than building
the wire frame inline.

Delivery: a single ``dispatch.emit`` call. ``dispatch.emit`` Signals
running :class:`TriggerListenerWorkflow` consumers via Temporal
Visibility AND broadcasts the same envelope to in-process WS clients on
the ``task_completed`` wire-routing key — one canonical path. Pre-Wave-13
this also called ``broadcaster.send_custom_event`` for the legacy
collector path; that's dead since taskTrigger is canary-registered and
the deployment manager skips ``setup_event_trigger`` for canary types.
"""

from __future__ import annotations

from typing import Any, Optional


# Outer wire-routing key. FE consumers (if any) and the legacy
# back-compat receipt path still switch on this string; the inner
# envelope carries ``com.opencompany.agent.task.completed``.
_WIRE_ROUTING_KEY = "task_completed"


async def broadcast_agent_task_completed(
    *,
    task_id: str,
    agent_name: str,
    agent_node_id: str,
    parent_node_id: str,
    workflow_id: Optional[str] = None,
    result: Optional[str] = None,
) -> None:
    """Broadcast a delegated agent task completing successfully."""
    await _broadcast_task_event(
        task_id=task_id,
        status="completed",
        agent_name=agent_name,
        agent_node_id=agent_node_id,
        parent_node_id=parent_node_id,
        workflow_id=workflow_id,
        result=result,
    )


async def broadcast_agent_task_failed(
    *,
    task_id: str,
    agent_name: str,
    agent_node_id: str,
    parent_node_id: str,
    workflow_id: Optional[str] = None,
    error: str,
) -> None:
    """Broadcast a delegated agent task failing."""
    await _broadcast_task_event(
        task_id=task_id,
        status="error",
        agent_name=agent_name,
        agent_node_id=agent_node_id,
        parent_node_id=parent_node_id,
        workflow_id=workflow_id,
        error=error,
    )


async def _broadcast_task_event(
    *,
    task_id: str,
    status: str,  # "completed" | "error"
    agent_name: str,
    agent_node_id: str,
    parent_node_id: str,
    workflow_id: Optional[str] = None,
    result: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Single delivery path: build the envelope, route through
    :func:`services.events.dispatch.emit`.

    ``emit`` does two things in one call: (a) Visibility query for
    running :class:`TriggerListenerWorkflow` consumers tagged with
    ``EventType='com.opencompany.agent.task.completed'`` and signals each;
    (b) direct in-process WS broadcast on the ``task_completed`` wire
    key so any FE consumers receive the envelope. Payload shape in
    ``data`` is exactly what the taskTrigger filter reads.
    """
    from services.events.dispatch import emit
    from services.events.envelope import WorkflowEvent

    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "agent_name": agent_name,
        "agent_node_id": agent_node_id,
        "parent_node_id": parent_node_id,
        "workflow_id": workflow_id,
    }
    if status == "completed" and result is not None:
        payload["result"] = result
    elif status == "error" and error is not None:
        payload["error"] = error

    envelope = WorkflowEvent.task_completed(
        task_id=task_id,
        status="completed" if status == "completed" else "error",
        agent=agent_name,
        data=payload,
    )
    await emit(envelope, wire_routing_key=_WIRE_ROUTING_KEY)


__all__ = [
    "broadcast_agent_task_completed",
    "broadcast_agent_task_failed",
]
