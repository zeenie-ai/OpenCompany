"""Wake a waiting gate the moment its draft is decided.

Process-local and best-effort: a gate also re-reads its row every few
seconds, so a decision made in another process (or one this process
missed) is still seen, just a little later.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Set

_waiters: Dict[str, Set["asyncio.Future[None]"]] = {}


async def wait_for_change(approval_id: str, timeout: float) -> bool:
    """Sleep until ``notify(approval_id)`` or ``timeout`` seconds. True when
    woken by a notification."""
    loop = asyncio.get_running_loop()
    future: "asyncio.Future[None]" = loop.create_future()
    _waiters.setdefault(approval_id, set()).add(future)
    try:
        await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        waiting = _waiters.get(approval_id)
        if waiting is not None:
            waiting.discard(future)
            if not waiting:
                _waiters.pop(approval_id, None)
        if not future.done():
            future.cancel()


def waiting(approval_id: str) -> int:
    """How many gates in this process are waiting on ``approval_id``."""
    return len(_waiters.get(approval_id, ()))


def notify(approval_id: str) -> int:
    """Wake everything waiting on ``approval_id``. Returns how many."""
    woken = 0
    for future in list(_waiters.get(approval_id, ())):
        if not future.done():
            future.set_result(None)
            woken += 1
    return woken


def reset_for_tests() -> None:
    for waiting in _waiters.values():
        for future in waiting:
            if not future.done():
                future.cancel()
    _waiters.clear()


__all__ = ["notify", "reset_for_tests", "wait_for_change", "waiting"]
