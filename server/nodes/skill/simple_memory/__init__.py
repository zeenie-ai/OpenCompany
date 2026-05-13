"""Simple Memory — Wave 11.E.3 inlined.

Markdown-based conversation memory, optionally backed by a vector
store for semantic recall. Connects upward to an agent's input-memory
handle. The plugin queries the in-memory ``MessageStore`` directly;
agents read the ``memory_content`` parameter (the editable markdown)
plus the live message log returned here.

Field shape mirrors the pre-Wave-11 nodeDefinitions (camelCase on main)
but with snake_case names, since plugin Pydantic params are the source
of truth post-migration.  No buffer/window split, no clear_on_run --
trimming is always windowed by ``window_size`` and the UI exposes a
manual "Clear Memory" button instead.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

logger = get_logger(__name__)


class SimpleMemoryParams(BaseModel):
    session_id: str = Field(
        default="",
        title="Session ID (Override)",
        description=(
            "Leave empty to auto-use connected agent ID. "
            "Set manually to share memory across agents."
        ),
        json_schema_extra={"placeholder": "Auto (uses agent ID)"},
    )
    window_size: int = Field(
        default=100,
        ge=1,
        le=100,
        title="Window Size",
        description=(
            "Number of message pairs to keep in short-term memory "
            "(uses global default from Settings)"
        ),
    )
    memory_content: str = Field(
        default="# Conversation History\n\n*No messages yet.*\n",
        title="Conversation History",
        description="Recent conversation history (editable)",
        json_schema_extra={
            "rows": 15,
            "editor": "code",
            "editorLanguage": "markdown",
        },
    )
    long_term_enabled: bool = Field(
        default=False,
        title="Enable Long-Term Memory",
        description="Archive old messages to vector DB for semantic retrieval",
    )
    retrieval_count: int = Field(
        default=3,
        ge=1,
        le=10,
        title="Retrieval Count",
        description="Number of relevant memories to retrieve from long-term storage",
        json_schema_extra={"displayOptions": {"show": {"long_term_enabled": [True]}}},
    )
    last_session_id: Optional[str] = Field(
        default=None,
        title="Last Claude Session ID",
        description=(
            "Internal: the session UUID claude returned on the most "
            "recent successful run. Drives `--resume <UUID>` on the "
            "next spawn so claude finds and continues its own JSONL "
            "transcript on disk. Hidden from the UI; clearing the "
            "memory wipes this too."
        ),
        json_schema_extra={"hidden": True},
    )

    model_config = ConfigDict(extra="ignore")


class SimpleMemoryOutput(BaseModel):
    memory_content: Optional[str] = None
    message_count: Optional[int] = None
    session_id: Optional[str] = None
    messages: Optional[list[Any]] = None
    window_size: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class SimpleMemoryNode(ActionNode):
    type = "simpleMemory"
    display_name = "Simple Memory"
    subtitle = "Conversation History"
    group = ("tool", "memory")
    description = "Markdown-based conversation memory with optional vector DB"
    component_kind = "model"
    handles = (
        {"name": "output-memory", "kind": "output", "position": "top",
         "label": "Memory", "role": "memory"},
    )
    ui_hints = {"isMemoryPanel": True, "hasCodeEditor": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = SimpleMemoryParams
    Output = SimpleMemoryOutput

    @Operation("read")
    async def read(self, ctx: NodeContext, params: SimpleMemoryParams) -> SimpleMemoryOutput:
        """Return the current message log for the session.

        Always windowed by ``window_size`` -- no buffer/window split.
        An empty ``session_id`` resolves to ``"default"`` so callers
        that accept the auto-mode default still get a stable bucket.
        """
        from services.memory_store import get_messages

        session_id = params.session_id or "default"
        window_size = params.window_size

        messages = get_messages(session_id, window_size)
        return SimpleMemoryOutput(
            memory_content=params.memory_content,
            message_count=len(messages),
            session_id=session_id,
            messages=messages,
            window_size=window_size,
        )
