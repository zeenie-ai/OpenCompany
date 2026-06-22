"""Write Todos Tool — Wave 11.C migration."""

from __future__ import annotations

import json
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.plugin import NodeContext, Operation, TaskQueue, ToolNode


class TodoItem(BaseModel):
    id: Optional[str] = None
    content: str
    status: Literal["pending", "in_progress", "completed"] = "pending"


class WriteTodosParams(BaseModel):
    todos: List[TodoItem] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")

    @field_validator("todos", mode="before")
    @classmethod
    def _coerce_todos(cls, value: Any) -> Any:
        """Accept a JSON-encoded string in the ``todos`` slot — LLM
        providers (notably Gemini) sometimes stringify array arguments
        in tool calls. Same boundary-coercion pattern as
        ``AndroidServiceParams._coerce_parameters``. Malformed JSON
        passes through unchanged so Pydantic raises a proper
        ValidationError the LLM can correct."""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                try:
                    parsed = json.loads(stripped)
                except (ValueError, TypeError):
                    return value
                if isinstance(parsed, list):
                    return parsed
        return value


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
    ui_hints = {"isToolPanel": True, "hideRunButton": True, "isTodoEditor": True}
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

        # Typed todos_updated CloudEvent via the centralized dispatcher so an
        # open Current Todos panel refreshes live during the run. (The
        # node_status broadcast above drives canvas glow; this drives the
        # panel's ['todos', session_key] query — same wire frame the panel's
        # manual set_todos edits emit.)
        from ._events import dispatch_todos_updated

        await dispatch_todos_updated(
            session_key=session_key,
            todos=stored,
            node_id=ctx.node_id,
            workflow_id=ctx.workflow_id,
        )

        get_logger(__name__).info(
            "[WriteTodos] Updated %d todos (session=%s)",
            len(stored),
            session_key,
        )
        # ``stored`` is the validated list — the declared Output contract
        # (``todos: Optional[list]``). The pre-fix ``format_for_llm()``
        # call leaked TodoService's raw JSON STRING into this key, which
        # the Output validation now rejects; the LLM-facing serialization
        # happens downstream anyway (``_serialise_tool_result`` /
        # LangChain dump the whole dict to JSON).
        return {
            "message": f"Updated todo list ({len(stored)} items)",
            "todos": stored,
            "count": len(stored),
        }


# --- self-registration on import -------------------------------------------
# The parameter-panel Current Todos editor reads/writes the live TodoService
# state through these WS handlers (self-contained plugin-folder pattern —
# core router needs no edit). See nodes/telegram/__init__.py for the template.
from services.ws_handler_registry import register_ws_handlers  # noqa: E402
from ._handlers import WS_HANDLERS  # noqa: E402

register_ws_handlers(WS_HANDLERS)
