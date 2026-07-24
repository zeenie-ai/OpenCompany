"""AI Agent — Wave 11.C migration (tool-calling agent).

Tool-calling agent with memory, skills, and iterative
reasoning. Delegates execution to ``handlers/ai.handle_ai_agent`` —
that body owns the agent-loop construction + tool binding +
streaming + memory persistence + delegation.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._handles import STD_AGENT_HINTS, std_agent_handles


class AIAgentParams(BaseModel):
    """AI Agent tuning surface.

    ``api_key`` is intentionally NOT a node field — credentials are stored
    via the credentials DB and auto-injected at execution time by
    ``services/node_executor._inject_api_keys`` (see AI_MODEL_TYPES in
    ``server/constants.py``).

    Tuning fields use the ``group="options"`` convention so the parameter
    panel nests them in a collapsible "Options" collection rather than
    rendering flat beside provider/model/prompt. See
    ``docs-internal/plugin_system.md`` for the convention spec.
    """

    prompt: str = Field(
        default="",
        json_schema_extra={
            "placeholder": "Enter your prompt or use template variables...",
            "rows": 4,
        },
    )
    provider: Literal[
        "openai",
        "anthropic",
        "gemini",
        "openrouter",
        "xai",
        "groq",
        "cerebras",
        "deepseek",
        "kimi",
        "mistral",
        # Local-server providers — agent execution reads
        # ``{provider}_proxy`` to point the native OpenAI client at the
        # user's localhost server. Without these entries the dropdown
        # silently falls back to ``"openai"`` and execute_agent ends
        # up calling api.openai.com instead.
        "ollama",
        "lmstudio",
    ] = "openai"
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
        json_schema_extra={"numberStepSize": 0.1, "group": "options"},
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


class AIAgentOutput(BaseModel):
    response: Optional[str] = None
    thinking: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    finish_reason: Optional[str] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class AIAgentNode(ActionNode):
    type = "aiAgent"
    display_name = "AI Agent"
    subtitle = "AI Agent"
    group = ("agent",)
    description = "AI agent with tool calling, memory, and iterative reasoning"
    component_kind = "agent"
    tool_name = "delegate_to_ai_agent"
    handles = std_agent_handles()
    ui_hints = STD_AGENT_HINTS
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = AIAgentParams
    Output = AIAgentOutput

    @Operation("execute", cost={"service": "ai_agent", "action": "run", "count": 1})
    async def execute_op(self, ctx: NodeContext, params: AIAgentParams) -> Any:
        """Inlined from handlers/ai.handle_ai_agent (Wave 11.D.6).

        Pre-dispatch flow (edge walk + task inject + prompt fallback)
        lives in :mod:`nodes.agent._inline`. This method just calls
        ``AIService.execute_agent`` with the prepared kwargs. The
        underlying agent loop + tool binding + memory I/O + streaming
        hooks stay in AIService.
        """
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
            log_prefix="[AI Agent]",
        )
        # ``execute_agent`` raises ``NodeUserError`` directly for typed
        # openai SDK failures (context overflow, bad key, server down).
        # Anything that comes back here as ``success=False`` is a real
        # bug worth a stacktrace via ``RuntimeError``.
        response = await ai_service.execute_agent(ctx.node_id, **kwargs)
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "AI Agent execution failed")
