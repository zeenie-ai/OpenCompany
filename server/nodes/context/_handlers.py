"""Authorized WebSocket handlers for the Context panel.

Stored conversations are returned only from ``get_agent_context`` after the
saved workflow is verified to own the requested Context node. Conversations
include legacy node conversations and isolated instance conversations. The
Context node is an optional viewer; instance history is owned by its session.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.agent_context import (
    clear_conversation,
    list_conversations,
    load_conversation,
)
from services.plugin import NodeUserError
from services.plugin.ws import ws_response

from ._events import dispatch_context_updated

logger = get_logger(__name__)


def _database():
    from core.container import container

    return container.database()


def _require_external_socket(websocket: WebSocket) -> None:
    """The internal unauthenticated worker socket may not read conversations."""

    scope = getattr(websocket, "scope", {}) or {}
    if scope.get("path") == "/ws/internal":
        raise NodeUserError("Context inspection requires an authenticated client")


def _authenticated_owner(websocket: WebSocket) -> str:
    state = getattr(websocket, "state", None)
    value = getattr(state, "user_id", None) if state is not None else None
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value)
    scope = getattr(websocket, "scope", {}) or {}
    value = scope.get("user_id") if isinstance(scope, dict) else None
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value)
    return "owner"


async def _authorize_context_node(
    *,
    websocket: WebSocket,
    workflow_id: str,
    context_node_id: str,
) -> Any:
    _require_external_socket(websocket)
    if not workflow_id or not context_node_id:
        raise NodeUserError("workflow_id and context_node_id are required")
    workflow = await _database().get_workflow(workflow_id)
    if workflow is None:
        raise NodeUserError("Workflow not found")
    graph = workflow.data if isinstance(workflow.data, dict) else {}
    stored_owner = str(graph.get("owner_id") or "")
    if stored_owner and stored_owner != _authenticated_owner(websocket):
        raise NodeUserError("Workflow access denied")
    owned = any(
        isinstance(node, dict)
        and str(node.get("id") or "") == context_node_id
        and str(node.get("type") or "") == "context"
        for node in graph.get("nodes", [])
    )
    if not owned:
        raise NodeUserError(
            "Context node does not belong to the requested workflow"
        )
    return workflow


def _optional_generation(data: Dict[str, Any]) -> Optional[int]:
    value = data.get("generation")
    if value in (None, ""):
        return None
    try:
        generation = int(value)
    except (TypeError, ValueError) as exc:
        raise NodeUserError("generation must be an integer") from exc
    if generation < 0:
        raise NodeUserError("generation must be non-negative")
    return generation


def _empty_context() -> Dict[str, Any]:
    return {
        "conversations": [],
        "generation": None,
        "agent_node_id": None,
        "agent_instance_id": None,
        "updated_at": None,
        "message_count": 0,
        "messages": [],
    }


async def _instance_conversations(database: Any, workflow_id: str) -> list[dict]:
    from sqlalchemy import func
    from sqlmodel import select
    from models.agent_instances import AgentInstance, AgentInstanceConversation

    # Only metadata is loaded for the selector; fetch one full transcript below.
    async with database.get_session() as session:
        result = await session.execute(select(
            AgentInstance.id, AgentInstance.template_node_id, AgentInstance.generation,
            AgentInstance.display_name, AgentInstance.job_id,
            AgentInstanceConversation.updated_at,
            func.json_array_length(AgentInstanceConversation.messages),
        ).join(AgentInstanceConversation, AgentInstanceConversation.instance_id == AgentInstance.id)
          .where(AgentInstance.workflow_id == workflow_id))
        return [{"workflow_id": workflow_id, "agent_instance_id": row[0],
                 "agent_node_id": row[1], "generation": row[2], "display_name": row[3],
                 "job_id": row[4], "updated_at": row[5].isoformat() if row[5] else None,
                 "message_count": row[6] or 0} for row in result.all()]


@ws_response
async def handle_get_agent_context(
    data: Dict[str, Any],
    websocket: WebSocket,
) -> Dict[str, Any]:
    """Return the live conversation for one workflow's Context node.

    The panel shows the agent's CURRENT context only: rows from the newest
    stored generation. Prior generations stay in the store as inert history
    (cleared with the workflow) but are deliberately not browsable here.
    ``conversations`` lists every stored conversation in the live
    generation, not only the one for the agent wired to this Context node
    (save rejects a Context shared by several agents), and the panel offers
    them in a selector; ``messages`` is the transcript of the requested
    ``agent_node_id``, else the newest row.
    """

    workflow_id = str(data.get("workflow_id") or "")
    context_node_id = str(data.get("context_node_id") or "")
    await _authorize_context_node(
        websocket=websocket,
        workflow_id=workflow_id,
        context_node_id=context_node_id,
    )
    agent_node_id = str(data.get("agent_node_id") or "") or None
    instance_id = str(data.get("agent_instance_id") or "") or None

    database = _database()
    rows = await list_conversations(database, workflow_id=workflow_id)
    rows.extend(await _instance_conversations(database, workflow_id))
    rows.sort(key=lambda row: (row["generation"], row.get("updated_at") or ""), reverse=True)
    live_generation = rows[0]["generation"] if rows else None
    conversations = [
        row for row in rows if row["generation"] == live_generation
    ]
    selected = next(
        (
            row
            for row in conversations
            if (row.get("agent_instance_id") == instance_id if instance_id else
                (agent_node_id is None or (not row.get("agent_instance_id") and row["agent_node_id"] == agent_node_id)))
        ),
        None,
    )
    if selected is None:
        context = _empty_context()
        context["conversations"] = conversations
        return {"success": True, "context": context}

    if selected.get("agent_instance_id"):
        from services.agent_instances.service import AgentInstanceService
        transcript = await AgentInstanceService(database).get_transcript(selected["agent_instance_id"])
        messages = transcript["messages"]
    else:
        messages = await load_conversation(
            database, workflow_id=workflow_id, generation=selected["generation"],
            agent_node_id=selected["agent_node_id"],
        )
    return {
        "success": True,
        "context": {
            "conversations": conversations,
            "generation": selected["generation"],
            "agent_node_id": selected["agent_node_id"],
            "agent_instance_id": selected.get("agent_instance_id"),
            "display_name": selected.get("display_name"),
            "job_id": selected.get("job_id"),
            "updated_at": selected["updated_at"],
            "message_count": len(messages),
            "messages": messages,
        },
    }


async def forget_conversations(
    database: Any,
    *,
    workflow_id: str,
    announce_as: str,
    generation: Optional[int] = None,
    agent_node_id: Optional[str] = None,
) -> int:
    """Delete stored conversations, every one of the workflow's unless
    narrowed by ``generation`` and/or ``agent_node_id``. Warm claude
    subprocesses holding a cleared conversation in memory are terminated so
    the next turn cannot silently continue from wiped state, and
    ``context.updated`` (subject ``announce_as``) refreshes an open panel.
    Returns the conversations deleted."""

    cleared = await clear_conversation(
        database,
        workflow_id=workflow_id,
        generation=generation,
        agent_node_id=agent_node_id,
    )
    try:
        from services.cli_agent.factory import get_session_pool

        pool = get_session_pool("claude")
        terminate = getattr(pool, "terminate_conversations", None)
        if callable(terminate):
            await terminate(workflow_id, agent_node_id=agent_node_id)
    except Exception as exc:
        # Best-effort: the acquire-time generation fence still protects
        # cross-generation reuse; only a same-generation warm process can
        # linger, and it dies at the idle TTL.
        logger.debug("[Context] pool termination skipped: %s", exc)
    try:
        await dispatch_context_updated(
            workflow_id=workflow_id,
            generation=generation or 0,
            agent_node_id=announce_as,
            message_count=0,
        )
    except Exception as exc:
        logger.debug("[Context] clear broadcast failed: %s", exc)
    return cleared


async def on_chat_cleared(*, database: Any, workflow_id: str) -> None:
    """The owner cleared the workflow's chat (services/chat_thread
    ``clear_chat_session``): its agents forget the conversation too, so they
    start over with the chat. Every conversation of the workflow, as a
    Reset clears them."""

    await forget_conversations(database, workflow_id=workflow_id, announce_as=workflow_id)


@ws_response
async def handle_clear_agent_context(
    data: Dict[str, Any],
    websocket: WebSocket,
) -> Dict[str, Any]:
    """Delete stored conversations for one workflow's Context node.

    Narrowable by ``generation`` and/or ``agent_node_id``; without either,
    every conversation for the workflow is deleted (``forget_conversations``).
    """

    workflow_id = str(data.get("workflow_id") or "")
    context_node_id = str(data.get("context_node_id") or "")
    await _authorize_context_node(
        websocket=websocket,
        workflow_id=workflow_id,
        context_node_id=context_node_id,
    )
    if data.get("agent_instance_id"):
        raise NodeUserError("Instance history is managed by its job and cannot be cleared here")
    agent_node_id = str(data.get("agent_node_id") or "") or None
    cleared = await forget_conversations(
        _database(),
        workflow_id=workflow_id,
        announce_as=agent_node_id or context_node_id,
        generation=_optional_generation(data),
        agent_node_id=agent_node_id,
    )
    return {"success": True, "cleared": cleared}


WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]

WS_HANDLERS: Dict[str, WSHandler] = {
    "get_agent_context": handle_get_agent_context,
    "clear_agent_context": handle_clear_agent_context,
}

__all__ = [
    "WS_HANDLERS",
    "forget_conversations",
    "handle_clear_agent_context",
    "handle_get_agent_context",
    "on_chat_cleared",
]
