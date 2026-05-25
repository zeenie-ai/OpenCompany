"""Shared base for chat-model plugins.

8 providers (openai / anthropic / openrouter / groq / cerebras /
deepseek / kimi / mistral) all delegate to ``handle_ai_chat_model`` —
only the type / display_name / icon / colour / description vary.
Per-provider tuning fields (top_k, safety_settings, thinking_budget,
reasoning_effort, reasoning_format) are set on the per-provider Params
when relevant; the union from ``models/nodes.py`` is pre-existing and
can be re-used.

Each provider gets its own file under ``nodes/model/`` so the path
matches the palette grouping; this base lives in ``_base.py`` (private).
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class ChatModelParams(BaseModel):
    """Shared params surface — providers override fields where their
    capabilities differ (e.g. fixed temperature for o-series, no
    thinking for openrouter passthrough).

    Snake_case field names throughout; JSON Schema keys match field
    names exactly. ``displayOptions.show`` references resolve because
    no alias/field-name divergence can occur.
    """

    prompt: str = Field(
        default="",
        json_schema_extra={"rows": 4, "placeholder": "{{ $json.message }}"},
    )
    model: str = Field(
        default="",
        json_schema_extra={"placeholder": "Select a model..."},
    )
    # default=None so an unset value falls through to ``agent.default_temperature``
    # in server/config/llm_defaults.json (resolved by _resolve_temperature).
    temperature: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=2.0,
        json_schema_extra={"numberStepSize": 0.1},
    )
    # default=None so an unset value is absent from the dumped dict and the
    # backend (_resolve_max_tokens) falls through to the per-model default
    # in server/config/llm_defaults.json instead of being silently capped.
    max_tokens: Optional[int] = Field(default=None, ge=1, le=200000)
    system_prompt: Optional[str] = Field(
        default="",
        json_schema_extra={"rows": 3, "placeholder": "You are a helpful assistant."},
    )
    api_key: Optional[str] = Field(
        default=None,
        json_schema_extra={"password": True},
    )
    top_p: Optional[float] = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        json_schema_extra={"numberStepSize": 0.1},
    )
    thinking_enabled: bool = Field(default=False)
    thinking_budget: Optional[int] = Field(default=None, ge=1)
    reasoning_effort: Optional[Literal["low", "medium", "high"]] = Field(default=None)
    reasoning_format: Optional[Literal["parsed", "hidden"]] = Field(default=None)

    model_config = ConfigDict(extra="ignore")


class ChatModelOutput(BaseModel):
    response: Optional[str] = None
    thinking: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    finish_reason: Optional[str] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatModelBase(ActionNode, abstract=True):
    """Subclass and set type/display_name/icon/color/description.
    Override Params to add provider-specific tuning fields."""

    component_kind = "model"
    handles = ({"name": "output-model", "kind": "output", "position": "right", "label": "Model", "role": "model"},)
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.AI_HEAVY

    Params = ChatModelParams
    Output = ChatModelOutput

    @Operation("chat", cost={"service": "chat_model", "action": "chat", "count": 1})
    async def chat(self, ctx: NodeContext, params: ChatModelParams) -> Any:
        from services.plugin.deps import get_ai_service

        ai_service = get_ai_service()
        # ``execute_chat`` raises ``NodeUserError`` directly for typed
        # openai SDK failures (context overflow, bad key, server down) —
        # BaseNode.execute() then logs at WARN with no traceback.
        # ``RuntimeError`` here only fires for "success=False" responses
        # that aren't typed SDK errors (real bugs worth a stacktrace).
        response = await ai_service.execute_chat(
            ctx.node_id,
            self.type,
            params.model_dump(),
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or f"{self.type} chat failed")
