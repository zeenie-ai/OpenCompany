"""Who hears about approval changes, without this package knowing them.

The approval-gate plugin registers the ``approval_lifecycle`` broadcast;
the employees package registers the summary refresh (its pending count).
A failing listener is logged and never fails the change.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, List

from core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ApprovalChange:
    #: requested | decided | expired | cancelled
    stage: str
    approval_id: str
    workflow_id: str
    status: str
    revision: int


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
    return ApprovalChange(stage=stage, approval_id=row.id, workflow_id=row.workflow_id, status=row.status, revision=row.revision)


__all__ = ["ApprovalChange", "change_of", "notify_approval_changed", "register_approval_listener"]
