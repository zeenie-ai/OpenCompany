"""Chat Agent (Zeenie) — Wave 11.C migration.

Conversational agent with skills + memory. Distinct from
:class:`AIAgentNode` because it uses ``handle_chat_agent`` which
applies a different default system message + skill prompt assembly.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._handles import STD_AGENT_HINTS, std_agent_handles


class ChatAgentParams(BaseModel):
    """Zeenie tuning surface.

    Matches AIAgentParams: no ``api_key`` field (credentials auto-injected
    at execution time), tuning fields grouped under "options" for
    collapsible rendering. See docs-internal/plugin_system.md for the
    convention.
    """

    provider: Literal[
        "openai",
        "anthropic",
        "gemini",
        "openrouter",
        "groq",
        "cerebras",
        "deepseek",
        "kimi",
        "mistral",
        # Local-server providers — see ai_agent.Params for the proxy_url
        # rationale. Same fix; same reason.
        "ollama",
        "lmstudio",
    ] = "openai"
    model: str = Field(
        default="",
        json_schema_extra={"placeholder": "Select a model..."},
    )
    prompt: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "Optional: leave empty to use connected input",
            "rows": 4,
        },
    )
    system_message: Optional[str] = Field(
        default="",
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


class ChatAgentOutput(BaseModel):
    response: Optional[str] = None
    thinking: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    finish_reason: Optional[str] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatAgentNode(ActionNode):
    type = "chatAgent"
    display_name = "Zeenie"
    subtitle = "Personal Assistant"
    group = ("agent",)
    description = "Conversational AI agent with skills and memory"
    component_kind = "agent"
    tool_name = "delegate_to_chat_agent"
    handles = std_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = ChatAgentParams
    Output = ChatAgentOutput

    @Operation("execute", cost={"service": "chat_agent", "action": "run", "count": 1})
    async def execute_op(self, ctx: NodeContext, params: ChatAgentParams) -> Any:
        """Inlined from handlers/ai.handle_chat_agent (Wave 11.D.6)."""
        from services.plugin.deps import get_ai_service, get_database

        from .._inline import prepare_agent_call

        ai_service = get_ai_service()
        database = get_database()
        kwargs = await prepare_agent_call(
            node_id=ctx.node_id,
            node_type=self.type,
            parameters=params.model_dump(),
            context=ctx.raw,
            database=database,
            log_prefix="[Chat Agent]",
        )
        response = await ai_service.execute_chat_agent(ctx.node_id, **kwargs)
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "Chat Agent execution failed")
