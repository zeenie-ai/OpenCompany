"""A node call that failed for a reason that clears by itself runs again.

Perplexity Search met it: two or three Web Agent runs searching within the
same second were refused with HTTP 429 by a per-second limit.
``BaseNode._execute_body`` turns every exception into a failure envelope, so
the node's activity completed with that failure, the plugin's retry policy
never ran, and the refusal went straight back to the model.

Now the body reports such a failure (an HTTP 408, 429 or 5xx that the shared
provider classifier, ``LLMError.from_exception``, marks retryable) to the
activity wrapper, in a dict the wrapper puts in the context under
``TRANSIENT_FAILURES_KEY``. The envelope, and what the model reads, stay as
they were. The wrapper then fails the attempt with a benign, retryable
``ApplicationError`` when Temporal would run it again: the call is safe to
repeat and is not a send (RFC-0004 Q8 and Q9), the policy has an attempt
left, and the wait the provider asked for fits the policy's maximum interval
and the schedule-to-close budget. Otherwise, the last attempt included, the
result stands as before. An attempt that will run again ends no chat step,
records nothing and shows no error.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from pydantic import BaseModel

import nodes  # noqa: F401 -- populate plugin registry

from services.chat import steps as chat_steps
from services.node_registry import get_node_class
from services.plugin import NodeContext, Operation, ToolNode
from services.plugin.base import TRANSIENT_FAILURES_KEY
from tests.nodes._mocks import patched_container, patched_pricing

URL = "https://api.perplexity.ai/chat/completions"
NODE_ID = "wf:perplexitySearch:1"

RATE_LIMITED = (
    "Client error '429 Too Many Requests' for url 'https://api.perplexity.ai/chat/completions'\n"
    "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/429"
)
#: The envelope the node body returns for that refusal (BaseNode._wrap_error).
FAILURE = {
    "success": False,
    "error": RATE_LIMITED,
    "error_type": "HTTPStatusError",
    "execution_time": 0.2,
    "timestamp": "2026-11-05T10:00:00+00:00",
}
#: What the body reports for it: the provider asked for a 2 s wait.
REPORT = {"error_type": "HTTPStatusError", "retry_after": 2.0}


def _without_timing(envelope: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in envelope.items() if key not in ("execution_time", "timestamp")}


# ---------------------------------------------------------------------------
# The body: which failures are reported, and the envelope stays as it was
# ---------------------------------------------------------------------------


async def _search(harness, monkeypatch, *, reports=None):
    """One run of the real Perplexity node against the mocked endpoint, with a
    logger of its own. ``reports`` is where the activity wrapper collects a
    transient failure; without it the call is one made in process."""
    logger = MagicMock()
    monkeypatch.setattr("services.plugin.base.logger", logger)
    extra = None if reports is None else {TRANSIENT_FAILURES_KEY: reports}
    context = harness.build_context(execution_id="ex-1", extra=extra)
    with patched_container(auth_api_keys={"perplexity": "tk"}), patched_pricing():
        result = await harness.execute("perplexitySearch", {"query": "x"}, node_id=NODE_ID, context=context)
    return result, logger


@respx.mock
async def test_a_rate_limit_is_reported_and_the_envelope_is_unchanged(harness, monkeypatch):
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "3"}, text="slow down"))
    reports: Dict[str, Any] = {}

    result, logger = await _search(harness, monkeypatch, reports=reports)
    in_process, _ = await _search(harness, monkeypatch)

    harness.assert_envelope(result, success=False)
    # The error the run and the model see is the one they saw before.
    assert result["error_type"] == "HTTPStatusError"
    assert "429" in result["error"]
    for key in ("retryable", "retry_after", TRANSIENT_FAILURES_KEY):
        assert key not in result
    assert _without_timing(result) == _without_timing(in_process)
    # The report says how it failed and how long the provider asked to wait.
    assert reports == {NODE_ID: {"error_type": "HTTPStatusError", "retry_after": 3.0}}
    # An expected refusal is one WARN line, not a traceback.
    logger.warning.assert_called_once()
    logger.exception.assert_not_called()


@pytest.mark.parametrize("status", [503, 408], ids=["server error (503)", "request timeout (408)"])
@respx.mock
async def test_a_status_that_clears_by_itself_is_reported(harness, monkeypatch, status):
    respx.post(URL).mock(return_value=httpx.Response(status, text="try later"))
    reports: Dict[str, Any] = {}

    result, logger = await _search(harness, monkeypatch, reports=reports)

    assert result["success"] is False and str(status) in result["error"]
    # No Retry-After header: no wait, the retry policy's backoff applies.
    assert reports == {NODE_ID: {"error_type": "HTTPStatusError", "retry_after": None}}
    logger.warning.assert_called_once()
    logger.exception.assert_not_called()


@respx.mock
async def test_a_refused_key_is_not_reported(harness, monkeypatch):
    respx.post(URL).mock(return_value=httpx.Response(401, text="bad key"))
    reports: Dict[str, Any] = {}

    result, logger = await _search(harness, monkeypatch, reports=reports)

    assert result["success"] is False and "401" in result["error"]
    assert reports == {}
    # Not a refusal that clears by itself: it keeps its traceback.
    logger.exception.assert_called_once()
    logger.warning.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(httpx.ConnectError("refused"), id="a refused connection"),
        # The classifier marks a timeout retryable; without a status it is
        # still not reported, since the request may have run.
        pytest.param(httpx.ReadTimeout("timed out"), id="a client-side timeout"),
    ],
)
@respx.mock
async def test_a_failure_without_a_status_is_not_reported(harness, monkeypatch, failure):
    """No HTTP status: the request may have run, or fails the same way again."""
    respx.post(URL).mock(side_effect=failure)
    reports: Dict[str, Any] = {}

    result, logger = await _search(harness, monkeypatch, reports=reports)

    assert result["success"] is False and result["error_type"] == type(failure).__name__
    assert reports == {}
    logger.exception.assert_called_once()
    logger.warning.assert_not_called()


class _SdkError(Exception):
    """How a provider SDK raises: the HTTP status and the error body on the
    exception, no httpx response."""

    def __init__(self, message: str, *, status_code: int, body: Dict[str, Any]):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@pytest.mark.parametrize(
    ("code", "message", "report"),
    [
        pytest.param(
            "insufficient_quota", "You exceeded your current quota", None, id="a spent quota is not reported"
        ),
        pytest.param(
            "rate_limit_exceeded",
            "Rate limit reached for requests",
            {"error_type": "_SdkError", "retry_after": None},
            id="a rate limit is reported",
        ),
    ],
)
async def test_an_sdk_429_is_reported_only_when_it_is_a_rate_limit(harness, monkeypatch, code, message, report):
    """A 429 can mean the quota or the bill is spent, which no wait fixes;
    the SDK's error body tells the two apart."""
    cls = get_node_class("perplexitySearch")

    async def refused(self, op_spec, params_obj, context):
        raise _SdkError("Error code: 429", status_code=429, body={"error": {"code": code, "message": message}})

    monkeypatch.setattr(cls, "_run_operation", refused)
    reports: Dict[str, Any] = {}

    result, logger = await _search(harness, monkeypatch, reports=reports)

    assert result["success"] is False and result["error_type"] == "_SdkError"
    assert reports == ({} if report is None else {NODE_ID: report})
    if report is None:
        logger.exception.assert_called_once()
    else:
        logger.exception.assert_not_called()


@respx.mock
async def test_in_process_a_rate_limit_answers_as_before(harness, monkeypatch):
    """No activity gave the call a place to report: the same envelope, with
    nothing added and nothing raised."""
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "3"}, text="slow down"))

    result, logger = await _search(harness, monkeypatch)

    assert set(result) == {"success", "error", "error_type", "execution_time", "timestamp", "execution_id"}
    assert result["success"] is False
    assert result["error_type"] == "HTTPStatusError" and "429" in result["error"]
    logger.warning.assert_called_once()
    logger.exception.assert_not_called()


# ---------------------------------------------------------------------------
# The tool path: the model reads the same error, the wrapper gets the report
# ---------------------------------------------------------------------------


class _LookupParams(BaseModel):
    query: str = ""


class _LookupOutput(BaseModel):
    answer: str = ""


def _rate_limited() -> httpx.HTTPStatusError:
    """What ``response.raise_for_status()`` raises for a 429 asking for 4 s."""
    request = httpx.Request("GET", "https://lookup.example.test/search")
    response = httpx.Response(429, headers={"Retry-After": "4"}, request=request)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("a 429 response must raise")


class _RateLimitedLookup(ToolNode, abstract=True):
    type = "_testRateLimitedLookup"
    display_name = "Rate-limited lookup"
    group = ("tool",)
    description = "Test-only tool whose provider refuses with a rate limit"
    Params = _LookupParams
    Output = _LookupOutput

    @Operation("lookup")
    async def lookup(self, ctx: NodeContext, params: _LookupParams) -> _LookupOutput:
        raise _rate_limited()


async def test_a_tool_call_reports_a_rate_limit_and_the_model_reads_the_same_error(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr("services.plugin.base.logger", logger)
    reports: Dict[str, Any] = {}

    payload = await _RateLimitedLookup().execute_as_tool(
        {"query": "x"},
        {},
        NodeContext(node_id="tool-1", node_type=_RateLimitedLookup.type, raw={TRANSIENT_FAILURES_KEY: reports}),
    )

    assert payload == {"success": False, "error": str(_rate_limited()), "error_type": "HTTPStatusError"}
    assert reports == {"tool-1": {"error_type": "HTTPStatusError", "retry_after": 4.0}}
    logger.warning.assert_called_once()
    logger.exception.assert_not_called()


# ---------------------------------------------------------------------------
# The activity: when Temporal runs the call again
# ---------------------------------------------------------------------------


def _install_activity_fakes(monkeypatch, execute_result=FAILURE, *, report=REPORT, reported_for=None, saved=None):
    """The activity's surroundings. ``execute_node`` returns ``execute_result``
    and, like a node body that failed transiently, reports ``report`` in the
    dict the activity passed in its extras (for ``reported_for``, else for the
    node it runs). ``container.database()`` holds the node's ``saved``
    settings."""
    from core.container import container

    async def execute_node(**kwargs):
        if report is not None:
            kwargs["extras"][TRANSIENT_FAILURES_KEY][reported_for or kwargs["node_id"]] = dict(report)
        return dict(execute_result)

    workflow_service = MagicMock()
    workflow_service.execute_node = AsyncMock(side_effect=execute_node)
    monkeypatch.setattr(container, "workflow_service", lambda: workflow_service)

    database = MagicMock()
    database.get_node_parameters = AsyncMock(return_value=dict(saved or {}))
    monkeypatch.setattr(container, "database", lambda: database)

    broadcaster = MagicMock()
    broadcaster.update_node_status = AsyncMock()
    broadcaster.update_node_output = AsyncMock()
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)
    return SimpleNamespace(service=workflow_service, database=database, broadcaster=broadcaster)


def _environment(
    *,
    attempt=1,
    maximum_attempts=3,
    maximum_interval=timedelta(seconds=60),
    non_retryable=(),
    schedule_to_close=None,
    policy=True,
):
    from temporalio.common import RetryPolicy
    from temporalio.testing import ActivityEnvironment

    env = ActivityEnvironment()
    env.info = dataclasses.replace(
        env.info,
        attempt=attempt,
        scheduled_time=datetime.now(timezone.utc),
        schedule_to_close_timeout=schedule_to_close,
        retry_policy=RetryPolicy(
            maximum_attempts=maximum_attempts,
            maximum_interval=maximum_interval,
            non_retryable_error_types=list(non_retryable),
        )
        if policy
        else None,
    )
    return env


async def _activity(env, node_type="perplexitySearch", node_data=None):
    return await env.run(
        get_node_class(node_type).as_activity(),
        {"node_id": f"{node_type}_1", "workflow_id": "wf-1", "node_data": dict(node_data or {})},
    )


def _statuses(broadcaster) -> List[str]:
    return [call.args[1] for call in broadcaster.update_node_status.await_args_list]


def _as_returned(result: Dict[str, Any]) -> Dict[str, Any]:
    """The result without the keys the activity sets on every result."""
    return {key: value for key, value in result.items() if key not in ("node_id", "node_type", "execution_id", "timestamp")}


def _assert_stands(result, broadcaster, envelope=FAILURE):
    """The attempt completed with the body's envelope, unchanged, and the
    user sees the error."""
    assert _as_returned(result) == {key: value for key, value in envelope.items() if key != "timestamp"}
    for key in ("retryable", "retry_after", TRANSIENT_FAILURES_KEY):
        assert key not in result
    assert _statuses(broadcaster)[-1] == "error"


async def test_a_transient_failure_runs_again_after_the_providers_wait(monkeypatch):
    from temporalio.exceptions import ApplicationError, ApplicationErrorCategory

    fakes = _install_activity_fakes(monkeypatch)

    with pytest.raises(ApplicationError) as info:
        await _activity(_environment())

    assert info.value.type == "HTTPStatusError"
    assert info.value.non_retryable is False
    assert info.value.next_retry_delay == timedelta(seconds=2)
    # A planned retry is no failure: the SDK logs it at DEBUG, no failure metric.
    assert info.value.category == ApplicationErrorCategory.BENIGN
    assert info.value.message == FAILURE["error"]
    fakes.service.execute_node.assert_awaited_once()
    assert _statuses(fakes.broadcaster) == ["executing"], "a retry the user waits through is not an error"
    fakes.broadcaster.update_node_output.assert_not_awaited()


async def test_without_a_wait_the_policy_backoff_applies(monkeypatch):
    from temporalio.exceptions import ApplicationError, ApplicationErrorCategory

    fakes = _install_activity_fakes(monkeypatch, report={**REPORT, "retry_after": None})

    with pytest.raises(ApplicationError) as info:
        await _activity(_environment())

    assert info.value.next_retry_delay is None
    assert info.value.non_retryable is False
    assert info.value.category == ApplicationErrorCategory.BENIGN
    assert "error" not in _statuses(fakes.broadcaster)


async def test_a_wait_that_fits_the_schedule_to_close_budget_runs_again(monkeypatch):
    from temporalio.exceptions import ApplicationError

    fakes = _install_activity_fakes(monkeypatch)

    with pytest.raises(ApplicationError) as info:
        await _activity(_environment(schedule_to_close=timedelta(minutes=10)))

    assert info.value.next_retry_delay == timedelta(seconds=2)
    assert "error" not in _statuses(fakes.broadcaster)


@pytest.mark.parametrize(
    "environment",
    [
        pytest.param({"attempt": 3, "maximum_attempts": 3}, id="the last attempt (3 of 3)"),
        pytest.param({"maximum_interval": timedelta(seconds=1)}, id="a wait longer than the maximum interval"),
        pytest.param({"maximum_interval": None}, id="a wait with no maximum interval to bound it"),
        pytest.param({"schedule_to_close": timedelta(seconds=1)}, id="a wait past the schedule-to-close budget"),
        pytest.param({"non_retryable": ("HTTPStatusError",)}, id="a type the policy lists as non-retryable"),
        pytest.param({"policy": False}, id="no retry policy"),
    ],
)
async def test_the_result_stands_when_temporal_would_not_run_the_call_again(monkeypatch, environment):
    fakes = _install_activity_fakes(monkeypatch)

    result = await _activity(_environment(**environment))

    _assert_stands(result, fakes.broadcaster)


async def test_with_unlimited_attempts_a_late_attempt_still_runs_again(monkeypatch):
    from temporalio.exceptions import ApplicationError

    fakes = _install_activity_fakes(monkeypatch)

    with pytest.raises(ApplicationError) as info:
        await _activity(_environment(attempt=7, maximum_attempts=0))

    assert info.value.non_retryable is False
    assert "error" not in _statuses(fakes.broadcaster)


async def test_a_call_not_safe_to_repeat_answers_at_once(monkeypatch):
    """A shell command is not listed as safe to repeat
    (services/approvals/repeats.py), so its refusal goes back as it is."""
    fakes = _install_activity_fakes(monkeypatch)

    result = await _activity(_environment(), node_type="shell")

    _assert_stands(result, fakes.broadcaster)


@pytest.mark.parametrize(
    ("node_data", "saved", "runs_again"),
    [
        pytest.param({"operation": "share"}, {}, False, id="a share is a send"),
        pytest.param(
            {"label": "Drive"}, {"operation": "share"}, False, id="a share the saved settings make is a send"
        ),
        pytest.param({"operation": "list"}, {}, True, id="a list runs again"),
    ],
)
async def test_a_send_is_never_sent_again(monkeypatch, node_data, saved, runs_again):
    """Drive lists share as safe to repeat, but a share sends: an operation
    can fail after its send went out (RFC-0004 Q8). It is judged on what the
    call runs with, the node's saved settings under the call's data."""
    from temporalio.exceptions import ApplicationError

    fakes = _install_activity_fakes(monkeypatch, saved=saved)

    if runs_again:
        with pytest.raises(ApplicationError) as info:
            await _activity(_environment(), node_type="googleDrive", node_data=node_data)
        assert info.value.non_retryable is False
        assert "error" not in _statuses(fakes.broadcaster)
    else:
        result = await _activity(_environment(), node_type="googleDrive", node_data=node_data)
        _assert_stands(result, fakes.broadcaster)


async def test_a_failure_without_a_report_completes_the_attempt(monkeypatch):
    refused = {
        "success": False,
        "error": "Client error '401 Unauthorized' for url 'https://api.perplexity.ai/chat/completions'",
        "error_type": "HTTPStatusError",
        "execution_time": 0.1,
        "timestamp": "2026-11-05T10:00:00+00:00",
    }
    fakes = _install_activity_fakes(monkeypatch, refused, report=None)

    result = await _activity(_environment())

    _assert_stands(result, fakes.broadcaster, refused)


async def test_a_tool_error_payload_with_a_report_runs_again(monkeypatch):
    """A legacy flat tool error still retries from its transient report;
    retry eligibility does not depend on the public failure marker."""
    from temporalio.exceptions import ApplicationError, ApplicationErrorCategory

    fakes = _install_activity_fakes(monkeypatch, {"error": RATE_LIMITED})

    with pytest.raises(ApplicationError) as info:
        await _activity(_environment(), node_type="calculatorTool")

    assert info.value.category == ApplicationErrorCategory.BENIGN
    assert info.value.non_retryable is False
    assert info.value.message == RATE_LIMITED
    # Neither an error nor the flat payload as a success.
    assert _statuses(fakes.broadcaster) == ["executing"]
    fakes.broadcaster.update_node_output.assert_not_awaited()


async def test_a_report_for_another_node_leaves_this_result_standing(monkeypatch):
    fakes = _install_activity_fakes(monkeypatch, reported_for="wf:perplexitySearch:2")

    result = await _activity(_environment())

    _assert_stands(result, fakes.broadcaster)


@pytest.mark.parametrize("last_attempt", [pytest.param(False, id="a retry"), pytest.param(True, id="the last attempt")])
@respx.mock
async def test_the_report_travels_the_real_pipeline(harness, monkeypatch, tmp_path, last_attempt):
    """From the activity's extras through WorkflowService and NodeExecutor to
    the real node's NodeContext, and back to the activity, under the retry
    policy Perplexity Search declares."""
    from services.workflow import WorkflowService
    from temporalio.exceptions import ApplicationError, ApplicationErrorCategory

    declared = get_node_class("perplexitySearch").retry_policy
    respx.post(URL).mock(return_value=httpx.Response(429, headers={"Retry-After": "2"}, text="slow down"))
    harness.settings.workspace_base_resolved = str(tmp_path)
    service = WorkflowService(
        harness.database,
        harness.ai_service,
        harness.maps_service,
        harness.text_service,
        harness.android_service,
        MagicMock(name="CacheService"),
        harness.settings,
    )
    broadcaster = MagicMock()
    broadcaster.update_node_status = AsyncMock()
    broadcaster.update_node_output = AsyncMock()
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)
    monkeypatch.setattr("services.plugin.base.logger", MagicMock())
    env = _environment(
        attempt=declared.maximum_attempts if last_attempt else 1,
        maximum_attempts=declared.maximum_attempts,
        maximum_interval=declared.maximum_interval,
        non_retryable=declared.non_retryable_error_types,
    )

    with patched_container(auth_api_keys={"perplexity": "tk"}) as container, patched_pricing():
        container.workflow_service = MagicMock(return_value=service)
        if last_attempt:
            result = await _activity(env, node_data={"query": "x"})
        else:
            with pytest.raises(ApplicationError) as info:
                await _activity(env, node_data={"query": "x"})

    assert respx.calls.call_count == 1
    if not last_attempt:
        assert info.value.category == ApplicationErrorCategory.BENIGN
        assert info.value.type == "HTTPStatusError"
        assert info.value.next_retry_delay == timedelta(seconds=2)
        assert "429" in info.value.message
        assert _statuses(broadcaster) == ["executing"]
    else:
        # The last attempt answers exactly as before.
        assert result["success"] is False and result["error_type"] == "HTTPStatusError" and "429" in result["error"]
        for key in ("retryable", "retry_after", TRANSIENT_FAILURES_KEY):
            assert key not in result
        assert _statuses(broadcaster)[-1] == "error"


# ---------------------------------------------------------------------------
# Chat steps and approval records
# ---------------------------------------------------------------------------


STREAM = {"run_id": "r_1", "session_id": "wf", "workflow_id": "wf", "reply_message_id": "a_r_1"}
CHAT_CONTEXT = {
    "node_id": NODE_ID,
    "workflow_id": "wf",
    "node_data": {},
    "tool_call_id": "call_1",
    "chat_run_id": "r_1",
    "chat_stream": STREAM,
}


@pytest.fixture
def chat(monkeypatch):
    """A tool call of the agent answering a chat run: the events it
    publishes, the steps saved on the run, whether the owner stopped it, and
    an approval check that records how the call went."""
    import services.chat.hub as hub_module
    from services.approvals import tool_calls
    from services.chat import ledger

    fakes = _install_activity_fakes(monkeypatch)
    published: List[Dict[str, Any]] = []

    class Hub:
        def publish(self, **kwargs):
            published.append(kwargs)

    monkeypatch.setattr(hub_module, "get_chat_hub", lambda: Hub())
    saved: List[Any] = []

    async def record_step(database, run_id, step):
        saved.append((run_id, step))

    stopping = {"value": False}

    async def is_stopping(database, run_id):
        return stopping["value"]

    monkeypatch.setattr(ledger, "record_step", record_step)
    monkeypatch.setattr(ledger, "is_stopping", is_stopping)
    record = AsyncMock()

    async def check(context, node_cls):
        return tool_calls.Checked(record=record)

    monkeypatch.setattr(tool_calls, "check", check)
    return SimpleNamespace(**vars(fakes), published=published, saved=saved, stopping=stopping, record=record)


async def _chat_activity(env, node_type="perplexitySearch"):
    return await env.run(get_node_class(node_type).as_activity(), dict(CHAT_CONTEXT))


async def test_an_attempt_that_runs_again_ends_no_step_and_records_nothing(chat):
    from temporalio.exceptions import ApplicationError, ApplicationErrorCategory

    with pytest.raises(ApplicationError) as info:
        await _chat_activity(_environment())

    assert info.value.category == ApplicationErrorCategory.BENIGN
    # The step stays open for the next attempt.
    assert [event["suffix"] for event in chat.published] == ["step.started"]
    assert chat.saved == []
    chat.record.assert_not_awaited()
    assert "error" not in _statuses(chat.broadcaster)


async def test_the_last_attempt_ends_its_step_as_failed_and_records_it(chat):
    result = await _chat_activity(_environment(attempt=3, maximum_attempts=3))

    assert result["success"] is False and result["error"] == RATE_LIMITED
    finished = [event for event in chat.published if event["suffix"] == "step.finished"]
    assert [event["fields"]["state"] for event in finished] == ["failed"]
    assert [(run_id, step["state"]) for run_id, step in chat.saved] == [("r_1", "failed")]
    chat.record.assert_awaited_once_with(result)
    assert _statuses(chat.broadcaster)[-1] == "error"


@pytest.mark.parametrize(
    ("attempt", "events"),
    [
        pytest.param(1, [], id="nothing was shown yet"),
        pytest.param(
            2,
            [("step.started", None), ("step.finished", "skipped")],
            id="the step a retried attempt left open ends as skipped",
        ),
    ],
)
async def test_a_stop_during_the_backoff_ends_the_open_step(chat, attempt, events):
    chat.stopping["value"] = True

    result = await _chat_activity(_environment(attempt=attempt))

    chat.service.execute_node.assert_not_awaited()
    assert [(event["suffix"], event["fields"].get("state")) for event in chat.published] == events
    # The one step, under the id the earlier attempt announced it with (the
    # hub drops the repeated start by its key).
    assert {event["fields"]["step_id"] for event in chat.published} <= {"call_1"}
    if events:
        assert chat.published[0]["event_key"] == "step:call_1:start"
    assert [step["state"] for _, step in chat.saved] == [state for suffix, state in events if suffix == "step.finished"]
    assert result["success"] is True and result["result"] == chat_steps.STOPPED_RESULT
    assert _statuses(chat.broadcaster) == ["skipped"]


async def test_a_stop_during_the_attempt_answers_at_once(chat):
    """The owner stopped the run while the provider was refusing: no retry
    is planned, so the run ends on its next model step instead of waiting
    through the backoff."""
    fail = chat.service.execute_node.side_effect

    async def stopped_while_running(**kwargs):
        chat.stopping["value"] = True
        return await fail(**kwargs)

    chat.service.execute_node.side_effect = stopped_while_running

    result = await _chat_activity(_environment())

    assert result["success"] is False and result["error"] == RATE_LIMITED
    finished = [event for event in chat.published if event["suffix"] == "step.finished"]
    assert [event["fields"]["state"] for event in finished] == ["failed"]
    chat.record.assert_awaited_once_with(result)
    assert _statuses(chat.broadcaster)[-1] == "error"


async def test_a_later_attempt_of_a_call_not_safe_to_repeat_is_not_called_skipped(chat):
    """Attempt 2 of a shell command follows an attempt that broke off and
    may have run it. A stopped run does not change that: it is answered as
    possibly done, with a card, never as not run."""
    chat.stopping["value"] = True

    result = await _chat_activity(_environment(attempt=2), node_type="shell")

    chat.service.execute_node.assert_not_awaited()
    assert result["success"] is False and result["error_type"] == "OutcomeUnknown"
    finished = [event for event in chat.published if event["suffix"] == "step.finished"]
    assert [(event["fields"]["state"], event["fields"].get("detail")) for event in finished] == [
        ("failed", "It may already have happened")
    ]
    assert [step["state"] for _, step in chat.saved] == ["failed"]
    chat.record.assert_awaited_once()
    assert chat.record.await_args.kwargs == {"unknown": True}
