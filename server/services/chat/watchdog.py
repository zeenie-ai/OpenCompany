"""The chat run watchdog: ``ledger.sweep`` every ``runs.watchdog_interval_s``
(config/chat_defaults.json).

main.py starts it once the database is up and stops it at shutdown. The
first sweep runs at once, so a run a crash left open ends once the server
is back; a pending run's wait counts from this process's start, so the
restart itself does not expire it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from core.logging import get_logger
from services.chat import ledger
from services.chat.config import runs_setting

logger = get_logger(__name__)


async def temporal_workflow_closed(workflow_id: str, run_id: Optional[str]) -> Optional[bool]:
    """Whether the Temporal workflow has closed: True once it is no longer
    running (or Temporal no longer knows it), False while it runs, None when
    Temporal cannot be asked."""
    try:
        from core.container import container

        wrapper = container.temporal_client()
    except Exception:  # noqa: BLE001 - Temporal is optional
        return None
    client = getattr(wrapper, "client", None) if wrapper is not None else None
    if client is None:
        return None
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.service import RPCError, RPCStatusCode

    try:
        description = await client.get_workflow_handle(workflow_id, run_id=run_id).describe()
    except RPCError as exc:
        return True if exc.status == RPCStatusCode.NOT_FOUND else None
    except Exception:  # noqa: BLE001 - unknown is not closed
        return None
    return description.status != WorkflowExecutionStatus.RUNNING


async def cancel_temporal_workflow(workflow_id: str, run_id: Optional[str]) -> None:
    """Ask Temporal to cancel a workflow (a stopped run's). Does nothing when
    Temporal cannot be reached; a workflow that already closed is fine."""
    try:
        from core.container import container

        wrapper = container.temporal_client()
    except Exception:  # noqa: BLE001 - Temporal is optional
        return
    client = getattr(wrapper, "client", None) if wrapper is not None else None
    if client is None:
        return
    from temporalio.service import RPCError

    try:
        await client.get_workflow_handle(workflow_id, run_id=run_id).cancel()
    except RPCError:
        # Already closed, or unknown: nothing left to cancel.
        return


class ChatRunWatchdog:
    def __init__(self, database: Any, *, interval: Optional[float] = None) -> None:
        self.database = database
        self.interval = float(interval if interval is not None else runs_setting("watchdog_interval_s"))
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop(), name="chat-run-watchdog")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await ledger.sweep(
                    self.database,
                    temporal_status=temporal_workflow_closed,
                    temporal_cancel=cancel_temporal_workflow,
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the next sweep tries again
                logger.warning("Chat run sweep failed", exc_info=True)
            try:
                # Drafts held for the owner wait on nothing, so they end here
                # once past their expiry (services/approvals/reconcile.py).
                from services.approvals.reconcile import expire_due

                await expire_due(self.database)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the next round tries again
                logger.warning("Held draft expiry failed", exc_info=True)
            await asyncio.sleep(self.interval)


__all__ = ["ChatRunWatchdog", "cancel_temporal_workflow", "temporal_workflow_closed"]
