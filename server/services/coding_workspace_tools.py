"""Virtual Coding Agent tools built and executed through existing plugins."""

from __future__ import annotations

from typing import Any

from services.agent_bindings import node_tools

WORKSPACE_TOOL_TYPES = ("fileRead", "fileModify", "fsSearch", "shell")
WORKSPACE_ACTIVITY = "coding.workspace_tool"


def workspace_binding_id(agent_node_id: str, node_type: str) -> str:
    return f"coding-workspace:{agent_node_id}:{node_type}"


def validate_tool_names(tools: list[dict]) -> None:
    """Never let the provider or SDK choose between ambiguous bindings."""
    seen: set[str] = set()
    for tool in tools:
        name = str(tool.get("name") or "")
        if not name or name in seen:
            from temporalio.exceptions import ApplicationError
            raise ApplicationError(
                f"Coding Agent tool name is empty or duplicated: {name!r}. Rename or disconnect the conflicting tool.",
                type="CodingToolNameConflict", non_retryable=True,
            )
        seen.add(name)


async def build_workspace_tools(ai_service: Any, agent_node_id: str, connected: list[dict], *, definition, policy, connected_types=()) -> list[dict]:
    from services.node_registry import get_node_class

    present = {tool.get("node_type") for tool in connected} | set(connected_types)
    result = []
    for node_type in WORKSPACE_TOOL_TYPES:
        if node_type in present:
            continue
        cls = get_node_class(node_type)
        if cls is None:
            raise RuntimeError(f"Workspace coding plugin {node_type!r} is not installed")
        # No physical node id: automatic tools have no editable schema/config
        # row and must not consult a coincidentally matching database identity.
        info = {"node_id": "", "node_type": node_type, "parameters": {}, "label": cls.display_name}
        built = await node_tools(ai_service, info)
        if len(built) != 1:
            raise RuntimeError(f"Workspace coding plugin {node_type!r} must provide exactly one tool")
        tool, config = built[0]
        wire = definition(tool)
        if node_type == "shell":
            wire["description"] += (
                " Runs from the managed workflow workspace using Nushell when installed. "
                "Use ';' for sequential commands and Nushell 'try { ... } catch { ... }'; "
                "spaced && and || are rejected. Commands are limited by timeout; "
                "this is host shell execution, not an operating-system sandbox."
            )
        identifier = workspace_binding_id(agent_node_id, node_type)
        result.append({
            "name": tool.name, "definition": wire, "node_type": node_type,
            "version": cls.version, "task_queue": cls.task_queue,
            "tool_node_id": identifier, "binding_key": identifier,
            "parameters": {}, "tool_info": info, "virtual_workspace": True,
            "activity_name": WORKSPACE_ACTIVITY, "activity_policy": policy(cls),
            "needs_canvas": False, "llm_hidden": False,
        })
    validate_tool_names([*connected, *result])
    return result


async def execute_workspace_tool(payload: dict) -> dict:
    """Execute a trusted virtual binding without node-output/status writes."""
    from core.container import container
    from services.node_registry import get_node_class
    from services.plugin import NodeContext, NodeUserError
    from services.chat import steps
    from services.approvals import tool_calls as approvals
    from services.employees.permissions import assert_runtime_access
    from services.status_broadcaster import get_status_broadcaster

    node_type = payload.get("node_type")
    parent_id = str(payload.get("parent_node_id") or "")
    expected_id = workspace_binding_id(parent_id, str(node_type or ""))
    if (node_type not in WORKSPACE_TOOL_TYPES or not parent_id
            or payload.get("pi_harness_version") != 1 or payload.get("node_id") != expected_id):
        from temporalio.exceptions import ApplicationError
        raise ApplicationError("Invalid automatic workspace binding", type="CodingWorkspaceScopeMismatch", non_retryable=True)
    args = payload.get("tool_args")
    if not isinstance(args, dict):
        return {"error": "Tool arguments must be an object", "error_type": "ValidationError"}
    cls = get_node_class(node_type)
    if cls is None:
        raise RuntimeError(f"Workspace plugin {node_type!r} is not installed")
    context = {**payload, "node_data": dict(args)}
    # Workspace selection belongs to the server. The model's cwd/workspace
    # fields never choose the execution root (the filesystem backend ignores
    # ShellParams.cwd and resolves from this context).
    service = container.workflow_service()
    context["workspace_dir"] = service._get_workspace_dir(payload.get("workflow_slug"))
    try:
        await assert_runtime_access(container.database(), str(payload.get("workflow_id") or ""), expected_id, node_type, context)
    except NodeUserError as exc:
        return {"error": str(exc), **exc.as_dict()}
    if await steps.run_stopped(context):
        return dict(steps.STOPPED_RESULT)
    checked = await approvals.check(context, cls)
    if checked.result is not None:
        held = checked.result
        return held.get("result", held)
    if checked.tool_args is not None:
        args = checked.tool_args
    refusal = await approvals.redo_refusal(context, cls, context["node_data"])
    if refusal is not None:
        await approvals.record_unknown(context, cls, context["node_data"], refusal, checked=checked)
        return refusal.get("result", refusal)
    step = steps.begin(context, cls)
    broadcaster = get_status_broadcaster()

    async def capability(state: str, error: str | None = None) -> None:
        try:
            await broadcaster.broadcast_agent_capability(
                parent_id, capability_kind="tool", capability_name=str(cls.tool_name), state=state,
                workflow_id=payload.get("workflow_id"), execution_id=payload.get("execution_id"),
                root_execution_id=payload.get("root_execution_id"), invocation_source="temporal",
                tool_call_id=payload.get("tool_call_id"), error_code=error,
            )
        except Exception:
            # Courtesy telemetry cannot repeat a completed filesystem effect.
            pass

    await capability("started")
    try:
        result = await cls().execute_as_tool(args, {}, NodeContext.from_legacy(parent_id, node_type, context))
        detail = steps.take_detail(result)
        failed = bool(result.get("error"))
        if checked.record is not None:
            await checked.record(result)
        await steps.end(step, state="failed" if failed else "done", detail=detail)
        await capability("failed" if failed else "completed", result.get("error_type") if failed else None)
        return result
    except BaseException:
        await steps.end(step, state="failed")
        await capability("failed", "WorkspaceToolInterrupted")
        raise
