"""Process Manager — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


_START = {"displayOptions": {"show": {"operation": ["start"]}}}
_GET_OUTPUT = {"displayOptions": {"show": {"operation": ["get_output"]}}}
_SEND_INPUT = {"displayOptions": {"show": {"operation": ["send_input"]}}}


class ProcessManagerParams(BaseModel):
    operation: Literal["start", "stop", "restart", "list", "send_input", "get_output"] = "list"
    name: str = Field(default="")
    command: str = Field(default="", json_schema_extra=_START)
    cwd: str = Field(default="", json_schema_extra=_START)
    env: Dict[str, str] = Field(default_factory=dict, json_schema_extra=_START)
    input_text: str = Field(default="", json_schema_extra=_SEND_INPUT)
    stream: Literal["stdout", "stderr"] = Field(default="stdout", json_schema_extra=_GET_OUTPUT)
    tail: int = Field(default=100, ge=1, le=10000, json_schema_extra=_GET_OUTPUT)

    model_config = ConfigDict(extra="ignore")


class ProcessManagerOutput(BaseModel):
    operation: Optional[str] = None
    pid: Optional[int] = None
    status: Optional[str] = None
    output: Optional[str] = None
    processes: Optional[list] = None

    model_config = ConfigDict(extra="allow")


class ProcessManagerNode(ActionNode):
    type = "processManager"
    display_name = "Process Manager"
    subtitle = "Long-Running Subprocess"
    group = ("utility", "tool")
    description = "Start, stop, restart, and manage long-running processes"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = ProcessManagerParams
    Output = ProcessManagerOutput

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: ProcessManagerParams) -> Any:
        """Inlined from handlers/process.py (Wave 11.D.1)."""
        import os
        from services.process_service import get_process_service

        svc = get_process_service()
        workflow_id = ctx.workflow_id or "default"
        workspace_dir = ctx.workspace_dir or ""
        # Each agent node gets its own subfolder in the workspace.
        agent_dir = os.path.join(workspace_dir, ctx.node_id) if workspace_dir else ""

        op = params.operation
        name = _clean(params.name)

        def _unwrap(response: Any) -> Any:
            """Raise on service-level failure envelopes so the plugin
            wrapper returns success=False instead of burying a
            ``{"success": False, "error": "..."}`` inside the result dict.

            ``NodeUserError`` (vs ``RuntimeError``) so the framework logs
            a single WARN line — these are user/LLM-correctable
            (process-not-found, command-not-found, bad cwd, ...), not
            server bugs warranting a stack trace.
            """
            if isinstance(response, dict) and response.get("success") is False:
                raise NodeUserError(response.get("error") or "process operation failed")
            return response

        if op == "start":
            return _unwrap(await svc.start(
                name=name,
                command=_clean(params.command),
                workflow_id=workflow_id,
                working_directory=_clean(params.cwd) or agent_dir,
            ))
        if op == "stop":
            return _unwrap(await svc.stop(name, workflow_id))
        if op == "restart":
            return _unwrap(await svc.restart(name, workflow_id))
        if op == "send_input":
            return _unwrap(await svc.send_input(name, workflow_id, _clean(params.input_text)))
        if op == "list":
            return {"processes": svc.list_processes(workflow_id)}
        if op == "get_output":
            return _unwrap(svc.get_output(name, workflow_id, params.stream, params.tail, 0))
        raise NodeUserError(f"Unknown operation: {op}")


def _clean(val: str) -> str:
    """LLMs sometimes pass literal 'None' string instead of omitting the field."""
    if not val or val == "None":
        return ""
    return val
