"""agent.compact_context fails and waits like an LLM step (1b.27)."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError, ApplicationErrorCategory
from temporalio.testing import ActivityEnvironment

from services.llm.protocol import LLMError, LLMErrorCategory, Message, message_to_wire
from services.temporal import agent_activities
from services.temporal.agent_activities import compact_context

_PAYLOAD = {
    "node_id": "agent-1",
    "provider": "openai",
    "model": "test-model",
    "messages": [message_to_wire(Message(role="user", content="go"))],
}


@pytest.fixture
def summarizer(monkeypatch):
    service = SimpleNamespace(compact_context=AsyncMock())
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: service)
    monkeypatch.setattr(agent_activities, "_resolve_activity_api_key", AsyncMock(return_value="owner-key"))
    return service.compact_context


async def _attempt(number):
    env = ActivityEnvironment()
    env.info = replace(env.info, attempt=number)
    return await env.run(compact_context, _PAYLOAD)


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempt", "final"), [(1, False), (2, False), (3, True), (9, True)])
async def test_an_empty_summary_stops_the_run_at_the_third_attempt(summarizer, attempt, final):
    summarizer.return_value = {"success": False, "error": "empty summary", "usage": {}}
    with pytest.raises(ApplicationError) as raised:
        await _attempt(attempt)
    assert raised.value.type == "CompactionFailed"
    assert raised.value.non_retryable is final


@pytest.mark.asyncio
async def test_a_rate_limit_waits_like_an_llm_step(summarizer):
    summarizer.side_effect = LLMError("private body", "openai", category=LLMErrorCategory.RATE_LIMIT, retryable=True)
    with pytest.raises(ApplicationError) as raised:
        await _attempt(4)
    failure = raised.value
    assert failure.type == "LLMError.rate_limit" and not failure.non_retryable
    assert failure.category == ApplicationErrorCategory.BENIGN
    assert failure.next_retry_delay == timedelta(seconds=40)  # the step's backoff at attempt 4
    assert summarizer.await_args.kwargs["raise_llm_errors"] is True


@pytest.mark.asyncio
async def test_a_missing_service_stays_retryable(monkeypatch):
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: None)
    with pytest.raises(ApplicationError) as raised:
        await _attempt(5)
    assert raised.value.type == "CompactionFailed" and not raised.value.non_retryable
