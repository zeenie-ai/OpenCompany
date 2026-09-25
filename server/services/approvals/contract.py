"""The approval step's contract with the graphs that use it.

An employee that asks before sending gets ``agent -> approvalGate ->
reply``: the gate holds the agent's draft until the owner presses Send or
Discard on its card. The edge into the gate skips it when the agent had
nothing to send (NO_REPLY); the edge out only fires for an approved draft.

The gate's output fails closed: ``text``, ``subject`` and ``recipient`` are
empty unless the draft was approved, so a mis-wired graph sends nothing.
"""

from __future__ import annotations

from typing import Any, Dict

APPROVAL_GATE_TYPE = "approvalGate"

#: The agent answers exactly this when a message needs no reply.
NO_REPLY = "NO_REPLY"

#: How long a draft waits for the owner before it expires.
DEFAULT_TIMEOUT_HOURS = 168

APPROVAL_STATUSES = ("pending", "approved", "discarded", "expired", "cancelled")
FINAL_STATUSES = frozenset({"approved", "discarded", "expired", "cancelled"})


def send_condition() -> Dict[str, Any]:
    """On the edge into the gate: the agent wrote something to send."""
    return {"field": "result.response", "operator": "neq", "value": NO_REPLY}


def approved_edge_condition() -> Dict[str, Any]:
    """On the edge out of the gate: the owner pressed Send."""
    return {"field": "result.approved", "operator": "is_true"}


__all__ = [
    "APPROVAL_GATE_TYPE",
    "APPROVAL_STATUSES",
    "DEFAULT_TIMEOUT_HOURS",
    "FINAL_STATUSES",
    "NO_REPLY",
    "approved_edge_condition",
    "send_condition",
]
