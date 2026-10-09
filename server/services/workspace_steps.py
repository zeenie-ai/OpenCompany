"""The Workspace's step log: what an employee did on each surface, in order
(design handoff "Workspace panel", the footer timeline).

The Browser node records its site actions and screenshots, the Canvas node
each display, and the phone each action of an AI task and the owner taking
it over and handing it back, one short line each, worded by the recorder.
A step names what happened, never what was typed or shown: no typed text, no
page content, and nothing while a protected login is in progress.

Recording is best effort (:func:`record_step` never raises): the work itself
never fails over its log. A workflow keeps its newest
``MAX_STEPS_PER_WORKFLOW`` steps; deleting the workflow deletes them. Each
new step sends the identity-only ``workspace_step`` broadcast, and Home's
Workspace reads the steps through the owner-checked ``workspace_steps_list``.

The module owns its table, like the Canvas board (``ensure_schema`` creates
it on first use), and never imports ``nodes/``: the plugins call it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import WebSocket
from sqlalchemy import delete, select
from sqlmodel import Field, SQLModel

from core.logging import get_logger
from services.events.envelope import WorkflowEvent
from services.ws_handler_registry import ws_handler

logger = get_logger(__name__)

Surface = Literal["browser", "canvas", "mobile"]
SURFACES = ("browser", "canvas", "mobile")
#: Each workflow keeps its newest steps, oldest dropped first.
MAX_STEPS_PER_WORKFLOW = 200
MAX_TEXT_CHARS = 200
WIRE_KEY = "workspace_step"
SOURCE = "opencompany://services/workspace"


class WorkspaceStep(SQLModel, table=True):
    __tablename__ = "workspace_steps"

    id: Optional[int] = Field(default=None, primary_key=True)
    workflow_id: str = Field(index=True, max_length=255)
    surface: str = Field(max_length=16)
    text: str = Field(max_length=MAX_TEXT_CHARS)
    node_id: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


_schema_lock = asyncio.Lock()
_initialized_engines: set[Any] = set()


async def ensure_schema(database: Any) -> None:
    engine = getattr(database, "engine", None)
    if engine is None:
        raise RuntimeError("Database is not initialized")
    if engine in _initialized_engines:
        return
    async with _schema_lock:
        if engine in _initialized_engines:
            return
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: WorkspaceStep.__table__.create(sync, checkfirst=True))
        _initialized_engines.add(engine)


def workspace_step(*, workflow_id: str, surface: str, step_id: int) -> WorkflowEvent:
    """A step was recorded: identity only, the Workspace reads it back."""
    return WorkflowEvent(
        source=SOURCE,
        type="com.opencompany.workspace.step",
        subject=workflow_id,
        data={"workflow_id": workflow_id, "surface": surface, "step_id": step_id},
    )


def _row(step: WorkspaceStep) -> Dict[str, Any]:
    at = step.created_at if step.created_at.tzinfo else step.created_at.replace(tzinfo=timezone.utc)
    return {"id": step.id, "surface": step.surface, "text": step.text, "node_id": step.node_id, "at": at.isoformat()}


async def record_step(
    database: Any, *, workflow_id: Optional[str], surface: Surface, text: str, node_id: Optional[str] = None
) -> None:
    """Add one step to a workflow's log and announce it. Best effort: a step
    that cannot be saved is logged, never raised. An unsaved run (no
    workflow) has no log."""
    if not workflow_id or surface not in SURFACES or not text.strip():
        return
    try:
        await ensure_schema(database)
        async with database.get_session() as session:
            step = WorkspaceStep(workflow_id=workflow_id, surface=surface, text=text.strip()[:MAX_TEXT_CHARS], node_id=node_id)
            session.add(step)
            await session.flush()
            # Keep the newest steps only.
            keep = (
                select(WorkspaceStep.id)
                .where(WorkspaceStep.workflow_id == workflow_id)
                .order_by(WorkspaceStep.id.desc())
                .limit(MAX_STEPS_PER_WORKFLOW)
            )
            await session.execute(
                delete(WorkspaceStep).where(WorkspaceStep.workflow_id == workflow_id, WorkspaceStep.id.not_in(keep))
            )
            await session.commit()
            step_id = int(step.id)
    except Exception:
        logger.warning("Workspace step not recorded", workflow_id=workflow_id, surface=surface, exc_info=True)
        return
    try:
        from services.status_broadcaster import get_status_broadcaster

        event = workspace_step(workflow_id=workflow_id, surface=surface, step_id=step_id)
        await get_status_broadcaster().broadcast({"type": WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)})
    except Exception:
        logger.warning("workspace_step broadcast failed", workflow_id=workflow_id, exc_info=True)


async def list_steps(database: Any, workflow_id: str) -> List[Dict[str, Any]]:
    """A workflow's steps, oldest first."""
    await ensure_schema(database)
    async with database.get_session() as session:
        rows = (
            await session.execute(
                select(WorkspaceStep).where(WorkspaceStep.workflow_id == workflow_id).order_by(WorkspaceStep.id.asc())
            )
        ).scalars().all()
    return [_row(row) for row in rows]


async def delete_steps(database: Any, workflow_id: str) -> None:
    await ensure_schema(database)
    async with database.get_session() as session:
        await session.execute(delete(WorkspaceStep).where(WorkspaceStep.workflow_id == workflow_id))
        await session.commit()


@ws_handler("workflow_id")
async def handle_workspace_steps_list(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """A workflow's steps, oldest first: ``{success, workflow_id, steps: [{id,
    surface, text, node_id, at}]}``. Only the workflow's owner reads them (the
    chat's owner check: never the internal worker socket)."""
    from core.container import container
    from services.chat.access import ChatAccessDenied, authorize_session

    workflow_id = str(data["workflow_id"])
    database = container.database()
    try:
        scope = await authorize_session(database, websocket, workflow_id)
    except ChatAccessDenied:
        return {"success": False, "error": "access_denied"}
    if scope.workflow_id is None:
        return {"success": False, "error": "access_denied"}
    return {"workflow_id": workflow_id, "steps": await list_steps(database, workflow_id)}


async def _on_workflow_deleted(database: Any, workflow_id: str) -> None:
    await delete_steps(database, workflow_id)


WS_HANDLERS = {"workspace_steps_list": handle_workspace_steps_list}


def _register() -> None:
    from services.workflow_storage.hooks import register_workflow_deleted_hook
    from services.ws_handler_registry import register_ws_handlers

    register_ws_handlers(WS_HANDLERS)
    register_workflow_deleted_hook(_on_workflow_deleted)


_register()

__all__ = [
    "MAX_STEPS_PER_WORKFLOW",
    "SURFACES",
    "WorkspaceStep",
    "delete_steps",
    "handle_workspace_steps_list",
    "list_steps",
    "record_step",
    "workspace_step",
]
