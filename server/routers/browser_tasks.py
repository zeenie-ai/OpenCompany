"""Authorized Browser Workspace commands and recent task history."""

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

router = APIRouter(prefix="/api/browser/tasks", tags=["browser"])


class TaskSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: str = Field(min_length=1, max_length=255)
    agent_node_id: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1, max_length=20000)
    submission_id: UUID


def principal(request: Request) -> str:
    from constants import OWNER_PRINCIPAL_ID
    return str(getattr(request.state, "user_id", None) or OWNER_PRINCIPAL_ID)


async def call(operation):
    from services.plugin import NodeUserError
    try:
        return await operation
    except NodeUserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid submission identity") from None


@router.get("/discovery")
async def discover(request: Request, workflow_id: str, browser_node_id: str):
    from services.browser_tasks import discovery
    from services.plugin.deps import get_database
    return await call(discovery(get_database(), principal(request), workflow_id, browser_node_id))


@router.post("")
async def submit_task(request: Request, body: TaskSubmission):
    from services.node_invocations import submit
    from services.authz.workflow_node import resolve_workflow_node
    from services.plugin import NodeUserError
    async def operation():
        _, _, node = await resolve_workflow_node(principal(request), body.workflow_id, body.agent_node_id)
        if node.get("type") != "browser_agent":
            raise NodeUserError("Select a Browser AI Agent")
        return await submit(principal(request), body.workflow_id, body.agent_node_id, body.prompt, str(body.submission_id))
    return await call(operation())


@router.get("/history")
async def history(request: Request, workflow_id: str, node_id: str | None = None,
                  cursor: str | None = None, limit: int = Query(20, ge=1, le=100)):
    from services.workspace_task_history import list_tasks
    from services.plugin.deps import get_database
    return await call(list_tasks(get_database(), principal(request), workflow_id, node_id=node_id, cursor=cursor, limit=limit))


@router.get("/{submission_id}")
async def task_status(request: Request, submission_id: str, workflow_id: str, agent_node_id: str):
    from services.node_invocations import status
    return await call(status(principal(request), workflow_id, agent_node_id, submission_id))


@router.delete("/{submission_id}")
async def cancel_task(request: Request, submission_id: str, workflow_id: str, agent_node_id: str):
    from services.node_invocations import status
    return await call(status(principal(request), workflow_id, agent_node_id, submission_id, cancel=True))
