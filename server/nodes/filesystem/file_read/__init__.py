"""File Read — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class FileReadParams(BaseModel):
    file_path: str = Field(...)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1, le=10000)

    model_config = ConfigDict(extra="ignore")


class FileReadOutput(BaseModel):
    content: Optional[str] = None
    line_count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class FileReadNode(ActionNode):
    type = "fileRead"
    display_name = "File Read"
    subtitle = "Read Contents"
    group = ("filesystem", "tool")
    description = "Read file contents with line numbers and pagination"
    tool_name = "file_read"
    tool_description = (
        "Read file contents with pagination. Returns line-numbered text. "
        "Paths resolve inside the per-workflow workspace — use "
        "workspace-relative paths (e.g. 'reports/data.csv'); '..' is not "
        "allowed."
    )
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = FileReadParams
    Output = FileReadOutput

    @Operation("read")
    async def read(self, ctx: NodeContext, params: FileReadParams) -> Any:
        """Inlined from handlers/filesystem.py (Wave 11.D.1)."""
        import asyncio
        from .._backend import get_backend, normalize_virtual_path

        if not params.file_path:
            raise NodeUserError("file_path is required")
        backend = get_backend(params.model_dump(), ctx.raw)
        file_path = normalize_virtual_path(params.file_path)
        try:
            result = await asyncio.to_thread(
                backend.read,
                file_path,
                offset=params.offset,
                limit=params.limit,
            )
        except (FileNotFoundError, IsADirectoryError, ValueError) as e:
            # File doesn't exist / is a directory / bad offset — the
            # LLM should retry with corrected input, not fail the run.
            raise NodeUserError(str(e)) from e
        # backend.read returns deepagents' ReadResult dataclass — unwrap
        # it into the declared Output contract (same shape fileModify /
        # fsSearch already return for their backend results). Returning
        # the raw dataclass breaks JSON persistence of node_outputs.
        if result.error:
            raise NodeUserError(result.error)
        file_data = result.file_data or {}
        content = file_data.get("content", "")
        return FileReadOutput(
            content=content,
            line_count=len(content.splitlines()),
            file_path=file_path,
        )
