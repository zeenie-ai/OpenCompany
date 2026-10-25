"""The approval step's contract with the graphs and tools that use it.

An employee that asks before sending gets ``agent -> approvalGate ->
reply``: the gate holds the agent's draft until the owner presses Send or
Discard on its card. The edge into the gate skips it when the agent had
nothing to send (NO_REPLY); the edge out only fires for an approved draft.

The gate's output fails closed: ``text``, ``subject`` and ``recipient`` are
empty unless the draft was approved, so a mis-wired graph sends nothing.

A tool call that sends is held the same way (``services/approvals/
tool_calls.py``), as a row of kind ``tool_call``. A call that broke off
midway and may have happened leaves a card: a ``tool_call`` row for a send
the card can show, else an ``effect`` row, which the owner dismisses.
"""

from __future__ import annotations

from typing import Any, Dict

APPROVAL_GATE_TYPE = "approvalGate"

#: The agent answers exactly this when a message needs no reply.
NO_REPLY = "NO_REPLY"

#: How long a draft waits for the owner before it expires.
DEFAULT_TIMEOUT_HOURS = 168

APPROVAL_KINDS = ("gate", "tool_call", "effect")
APPROVAL_STATUSES = ("pending", "approved", "sending", "sent", "failed", "discarded", "expired", "cancelled")
#: Nothing moves a row out of these.
FINAL_STATUSES = frozenset({"sent", "expired", "cancelled"})
#: A send's outcome: it went, it did not, or it broke off and may have.
OUTCOMES = ("sent", "not_sent", "unknown")

#: How long Undo works after Send. The send waits this long.
UNDO_SECONDS = 5.0
#: How long Restore works after Discard on a gate's draft (its run waits
#: this long before it goes on without it). A held tool call can be
#: restored until it expires.
RESTORE_SECONDS = 5.0


def send_condition() -> Dict[str, Any]:
    """On the edge into the gate: the agent wrote something to send."""
    return {"field": "result.response", "operator": "neq", "value": NO_REPLY}


def approved_edge_condition() -> Dict[str, Any]:
    """On the edge out of the gate: the owner pressed Send."""
    return {"field": "result.approved", "operator": "is_true"}


__all__ = [
    "APPROVAL_GATE_TYPE",
    "APPROVAL_KINDS",
    "APPROVAL_STATUSES",
    "DEFAULT_TIMEOUT_HOURS",
    "FINAL_STATUSES",
    "NO_REPLY",
    "OUTCOMES",
    "RESTORE_SECONDS",
    "UNDO_SECONDS",
    "approved_edge_condition",
    "send_condition",
]
