"""Helpers for the chat run tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from starlette.websockets import WebSocketState

from models.database import WorkflowControlExecution


class FakeSocket:
    """A status socket: records what the hub writes to it."""

    def __init__(self, *, path: str = "/ws/status", user_id: Optional[str] = None) -> None:
        self.scope = {"path": path}
        self.state = SimpleNamespace(user_id=user_id)
        self.client_state = WebSocketState.CONNECTED
        self.application_state = WebSocketState.CONNECTED
        self.sent: List[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    def events(self) -> List[Dict[str, Any]]:
        import json

        return [json.loads(text)["data"] for text in self.sent]


async def saved_workflow(database, workflow_id: str) -> None:
    """The saved workflow a chat session names (chat refuses a session that
    names none). An empty graph, saved only if missing."""
    if await database.get_workflow(workflow_id) is None:
        await database.save_workflow(workflow_id, workflow_id, f"{workflow_id}_1", {"nodes": [], "edges": []})


async def add_control(
    database,
    workflow_id: str,
    status: str,
    *,
    generation: int = 1,
    nodes: Optional[List[Dict[str, Any]]] = None,
):
    """A control generation of a saved workflow, its graph holding ``nodes``."""
    await saved_workflow(database, workflow_id)
    async with database.get_session() as session:
        session.add(
            WorkflowControlExecution(
                id=f"{workflow_id}-{generation}",
                workflow_id=workflow_id,
                generation=generation,
                execution_id=f"gen-{generation}",
                root_execution_id=f"gen-{generation}",
                graph_hash="0" * 64,
                graph_snapshot={"nodes": list(nodes or []), "edges": []},
                status=status,
                idempotency_key=f"k-{generation}",
            )
        )
        await session.commit()


def chat_trigger(workflow_id: str, **data: Any) -> Dict[str, Any]:
    return {"id": f"{workflow_id}:chatTrigger:1", "type": "chatTrigger", "data": {"label": "Chat", **data}}


async def talking(database, workflow_id: str = "wf", status: str = "running", generation: int = 1) -> None:
    """A deployed workflow whose chat trigger answers its own session."""
    await add_control(database, workflow_id, status, generation=generation, nodes=[chat_trigger(workflow_id)])


def chat_updates(frames) -> List[Dict[str, Any]]:
    return [frame["data"]["data"] for frame in frames if frame["type"] == "chat.updated"]
