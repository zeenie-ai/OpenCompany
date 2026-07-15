"""Todo list service -- JSON-based per-node todo state management.

Provides the write_todos tool capability for AI agents to create and manage
structured task lists during complex multi-step operations.

State is persisted as JSON strings. Modern callers use a workflow + node
composite key so two ``writeTodos`` nodes on one canvas remain independent;
legacy callers that do not provide ``node_id`` retain the historical
workflow-only fallback.
Follows the singleton pattern used by browser_service.py and the
TelegramService inside ``nodes/telegram/_service.py``.
"""

import json
from typing import Dict, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)

VALID_STATUSES = frozenset(("pending", "in_progress", "completed"))
TODO_KEY_VERSION = "todo:v2"
UNSAVED_WORKFLOW_ID = "unsaved"


def todo_session_key(
    workflow_id: Optional[str] = None,
    node_id: Optional[str] = None,
) -> str:
    """Return the canonical storage key for a todo-list owner.

    A workflow can contain more than one ``writeTodos`` node, so workflow
    identity alone is not sufficient. Whenever ``node_id`` is present the key
    is versioned and node-scoped; unsaved workflows use an explicit stable
    scope. Missing-node callers intentionally keep the pre-v2 fallback for
    compatibility with older agents and WS clients.
    """
    if node_id:
        workflow_scope = workflow_id or UNSAVED_WORKFLOW_ID
        return f"{TODO_KEY_VERSION}:{workflow_scope}:{node_id}"
    return workflow_id or "default"


def todo_workflow_prefix(workflow_id: str) -> str:
    """Return the prefix shared by all v2 todo keys in ``workflow_id``."""
    return f"{TODO_KEY_VERSION}:{workflow_id}:"


class TodoService:
    """Manages independently keyed todo lists stored as JSON.

    Each session maintains an independent todo list that persists across
    tool calls within the same execution.
    """

    def __init__(self):
        self._store: Dict[str, str] = {}  # session_key -> JSON string

    def write(self, session_key: str, todos: List[dict]) -> List[dict]:
        """Replace the todo list for a session.

        Validates each item, serializes to JSON, and stores.

        Args:
            session_key: Canonical workflow + node key, or a legacy fallback.
            todos: List of todo dicts with 'content' and 'status' keys.

        Returns:
            The validated and stored todo list.
        """
        validated = []
        for item in todos:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            status = item.get("status", "pending")
            if status not in VALID_STATUSES:
                status = "pending"
            validated.append({"content": content, "status": status})

        self._store[session_key] = json.dumps(validated)
        logger.debug(
            "[TodoService] Updated session=%s: %d items (%d pending, %d in_progress, %d completed)",
            session_key,
            len(validated),
            sum(1 for t in validated if t["status"] == "pending"),
            sum(1 for t in validated if t["status"] == "in_progress"),
            sum(1 for t in validated if t["status"] == "completed"),
        )
        return validated

    def get(self, session_key: str) -> List[dict]:
        """Get current todo list for a session, deserialized from JSON."""
        raw = self._store.get(session_key)
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    def get_json(self, session_key: str) -> str:
        """Get current todo list as a raw JSON string."""
        return self._store.get(session_key, "[]")

    def clear(self, session_key: str) -> None:
        """Clear todos for a session."""
        self._store.pop(session_key, None)

    def clear_workflow(self, workflow_id: str) -> List[str]:
        """Clear every v2 node-scoped todo list in a workflow.

        Returns the concrete keys removed so memory-clear callers can expose
        accurate diagnostics.  The legacy workflow-only key is deliberately
        left to the caller because that compatibility key may be part of a
        broader legacy clear set.
        """
        prefix = todo_workflow_prefix(workflow_id)
        cleared = [key for key in self._store if key.startswith(prefix)]
        for key in cleared:
            self.clear(key)
        return cleared

    def format_for_llm(self, session_key: str) -> str:
        """Format todos as JSON string for LLM consumption."""
        return self.get_json(session_key)


_service: Optional[TodoService] = None


def get_todo_service() -> TodoService:
    """Lazy singleton accessor."""
    global _service
    if _service is None:
        _service = TodoService()
    return _service
