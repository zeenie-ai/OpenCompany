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

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

logger = get_logger(__name__)


class SimpleMemoryParams(BaseModel):
    session_id: str = Field(
        default="",
        title="Session ID (Override)",
        description=("Leave empty to auto-use connected agent ID. " "Set manually to share memory across agents."),
        json_schema_extra={"placeholder": "Auto (uses agent ID)"},
    )
    window_size: int = Field(
        default=100,
        ge=1,
        le=100,
        title="Window Size",
        description=("Number of message pairs to keep in short-term memory " "(uses global default from Settings)"),
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
    embedding_provider: Literal["huggingface", "openai", "ollama"] = Field(
        default="huggingface",
        title="Embedding Provider",
        description=(
            "Provider for long-term semantic memory. HuggingFace uses the "
            "optional local-embeddings extra; OpenAI uses the stored OpenAI "
            "credential."
        ),
        json_schema_extra={
            "displayOptions": {"show": {"long_term_enabled": [True]}}
        },
    )
    embedding_model: str = Field(
        default="",
        title="Embedding Model",
        description=(
            "Provider-specific embedding model. Empty uses BAAI/"
            "bge-small-en-v1.5 for HuggingFace, text-embedding-3-small "
            "for OpenAI, or nomic-embed-text for Ollama."
        ),
        json_schema_extra={
            "displayOptions": {"show": {"long_term_enabled": [True]}}
        },
    )
    embedding_endpoint: str = Field(
        default="",
        title="Embedding Endpoint",
        description=(
            "Optional OpenAI-compatible base URL or Ollama host. "
            "Credentials are never stored here."
        ),
        json_schema_extra={
            "displayOptions": {"show": {"long_term_enabled": [True]}}
        },
    )
    last_session_id: Optional[str] = Field(
        default=None,
        title="Last Claude Session ID",
        description=(
            "Internal: the session UUID claude returned on the most "
            "recent successful run. Display-only — `claude_code_agent` "
            "no longer reads this field. Continuity is driven by "
            "`--continue` so claude tracks its own latest session per "
            "cwd. Kept for back-compat + diagnostic display; clearing "
            "the memory wipes it."
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
    handles = ({"name": "output-memory", "kind": "output", "position": "top", "label": "Memory", "role": "memory"},)
    ui_hints = {"isMemoryPanel": True, "hasCodeEditor": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = SimpleMemoryParams
    Output = SimpleMemoryOutput

    @classmethod
    async def reset_execution_state(
        cls, *, node_id: str, workflow_id: str, execution_id: str,
        graph: dict[str, Any], database: Any,
    ) -> dict[str, Any]:
        """Clear this plugin's mutable state after the execution is archived."""
        from services.memory import clear_agent_session_state
        from services.memory_store import clear_session as clear_direct_session

        params = await database.get_node_parameters(node_id) or {}
        configured = str(params.get("session_id") or "").strip()
        if configured and configured != "default":
            sessions = [configured]
        else:
            sessions = [
                str(edge.get("target"))
                for edge in graph.get("edges", [])
                if edge.get("source") == node_id
                and (edge.get("targetHandle") or edge.get("target_handle")) == "input-memory"
                and edge.get("target")
            ]
            sessions.append("default")
        sessions = list(dict.fromkeys(sessions))
        for index, session_id in enumerate(sessions):
            await clear_agent_session_state(
                session_id=session_id,
                workflow_id=workflow_id,
                clear_long_term=True,
                memory_node_id=node_id if index == 0 else None,
            )
            clear_direct_session(session_id)
            await database.clear_conversation(session_id)
            await database.reset_session_token_state(session_id)
        return {
            "reset": True,
            "parameters": await database.get_node_parameters(node_id) or {},
            "sessions": sessions,
        }

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
