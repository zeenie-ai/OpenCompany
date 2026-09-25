"""``employee_lifecycle``: Normal mode's employee changes as CloudEvents.

Type ``com.opencompany.employee.{hired|updated|removed}``, subject = the
workflow id (an employee is a workflow). Data:
``{workflow_id, revision, employee?, reason?}``. ``hired`` and ``updated``
carry the fresh summary so an open Home tab upserts it without a refetch;
``removed`` carries identity only. ``revision`` lets a client drop a
broadcast that arrives after a newer one.

``updated`` is coalesced to at most one per second per employee (run
records and approvals can burst): the first change goes out at once, the
rest collapse into one trailing broadcast built from the state at that
moment. Control changes (Start, Pause, Resume, a recovery pause) skip the
window through ``employee_changed_now``: the owner has just pressed the
button and is watching the card, and a second's delay would show the old
state after the control plane has moved. Builds for one employee run one
at a time, in call order, so a slower build never lands after a newer one.

The client subscribes through the generic ``addEventListener`` fan-out, so
there is no dedicated WebSocketContext case.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Literal, Optional, Set

from core.logging import get_logger
from services.events.envelope import WorkflowEvent

logger = get_logger(__name__)

WIRE_KEY = "employee_lifecycle"
SOURCE = "opencompany://services/employees"
COALESCE_SECONDS = 1.0

EmployeeStage = Literal["hired", "updated", "removed"]
SummaryBuilder = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]


def employee_lifecycle_event(
    stage: EmployeeStage,
    *,
    workflow_id: str,
    revision: int,
    employee: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> WorkflowEvent:
    data: Dict[str, Any] = {"workflow_id": workflow_id, "revision": int(revision)}
    if employee is not None:
        data["employee"] = employee
    if reason:
        data["reason"] = reason
    return WorkflowEvent(
        source=SOURCE,
        type=f"com.opencompany.employee.{stage}",
        subject=workflow_id,
        data=data,
    )


async def broadcast_employee_event(
    stage: EmployeeStage,
    *,
    workflow_id: str,
    revision: int,
    employee: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
) -> None:
    """Send one ``employee_lifecycle`` frame to every client. Best-effort."""
    from services.status_broadcaster import get_status_broadcaster

    event = employee_lifecycle_event(stage, workflow_id=workflow_id, revision=revision, employee=employee, reason=reason)
    try:
        await get_status_broadcaster().broadcast({"type": WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)})
    except Exception:
        logger.warning("employee_lifecycle broadcast failed", stage=stage, workflow_id=workflow_id, exc_info=True)


# ----- coalesced "updated" -----

_builder: Optional[SummaryBuilder] = None
_last_sent: Dict[str, float] = {}
_pending: Set[str] = set()
_tasks: Set[asyncio.Task] = set()
_build_locks: Dict[str, asyncio.Lock] = {}


def set_summary_builder(builder: Optional[SummaryBuilder]) -> None:
    """The package registers how to build one employee's summary; kept as an
    injected callable so this module never imports the summary code."""
    global _builder
    _builder = builder


async def _broadcast_latest(workflow_id: str) -> None:
    """Build the summary from the current state and broadcast it. One build
    at a time per employee (the lock is FIFO), so broadcasts leave in the
    order the changes happened."""
    builder = _builder
    if builder is None:
        return
    async with _build_locks.setdefault(workflow_id, asyncio.Lock()):
        try:
            summary = await builder(workflow_id)
        except Exception:
            logger.warning("employee summary build failed", workflow_id=workflow_id, exc_info=True)
            return
        if summary is None:
            return
        await broadcast_employee_event(
            "updated", workflow_id=workflow_id, revision=int(summary.get("revision") or 0), employee=summary
        )


async def _send_updated(workflow_id: str) -> None:
    _pending.discard(workflow_id)
    _last_sent[workflow_id] = time.monotonic()
    await _broadcast_latest(workflow_id)


def _spawn(coro: Awaitable[None]) -> None:
    task = asyncio.ensure_future(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def employee_changed(workflow_id: str) -> None:
    """Announce that an employee's summary may have changed. Coalesced: at
    most one ``updated`` broadcast per ``COALESCE_SECONDS`` per employee,
    always built from the latest state. Needs a running event loop."""
    if not workflow_id or workflow_id in _pending:
        return
    since = time.monotonic() - _last_sent.get(workflow_id, float("-inf"))
    if since >= COALESCE_SECONDS:
        _pending.add(workflow_id)
        _spawn(_send_updated(workflow_id))
        return
    _pending.add(workflow_id)

    async def trailing() -> None:
        await asyncio.sleep(COALESCE_SECONDS - since)
        await _send_updated(workflow_id)

    _spawn(trailing())


def employee_changed_now(workflow_id: str) -> None:
    """Announce a change the owner is waiting to see (a control-plane
    transition): broadcast at once, outside the coalescing window. It still
    counts as a send for the window, and a trailing update already scheduled
    still goes out, built from the state at that moment."""
    if not workflow_id:
        return
    _last_sent[workflow_id] = time.monotonic()
    _spawn(_broadcast_latest(workflow_id))


def forget_employee(workflow_id: str) -> None:
    """Drop coalescing state for a removed employee."""
    _last_sent.pop(workflow_id, None)
    _pending.discard(workflow_id)
    _build_locks.pop(workflow_id, None)


def reset_for_tests() -> None:
    _last_sent.clear()
    _pending.clear()
    _build_locks.clear()


__all__ = [
    "COALESCE_SECONDS",
    "WIRE_KEY",
    "broadcast_employee_event",
    "employee_changed",
    "employee_changed_now",
    "employee_lifecycle_event",
    "forget_employee",
    "set_summary_builder",
]
