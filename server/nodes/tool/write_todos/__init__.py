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
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top",
         "label": "Tool", "role": "tools"},
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
                ctx.node_id, "executing",
                {"phase": "todo_update", "todos": stored},
                workflow_id=ctx.workflow_id,
            )

        get_logger(__name__).info(
            "[WriteTodos] Updated %d todos (session=%s)", len(stored), session_key,
        )
        return {
            "message": f"Updated todo list ({len(stored)} items)",
            "todos": service.format_for_llm(session_key),
            "count": len(stored),
        }
