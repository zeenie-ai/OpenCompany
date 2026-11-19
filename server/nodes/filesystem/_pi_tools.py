"""Shared adapter for Pi SDK tools executed as ordinary workflow leaves."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class PiParams(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PiShellParams(PiParams):
    command: str = Field(..., min_length=1, description="Command in this node's shell syntax.")
    timeout: int = Field(default=30, ge=1, le=600, description="Maximum command duration in seconds.")


class PiToolOutput(BaseModel):
    content: list[dict[str, Any]] = Field(default_factory=list)
    details: Any = None
    structuredContent: Any = None
    isError: bool = False
    stdout: str | None = None
    exit_code: int | None = None
    truncated: bool | None = None
    llm_media: list[dict[str, Any]] | None = None

    model_config = ConfigDict(extra="allow")


class PiToolFailure(NodeUserError):
    """Retain the SDK's public output when a command exits unsuccessfully."""

    def __init__(self, result: dict[str, Any]) -> None:
        message = "\n".join(str(item.get("text", "")) for item in result.get("content", []) if item.get("type") == "text")
        super().__init__(message or "Pi tool execution failed")
        self.result = result

    def as_dict(self) -> dict[str, Any]:
        return {**self.result, **super().as_dict(), "error_type": "PiToolError"}


def _persist_images(result: dict[str, Any], ctx: NodeContext) -> dict[str, Any]:
    """Store image bytes once; only durable media references reach history."""
    from services.media.workspace import write_media

    extensions = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
    output = dict(result)
    content = []
    media = []
    for block in result.get("content", []):
        if block.get("type") != "image":
            content.append(block)
            continue
        mime = block.get("mimeType")
        if mime not in extensions:
            raise NodeUserError("Pi returned an unsupported image format")
        try:
            data = base64.b64decode(block["data"], validate=True)
        except (ValueError, KeyError, TypeError) as exc:
            raise NodeUserError("Pi returned invalid image data") from exc
        ref = write_media(data, ctx=ctx, stem="pi-read", ext=extensions[mime], kind="image", mime_type=mime).model_dump(mode="json")
        media.append({"ref": ref, "detail": "auto"})
        content.append({"type": "text", "text": f"Image: {ref['path']}"})
    output["content"] = content
    if media:
        output["llm_media"] = media
        structured = output.get("structuredContent")
        if isinstance(structured, dict) and structured.get("type") == "image":
            output["structuredContent"] = {"type": "image", "ref": media[0]["ref"], "note": structured.get("note", "")}
    return output


class PiToolNode(ActionNode, abstract=True):
    sdk_tool: ClassVar[str] = ""
    group = ("filesystem", "tool")
    component_kind = "square"
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    Output = PiToolOutput
    tool_error_fields = frozenset({"content", "details", "structuredContent", "isError", "stdout", "exit_code", "truncated", "llm_media"})

    @Operation("execute")
    async def execute_op(self, ctx: NodeContext, params: BaseModel) -> Any:
        from services.media.workspace import workspace_root
        from services.pi_builtin_tools import execute_pi_tool
        from ._backend import get_path_lock, resolve_within

        root = workspace_root(ctx).resolve()
        args = params.model_dump(exclude_none=True)
        path = None
        if self.sdk_tool not in {"bash", "powershell"}:
            try:
                path = resolve_within(root, args.get("path", "."))
            except (OSError, ValueError) as exc:
                raise NodeUserError(str(exc)) from exc
            # The SDK accepts absolute paths. Only this trusted, contained path
            # crosses the bridge; the host also validates each filesystem IO.
            args["path"] = str(path)

        async def run() -> dict[str, Any]:
            return await execute_pi_tool(self.sdk_tool, args, root, timeout=args.get("timeout", 30))

        if self.sdk_tool in {"write", "edit"}:
            # Share the legacy filesystem lock so parallel connected tools do
            # not overwrite each other's read/modify/write on the same worker.
            async with get_path_lock(path):
                result = await run()
        else:
            result = await run()
        if any(block.get("type") == "image" for block in result.get("content", [])):
            result = await asyncio.to_thread(_persist_images, result, ctx)
        result = dict(result)
        structured = result.get("structuredContent")
        if self.sdk_tool in {"bash", "powershell"} and isinstance(structured, dict):
            # Pi's structured output can be 1 MiB. Duplicating it in stdout
            # exceeds Temporal's 2 MiB limit; the terminal uses Pi's short tail.
            tail = "\n".join(str(block.get("text", "")) for block in result.get("content", []) if block.get("type") == "text")
            truncation = (result.get("details") or {}).get("truncation") or {}
            result.update(stdout=tail, exit_code=structured.get("exit_code"),
                          truncated=bool(structured.get("truncated") or truncation.get("truncated")))
        if result.get("isError"):
            self._check_result_size(result)
            raise PiToolFailure(result)
        return result
