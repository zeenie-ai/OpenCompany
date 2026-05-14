"""Wave 12 B8: CloudEvents broadcaster wrappers for agent task delegation.

Plugin-folder-owned wrappers around the cross-cutting
``WorkflowEvent.task_completed`` factory (cross-cutting factory itself
stays in ``services/events/envelope.py`` per RFC §6.4 — it's
parametrised by ``task_id`` + ``agent`` + ``status`` and uniformly
applies to every delegated agent task, not single-plugin shape).

What lives here vs the central factory:
  - Central ``WorkflowEvent.task_completed(task_id, status, agent, data)``
    factory STAYS in services/events/envelope.py (cross-cutting).
  - The broadcaster *wrappers* below live with the agent plugin folder
    so the 3 callsites in ``services/handlers/tools.py`` (the
    fire-and-forget delegation dispatcher) import from
    ``nodes.agent._events`` rather than building the wire frame
    inline.

Wire-routing key ``"task_completed"`` is preserved — the
``taskTrigger`` plugin filter still subscribes on this string. The
inner envelope carries the spec-compliant ``com.machinaos.agent.task.{
succeeded, failed}`` type.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


# Legacy wire-routing key. The ``taskTrigger`` plugin filters by this
# string today (see ``services/event_waiter.py:TRIGGER_REGISTRY``).
_LEGACY_EVENT_TYPE = "task_completed"


async def broadcast_agent_task_completed(
    *,
    task_id: str,
    agent_name: str,
    agent_node_id: str,
    parent_node_id: str,
    workflow_id: Optional[str] = None,
    result: Optional[str] = None,
) -> None:
    """Broadcast a delegated agent task completing successfully.

    Replaces inline ``broadcaster.send_custom_event('task_completed',
    {...})`` calls in ``services/handlers/tools.py``. Builds the
    payload exactly as the consumer (taskTrigger filter) expects.
    """
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
    """Broadcast a delegated agent task failing.

    Replaces inline ``broadcaster.send_custom_event('task_completed',
    {status: 'error', ...})`` calls in
    ``services/handlers/tools.py``.
    """
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
    """Internal helper. Builds the legacy raw-dict payload shape the
    taskTrigger filter expects, then routes through
    ``send_custom_event`` (which both broadcasts on the WS + dispatches
    to event_waiter)."""
    from services.status_broadcaster import get_status_broadcaster

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

    broadcaster = get_status_broadcaster()
    await broadcaster.send_custom_event(_LEGACY_EVENT_TYPE, payload)


__all__ = [
    "broadcast_agent_task_completed",
    "broadcast_agent_task_failed",
]
