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
        from .._backend import (
            get_backend,
            get_path_lock,
            normalize_virtual_path,
            perform_string_replacement,
            run_sync_until_complete,
        )

        if not params.file_path:
            raise NodeUserError("file_path is required")
        backend = get_backend(params.model_dump(), ctx.raw)
        file_path = normalize_virtual_path(params.file_path)
        resolved = backend._resolve_path(file_path)
        path_lock = get_path_lock(resolved)

        if params.operation == "write":
            def _do_write():
                if resolved.exists() and resolved.is_dir():
                    raise IsADirectoryError(f"Cannot write to {file_path}: path is a directory")
                backend.atomic_write_text(resolved, params.content)

            try:
                async with path_lock:
                    await run_sync_until_complete(_do_write)
            except (OSError, ValueError) as e:
                raise NodeUserError(str(e)) from e
            return {"operation": "write", "file_path": file_path}

        if params.operation == "edit":
            if not params.old_string:
                raise NodeUserError("old_string is required for edit")
            def _do_edit():
                if not resolved.exists() or not resolved.is_file():
                    return None, None, f"Error: File '{file_path}' not found"
                content = backend.read_text_secure(resolved)
                old_string = params.old_string.replace("\r\n", "\n").replace("\r", "\n")
                new_string = params.new_string.replace("\r\n", "\n").replace("\r", "\n")
                replacement = perform_string_replacement(content, old_string, new_string, params.replace_all)
                if isinstance(replacement, str):
                    return None, None, replacement
                new_content, occurrences = replacement
                backend.atomic_write_text(resolved, new_content)
                return file_path, int(occurrences), None

            try:
                async with path_lock:
                    result_path, occurrences, error = await run_sync_until_complete(_do_edit)
            except (OSError, UnicodeError, ValueError) as e:
                raise NodeUserError(f"Error editing file '{file_path}': {e}") from e
            if error:
                raise NodeUserError(error)
            return {
                "operation": "edit",
                "file_path": result_path or file_path,
                "occurrences": occurrences,
            }

        raise NodeUserError(f"Unknown operation: {params.operation}")
