"""Bounded client Update calls (1b.15).

A socket request or a recovery loop must not wait for ever on a workflow that
does not answer: a stuck run, a busy worker, an engine that is down.
``bounded_update`` gives the call a deadline and says "pending" when it
passes. The Update may still be admitted and applied later, so a caller treats
pending as "not known yet", never as refused.
"""

import asyncio
from typing import Awaitable, TypeVar

from temporalio.client import WorkflowUpdateRPCTimeoutOrCancelledError

T = TypeVar("T")


class UpdatePending(Exception):
    """Our deadline passed first. The Update may still be admitted and applied."""


async def bounded_update(call: Awaitable[T], seconds: float) -> T:
    """Await an Update call for at most ``seconds``.

    The SDK turns any interrupted wait into
    ``WorkflowUpdateRPCTimeoutOrCancelledError``, our deadline included, and
    its ``rpc_timeout`` bounds only the first RPC, so the deadline is ours. A
    cancel of the caller's own task comes back as ``CancelledError`` (the SDK
    cannot tell it from a timeout); an expired deadline raises
    ``UpdatePending``; anything else passes through unchanged.
    """
    try:
        async with asyncio.timeout(seconds) as deadline:
            return await call
    except (TimeoutError, WorkflowUpdateRPCTimeoutOrCancelledError) as exc:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError() from exc
        if deadline.expired():
            raise UpdatePending(f"no answer in {seconds:g} s") from exc
        raise
