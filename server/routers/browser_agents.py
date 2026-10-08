"""Authorized atomic creation of the Browser AI Agent capability."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from core.container import container
from models.database import Workflow
from services.browser_agent_recipe import browser_agent_additions
from services.plugin import NodeUserError
from services.workflow_storage.mutate import apply_graph_additions

router = APIRouter(prefix="/api/browser/agents", tags=["browser"])


class CreateBrowserAgent(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=255)
    mutation_id: UUID
    browser_node_id: str | None = Field(default=None, max_length=255)
    position: tuple[float, float] = (0, 0)
    provider: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, max_length=255)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


@router.post("")
async def create_browser_agent(body: CreateBrowserAgent, request: Request):
    database = container.database()
    principal = str(getattr(request.state, "user_id", None) or "owner")

    async def authorize(session):
        saved = (await session.execute(select(Workflow).where(Workflow.id == body.workflow_id))).scalar_one_or_none()
        if saved is None or (saved.data.get("owner_id") and str(saved.data["owner_id"]) != principal):
            raise HTTPException(status_code=404, detail="Workflow not found")

    # A cached mutation receipt must not bypass authorization after access
    # was revoked. The transaction callback also checks at mutation time.
    async with database.get_session() as session:
        await authorize(session)
    from services.node_allowlist import get_node_allowlist_service
    from models.node_metadata import NODE_METADATA
    policy = get_node_allowlist_service().get_config()
    for kind in ("browser_agent", "browser", "masterSkill", "context", "visionAnalyze"):
        metadata = NODE_METADATA.get(kind) or {}
        groups = metadata.get("group") or []
        if isinstance(groups, str):
            groups = [groups]
        if kind in policy["disabled_nodes"] or set(groups).intersection(policy["disabled_groups"]):
            raise HTTPException(status_code=403, detail="Browser AI Agent is disabled by the operator")
    if "web_agent" in policy["disabled_skill_folders"]:
        raise HTTPException(status_code=403, detail="Browser instructions are disabled by the operator")

    def prepare(graph):
        if body.browser_node_id:
            node = next((node for node in graph.get("nodes", []) if node.get("id") == body.browser_node_id), None)
            if node is None or node.get("type") != "browser":
                raise NodeUserError("Select a saved Browser tool in this workflow.")
            nodes = {node["id"]: node for node in graph.get("nodes", [])}
            existing = next((edge["target"] for edge in graph.get("edges", [])
                             if edge.get("source") == body.browser_node_id and edge.get("targetHandle") == "input-tools"
                             and nodes.get(edge.get("target"), {}).get("type") == "browser_agent"), None)
            if existing:
                from services.graph_build import GraphAdditions
                return GraphAdditions(), {"agent": existing, "browser": body.browser_node_id}
        parameters = {key: value for key, value in {"provider": body.provider, "model": body.model}.items() if value is not None}
        return browser_agent_additions(agent_parameters=parameters, position=body.position,
                                       browser_node_id=body.browser_node_id), {}

    try:
        result = await apply_graph_additions(database, body.workflow_id, browser_agent_additions(),
                                            mutation_id=f"browser-agent:{principal}:{body.mutation_id}",
                                            prepare=prepare, authorize=authorize)
    except NodeUserError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"node_ids": result.node_ids, "operations": result.operations, "applied": result.applied,
            "saved_revision": result.saved_revision}
