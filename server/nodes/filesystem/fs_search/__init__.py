"""FS Search — Wave 11.C migration. ls / glob / grep modes."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class FsSearchParams(BaseModel):
    mode: Literal["ls", "glob", "grep"] = "ls"
    path: str = Field(default=".")
    pattern: str = Field(default="")

    model_config = ConfigDict(extra="ignore")


class FsSearchOutput(BaseModel):
    matches: Optional[list] = None
    count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class FsSearchNode(ActionNode):
    type = "fsSearch"
    display_name = "FS Search"
    subtitle = "ls/glob/grep"
    group = ("filesystem", "tool")
    description = "Search the filesystem (ls, glob, grep)"
    tool_name = "fs_search"
    tool_description = (
        "Search the filesystem. Modes: ls (list directory), glob (pattern match files), grep (search file contents for text)."
    )
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = FsSearchParams
    Output = FsSearchOutput

    @Operation("search")
    async def search(self, ctx: NodeContext, params: FsSearchParams) -> Any:
        """Inlined from handlers/filesystem.py (Wave 11.D.1)."""
        import asyncio
        from .._backend import get_backend, normalize_virtual_path

        backend = get_backend(params.model_dump(), ctx.raw)
        path = normalize_virtual_path(params.path)

        if params.mode == "ls":
            entries = await asyncio.to_thread(backend.ls_info, path)
            return {
                "path": path,
                "entries": [dict(e) for e in entries],
                "count": len(entries),
            }

        if params.mode == "glob":
            if not params.pattern:
                raise NodeUserError("pattern is required for glob mode")
            matches = await asyncio.to_thread(
                backend.glob_info,
                params.pattern,
                path=path,
            )
            return {
                "path": path,
                "pattern": params.pattern,
                "matches": [dict(m) for m in matches],
                "count": len(matches),
            }

        if params.mode == "grep":
            if not params.pattern:
                raise NodeUserError("pattern is required for grep mode")
            result = await asyncio.to_thread(
                backend.grep_raw,
                params.pattern,
                path=path,
            )
            if isinstance(result, str):
                raise NodeUserError(result)
            return {
                "path": path,
                "pattern": params.pattern,
                "matches": [dict(m) for m in result],
                "count": len(result),
            }

        raise NodeUserError(f"Unknown mode: {params.mode}")
