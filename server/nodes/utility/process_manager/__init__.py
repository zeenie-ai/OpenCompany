"""Process Manager — Wave 11.C migration."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.paths import safe_path_component
from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue


class ProcessOperationError(NodeUserError):
    def __init__(self, response: dict, scopes: dict, facts: dict):
        super().__init__(response.get("error") or "process operation failed")
        self.details = {key: response[key] for key in ("code", "ports", "owners") if key in response}
        if scopes:
            self.details["owner_scope"] = scopes
        self.details["runtime_context"] = facts

    def as_dict(self):
        return {**super().as_dict(), **self.details}


_START = {"displayOptions": {"show": {"operation": ["start"]}}}
_GET_OUTPUT = {"displayOptions": {"show": {"operation": ["get_output"]}}}
_SEND_INPUT = {"displayOptions": {"show": {"operation": ["send_input"]}}}


class ProcessManagerParams(BaseModel):
    operation: Literal["start", "stop", "restart", "list", "send_input", "get_output"] = "list"
    name: str = Field(default="")
    command: str = Field(default="", json_schema_extra=_START)
    cwd: str = Field(default="", json_schema_extra=_START)
    env: Dict[str, str] = Field(default_factory=dict, json_schema_extra=_START)
    ports: List[int] = Field(
        default_factory=list,
        json_schema_extra={**_START, "description": "Listener ports to reserve before spawning"},
    )
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
    tool_name = "process_manager"
    tool_description = "Start, stop, and manage long-running processes (dev servers, watchers, build tools). For servers, always declare listener ports in ports; startup fails safely with PORT_IN_USE instead of launching on an occupied port. Operations: start, stop, restart, list, send_input, get_output. Output streams to Terminal tab."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": True, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True
    ui_hints = {
        "isProcessManagerPanel": True,
        "hideInputSection": True,
        "hideOutputSection": True,
    }

    Params = ProcessManagerParams
    Output = ProcessManagerOutput
    tool_error_fields = frozenset({"code", "ports", "owners", "owner_scope", "runtime_context"})

    @Operation("dispatch")
    async def dispatch(self, ctx: NodeContext, params: ProcessManagerParams) -> Any:
        """Inlined from handlers/process.py (Wave 11.D.1)."""
        from services.process_service import get_process_service

        svc = get_process_service()
        workflow_id = ctx.workflow_id or "default"
        workspace_dir = ctx.workspace_dir or ""
        # Each agent node gets its own subfolder in the workspace. Sanitized:
        # canvas ids can be colon-namespaced ("2:processManager:4") and
        # ntpath.join parses "2:" as a drive, discarding the workspace prefix,
        # so the raw id failed the ProcessService containment guardrail.
        agent_dir = os.path.join(workspace_dir, safe_path_component(ctx.node_id, "node")) if workspace_dir else ""
        v1 = ctx.raw.get("runtime_behavior_version") == 1
        if params.operation == "start":
            # Every agent resolves a relative cwd against the workflow
            # workspace. The older rule resolved it against the backend's
            # own folder, which the ProcessService guardrail always refused.
            from services.runtime_tool_context import process_cwd
            try:
                agent_dir = process_cwd(workspace_dir, ctx.node_id, _clean(params.cwd), version=1)
            except ValueError as exc:
                raise NodeUserError(str(exc)) from None

        op = params.operation
        name = _clean(params.name)
        facts = {}
        if v1:
            from services.runtime_tool_context import runtime_facts
            facts = runtime_facts(workspace_dir, ctx.node_id, _clean(params.command))
            if op == "start":
                facts["effective_cwd"] = agent_dir

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
                if v1:
                    scopes = svc.classify_port_owners(response.get("owners") or {}, workflow_id)
                    raise ProcessOperationError(response, scopes, facts)
                raise NodeUserError(response.get("error") or "process operation failed")
            if v1 and isinstance(response, dict):
                response = {**response, "runtime_context": facts}
            return response

        if op == "start":
            return _unwrap(
                await svc.start(
                    name=name,
                    command=_clean(params.command),
                    workflow_id=workflow_id,
                    working_directory=agent_dir,
                    ports=params.ports,
                    extra_env=params.env,
                )
            )
        if op == "stop":
            return _unwrap(await svc.stop(name, workflow_id))
        if op == "restart":
            return _unwrap(await svc.restart(name, workflow_id))
        if op == "send_input":
            return _unwrap(await svc.send_input(name, workflow_id, _clean(params.input_text)))
        if op == "list":
            return {"processes": svc.list_processes(workflow_id), **({"runtime_context": facts} if v1 else {})}
        if op == "get_output":
            return _unwrap(svc.get_output(name, workflow_id, params.stream, params.tail, 0))
        raise NodeUserError(f"Unknown operation: {op}")


def _clean(val: str) -> str:
    """LLMs sometimes pass literal 'None' string instead of omitting the field."""
    if not val or val == "None":
        return ""
    return val
