"""Task Manager Tool — Wave 11.C migration; body inlined Wave 11.I (M.O).

Inspects the in-memory delegation registry that lives on
``services.handlers.tools`` (``_delegated_tasks`` for in-flight asyncio
tasks, ``_delegation_results`` for completed-but-still-cached results,
plus ``get_delegated_task_status`` which adds a DB-fallback layer for
older runs). The plugin doesn't own that state — the delegation
lifecycle in ``tools.py`` does — so we read through to it rather than
duplicating registry plumbing here.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import NodeContext, Operation, TaskQueue, ToolNode

logger = get_logger(__name__)


class TaskManagerParams(BaseModel):
    action: Literal["create", "list", "complete", "delete", "update"] = "create"
    task_id: Optional[str] = Field(default=None)
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class TaskManagerOutput(BaseModel):
    task_id: Optional[str] = None
    tasks: Optional[list] = None
    success: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class TaskManagerNode(ToolNode):
    type = "taskManager"
    display_name = "Task Manager"
    subtitle = "AI Task Tracking"
    group = ("tool", "ai")
    description = "Task management tool for AI agents to create, track, and manage tasks"
    component_kind = "tool"
    tool_name = "task_manager"
    tool_description = "Track delegated sub-agent tasks. Operations: list_tasks (see all tasks), get_task (check specific task status/result), mark_done (cleanup completed tasks)."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    ui_hints = {"isToolPanel": True, "hideRunButton": True}
    annotations = {"destructive": True, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = TaskManagerParams
    Output = TaskManagerOutput

    @Operation("manage")
    async def manage(self, ctx: NodeContext, params: TaskManagerParams) -> Any:
        return await _execute_task_manager(
            params.model_dump(),
            {"node_id": ctx.node_id, "workspace_dir": ctx.workspace_dir or ""},
        )


async def _execute_task_manager(
    tool_args: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute task manager operations.

    Dual-purpose: works as AI tool (LLM fills args) or workflow node
    (uses params).

    Operations:
    - ``list_tasks``: list all active and completed delegated tasks
    - ``get_task``: detailed status for one task
    - ``mark_done``: drop a task from active tracking
    """
    # Read-through to the delegation registry owned by tools.py. Keeping
    # the singletons there is intentional -- the entire delegation
    # lifecycle (spawn / cleanup / refcount / DB persistence) lives in
    # tools.py and is genuine cross-cutting framework state.
    from services.handlers.tools import (
        _delegated_tasks,
        _delegation_results,
        get_delegated_task_status,
    )

    params = config.get("parameters", {})
    operation = tool_args.get("operation") or params.get("operation", "list_tasks")
    task_id = tool_args.get("task_id") or params.get("task_id")
    status_filter = tool_args.get("status_filter") or params.get("status_filter")
    database = config.get("database")

    logger.debug(f"[TaskManager] Operation: {operation}, task_id: {task_id}, filter: {status_filter}")

    if operation == "list_tasks":
        tasks = []

        # Active tasks from asyncio.Task tracking.
        for tid, task in _delegated_tasks.items():
            if task.done():
                try:
                    if task.cancelled():
                        status = "cancelled"
                    elif task.exception():
                        status = "error"
                    else:
                        status = "completed"
                except Exception:
                    status = "completed"
            else:
                status = "running"

            tasks.append({"task_id": tid, "status": status, "active": True})

        # Completed tasks from in-memory cache.
        for tid, result in _delegation_results.items():
            if tid not in [t["task_id"] for t in tasks]:
                tasks.append(
                    {
                        "task_id": tid,
                        "status": result.get("status", "completed"),
                        "agent_name": result.get("agent_name"),
                        "result_summary": str(result.get("result", ""))[:200],
                        "active": False,
                    }
                )

        if status_filter:
            tasks = [t for t in tasks if t.get("status") == status_filter]

        return {
            "success": True,
            "operation": "list_tasks",
            "tasks": tasks,
            "count": len(tasks),
            "running": sum(1 for t in tasks if t.get("status") == "running"),
            "completed": sum(1 for t in tasks if t.get("status") == "completed"),
            "errors": sum(1 for t in tasks if t.get("status") == "error"),
        }

    if operation == "get_task":
        if not task_id:
            return {"success": False, "error": "task_id is required for get_task operation"}

        # 3-layer lookup (live tasks -> memory cache -> DB).
        result = await get_delegated_task_status(task_ids=[task_id], database=database)
        tasks = result.get("tasks", [])

        if not tasks:
            return {
                "success": False,
                "error": f"Task {task_id} not found",
                "task_id": task_id,
            }

        task_info = tasks[0]
        return {
            "success": True,
            "operation": "get_task",
            "task_id": task_id,
            "status": task_info.get("status"),
            "agent_name": task_info.get("agent_name"),
            "result": task_info.get("result"),
            "error": task_info.get("error"),
        }

    if operation == "mark_done":
        if not task_id:
            return {"success": False, "error": "task_id is required for mark_done operation"}

        removed = False
        if task_id in _delegated_tasks:
            del _delegated_tasks[task_id]
            removed = True
        if task_id in _delegation_results:
            del _delegation_results[task_id]
            removed = True

        return {
            "success": True,
            "operation": "mark_done",
            "task_id": task_id,
            "removed": removed,
            "message": (
                f"Task {task_id} marked as done and removed from tracking" if removed else f"Task {task_id} was not in active tracking"
            ),
        }

    return {"success": False, "error": f"Unknown operation: {operation}"}
