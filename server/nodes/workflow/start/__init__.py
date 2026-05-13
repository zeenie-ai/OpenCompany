"""Start node — Wave 11.C migration.

Workflow entry point. Provides initial data to downstream nodes from
a user-authored JSON blob (``initialData``) on the node itself. Has
its own componentKind="start" because the StartNode component is a
distinct visual treatment from generic triggers.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class StartParams(BaseModel):
    initial_data: Any = Field(default=None)

    model_config = ConfigDict(extra="allow")


class StartOutput(BaseModel):
    data: Optional[Any] = None

    model_config = ConfigDict(extra="allow")


class StartNode(ActionNode):
    type = "start"
    display_name = "Start"
    subtitle = "Workflow Start"
    group = ("workflow",)
    description = "Starting point for workflow execution. Provides initial data to connected nodes."
    component_kind = "start"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    ui_hints = {
        "hideInputSection": True,
        "hideOutputSection": True,
        "hasInitialDataBlob": True,
    }
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = StartParams
    Output = StartOutput

    @Operation("emit")
    async def emit(self, ctx: NodeContext, params: StartParams) -> Any:
        import json

        raw = params.initial_data
        if raw is None:
            return {}
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except Exception:
                return {}
        return raw
