"""Authorized, idempotent creation of fresh agent identities."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import select

from core.container import container
from models.database import Workflow
from services.agent_runtime_policy import AgentRuntimeParams, new_agent_parameters
from services.graph_build import GraphAdditions, NewNode
from services.workflow_storage.mutate import apply_graph_additions

router = APIRouter(prefix="/api/agents", tags=["agents"])


class CreateAgent(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=255)
    node_type: str = Field(min_length=1, max_length=255)
    mutation_id: UUID
    position: tuple[float, float] = (0, 0)
    parameters: dict = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


@router.post("")
async def create_agent(body: CreateAgent, request: Request):
    from services.node_allowlist import get_node_allowlist_service
    from services.node_registry import get_node_class

    cls = get_node_class(body.node_type)
    if (cls is None or getattr(cls, "component_kind", "") != "agent"
            or not issubclass(cls.Params, AgentRuntimeParams)):
        raise HTTPException(status_code=400, detail="Select a supported agent type")
    # Browser agents require their complete owner-aware bundle.
    if body.node_type == "browser_agent":
        raise HTTPException(status_code=400, detail="Use Create Browser AI Agent")
    policy = get_node_allowlist_service().get_config()
    if body.node_type in policy["disabled_nodes"] or set(cls.group).intersection(policy["disabled_groups"]):
        raise HTTPException(status_code=403, detail="This agent is disabled by the operator")
    forbidden = {"runtime_behavior_version", "pi_harness_version", "api_key", "access_token", "profile_id"}
    forbidden.update(getattr(cls, "server_controlled_fields", ()))
    try:
        parameters = cls.Params.model_validate({key: value for key, value in body.parameters.items()
                                              if key not in forbidden}).model_dump(mode="json", exclude_unset=True)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Invalid initial agent parameters") from exc
    parameters = new_agent_parameters(parameters)
    database = container.database()
    principal = str(getattr(request.state, "user_id", None) or "owner")

    async def authorize(session):
        workflow = (await session.execute(select(Workflow).where(Workflow.id == body.workflow_id))).scalar_one_or_none()
        if workflow is None or str((workflow.data or {}).get("owner_id") or "owner") != principal:
            raise HTTPException(status_code=404, detail="Workflow not found")

    # Receipts do not confer access if ownership changes after creation.
    async with database.get_session() as session:
        await authorize(session)
    if body.node_type == "coding_agent":
        from services.coding_agent_recipe import coding_agent_additions
        additions = coding_agent_additions(agent_parameters=parameters, position=body.position)
        for node in additions.nodes:
            node_cls = get_node_class(node.type)
            if (node_cls is None or node.type in policy["disabled_nodes"]
                    or set(node_cls.group).intersection(policy["disabled_groups"])):
                raise HTTPException(status_code=403, detail=f"Coding Agent requires the enabled {node.label} node")
    else:
        nodes = [NewNode("agent", body.node_type, cls.display_name, parameters, body.position)]
        if getattr(cls, "requires_context", False):
            nodes.append(NewNode("context", "context", "Context", position=(body.position[0], body.position[1] + 180), context_of="agent"))
        additions = GraphAdditions(nodes=nodes)
    result = await apply_graph_additions(database, body.workflow_id, additions,
        mutation_id=f"create-agent:{principal}:{body.mutation_id}", authorize=authorize)
    if result is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"node_ids": result.node_ids, "operations": result.operations, "applied": result.applied,
            "saved_revision": result.saved_revision}
