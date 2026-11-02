"""The event-loop lag watchdog only logs (1b.39)."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from services import loop_watchdog


@pytest.fixture
def log(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(loop_watchdog, "logger", logger)
    monkeypatch.setattr(loop_watchdog, "QUIET_SECONDS", 0.5)
    return logger


async def watched(blocks):
    """Run the watchdog while the loop is blocked once per entry of ``blocks``."""
    task = asyncio.create_task(loop_watchdog.watch_loop_lag())
    await asyncio.sleep(0.15)
    for seconds in blocks:
        time.sleep(seconds)  # blocks the event loop on purpose
        await asyncio.sleep(0.1)
    await asyncio.sleep(0.8)  # longer than QUIET_SECONDS: the episode ends
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_blocked_loop_warns_once_and_says_when_it_is_responsive(log):
    await watched([0.5])
    assert log.warning.call_count == 1
    assert [call.args[0] for call in log.info.call_args_list] == ["The event loop is responsive again"]


async def test_blocks_close_together_are_one_episode(log):
    await watched([0.4, 0.4, 0.4])
    assert log.warning.call_count == 1


async def test_a_long_gap_is_info_not_a_warning(log, monkeypatch):
    monkeypatch.setattr(loop_watchdog, "GAP_SECONDS", 0.4)
    await watched([0.7])
    log.warning.assert_not_called()
    assert log.info.call_args_list[0].args[0].startswith("The event loop resumed after a long gap")
