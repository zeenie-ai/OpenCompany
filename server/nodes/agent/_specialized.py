"""Specialized agent base — subclass + set a few class attrs to mint a new agent.

All specialized agents (android / coding / web / task / social / travel
/ tool / productivity / payments / consumer / autonomous) and the two
team leads (orchestrator_agent / ai_employee) subclass this. In-process
(and on the per-type activity path) they run ``execute_op`` below, which
calls ``AIService.execute_chat_agent``; with the Temporal
``AgentWorkflow`` enabled they run as that child workflow instead
(``AGENT_WORKFLOW_TYPES`` in ``services/temporal/workflow.py``). The
class-level differences are display name, subtitle and description;
icon and colour are not class attributes — they come from the plugin
folder (``icon.svg`` / ``meta.json``) or ``nodes/visuals.json``. Each
agent has its own folder, ``nodes/agent/<type>/__init__.py``, so the
user can find ``android_agent`` directly — but the body lives here so
changing the dispatch path is a one-file edit.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from ._effort import AgentEffort
from ._handles import STD_AGENT_HINTS, std_agent_handles
from ._provider import ProviderRef


from services.agent_runtime_policy import AgentRuntimeParams


class SpecializedAgentParams(AgentRuntimeParams):
    """Identical to AIAgentParams — full LLM tuning surface.

    Mirrors AIAgentParams: no ``api_key`` field (credentials come from the
    credentials DB via runtime injection), tuning fields live in the
    "options" group so the panel renders a collapsible Options collection.
    """

    prompt: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "Enter your prompt or use template variables...",
            "rows": 4,
        },
    )
    provider: ProviderRef = "openai"
    model: str = Field(
        default="",
        json_schema_extra={"placeholder": "Select a model..."},
    )
    system_message: Optional[str] = Field(
        default="You are a helpful assistant",
        json_schema_extra={"rows": 3},
    )

    # ---- "Options" group (collapsed by default in the parameter panel) ----
    # default=None so an unset value falls through to ``agent.default_temperature``
    # in server/config/llm_defaults.json (resolved by _resolve_temperature).
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        json_schema_extra={"group": "options"},
    )
    # default=None so an unset value is absent from the dumped dict and the
    # backend (_resolve_max_tokens) falls through to the per-model default
    # in server/config/llm_defaults.json instead of being silently capped.
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        le=200000,
        json_schema_extra={"group": "options"},
    )
    effort: AgentEffort = None

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


class SpecializedAgentOutput(BaseModel):
    response: Optional[str] = None
    thinking: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    finish_reason: Optional[str] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class SpecializedAgentBase(ActionNode, abstract=True):
    """Subclass and set type/display_name/subtitle/description (icon and
    colour come from the plugin folder or ``nodes/visuals.json``)."""

    component_kind = "agent"
    requires_context = True
    handles = std_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = SpecializedAgentParams
    Output = SpecializedAgentOutput

    @Operation("execute", cost={"service": "specialized_agent", "action": "run", "count": 1})
    async def execute_op(self, ctx: NodeContext, params: SpecializedAgentParams) -> Any:
        """Inlined via ``prepare_agent_call`` (Wave 11.D.6).

        Every subclass (the specialized agents plus the orchestrator and
        ai_employee team leads) routes through
        :func:`AIService.execute_chat_agent` with identical pre-dispatch
        flow when it runs here rather than as an ``AgentWorkflow``.
        Team-lead teammate injection happens inside ``prepare_agent_call``
        based on ``self.type``.
        """
        from services.plugin.deps import get_ai_service, get_database

        from ._inline import prepare_agent_call

        ai_service = get_ai_service()
        database = get_database()
        kwargs = await prepare_agent_call(
            node_id=ctx.node_id,
            node_type=self.type,
            parameters=params.model_dump(),
            context=ctx.raw,
            database=database,
            log_prefix=f"[{self.type}]",
        )
        response = await ai_service.execute_chat_agent(ctx.node_id, **kwargs)
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or f"{self.type} execution failed")
