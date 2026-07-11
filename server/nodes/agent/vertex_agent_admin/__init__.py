"""Vertex Agent Admin — managed-agent lifecycle (Agents API).

Create / list / get / delete custom managed agents on the Gemini
Enterprise Agent Platform. Custom agents build on the prebuilt
Antigravity base agent with their own system instruction and built-in
tool set; run them with the ``vertex_managed_agent`` node by putting
the custom agent id in its ``agent`` parameter.

Agent creation is a long-running operation (~2-3 minutes the first
time, seconds afterwards) — the Python SDK blocks until it resolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue

from .._vertex import (
    DEFAULT_LOCATION,
    DEFAULT_MANAGED_AGENT,
    build_genai_client,
    raise_as_user_error,
    resolve_gemini_api_key_from_store,
)

logger = get_logger(__name__)

_CREATE_ONLY = {"displayOptions": {"show": {"operation": ["create"]}}}
_NEEDS_AGENT_ID = {"displayOptions": {"show": {"operation": ["create", "get", "delete"]}}}


class VertexAgentAdminParams(BaseModel):
    operation: Literal["create", "list", "get", "delete"] = "list"
    project_id: str = Field(
        default="",
        description=(
            "GCP project id (auth via gcloud Application Default "
            "Credentials). Leave empty to use a stored 'AIza' Gemini key."
        ),
        json_schema_extra={"placeholder": "my-gcp-project"},
    )
    agent_id: Optional[str] = Field(
        default=None,
        description="Custom agent id (lowercase letters, numbers, hyphens).",
        json_schema_extra=_NEEDS_AGENT_ID,
    )

    # Create fields
    description: Optional[str] = Field(default=None, json_schema_extra=_CREATE_ONLY)
    system_instruction: Optional[str] = Field(
        default=None,
        json_schema_extra={**_CREATE_ONLY, "rows": 3},
    )
    base_agent: str = Field(default=DEFAULT_MANAGED_AGENT, json_schema_extra=_CREATE_ONLY)
    tools: List[Literal["code_execution", "filesystem", "google_search", "url_context"]] = Field(
        default_factory=lambda: ["code_execution"],
        description="Built-in cloud tools the custom agent may use.",
        json_schema_extra=_CREATE_ONLY,
    )

    location: str = Field(
        default=DEFAULT_LOCATION,
        json_schema_extra={"group": "options"},
    )

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "groups": {
                "options": {
                    "display_name": "Options",
                    "placeholder": "Add Option",
                },
            },
        },
    )


class VertexAgentAdminOutput(BaseModel):
    operation: Optional[str] = None
    agent: Optional[Dict[str, Any]] = None
    agents: Optional[List[Dict[str, Any]]] = None
    count: Optional[int] = None
    deleted: Optional[bool] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(extra="allow")


def _agent_to_dict(agent: Any) -> Dict[str, Any]:
    if hasattr(agent, "model_dump"):
        return agent.model_dump(mode="json", exclude_none=True)
    if isinstance(agent, dict):
        return agent
    return {"id": str(agent)}


class VertexAgentAdminNode(ActionNode):
    type = "vertex_agent_admin"
    display_name = "Vertex Agent Admin"
    subtitle = "Agent Lifecycle"
    group = ("tool",)
    description = (
        "Create, list, inspect, and delete custom managed agents on the "
        "Gemini Enterprise Agent Platform."
    )
    component_kind = "square"
    # Lifecycle CRUD (incl. delete) is operator-facing, not LLM-callable.
    usable_as_tool = False
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.REST_API

    Params = VertexAgentAdminParams
    Output = VertexAgentAdminOutput

    async def _client(self, params: VertexAgentAdminParams) -> Any:
        api_key = "" if params.project_id else await resolve_gemini_api_key_from_store()
        return build_genai_client(api_key, params.project_id, params.location)

    @staticmethod
    def _require_agent_id(params: VertexAgentAdminParams) -> str:
        agent_id = (params.agent_id or "").strip()
        if not agent_id:
            raise NodeUserError(
                f"vertex_agent_admin: '{params.operation}' needs an agent_id."
            )
        return agent_id

    @Operation("create", cost={"service": "vertex_agent", "action": "create", "count": 1})
    async def create_op(self, ctx: NodeContext, params: VertexAgentAdminParams) -> Any:
        from services.status_broadcaster import get_status_broadcaster

        agent_id = self._require_agent_id(params)
        client = await self._client(params)
        await get_status_broadcaster().update_node_status(
            ctx.node_id,
            "executing",
            {"message": f"Creating agent '{agent_id}' (first create takes ~2-3 min)..."},
            workflow_id=ctx.workflow_id,
        )
        kwargs: Dict[str, Any] = {
            "id": agent_id,
            "base_agent": params.base_agent or DEFAULT_MANAGED_AGENT,
            "tools": [{"type": tool} for tool in params.tools],
        }
        if params.description:
            kwargs["description"] = params.description
        if params.system_instruction:
            kwargs["system_instruction"] = params.system_instruction
        try:
            agent = await client.aio.agents.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 — mapped below
            raise_as_user_error(exc, what=f"Agent create '{agent_id}'")
        return {
            "operation": "create",
            "agent": _agent_to_dict(agent),
            "timestamp": datetime.now().isoformat(),
        }

    @Operation("list", cost={"service": "vertex_agent", "action": "list", "count": 1})
    async def list_op(self, ctx: NodeContext, params: VertexAgentAdminParams) -> Any:
        client = await self._client(params)
        try:
            response = await client.aio.agents.list()
        except Exception as exc:  # noqa: BLE001 — mapped below
            raise_as_user_error(exc, what="Agent list")
        items = getattr(response, "agents", None) or (
            list(response) if not isinstance(response, dict) else []
        )
        agents = [_agent_to_dict(agent) for agent in (items or [])]
        return {
            "operation": "list",
            "agents": agents,
            "count": len(agents),
            "timestamp": datetime.now().isoformat(),
        }

    @Operation("get", cost={"service": "vertex_agent", "action": "get", "count": 1})
    async def get_op(self, ctx: NodeContext, params: VertexAgentAdminParams) -> Any:
        agent_id = self._require_agent_id(params)
        client = await self._client(params)
        try:
            agent = await client.aio.agents.get(id=agent_id)
        except Exception as exc:  # noqa: BLE001 — mapped below
            raise_as_user_error(exc, what=f"Agent get '{agent_id}'")
        return {
            "operation": "get",
            "agent": _agent_to_dict(agent),
            "timestamp": datetime.now().isoformat(),
        }

    @Operation("delete", cost={"service": "vertex_agent", "action": "delete", "count": 1})
    async def delete_op(self, ctx: NodeContext, params: VertexAgentAdminParams) -> Any:
        agent_id = self._require_agent_id(params)
        client = await self._client(params)
        try:
            await client.aio.agents.delete(id=agent_id)
        except Exception as exc:  # noqa: BLE001 — mapped below
            raise_as_user_error(exc, what=f"Agent delete '{agent_id}'")
        return {
            "operation": "delete",
            "deleted": True,
            "agent": {"id": agent_id},
            "timestamp": datetime.now().isoformat(),
        }
