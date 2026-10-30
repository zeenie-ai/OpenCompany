"""1b.15: a client Update call answers "pending" instead of hanging."""

from __future__ import annotations

import asyncio

import pytest
from temporalio.client import WorkflowUpdateFailedError, WorkflowUpdateRPCTimeoutOrCancelledError
from temporalio.exceptions import ApplicationError

from services.temporal.update_calls import UpdatePending, bounded_update


async def _answers():
    return {"ok": True}


async def _hangs():
    await asyncio.sleep(60)


async def _hangs_like_the_sdk():
    # The SDK recasts the cancel of its RPC wait as its own error.
    try:
        await asyncio.sleep(60)
    except asyncio.CancelledError as exc:
        raise WorkflowUpdateRPCTimeoutOrCancelledError() from exc


async def test_a_call_that_answers_in_time_returns_its_value():
    assert await bounded_update(_answers(), 1) == {"ok": True}


@pytest.mark.parametrize("call", [_hangs, _hangs_like_the_sdk])
async def test_a_call_past_its_deadline_is_pending(call):
    with pytest.raises(UpdatePending):
        await bounded_update(call(), 0.01)


@pytest.mark.parametrize("call", [_hangs, _hangs_like_the_sdk])
async def test_a_cancel_of_the_caller_stays_a_cancel(call):
    task = asyncio.create_task(bounded_update(call(), 30))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_timeout_raised_by_the_call_itself_is_not_pending():
    async def raises_timeout():
        raise TimeoutError("the transition's own timeout")

    with pytest.raises(TimeoutError, match="own timeout"):
        await bounded_update(raises_timeout(), 30)


async def test_a_failed_update_passes_through():
    async def fails():
        raise WorkflowUpdateFailedError(ApplicationError("refused"))

    with pytest.raises(WorkflowUpdateFailedError):
        await bounded_update(fails(), 30)
