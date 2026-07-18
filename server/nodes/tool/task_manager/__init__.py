"""Durable, execution-scoped task control for agent team leads."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, ToolNode

TaskOperation = Literal[
    "assign_task", "list_tasks", "get_task", "modify_task", "cancel_task",
    "retry_task", "reassign_task", "accept_task", "finish_team", "mark_done",
]


class TaskManagerParams(BaseModel):
    operation: TaskOperation = "list_tasks"
    task_id: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=500)
    mission: Optional[str] = Field(default=None, max_length=10000)
    context: Optional[Dict[str, Any]] = None
    acceptance_criteria: Optional[Dict[str, Any]] = None
    depends_on: Optional[List[str]] = None
    assignee_node_id: Optional[str] = None
    delegate_name: Optional[str] = None
    expected_revision: Optional[int] = Field(default=None, ge=0)
    reason: Optional[str] = Field(default=None, max_length=2000)
    status_filter: Optional[str] = None
    include_history: bool = False
    model_config = ConfigDict(extra="allow")


class TaskManagerOutput(BaseModel):
    success: bool = True
    operation: Optional[str] = None
    task: Optional[dict] = None
    tasks: Optional[list] = None
    team: Optional[dict] = None
    model_config = ConfigDict(extra="allow")


class TaskManagerNode(ToolNode):
    type = "taskManager"
    display_name = "Task Manager"
    subtitle = "Durable Team Tasks"
    group = ("tool", "ai")
    description = "Assign, review, retry, reassign, cancel, and accept durable teammate tasks"
    component_kind = "tool"
    tool_name = "task_manager"
    tool_description = (
        "Manage the current lead execution's durable team tasks. Assign only connected "
        "teammates, inspect submitted work, then accept, retry, reassign, or cancel it. "
        "Use list_tasks with include_history=true to consult prior executions."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    ui_hints = {
        "isToolPanel": True,
        "isTaskManagerPanel": True,
        "hideInputSection": True,
        "hideOutputSection": True,
        "hideRunButton": True,
    }
    annotations = {"destructive": True, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    needs_canvas = True
    Params = TaskManagerParams
    Output = TaskManagerOutput

    @Operation("manage")
    async def manage(self, ctx: NodeContext, params: TaskManagerParams) -> Any:
        config = {
            **ctx.raw,
            "node_id": ctx.node_id,
            "workflow_id": ctx.workflow_id or ctx.raw.get("workflow_id"),
            "execution_id": ctx.execution_id or ctx.raw.get("execution_id"),
            "nodes": ctx.nodes,
            "edges": ctx.edges,
        }
        return await _execute_task_manager(params.model_dump(exclude_none=True), config)


def _scope(config: Dict[str, Any]) -> Dict[str, Any]:
    workflow_id = str(config.get("workflow_id") or "")
    lead_id = str(config.get("parent_node_id") or config.get("team_lead_node_id") or "")
    if not workflow_id or not lead_id:
        raise ValueError("Task Manager requires trusted workflow and team-lead execution context")
    return {"workflow_id": workflow_id, "team_lead_node_id": lead_id, "execution_id": config.get("execution_id")}


def _resolve_teammate(config: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    from services.plugin.edge_walker import build_teammate_descriptors

    lead_id = str(config.get("parent_node_id") or config.get("team_lead_node_id") or "")
    descriptors = build_teammate_descriptors(lead_id, config)
    node_id, delegate_name = args.get("assignee_node_id"), args.get("delegate_name")
    matches = [d for d in descriptors if (node_id and d["node_id"] == node_id) or (delegate_name and d["delegate_tool_name"] == delegate_name)]
    if len(matches) != 1:
        available = [{"node_id": d["node_id"], "delegate_name": d["delegate_tool_name"], "label": d["label"]} for d in descriptors]
        raise ValueError(f"Assignee must resolve to exactly one connected teammate; available={available}")
    return matches[0]


async def _execute_task_manager(args: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    from services.agent_team import get_agent_team_service

    service = get_agent_team_service()
    operation = str(args.get("operation") or "list_tasks")
    allowed_operations = {
        "assign_task", "list_tasks", "get_task", "modify_task", "cancel_task",
        "retry_task", "reassign_task", "accept_task", "finish_team", "mark_done",
    }
    if operation not in allowed_operations:
        raise ValueError(f"Unknown Task Manager operation: {operation}")
    scope = _scope(config)

    if operation == "list_tasks":
        if args.get("include_history"):
            tasks = await service.list_durable_task_history(
                workflow_id=scope["workflow_id"],
                team_lead_node_id=scope["team_lead_node_id"],
                status=args.get("status_filter"),
            )
        else:
            tasks = await service.list_durable_tasks(**scope, status=args.get("status_filter"))
        return {"success": True, "operation": operation, "tasks": tasks, "count": len(tasks)}
    if operation == "get_task":
        task_id = str(args.get("task_id") or "")
        if not task_id:
            raise ValueError("get_task requires task_id")
        task = await service.get_durable_task(**scope, task_id=task_id)
        return {"success": True, "operation": operation, "task": task}
    if operation == "assign_task":
        if not args.get("title") or not args.get("mission"):
            raise ValueError("assign_task requires title and mission")
        teammate = _resolve_teammate(config, args)
        task = await service.assign_durable_task(
            **scope, assignee_node_id=teammate["node_id"], title=args["title"],
            mission=args["mission"], context=args.get("context"),
            acceptance_criteria=args.get("acceptance_criteria"), depends_on=args.get("depends_on"),
            trace_id=config.get("tool_call_id"),
        )
        delegation = None
        if config.get("ai_service") and config.get("database"):
            from services.handlers.tools import _execute_delegated_agent

            child_config = {
                **config,
                "node_id": teammate["node_id"],
                "node_type": teammate["node_type"],
                "team_id": task["team_id"],
            }
            delegation = await _execute_delegated_agent(
                {"task": args["mission"], "context": args.get("context") or {}},
                child_config,
                precreated_task_id=task["id"],
            )
        # Temporal AgentWorkflow consumes this trusted scheduling envelope and
        # starts the matching child through its existing bounded child-workflow
        # machinery. Legacy execution may consume it through the shared
        # delegation coordinator without creating a second TeamTask.
        return {
            "success": True, "operation": operation, "task": task,
            "delegation": delegation,
            "delegation_request": {
                "team_task_id": task["id"], "assignee_node_id": teammate["node_id"],
                "delegate_name": teammate["delegate_tool_name"], "task": args["mission"],
                "context": args.get("context") or {},
            },
        }
    if operation == "finish_team":
        team = await service.finish_durable_team(**scope)
        return {"success": True, "operation": operation, "team": team}

    task_id = str(args.get("task_id") or "")
    revision = args.get("expected_revision", args.get("revision"))
    # Completion review commonly follows immediately after one child result.
    # Resolve that unambiguous submitted task inside the trusted execution
    # scope instead of leaving it permanently submitted because the model
    # omitted identifiers. Never guess when multiple reviews are pending.
    if not task_id and operation in {"accept_task", "mark_done"}:
        submitted = await service.list_durable_tasks(**scope, status="submitted")
        if len(submitted) == 1:
            task_id = submitted[0]["id"]
            revision = submitted[0]["revision"]
        else:
            raise ValueError(
                "accept_task requires task_id when there is not exactly one submitted task; "
                "call list_tasks or get_task first"
            )
    if not task_id:
        raise ValueError(f"{operation} requires task_id")
    if revision is None:
        current = await service.get_durable_task(**scope, task_id=task_id)
        revision = current.get("revision")
    if revision is None:
        raise ValueError(f"{operation} requires expected_revision")
    mapped = "accept" if operation in {"accept_task", "mark_done"} else operation.removesuffix("_task")
    payload = {k: args[k] for k in ("title", "mission", "context", "acceptance_criteria", "reason") if k in args}
    if operation == "reassign_task":
        payload["assignee_node_id"] = _resolve_teammate(config, args)["node_id"]
    task = await service.mutate_durable_task(
        **scope, task_id=task_id, revision=int(revision), operation=mapped, **payload,
    )
    return {"success": True, "operation": operation, "task": task}
