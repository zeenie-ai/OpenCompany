"""Show UI: the tool an employee uses to put a small interface in its chat
reply (docs-internal/chat_protocol.md, "Generated UI").

The employee calls ``show_ui`` with a json-render spec. ``services/genui/
spec.py`` checks it against ``config/chat_genui_catalog.json``: a spec that
breaks a rule fails the call with every reason, so the model writes it again.
``services/chat/parts.py`` then shows it: the spec is saved on the chat run as
a part and streamed to the chat as patches, and when the run ends it is
sealed into the reply message, where a reload finds it.

Only the agent answering the owner's chat message can show UI. Anywhere else
(a canvas Run, a delegated agent, a schedule) the call says nothing was
shown. The tool takes no working step of its own: the UI is what shows.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.genui.spec import SpecError, check_spec, describe_catalog
from services.plugin import NodeContext, NodeUserError, Operation, TaskQueue, ToolNode


# Flat on purpose: this IS the LLM tool schema. The spec's shape is in the
# tool description, generated from the catalog.
class ChatUiParams(BaseModel):
    spec: Dict[str, Any] = Field(
        ...,
        description='The interface as json-render\'s flat spec: {"root": "<id>", "state": {...}, "elements": {...}}.',
    )

    model_config = ConfigDict(extra="ignore")

    @field_validator("spec", mode="before")
    @classmethod
    def _coerce_spec(cls, value: Any) -> Any:
        """Some providers send an object argument as a JSON string."""
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            return parsed
        return value


class ChatUiOutput(BaseModel):
    """What the model reads back: whether it showed, and what changed."""

    shown: bool = False
    ui_id: Optional[str] = None
    elements: Optional[int] = None
    dropped: Optional[List[Dict[str, str]]] = None
    notes: Optional[List[str]] = None
    message: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatUiNode(ToolNode):
    type = "chatUi"
    display_name = "Show UI"
    subtitle = "Interface in a reply"
    group = ("chat", "tool")
    description = (
        "Lets an employee show a small interface inside its chat reply: choices, a short form, numbers, "
        "a chart, buttons. Needs Generated UI on in Settings."
    )
    component_kind = "tool"
    tool_name = "show_ui"
    # The description is generated from the catalog; pin it against stale
    # persisted ToolSchema rows.
    tool_schema_locked = True
    tool_description = describe_catalog()
    handles = ({"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},)
    ui_hints = {"hideRunButton": True}
    annotations = {"destructive": False, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    chat_step_hidden = True

    Params = ChatUiParams
    Output = ChatUiOutput

    @Operation("show")
    async def show(self, ctx: NodeContext, params: ChatUiParams) -> ChatUiOutput:
        from services.chat.parts import show_ui
        from services.plugin.deps import get_database

        try:
            checked = check_spec(params.spec)
        except SpecError as error:
            raise NodeUserError(
                "The interface was not shown. Fix these and call show_ui again: " + "; ".join(error.problems)
            ) from None
        stream = ctx.raw.get("chat_stream") if isinstance(ctx.raw, dict) else None
        tool_call_id = ctx.raw.get("tool_call_id") if isinstance(ctx.raw, dict) else None
        dropped = checked.dropped or None
        notes = checked.notes or None
        if not isinstance(stream, dict) or not isinstance(tool_call_id, str) or not tool_call_id:
            return ChatUiOutput(
                shown=False,
                elements=checked.element_count,
                dropped=dropped,
                notes=notes,
                message="Nothing was shown: an interface shows only in your reply to the owner's chat message.",
            )
        ui_id = await show_ui(get_database(), stream, tool_call_id=tool_call_id, spec=checked.spec)
        return ChatUiOutput(
            shown=True,
            ui_id=ui_id,
            elements=checked.element_count,
            dropped=dropped,
            notes=notes,
            message="Shown under your reply; your text need not repeat what it shows.",
        )
