"""A provider outage stays visible until the next attempt; quota fails fast."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import ClientError, ServerError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from services.llm.protocol import LLMError, LLMErrorCategory, LLMResponse, Message, StreamEvent, message_to_wire
from services.status_broadcaster import StatusBroadcaster
from services.temporal.agent_activities import execute_llm_step


@pytest.fixture
def step(monkeypatch):
    import core.container as container_module
    import services.agent_runtime as runtime_module
    import services.status_broadcaster as status_module

    monkeypatch.setattr(container_module.container, "chat_unifier", object)
    run = AsyncMock()
    monkeypatch.setattr(runtime_module, "run_native_llm_step", run)
    broadcaster = StatusBroadcaster()
    broadcaster.broadcast = AsyncMock()
    monkeypatch.setattr(status_module, "get_status_broadcaster", lambda: broadcaster)
    payload = {
        "workflow_id": "graph-7", "node_id": "agent-1", "provider": "gemini", "model": "test-model",
        "api_key": "test", "messages": [message_to_wire(Message(role="user", content="go"))],
        "iteration": 7, "max_iterations": 100,
    }
    return ActivityEnvironment(), run, broadcaster, payload


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempt", "delay"), [(1, 5), (2, 10), (8, 300), (100, 300)])
async def test_internal_error_announces_backoff_then_clears_on_success(step, attempt, delay):
    env, run, broadcaster, payload = step
    env.info = replace(env.info, attempt=attempt)
    error = LLMError.from_exception("gemini", ServerError(500, {"error": {
        "code": 500, "status": "INTERNAL", "message": "Internal error encountered. secret",
    }}))
    run.side_effect = [error, LLMResponse(content="recovered")]
    with pytest.raises(ApplicationError) as raised:
        await env.run(execute_llm_step, payload)
    failure = raised.value
    assert failure.type == "LLMError.server"
    assert not failure.non_retryable
    assert failure.next_retry_delay == timedelta(seconds=delay)
    assert failure.__suppress_context__
    assert "secret" not in str(failure.details)
    event = broadcaster.broadcast.await_args.args[0]
    assert event["workflow_id"] == "graph-7"
    assert event["data"]["status"] == "executing"
    assert event["data"]["data"] == {
        "agent_type": "temporal", "phase": "retry_wait", "iteration": 7, "max_iterations": 100,
        "retry_message": "Gemini is temporarily unavailable.", "retry_after": delay,
        "retry_attempt": attempt + 1,
    }
    env.info = replace(env.info, attempt=attempt + 1)
    response = await env.run(execute_llm_step, payload)
    assert response["content"] == "recovered"
    status = broadcaster._status["nodes"]["agent-1"]
    assert status["data"]["phase"] == "llm_step"
    assert "retry_message" not in status["data"]
    assert run.await_count == 2
    assert all(call.kwargs["explicit_max_retries"] == 0 for call in run.await_args_list)
    assert all(call.kwargs["sdk_max_retries"] == 0 for call in run.await_args_list)


@pytest.mark.asyncio
@pytest.mark.parametrize("daily", [False, True])
async def test_google_retry_info_paces_throttles_but_cannot_override_a_daily_block(step, daily):
    env, run, broadcaster, payload = step
    quota_id = "GenerateRequestsPerDayPerProjectPerModel" if daily else "GenerateRequestsPerMinutePerProjectPerModel"
    run.side_effect = LLMError.from_exception("gemini", ClientError(429, {"error": {
        "code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded: private project",
        "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "45.5s"},
        ],
    }}))
    with pytest.raises(ApplicationError) as raised:
        await env.run(execute_llm_step, payload)
    failure = raised.value
    assert failure.non_retryable is daily
    if daily:
        assert failure.next_retry_delay is None
        assert failure.type == "LLMError.quota"
        assert failure.details[0]["requires_user_action"]
        assert "quota resets" in failure.details[0]["hint"]
        assert broadcaster.broadcast.await_count == 1  # attempt start only, never a retry promise
    else:
        assert failure.next_retry_delay == timedelta(seconds=45.5)
        assert broadcaster._status["nodes"]["agent-1"]["data"]["retry_after"] == 45.5
    assert "private project" not in str(failure.details)
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_status_failure_does_not_repeat_a_successful_model_call(step, monkeypatch):
    env, run, broadcaster, payload = step
    monkeypatch.setattr(broadcaster, "update_node_status", AsyncMock(side_effect=RuntimeError("socket closed")))
    run.return_value = LLMResponse(content="done")
    response = await env.run(execute_llm_step, payload)
    assert response["content"] == "done"
    run.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [
    pytest.param(float("nan"), id="nan"),
    pytest.param(float("inf"), id="infinity"),
    pytest.param(float("-inf"), id="negative-infinity"),
    pytest.param(0, id="zero"), pytest.param(-1, id="negative"),
    pytest.param(True, id="boolean"), pytest.param("12", id="string"),
    pytest.param(1e-9, id="below-timedelta-resolution"),
    pytest.param(315576000001, id="protobuf-duration-overflow"),
    pytest.param(1e300, id="timedelta-overflow"),
    pytest.param(10 ** 400, id="float-overflow"),
])
async def test_invalid_manual_provider_delay_preserves_failure_and_uses_backoff(step, delay):
    env, run, broadcaster, payload = step
    env.info = replace(env.info, attempt=3)
    run.side_effect = LLMError("private body", "gemini", category=LLMErrorCategory.RATE_LIMIT,
                               retryable=True, retry_after=delay)
    with pytest.raises(ApplicationError) as raised:
        await env.run(execute_llm_step, payload)
    failure = raised.value
    assert failure.type == "LLMError.rate_limit"
    assert not failure.non_retryable
    assert failure.next_retry_delay == timedelta(seconds=20)
    assert failure.details[0]["retry_after"] is None
    assert broadcaster._status["nodes"]["agent-1"]["data"]["retry_after"] == 20
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_logging_does_not_mask_failure_when_delay_is_missing(step, monkeypatch):
    from services.temporal import agent_activities

    env, run, _, payload = step
    run.side_effect = LLMError("private", "gemini", category=LLMErrorCategory.SERVER, retryable=True)
    expected = ApplicationError("Gemini is temporarily unavailable.", type="LLMError.server")
    monkeypatch.setattr(agent_activities, "_as_temporal_llm_error", lambda *_args, **_kwargs: expected)
    with pytest.raises(ApplicationError) as raised:
        await env.run(execute_llm_step, payload)
    assert raised.value is expected
    run.assert_awaited_once()


def _stream_events(monkeypatch, payload):
    from services.chat import hub, stream

    events = []
    monkeypatch.setattr(hub, "publish_run_event", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(stream, "stream_setting", lambda _name: 1)
    payload["chat_stream"] = {"run_id": "run-1", "session_id": "session-1", "workflow_id": "graph-7"}
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [False, True])
async def test_failed_partial_stream_is_withdrawn_before_any_retry(step, monkeypatch, retryable):
    env, run, _, payload = step
    events = _stream_events(monkeypatch, payload)

    async def fail(_unifier, **kwargs):
        await kwargs["on_event"](StreamEvent("text", "Unreviewed partial"))
        raise LLMError("private", "gemini", category=LLMErrorCategory.SERVER if retryable else LLMErrorCategory.QUOTA,
                       retryable=retryable)

    run.side_effect = fail
    with pytest.raises(ApplicationError) as raised:
        await env.run(execute_llm_step, payload)
    assert raised.value.non_retryable is not retryable
    assert [event["suffix"] for event in events] == ["text.started", "text.content", "custom"]
    assert events[-1]["fields"] == {"name": "opencompany.segment_discarded", "value": {"message_id": "run-1.7.1"}}
    assert events[-1]["event_key"] == "discard:run-1.7.1"
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_activity_cancellation_withdraws_partial_and_cancels_provider(step, monkeypatch):
    env, run, _, payload = step
    events = _stream_events(monkeypatch, payload)
    streamed = asyncio.Event()
    cancelled = asyncio.Event()

    async def wait(_unifier, **kwargs):
        try:
            await kwargs["on_event"](StreamEvent("text", "Unfinished partial"))
            streamed.set()
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    run.side_effect = wait
    task = asyncio.create_task(env.run(execute_llm_step, payload))
    await asyncio.wait_for(streamed.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert events[-1]["fields"] == {"name": "opencompany.segment_discarded", "value": {"message_id": "run-1.7.1"}}
    assert not any(event["suffix"] == "text.ended" for event in events)
    run.assert_awaited_once()
