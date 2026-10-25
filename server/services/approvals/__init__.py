"""What waits for the owner's OK before it goes out.

- ``contract``: the gate's contract with graphs (conditions, NO_REPLY, the
  statuses and the Undo and Restore windows).
- ``store``: the rows (models/approvals.py), moved by compare-and-swap.
- ``decisions``: the owner's send, undo, discard, restore and retry.
- ``rules``: a workflow's live Ask first rule.
- ``tool_calls``: holding a tool call that sends (``BaseNode.as_activity``).
- ``repeats``: which node calls may run again after an attempt broke off.
- ``execution`` / ``activities``: sending a held call once the owner
  pressed Send (``ApprovedToolCallWorkflow``).
- ``waiter``: wakes a waiting approval gate at once.
- ``reconcile``: ends what nothing waits for any more, and restarts sends
  that never started.
- ``events`` / ``listeners``: the ``approval_lifecycle`` broadcast and who
  else hears about changes.
- ``handlers``: the WebSocket commands.

The ``approvalGate`` node that waits on a row lives in
``nodes/workflow/approval_gate``; this package never imports ``nodes/``.

Importing the package registers the WebSocket commands, the broadcast and
the cleanup that runs when a workflow is deleted.
"""

from __future__ import annotations

from services.workflow_storage.hooks import register_workflow_deleted_hook as _register_workflow_deleted_hook
from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from .events import broadcast_approval_change as _broadcast_approval_change
from .handlers import WS_HANDLERS as _APPROVAL_WS_HANDLERS
from .handlers import on_workflow_deleted as _on_workflow_deleted
from .listeners import register_approval_listener as _register_approval_listener

_register_ws_handlers(_APPROVAL_WS_HANDLERS)
_register_approval_listener(_broadcast_approval_change)
_register_workflow_deleted_hook(_on_workflow_deleted)

__all__ = [
    "contract",
    "decisions",
    "events",
    "execution",
    "handlers",
    "listeners",
    "queries",
    "reconcile",
    "repeats",
    "rules",
    "store",
    "tool_calls",
    "waiter",
]
