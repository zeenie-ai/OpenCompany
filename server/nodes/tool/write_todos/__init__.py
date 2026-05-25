"""Write Todos Tool — Wave 11.C migration."""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, ToolNode


class TodoItem(BaseModel):
    id: Optional[str] = None
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


class WriteTodosParams(BaseModel):
    todos: List[TodoItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class WriteTodosOutput(BaseModel):
    todos: Optional[list] = None
    summary: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class WriteTodosNode(ToolNode):
    type = "writeTodos"
    display_name = "Write Todos"
    subtitle = "Plan-Work-Update Loop"
    group = ("tool", "ai")
    description = "Structured task list planning for complex multi-step operations"
    component_kind = "tool"
    tool_name = "write_todos"
    tool_description = (
        "Create and manage a structured task list for your current work session. "
        "This helps you track progress, organize complex tasks, and demonstrate "
        "thoroughness to the user. Only use this tool if you think it will be "
        "helpful in staying organized. If the user's request is trivial and takes "
        "less than 3 steps, it is better to NOT use this tool and just do the task "
        "directly. Use for complex multi-step tasks (3+ steps), non-trivial planning, "
        "or when user explicitly requests a todo list. Task states: pending, "
        "in_progress, completed. Mark tasks as in_progress BEFORE beginning work, "
        "completed IMMEDIATELY after finishing. Remove irrelevant tasks. Break "
        "complex tasks into smaller steps."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top", "label": "Tool", "role": "tools"},
    )
    ui_hints = {"isToolPanel": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": False, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = WriteTodosParams
    Output = WriteTodosOutput

    @Operation("write")
    async def write(self, ctx: NodeContext, params: WriteTodosParams) -> Any:
        """Inlined from handlers/todo.py (Wave 11.D.1)."""
        from core.logging import get_logger
        from services.todo_service import get_todo_service

        session_key = ctx.workflow_id or ctx.node_id or "default"
        service = get_todo_service()
        stored = service.write(session_key, [t.model_dump() for t in params.todos])

        # Real-time UI broadcast (optional; broadcaster lives on ctx.raw).
        broadcaster = ctx.raw.get("broadcaster")
        if broadcaster:
            await broadcaster.update_node_status(
                ctx.node_id,
                "executing",
                {"phase": "todo_update", "todos": stored},
                workflow_id=ctx.workflow_id,
            )

        get_logger(__name__).info(
            "[WriteTodos] Updated %d todos (session=%s)",
            len(stored),
            session_key,
        )
        return {
            "message": f"Updated todo list ({len(stored)} items)",
            "todos": service.format_for_llm(session_key),
            "count": len(stored),
        }
