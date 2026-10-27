"""The retry alert (1b.30): a quieter retry log, and stuck work on Home."""

from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.testing import ActivityEnvironment
from temporalio.worker import ExecuteActivityInput

from services import attention
from services.temporal import _interceptors
from services.temporal._interceptors import ActivityObservabilityInterceptor, retry_warns


@pytest.mark.parametrize(("limit", "warned"), [(10, [2, 10, 20, 30]), (8, [2, 8, 10, 20, 30])])
def test_retries_warn_at_the_first_the_limit_and_every_tenth(limit, warned):
    assert [attempt for attempt in range(2, 35) if retry_warns(attempt, limit)] == warned


class _Next:
    def __init__(self, failure=None):
        self.failure = failure

    async def execute_activity(self, input):
        if self.failure is not None:
            raise self.failure
        return "done"


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setattr(attention, "touch", AsyncMock())
    monkeypatch.setattr(attention, "resolve", AsyncMock())
    monkeypatch.setattr(_interceptors, "logger", MagicMock())
    return attention


async def _attempt(activity_type, number, failure=None):
    env = ActivityEnvironment()
    env.info = replace(env.info, activity_type=activity_type, attempt=number, workflow_id="wf", activity_id="act-1")
    interceptor = ActivityObservabilityInterceptor(_Next(failure))

    async def call():
        return await interceptor.execute_activity(ExecuteActivityInput(fn=None, args=[], executor=None, headers={}))

    return await env.run(call)


async def test_an_llm_step_past_its_limit_opens_the_item_and_its_success_resolves_it(store):
    assert await _attempt("agent.execute_llm_step", 9) == "done"
    store.touch.assert_awaited_once_with("stuck_retry", "wf:act-1")
    store.resolve.assert_awaited_once_with("stuck_retry", "wf:act-1")


async def test_a_failed_attempt_leaves_the_item_open(store):
    with pytest.raises(RuntimeError):
        await _attempt("agent.execute_llm_step", 9, RuntimeError("still down"))
    store.touch.assert_awaited_once()
    store.resolve.assert_not_awaited()


@pytest.mark.parametrize(
    ("activity_type", "number"),
    [
        ("agent.execute_llm_step", 8),  # at its limit, not past it
        ("node.httpRequest.v1", 10),
        ("agent.acquire_subagent_permit", 50),
        ("node.approvalGate.v1", 50),
        ("coding.pi_drive_turn", 50),
    ],
)
async def test_no_item_up_to_the_limit_or_for_a_planned_wait(store, activity_type, number):
    await _attempt(activity_type, number)
    store.touch.assert_not_awaited()
    store.resolve.assert_not_awaited()


async def test_the_retry_log_is_quiet_between_its_warnings(store):
    await _attempt("node.httpRequest.v1", 2)
    _interceptors.logger.warning.assert_called_once()
    _interceptors.logger.warning.reset_mock()
    await _attempt("node.httpRequest.v1", 3)
    await _attempt("agent.acquire_subagent_permit", 2)
    _interceptors.logger.warning.assert_not_called()
