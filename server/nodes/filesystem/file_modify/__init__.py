"""File Modify — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


_WRITE = {"displayOptions": {"show": {"operation": ["write"]}}}
_EDIT = {"displayOptions": {"show": {"operation": ["edit"]}}}


class FileModifyParams(BaseModel):
    operation: Literal["write", "edit"] = "write"
    file_path: str = Field(...)
    content: str = Field(default="", json_schema_extra=_WRITE)
    old_string: str = Field(default="", json_schema_extra=_EDIT)
    new_string: str = Field(default="", json_schema_extra=_EDIT)
    replace_all: bool = Field(default=False, json_schema_extra=_EDIT)

    model_config = ConfigDict(extra="ignore")


class FileModifyOutput(BaseModel):
    written: Optional[bool] = None
    replacements: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class FileModifyNode(ActionNode):
    type = "fileModify"
    display_name = "File Modify"
    subtitle = "Write/Edit"
    group = ("filesystem", "tool")
    description = "Write new files or edit existing files"
    tool_name = "file_modify"
    tool_description = (
        "Write a new file or edit an existing file with string replacement. "
        "Operations: write (create/overwrite), edit (find and replace). "
        "Paths resolve inside the per-workflow workspace — use "
        "workspace-relative paths; '..' is not allowed."
    )
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = FileModifyParams
    Output = FileModifyOutput

    @Operation("modify")
    async def modify(self, ctx: NodeContext, params: FileModifyParams) -> Any:
        """Inlined from handlers/filesystem.py (Wave 11.D.1)."""
        import asyncio
        from .._backend import get_backend, normalize_virtual_path

        if not params.file_path:
            raise NodeUserError("file_path is required")
        backend = get_backend(params.model_dump(), ctx.raw)
        file_path = normalize_virtual_path(params.file_path)

        if params.operation == "write":
            # ``write`` is wholesale create-or-replace. deepagents'
            # backend.write() refuses to overwrite by design, so unlink any
            # pre-existing file at the resolved path first; ``edit`` remains
            # the surgical option for callers that want a partial change.
            def _do_write():
                resolved = backend._resolve_path(file_path)
                if resolved.exists():
                    if resolved.is_dir():
                        raise IsADirectoryError(f"Cannot write to {file_path}: path is a directory")
                    resolved.unlink()
                return backend.write(file_path, params.content)

            try:
                result = await asyncio.to_thread(_do_write)
            except (OSError, ValueError) as e:
                raise NodeUserError(str(e)) from e
            if result.error:
                raise NodeUserError(result.error)
            return {"operation": "write", "file_path": result.path or file_path}

        if params.operation == "edit":
            if not params.old_string:
                raise NodeUserError("old_string is required for edit")
            result = await asyncio.to_thread(
                backend.edit,
                file_path,
                params.old_string,
                params.new_string,
                replace_all=params.replace_all,
            )
            if result.error:
                raise NodeUserError(result.error)
            return {
                "operation": "edit",
                "file_path": result.path or file_path,
                "occurrences": result.occurrences,
            }

        raise NodeUserError(f"Unknown operation: {params.operation}")
