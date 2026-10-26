"""Who hears about approval changes, without this package knowing them.

This package registers the ``approval_lifecycle`` broadcast
(``services/approvals/__init__.py``); the employees package registers the
summary refresh (its pending count). A failing listener is logged and never
fails the change.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ApprovalChange:
    #: requested | decided | undone | restored | sending | sent | failed |
    #: expired | cancelled
    stage: str
    approval_id: str
    workflow_id: str
    status: str
    revision: int
    #: The chat run a held tool call was made in.
    run_id: Optional[str] = None
    #: ``turn`` when its agent's turn waits on it (services/approvals/
    #: waiting.py).
    runtime: Optional[str] = None


ApprovalListener = Callable[[ApprovalChange], Any]
_LISTENERS: List[ApprovalListener] = []


def register_approval_listener(listener: ApprovalListener) -> None:
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


async def notify_approval_changed(change: ApprovalChange) -> None:
    for listener in list(_LISTENERS):
        try:
            result = listener(change)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.warning("Approval listener failed", stage=change.stage, approval_id=change.approval_id, exc_info=True)


def change_of(row: Any, stage: str) -> ApprovalChange:
    return ApprovalChange(
        stage=stage,
        approval_id=row.id,
        workflow_id=row.workflow_id,
        status=row.status,
        revision=row.revision,
        run_id=getattr(row, "run_id", None) or None,
        runtime=getattr(row, "runtime", None) or None,
    )


__all__ = ["ApprovalChange", "change_of", "notify_approval_changed", "register_approval_listener"]
