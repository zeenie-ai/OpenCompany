"""Authorized atomic creation of the Browser AI Agent capability."""
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select

from core.container import container
from models.database import Workflow, NodeParameter
from services.browser_agent_recipe import browser_agent_additions
from services.plugin import NodeUserError
from services.workflow_storage.mutate import apply_graph_additions

router = APIRouter(prefix="/api/browser/agents", tags=["browser"])


class CreateBrowserAgent(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=255)
    mutation_id: UUID
    browser_node_id: str | None = Field(default=None, max_length=255)
    parent_node_id: str | None = Field(default=None, max_length=255)
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
        if saved is None or str((saved.data or {}).get("owner_id") or "owner") != principal:
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
        from services.agent_runtime_policy import new_agent_runtime_enabled
        # Existing profile identities are never transferred to a new v1 agent.
        if new_agent_runtime_enabled() and body.browser_node_id:
            raise HTTPException(status_code=409, detail={"code": "private_browser_required",
                "message": "Create separate Browser tool/profile for this new agent. The selected Browser will remain unchanged."})
        selected = body.browser_node_id
        return browser_agent_additions(agent_parameters=parameters, position=body.position,
                                       browser_node_id=selected), {}

    try:
        result = await apply_graph_additions(database, body.workflow_id, browser_agent_additions(),
                                            mutation_id=f"browser-agent:{principal}:{body.mutation_id}",
                                            prepare=prepare, authorize=authorize, editor_team_parent=body.parent_node_id or "")
    except (ValueError, NodeUserError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"node_ids": result.node_ids, "operations": result.operations, "applied": result.applied,
            "saved_revision": result.saved_revision}


class CreatePrivateBrowser(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=255)
    mutation_id: UUID
    position: tuple[float, float] = (0, 0)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


@router.post("/{agent_node_id}/tools")
async def create_private_browser(agent_node_id: str, body: CreatePrivateBrowser, request: Request):
    """Explicit remediation; never upgrades or edits the selected old tool."""
    from services.agent_runtime_policy import runtime_behavior_version
    from services.graph_build import GraphAdditions, NewNode, tool_edge, EdgeRemoval
    from services.browser_resources import BrowserResourceError
    from models.browser_profiles import BrowserProfileReservation
    from services.node_registry import get_node_class
    from services.node_allowlist import get_node_allowlist_service
    database = container.database()
    principal = str(getattr(request.state, "user_id", None) or "owner")
    reused = {}

    async def authorize(session):
        saved = await session.get(Workflow, body.workflow_id)
        graph = saved.data if saved else {}
        agent = next((n for n in graph.get("nodes", []) if n.get("id") == agent_node_id), None)
        cls = get_node_class(agent.get("type")) if agent else None
        params = await session.get(NodeParameter, agent_node_id)
        if saved is None or str(graph.get("owner_id") or "owner") != principal or cls is None or cls.component_kind != "agent":
            raise HTTPException(status_code=404, detail="Agent not found")
        from services.agent_bindings import BROWSER_AGENT_ONLY, agent_can_bind_tool
        if not agent_can_bind_tool(agent["type"], "browser"):
            raise HTTPException(status_code=409, detail=BROWSER_AGENT_ONLY)
        if runtime_behavior_version(params.parameters if params else {}) != 1:
            raise HTTPException(status_code=409, detail="Create a new agent to use a private Browser profile.")
        reused.clear()
        for edge in graph.get("edges", []):
            if edge.get("target") != agent_node_id or edge.get("targetHandle") != "input-tools":
                continue
            reservation = (await session.execute(select(BrowserProfileReservation).where(
                BrowserProfileReservation.owner_id == principal, BrowserProfileReservation.workflow_id == body.workflow_id,
                BrowserProfileReservation.browser_node_id == edge.get("source"),
                BrowserProfileReservation.agent_node_id == agent_node_id))).scalar_one_or_none()
            if reservation:
                reused["browser"] = str(edge["source"])
                break

    async with database.get_session() as session:
        await authorize(session)
    policy = get_node_allowlist_service().get_config()
    if "browser" in policy["disabled_nodes"] or {"browser", "tool"}.intersection(policy["disabled_groups"]):
        raise HTTPException(status_code=403, detail="Browser is disabled by the operator")

    def prepare(graph):
        if reused:
            return GraphAdditions(), dict(reused)
        nodes = {n["id"]: n for n in graph.get("nodes", [])}
        removed = tuple(EdgeRemoval(str(e["source"]), agent_node_id, "input-tools")
            for e in graph.get("edges", []) if e.get("target") == agent_node_id
            and e.get("targetHandle") == "input-tools" and nodes.get(e.get("source"), {}).get("type") == "browser")
        return GraphAdditions(nodes=(NewNode("browser", "browser", "Private Browser", {}, body.position),),
            edges=(tool_edge("browser", agent_node_id),), removed_edges=removed), {}

    try:
        result = await apply_graph_additions(database, body.workflow_id, GraphAdditions(),
            mutation_id=f"private-browser:{principal}:{body.mutation_id}", prepare=prepare, authorize=authorize)
    except BrowserResourceError as exc:
        raise HTTPException(status_code=409, detail=exc.as_dict()) from None
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"node_ids": result.node_ids, "operations": result.operations, "applied": result.applied,
            "saved_revision": result.saved_revision}
