"""Run records: one row per finished run of a deployed workflow.

The employee card's "N done today" counts successful runs since the start
of the owner's day (``profile_timezone``, UTC when unset), and Settings >
Billing counts them since the start of the owner's month. Records are
written once per run (``run_id`` is unique, so an activity retry or both
runtimes seeing the same run changes nothing) and pruned after
``RETENTION_DAYS``. A new record refreshes the employee's summary through
the coalesced ``employee_lifecycle`` broadcast.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from core.logging import get_logger
from models.employees import WorkflowRunRecord

logger = get_logger(__name__)

#: Longer than any month, so this month's count is always complete.
RETENTION_DAYS = 35
RUN_STATUSES = ("success", "failed")
RUNTIMES = ("temporal", "local")
#: Pruning runs at most this often per process.
PRUNE_EVERY_SECONDS = 3600.0

_last_prune = float("-inf")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def owner_zone(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def start_of_day(now: datetime, zone: ZoneInfo) -> datetime:
    """Midnight today in ``zone``, as a UTC instant."""
    local = now.astimezone(zone)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc)


def start_of_month(now: datetime, zone: ZoneInfo) -> datetime:
    """Midnight on the 1st of this month in ``zone``, as a UTC instant."""
    local = now.astimezone(zone)
    first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return first.astimezone(timezone.utc)


async def record_run(
    database: Any,
    *,
    workflow_id: str,
    run_id: str,
    status: str,
    runtime: str,
    generation: int = 0,
    finished_at: Optional[datetime] = None,
) -> bool:
    """Record a finished run. False when this run was already recorded."""
    if not workflow_id or not run_id:
        return False
    row = WorkflowRunRecord(
        workflow_id=str(workflow_id),
        run_id=str(run_id)[:255],
        generation=int(generation or 0),
        status=status if status in RUN_STATUSES else "failed",
        runtime=runtime if runtime in RUNTIMES else "local",
        finished_at=(finished_at or _utcnow()).astimezone(timezone.utc),
    )
    try:
        async with database.get_session() as session:
            session.add(row)
            await session.commit()
    except IntegrityError:
        return False
    from services.employees.events import employee_changed

    employee_changed(str(workflow_id))
    await _maybe_prune(database)
    return True


async def done_today(
    database: Any,
    workflow_ids: Iterable[str],
    *,
    zone: ZoneInfo,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Successful runs since midnight in the owner's zone, per workflow."""
    ids = [str(workflow_id) for workflow_id in workflow_ids if workflow_id]
    if not ids:
        return {}
    since = start_of_day(now or _utcnow(), zone)
    async with database.get_session() as session:
        result = await session.execute(
            select(WorkflowRunRecord.workflow_id, func.count())
            .where(
                WorkflowRunRecord.workflow_id.in_(ids),
                WorkflowRunRecord.status == "success",
                WorkflowRunRecord.finished_at >= since,
            )
            .group_by(WorkflowRunRecord.workflow_id)
        )
        return {workflow_id: int(count) for workflow_id, count in result.all()}


async def done_this_month(database: Any, *, zone: ZoneInfo, now: Optional[datetime] = None) -> int:
    """Successful runs since the 1st of the month in the owner's zone, across every workflow."""
    since = start_of_month(now or _utcnow(), zone)
    async with database.get_session() as session:
        result = await session.execute(
            select(func.count())
            .select_from(WorkflowRunRecord)
            .where(WorkflowRunRecord.status == "success", WorkflowRunRecord.finished_at >= since)
        )
        return int(result.scalar_one() or 0)


async def latest_run(database: Any, workflow_id: str) -> Optional[Dict[str, Any]]:
    async with database.get_session() as session:
        result = await session.execute(
            select(WorkflowRunRecord)
            .where(WorkflowRunRecord.workflow_id == workflow_id)
            .order_by(WorkflowRunRecord.finished_at.desc(), WorkflowRunRecord.id.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
    if row is None:
        return None
    finished = row.finished_at if row.finished_at.tzinfo else row.finished_at.replace(tzinfo=timezone.utc)
    return {"status": row.status, "runtime": row.runtime, "finished_at": finished.isoformat(), "generation": row.generation}


async def prune_run_records(database: Any, *, now: Optional[datetime] = None, days: int = RETENTION_DAYS) -> int:
    cutoff = (now or _utcnow()) - timedelta(days=days)
    async with database.get_session() as session:
        result = await session.execute(delete(WorkflowRunRecord).where(WorkflowRunRecord.finished_at < cutoff))
        await session.commit()
        return int(result.rowcount or 0)


async def delete_runs_for_workflow(database: Any, workflow_id: str) -> int:
    async with database.get_session() as session:
        result = await session.execute(delete(WorkflowRunRecord).where(WorkflowRunRecord.workflow_id == workflow_id))
        await session.commit()
        return int(result.rowcount or 0)


async def _maybe_prune(database: Any) -> None:
    global _last_prune
    if time.monotonic() - _last_prune < PRUNE_EVERY_SECONDS:
        return
    _last_prune = time.monotonic()
    try:
        removed = await prune_run_records(database)
        if removed:
            logger.info("Pruned old run records", removed=removed)
    except Exception:
        logger.warning("Could not prune run records", exc_info=True)


def reset_for_tests() -> None:
    global _last_prune
    _last_prune = float("-inf")


__all__ = [
    "RETENTION_DAYS",
    "delete_runs_for_workflow",
    "done_this_month",
    "done_today",
    "latest_run",
    "owner_zone",
    "prune_run_records",
    "record_run",
    "start_of_day",
    "start_of_month",
]
