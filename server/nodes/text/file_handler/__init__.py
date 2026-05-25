"""File Handler — Wave 11.C migration.

Content-metadata wrapper (NOT file I/O). Takes a text content blob plus
file-type / file-name metadata and returns a wrapped ``{type: "file",
data: {...}}`` envelope for downstream nodes. Actual file I/O lives in
``nodes/filesystem/file_read`` / ``file_modify``.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class FileHandlerParams(BaseModel):
    file_type: Literal[
        "generic",
        "markdown",
        "text",
        "json",
        "csv",
        "html",
        "xml",
    ] = Field(
        default="generic",
        description="Content type tag used for downstream processing hints.",
    )
    file_name: str = Field(
        default="untitled.txt",
        description="Filename label attached to the wrapped content.",
    )
    content: str = Field(
        default="",
        description="Text content to wrap.",
        json_schema_extra={"rows": 8},
    )

    model_config = ConfigDict(extra="ignore")


class FileHandlerOutput(BaseModel):
    type: Optional[str] = None
    data: Optional[dict] = None
    node_id: Optional[str] = None
    timestamp: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class FileHandlerNode(ActionNode):
    type = "fileHandler"
    display_name = "File Handler"
    subtitle = "Wrap Content Metadata"
    group = ("text",)
    description = "Wrap text content with file-type / file-name metadata"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = FileHandlerParams
    Output = FileHandlerOutput

    @Operation("wrap")
    async def wrap(self, ctx: NodeContext, params: FileHandlerParams) -> Any:
        from services.plugin.deps import get_text_service

        text_service = get_text_service()
        # Pass schema-canonical snake_case through to the service. The
        # service's *output* dict still carries camelCase keys
        # (`fileName` / `fileType`) by historical wire contract -- the
        # frontend reads them that way -- but parameters in are now
        # snake_case end-to-end.
        response = await text_service.execute_file_handler(
            ctx.node_id,
            params.model_dump(),
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "File handler failed")
