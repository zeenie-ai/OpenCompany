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

Delivery: ``dispatch.emit`` Signals the running consumers of the task's
own workflow (the envelope's ``workflow_id`` scopes the Visibility
query), so a completion never fires another workflow's taskTrigger.
The WebSocket frame on the ``task_completed`` key carries the task's
identity only: the broadcast reaches every connected socket, and the
Task Manager panel needs just the team id to refresh. Pre-Wave-13 this
also called ``broadcaster.send_custom_event`` for the legacy collector
path; that's dead since taskTrigger is canary-registered and the
deployment manager skips ``setup_event_trigger`` for canary types.
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
    event_id: Optional[str] = None,
    lifecycle_data: Optional[dict[str, Any]] = None,
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
        event_id=event_id,
        lifecycle_data=lifecycle_data,
    )


async def broadcast_agent_task_failed(
    *,
    task_id: str,
    agent_name: str,
    agent_node_id: str,
    parent_node_id: str,
    workflow_id: Optional[str] = None,
    error: str,
    event_id: Optional[str] = None,
    lifecycle_data: Optional[dict[str, Any]] = None,
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
        event_id=event_id,
        lifecycle_data=lifecycle_data,
    )


async def publish_instance_task_outcome(outcome: dict[str, Any]) -> None:
    """Publish the receipt committed alongside a v3 instance review delivery."""
    status = {"submitted": "completed", "failed": "error"}[outcome["status"]]
    task_id = outcome["task_id"]
    event_id = f"{task_id}:attempt:{outcome['attempt']}:revision:{outcome['revision']}:{status}"
    lifecycle = {key: outcome[key] for key in (
        "generation", "team_id", "job_id", "execution_id", "root_execution_id",
        "attempt", "revision", "agent_instance_id", "assigning_run_id", "reviewer_instance_id",
    ) if key in outcome}
    await _broadcast_task_event(
        task_id=task_id, status=status, agent_name=outcome["agent_name"],
        agent_node_id=outcome["agent_node_id"], parent_node_id=outcome["parent_node_id"],
        workflow_id=outcome["workflow_id"], result=outcome.get("result"),
        error=outcome.get("error"), lifecycle_data=lifecycle, event_id=event_id,
        controller_workflow_id=outcome.get("controller_workflow_id"),
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
    event_id: Optional[str] = None,
    lifecycle_data: Optional[dict[str, Any]] = None,
    controller_workflow_id: Optional[str] = None,
) -> None:
    """Build the envelope and route it through
    :func:`services.events.dispatch.emit` to the consumers of
    ``workflow_id`` only, then broadcast the task's identity.

    The envelope's ``data`` is exactly what the taskTrigger filter reads.
    It holds the child agent's answer or error, so it is not broadcast.
    """
    from services.events.dispatch import emit, signal_generation_event
    from services.events.envelope import WorkflowEvent
    from services.status_broadcaster import get_status_broadcaster

    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": status,
        "agent_name": agent_name,
        "agent_node_id": agent_node_id,
        "parent_node_id": parent_node_id,
        "workflow_id": workflow_id,
        **(lifecycle_data or {}),
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
    # Temporal activity retries must reproduce the same event identity. The
    # listener workflow can therefore deduplicate a completion that was
    # delivered immediately before a worker failure/retry.
    if event_id:
        envelope.id = event_id
    envelope.workflow_id = workflow_id
    if controller_workflow_id:
        if not await signal_generation_event(envelope, controller_workflow_id, payload["generation"]):
            return
    else:
        await emit(envelope, wire_routing_key=_WIRE_ROUTING_KEY, broadcast=False)

    identity = WorkflowEvent.task_completed(
        task_id=task_id,
        status="completed" if status == "completed" else "error",
        agent=agent_name,
        data={
            "task_id": task_id,
            "status": status,
            "team_id": (lifecycle_data or {}).get("team_id"),
            "workflow_id": workflow_id,
        },
    )
    identity.id = envelope.id
    await get_status_broadcaster().broadcast({"type": _WIRE_ROUTING_KEY, "data": identity.model_dump(mode="json")})


__all__ = [
    "broadcast_agent_task_completed",
    "broadcast_agent_task_failed",
    "publish_instance_task_outcome",
]
