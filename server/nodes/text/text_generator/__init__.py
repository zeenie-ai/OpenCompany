"""Text Generator — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class TextGeneratorParams(BaseModel):
    source: Literal["static", "ai", "file", "api"] = "static"
    text: str = Field(default="", json_schema_extra={"rows": 4})
    file_path: str = Field(default="")
    api_url: str = Field(default="")

    model_config = ConfigDict(extra="allow")


class TextGeneratorOutput(BaseModel):
    text: Optional[str] = None
    source: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class TextGeneratorNode(ActionNode):
    type = "textGenerator"
    display_name = "Text Generator"
    subtitle = "Static / AI Text"
    group = ("text",)
    description = "Generate text using static, AI, file, or API source"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.DEFAULT

    Params = TextGeneratorParams
    Output = TextGeneratorOutput

    @Operation("generate")
    async def generate(self, ctx: NodeContext, params: TextGeneratorParams) -> Any:
        from services.plugin.deps import get_text_service

        text_service = get_text_service()
        response = await text_service.execute_text_generator(
            ctx.node_id, params.model_dump(),
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "Text generator failed")
