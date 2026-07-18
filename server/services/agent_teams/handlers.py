"""Agent-team WS handlers extracted from ``routers/websocket.py`` (Wave 13.4).

10 handlers wrapping the team lifecycle (create / get / dissolve), task
queue (add / claim / complete / get), and the in-team message channel
(send / get). Business logic lives in ``services.agent_team`` (the
service singleton); these handlers just decode the request and route
through it.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket

from core.logging import get_logger
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)


@ws_handler("workflow_id", "team_lead_node_id")
async def handle_create_team(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Create a new agent team."""
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    team = await service.create_team(
        team_lead_node_id=data["team_lead_node_id"],
        teammate_node_ids=data.get("teammates", []),
        workflow_id=data["workflow_id"],
        config=data.get("config"),
        execution_id=data.get("execution_id"),
        root_execution_id=data.get("root_execution_id"),
        team_lead_type=data.get("team_lead_type", "orchestrator_agent"),
        team_lead_label=data.get("team_lead_label"),
    )
    return {"team": team} if team else {"success": False, "error": "Failed to create team"}


@ws_handler("team_id")
async def handle_get_team(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team info."""
    from core.container import container

    database = container.database()
    team = await database.get_team(data["team_id"])
    return {"team": team} if team else {"success": False, "error": "Team not found"}


@ws_handler()
async def handle_get_team_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team status with stats.

    Can provide team_id directly, or team_lead_node_id to find active team.
    """
    from services.agent_team import get_agent_team_service

    empty_status = {
        "members": [],
        "task_count": 0,
        "completed_count": 0,
        "active_count": 0,
        "pending_count": 0,
        "failed_count": 0,
        "active_tasks": [],
    }

    try:
        service = get_agent_team_service()
    except RuntimeError:
        return {"status": empty_status}

    team_id = data.get("team_id")

    if not team_id and not (data.get("workflow_id") and data.get("team_lead_node_id")):
        return {"status": {**empty_status, "message": "No team connected"}}

    status = await service.get_team_status(
        team_id, workflow_id=data.get("workflow_id"),
        team_lead_node_id=data.get("team_lead_node_id"), execution_id=data.get("execution_id"),
    )
    if status.get("error") == "Team not found":
        status = {**empty_status, "message": "No active team yet"}
    return {"status": status}


@ws_handler("team_id")
async def handle_dissolve_team(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Dissolve a team."""
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    success = await service.dissolve_team(data["team_id"], data.get("workflow_id"))
    return {"success": success}


@ws_handler("team_id", "title", "created_by")
async def handle_add_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Add task to team."""
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    task = await service.add_task(
        team_id=data["team_id"],
        title=data["title"],
        created_by=data["created_by"],
        description=data.get("description"),
        priority=data.get("priority", 3),
        depends_on=data.get("depends_on"),
    )
    return {"task": task} if task else {"success": False, "error": "Failed to add task"}


@ws_handler("team_id", "task_id", "agent_node_id")
async def handle_claim_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Claim a task."""
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    task = await service.claim_task(data["team_id"], data["task_id"], data["agent_node_id"])
    return {"task": task} if task else {"success": False, "error": "Task unavailable"}


@ws_handler("team_id", "task_id")
async def handle_complete_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Complete a task."""
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    success = await service.complete_task(data["team_id"], data["task_id"], data.get("result"))
    return {"success": success}


@ws_handler()
async def handle_get_team_tasks(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team tasks."""
    from services.agent_team import get_agent_team_service
    if data.get("workflow_id") and data.get("team_lead_node_id"):
        service = get_agent_team_service()
        if data.get("include_history"):
            tasks = await service.list_durable_task_history(
                workflow_id=data["workflow_id"],
                team_lead_node_id=data["team_lead_node_id"], status=data.get("status"),
            )
        else:
            tasks = await service.list_durable_tasks(
                workflow_id=data["workflow_id"], team_lead_node_id=data["team_lead_node_id"],
                execution_id=data.get("execution_id"), status=data.get("status"),
            )
    elif data.get("team_id"):
        # Compatibility read only; mutations never accept caller-selected team IDs.
        tasks = await get_agent_team_service().database.get_team_tasks(data["team_id"], data.get("status"))
    else:
        return {"success": False, "error": "workflow_id and team_lead_node_id required"}
    return {"tasks": tasks}


@ws_handler("workflow_id", "team_lead_node_id", "assignee_node_id", "title", "mission")
async def handle_assign_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from services.agent_team import get_agent_team_service
    task = await get_agent_team_service().assign_durable_task(
        workflow_id=data["workflow_id"], team_lead_node_id=data["team_lead_node_id"],
        execution_id=data.get("execution_id"), assignee_node_id=data["assignee_node_id"],
        title=data["title"], mission=data["mission"], context=data.get("context"),
        acceptance_criteria=data.get("acceptance_criteria"), depends_on=data.get("depends_on"),
        task_id=data.get("task_id"), trace_id=data.get("trace_id"),
    )
    return {"task": task}


@ws_handler("workflow_id", "team_lead_node_id", "task_id")
async def handle_get_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from services.agent_team import get_agent_team_service
    task = await get_agent_team_service().get_durable_task(
        workflow_id=data["workflow_id"], team_lead_node_id=data["team_lead_node_id"],
        execution_id=data.get("execution_id"), task_id=data["task_id"],
    )
    return {"task": task}


@ws_handler("workflow_id", "team_lead_node_id", "task_id", "operation")
async def handle_manage_team_task(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from services.agent_team import get_agent_team_service
    if "revision" not in data:
        return {"success": False, "error": "revision required"}
    task = await get_agent_team_service().mutate_durable_task(
        workflow_id=data["workflow_id"], team_lead_node_id=data["team_lead_node_id"],
        execution_id=data.get("execution_id"), task_id=data["task_id"],
        revision=int(data["revision"]), operation=data["operation"],
        title=data.get("title"), mission=data.get("mission"), context=data.get("context"),
        acceptance_criteria=data.get("acceptance_criteria"), reason=data.get("reason"),
        assignee_node_id=data.get("assignee_node_id"),
    )
    return {"task": task}


@ws_handler("workflow_id", "team_lead_node_id")
async def handle_finish_team(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from services.agent_team import get_agent_team_service
    status = await get_agent_team_service().finish_durable_team(
        workflow_id=data["workflow_id"], team_lead_node_id=data["team_lead_node_id"],
        execution_id=data.get("execution_id"),
    )
    return {"status": status}


@ws_handler("team_id", "from_agent", "content")
async def handle_send_team_message(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Send message in team."""
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    msg = await service.send_message(
        team_id=data["team_id"],
        from_agent=data["from_agent"],
        content=data["content"],
        to_agent=data.get("to_agent"),
        message_type=data.get("message_type", "direct"),
    )
    return {"message": msg} if msg else {"success": False, "error": "Failed to send message"}


@ws_handler("team_id")
async def handle_get_team_messages(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get team messages."""
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    messages = await service.get_messages(
        team_id=data["team_id"],
        agent_node_id=data.get("agent_node_id"),
        unread_only=data.get("unread_only", False),
    )
    return {"messages": messages}


WS_HANDLERS: Dict[str, Any] = {
    "create_team": handle_create_team,
    "get_team": handle_get_team,
    "get_team_status": handle_get_team_status,
    "dissolve_team": handle_dissolve_team,
    "add_team_task": handle_add_team_task,
    "claim_team_task": handle_claim_team_task,
    "complete_team_task": handle_complete_team_task,
    "get_team_tasks": handle_get_team_tasks,
    "assign_team_task": handle_assign_team_task,
    "get_team_task": handle_get_team_task,
    "manage_team_task": handle_manage_team_task,
    "finish_team": handle_finish_team,
    "send_team_message": handle_send_team_message,
    "get_team_messages": handle_get_team_messages,
}


__all__ = [
    "WS_HANDLERS",
    "handle_add_team_task",
    "handle_claim_team_task",
    "handle_complete_team_task",
    "handle_create_team",
    "handle_dissolve_team",
    "handle_get_team",
    "handle_get_team_messages",
    "handle_get_team_status",
    "handle_get_team_tasks",
    "handle_send_team_message",
]
