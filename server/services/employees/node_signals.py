"""Live state that a node's plugin reports for the Home employee card.

Some of what Home shows is not in the database: whether an employee's
browser is waiting for the owner right now lives in the Browser plugin's
memory. This registry lets a plugin publish such state without
``services/`` importing ``nodes/`` (the same shape as the approvals
listeners):

- the plugin registers a synchronous source per kind
  (``register_node_state_source("browser", fn)``), where
  ``fn(workflow_id, node_id)`` returns a small dict or ``None``;
- the employee summary builder reads it with :func:`node_state`;
- the plugin calls :func:`node_state_changed` when that state changes, which
  re-sends the employee's summary at once.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.logging import get_logger
from services.plugin.registry import IdempotentRegistry

logger = get_logger(__name__)

NodeStateSource = Callable[[str, str], Optional[Dict[str, Any]]]

_SOURCES: Dict[str, NodeStateSource] = {}
_REGISTRY: IdempotentRegistry[str, NodeStateSource] = IdempotentRegistry("node_state_source", items=_SOURCES)


def register_node_state_source(kind: str, source: NodeStateSource) -> None:
    """Idempotent on re-import; a different source for a kind raises."""
    _REGISTRY.register(kind, source)


def node_state(kind: str, workflow_id: str, node_id: Optional[str]) -> Optional[Dict[str, Any]]:
    source = _SOURCES.get(kind)
    if source is None or not node_id:
        return None
    try:
        return source(workflow_id, node_id)
    except Exception:  # noqa: BLE001 - a summary must never fail over live state
        logger.debug("[employees] node state source %s failed", kind, exc_info=True)
        return None


def node_state_changed(kind: str, workflow_id: str) -> None:
    """The plugin's state for ``workflow_id`` changed: refresh its summary now."""
    if not workflow_id or workflow_id.startswith("unsaved:"):
        return
    from .events import employee_changed_now

    employee_changed_now(workflow_id)


def reset_for_tests() -> None:
    _SOURCES.clear()


__all__ = ["NodeStateSource", "node_state", "node_state_changed", "register_node_state_source", "reset_for_tests"]
