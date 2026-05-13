"""Google Tasks — Wave 11.D.4 inlined."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import GoogleCredential

from .._base import build_google_service, run_sync, track_google_usage


def _ensure_rfc3339(due: str) -> str:
    return due if 'T' in due else f"{due}T00:00:00.000Z"


_CREATE = {"displayOptions": {"show": {"operation": ["create"]}}}
_LIST = {"displayOptions": {"show": {"operation": ["list"]}}}
_UPDATE = {"displayOptions": {"show": {"operation": ["update"]}}}
_COMPLETE_UPDATE_DELETE = {"displayOptions": {"show": {"operation": ["complete", "update", "delete"]}}}


class TasksParams(BaseModel):
    operation: Literal["create", "list", "complete", "update", "delete"] = "list"
    tasklist_id: str = Field(
        default="@default",
        json_schema_extra={"loadOptionsMethod": "googleTasklists"},
    )
    task_id: Optional[str] = Field(default=None, json_schema_extra=_COMPLETE_UPDATE_DELETE)

    # Create fields
    title: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    notes: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    due_date: Optional[str] = Field(default=None, json_schema_extra=_CREATE)
    status: Optional[str] = Field(default=None, json_schema_extra=_CREATE)

    # List fields
    show_completed: bool = Field(default=False, json_schema_extra=_LIST)
    show_hidden: bool = Field(default=False, json_schema_extra=_LIST)
    max_results: int = Field(default=100, json_schema_extra=_LIST)

    # Update-only fields
    update_title: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_notes: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_due_date: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)
    update_status: Optional[str] = Field(default=None, json_schema_extra=_UPDATE)

    model_config = ConfigDict(extra="ignore")


class TasksOutput(BaseModel):
    operation: Optional[str] = None
    task_id: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None
    completed: Optional[str] = None
    self_link: Optional[str] = None
    tasks: Optional[List[dict]] = None
    count: Optional[int] = None
    deleted: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class TasksNode(ActionNode):
    type = "googleTasks"
    display_name = "Tasks"
    subtitle = "Task Management"
    group = ("google", "tool")
    description = "Google Tasks create / list / complete / update / delete"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (GoogleCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = TasksParams
    Output = TasksOutput

    @Operation("dispatch", cost={"service": "tasks", "action": "op", "count": 1})
    async def dispatch(self, ctx: NodeContext, params: TasksParams) -> TasksOutput:
        svc = await build_google_service("tasks", "v1", params.model_dump(), ctx.raw)
        tasks_svc = svc.tasks()
        op = params.operation
        tasklist = params.tasklist_id

        if op == "create":
            if not params.title:
                raise RuntimeError("Task title is required")
            body = {'title': params.title}
            if params.notes:
                body['notes'] = params.notes
            if params.due_date:
                body['due'] = _ensure_rfc3339(params.due_date)
            result = await run_sync(lambda: tasks_svc.insert(tasklist=tasklist, body=body).execute())
            await track_google_usage("google_tasks", ctx.node_id, "create", 1, ctx.raw)
            return TasksOutput(
                operation="create",
                task_id=result.get('id'),
                title=result.get('title'),
                notes=result.get('notes'),
                due=result.get('due'),
                status=result.get('status'),
                self_link=result.get('selfLink'),
            )

        if op == "list":
            result = await run_sync(lambda: tasks_svc.list(
                tasklist=tasklist,
                showCompleted=params.show_completed,
                showHidden=params.show_hidden,
                maxResults=params.max_results,
            ).execute())
            items = result.get('items', [])
            formatted = [{
                'task_id': t.get('id'), 'title': t.get('title'), 'notes': t.get('notes'),
                'due': t.get('due'), 'status': t.get('status'),
                'completed': t.get('completed'), 'position': t.get('position'),
            } for t in items]
            await track_google_usage("google_tasks", ctx.node_id, "list", len(formatted), ctx.raw)
            return TasksOutput(operation="list", tasks=formatted, count=len(formatted))

        if op in ("complete", "update", "delete"):
            if not params.task_id:
                raise RuntimeError("Task ID is required")

            if op == "delete":
                await run_sync(lambda: tasks_svc.delete(tasklist=tasklist, task=params.task_id).execute())
                await track_google_usage("google_tasks", ctx.node_id, "delete", 1, ctx.raw)
                return TasksOutput(operation="delete", deleted=True, task_id=params.task_id)

            current = await run_sync(lambda: tasks_svc.get(tasklist=tasklist, task=params.task_id).execute())

            if op == "complete":
                current['status'] = 'completed'
            else:
                title = params.update_title or params.title
                notes = params.update_notes or params.notes
                due = params.update_due_date or params.due_date
                status = params.update_status or params.status
                if title:
                    current['title'] = title
                if notes:
                    current['notes'] = notes
                if due:
                    current['due'] = _ensure_rfc3339(due)
                if status:
                    current['status'] = status

            result = await run_sync(lambda: tasks_svc.update(
                tasklist=tasklist, task=params.task_id, body=current,
            ).execute())
            await track_google_usage("google_tasks", ctx.node_id, op, 1, ctx.raw)
            return TasksOutput(
                operation=op,
                task_id=result.get('id'),
                title=result.get('title'),
                notes=result.get('notes'),
                due=result.get('due'),
                status=result.get('status'),
                completed=result.get('completed'),
            )

        raise RuntimeError(f"Unknown Tasks operation: {op}")
