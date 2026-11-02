"""Logs when the event loop is blocked (RFC-0006 1b.39).

A coroutine that runs blocking code (a synchronous call, a heavy loop)
stalls every request, broadcast and heartbeat in the process. This task
ticks every :data:`TICK_SECONDS`; a tick that comes :data:`LAG_SECONDS` or
more late starts an episode, logged once as a WARNING. The episode ends
after :data:`QUIET_SECONDS` without a late tick, with one INFO line giving
the worst lag. A single gap of :data:`GAP_SECONDS` or more is logged as
INFO instead: the machine may have slept. It only logs.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from core.logging import get_logger

logger = get_logger(__name__)

TICK_SECONDS = 0.1
#: A tick this late means the loop was blocked.
LAG_SECONDS = 0.25
#: An episode ends after this long without a late tick.
QUIET_SECONDS = 5.0
#: A gap this long is more likely a sleep than a block.
GAP_SECONDS = 30.0


async def watch_loop_lag() -> None:
    worst = 0.0
    last_late: Optional[float] = None
    while True:
        started = time.monotonic()
        await asyncio.sleep(TICK_SECONDS)
        now = time.monotonic()
        lag = now - started - TICK_SECONDS
        if lag >= GAP_SECONDS:
            logger.info("The event loop resumed after a long gap (the machine may have slept)", gap_seconds=round(lag, 1))
        elif lag >= LAG_SECONDS:
            if last_late is None:
                logger.warning("The event loop is blocked", lag_seconds=round(lag, 3))
            worst, last_late = max(worst, lag), now
        if last_late is not None and now - last_late >= QUIET_SECONDS:
            logger.info("The event loop is responsive again", worst_lag_seconds=round(worst, 3))
            worst, last_late = 0.0, None


__all__ = ["watch_loop_lag"]
