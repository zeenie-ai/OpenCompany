"""Temporal activity failure boundary: the ``BaseNode.as_activity`` contract.

A plugin's structured failure envelope must become a typed
``ApplicationError`` (``type`` = the envelope's ``error_type``,
``non_retryable`` from its ``retryable`` verdict and the attempt cap, the
envelope itself as ``details[0]``) so the plugin RetryPolicy engages,
while success, ToolNode flat results, pre-executed and disabled nodes
keep returning normally.

The activity callable runs inside ``temporalio.testing.ActivityEnvironment``
(the SDK's documented activity harness) so ``activity.info()`` and
heartbeats are real, and every stub node is declared ``abstract=True`` so
nothing leaks into the live node registry.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from services.plugin import ActionNode
from services.plugin.scaling import RetryPolicy as PluginRetryPolicy
from services.plugin.tool import ToolNode
from services.temporal._failures import RETRY_ATTEMPTS_EXHAUSTED, RETRYABLE_TYPE_SUFFIX

pytestmark = pytest.mark.unit


class _ReadOnlyStub(ActionNode, abstract=True):
    type = "_testActivityBoundary"
    display_name = "stub"
    annotations = {"destructive": False, "readonly": True, "open_world": False}


class _MutatingStub(ActionNode, abstract=True):
    type = "_testMutatingBoundary"
    display_name = "stub"
    annotations = {"destructive": False, "readonly": False, "open_world": True}


class _DeclaredStub(ActionNode, abstract=True):
    type = "_testDeclaredBoundary"
    display_name = "stub"
    annotations = {"destructive": True}
    retry_policy = PluginRetryPolicy(maximum_attempts=5)


class _ToolStub(ToolNode, abstract=True):
    type = "_testToolActivityBoundary"
    display_name = "stub"


def _context(node_cls=_ReadOnlyStub, **overrides: Any) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "node_id": "n-1",
        "node_type": node_cls.type,
        "workflow_id": "wf-1",
        "execution_id": "exec-1",
        "node_data": {},
    }
    ctx.update(overrides)
    return ctx


def _failure(
    error: str = "boom",
    error_type: str = "RuntimeError",
    retryable: Optional[bool] = None,
    **extra: Any,
) -> Dict[str, Any]:
    envelope: Dict[str, Any] = {
        "success": False,
        "error": error,
        "error_type": error_type,
        "execution_time": 0.01,
        "timestamp": "2026-09-07T00:00:00+00:00",
    }
    if retryable is not None:
        envelope["retryable"] = retryable
    envelope.update(extra)
    return envelope


def _env(
    *,
    attempt: int = 1,
    policy: Optional[RetryPolicy] = RetryPolicy(maximum_attempts=3),
    activity_id: str = "n-1",
    run_id: str = "run-1",
    heartbeats: Optional[List[Any]] = None,
) -> ActivityEnvironment:
    env = ActivityEnvironment()
    env.info = dataclasses.replace(
        ActivityEnvironment.default_info(),
        attempt=attempt,
        retry_policy=policy,
        activity_id=activity_id,
        workflow_run_id=run_id,
    )
    if heartbeats is not None:
        env.on_heartbeat = lambda *args: heartbeats.append(args[0] if args else None)
    return env


def _statuses(broadcaster) -> List[Tuple[str, Dict[str, Any]]]:
    return [(call.args[1], call.args[2]) for call in broadcaster.update_node_status.await_args_list]


async def _run(node_cls, env: ActivityEnvironment, ctx: Optional[Dict[str, Any]] = None):
    return await env.run(node_cls.as_activity(), ctx or _context(node_cls))


@pytest.fixture
def broadcaster():
    b = MagicMock()
    b.update_node_status = AsyncMock()
    b.update_node_output = AsyncMock()
    return b


@pytest.fixture
def execute_node():
    return AsyncMock(return_value={"success": True, "result": {"output": "hello"}})


@pytest.fixture
def settings():
    return SimpleNamespace(temporal_plugin_failure_retries=True)


@pytest.fixture
def wired(broadcaster, execute_node, settings):
    """Wire the DI container and broadcaster the activity resolves at call time."""
    container = SimpleNamespace(
        workflow_service=lambda: SimpleNamespace(execute_node=execute_node),
        settings=lambda: settings,
    )
    with (
        patch("core.container.container", container),
        patch("services.status_broadcaster.get_status_broadcaster", return_value=broadcaster),
    ):
        yield


def test_stubs_do_not_pollute_the_registry():
    """``abstract=True`` keeps the stubs out of the live registry; PR #131's
    first version registered them and broke test_node_spec invariants."""
    from services.node_registry import registered_node_classes

    leaked = {t for t in registered_node_classes() if t.startswith("_test")}
    assert not leaked, leaked


class TestSuccessPath:
    async def test_success_envelope_returned_unchanged(self, wired, broadcaster, execute_node):
        heartbeats: List[Any] = []
        result = await _run(_ReadOnlyStub, _env(heartbeats=heartbeats))

        assert result["success"] is True
        assert result["result"] == {"output": "hello"}
        assert result["node_id"] == "n-1"
        statuses = _statuses(broadcaster)
        assert [s for s, _ in statuses] == ["executing", "success"]
        assert statuses[0][1]["attempt"] == 1
        assert statuses[0][1]["max_attempts"] == 3
        broadcaster.update_node_output.assert_awaited_once()
        assert "Executing _testActivityBoundary: n-1" in heartbeats
        assert "Node n-1 completed" in heartbeats

    async def test_tool_flat_dict_is_success(self, wired, broadcaster, execute_node):
        execute_node.return_value = {"todos": []}
        result = await _run(_ToolStub, _env())
        assert result["todos"] == []
        assert [s for s, _ in _statuses(broadcaster)] == ["executing", "success"]

    async def test_tool_flat_error_dict_is_still_returned(self, wired, broadcaster, execute_node):
        """``execute_as_tool`` hands the model ``{"error": ...}`` without a
        ``success`` key; that is content for the LLM, never a raise."""
        execute_node.return_value = {"error": "bad argument", "error_type": "ValidationError"}
        result = await _run(_ToolStub, _env())
        assert result["error"] == "bad argument"

    async def test_pre_executed_passthrough(self, wired, broadcaster, execute_node):
        result = await _run(_ReadOnlyStub, _env(), _context(pre_executed=True, trigger_output={"event": 1}))
        assert result["pre_executed"] is True
        assert result["result"] == {"event": 1}
        execute_node.assert_not_awaited()

    async def test_disabled_skips(self, wired, broadcaster, execute_node):
        result = await _run(_ReadOnlyStub, _env(), _context(node_data={"disabled": True}))
        assert result["skipped"] is True
        execute_node.assert_not_awaited()


class TestFailureRaisesTypedApplicationError:
    async def test_node_user_error_is_non_retryable(self, wired, broadcaster, execute_node):
        execute_node.return_value = _failure("missing field", "NodeUserError", retryable=False)

        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env())

        err = excinfo.value
        assert err.type == "NodeUserError"
        assert err.non_retryable is True
        assert err.message == "missing field"
        envelope = err.details[0]
        assert envelope["error"] == "missing field"
        assert envelope["error_type"] == "NodeUserError"
        assert envelope["node_id"] == "n-1"
        assert envelope["execution_id"] == "exec-1"

    async def test_retryable_failure_below_cap_is_retryable(self, wired, broadcaster, execute_node):
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)

        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(attempt=1))

        assert excinfo.value.type == "HTTPStatusError"
        assert excinfo.value.non_retryable is False

    async def test_retryable_failure_on_last_attempt_is_final(self, wired, broadcaster, execute_node):
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)

        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(attempt=3))

        assert excinfo.value.non_retryable is True

    async def test_missing_retryable_key_is_classified_by_name(self, wired, broadcaster, execute_node):
        """A handler that bypassed ``_execute_body`` stamps no verdict; the
        boundary falls back to the error_type name lists."""
        execute_node.return_value = _failure("boom", "RuntimeError")
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(attempt=1))
        assert excinfo.value.non_retryable is False

        execute_node.return_value = _failure("bad output", "OutputValidationError")
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(attempt=1))
        assert excinfo.value.non_retryable is True

    async def test_retryable_verdict_on_non_retryable_name_changes_the_type(
        self, wired, broadcaster, execute_node
    ):
        """Temporal vetoes by ``type`` name before it looks at ``non_retryable``;
        a NodeUserError wrapping a rate-limited provider must not carry the
        bare name or the server's list would stop the retry."""
        execute_node.return_value = _failure("rate limited", "NodeUserError", retryable=True)

        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(attempt=1))

        assert excinfo.value.type == f"NodeUserError{RETRYABLE_TYPE_SUFFIX}"
        assert excinfo.value.non_retryable is False
        assert excinfo.value.details[0]["error_type"] == "NodeUserError"

    async def test_retry_after_hint_becomes_next_retry_delay(self, wired, broadcaster, execute_node):
        execute_node.return_value = _failure(
            "rate limited", "HTTPStatusError", retryable=True, retry_after_seconds=7.5
        )
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(attempt=1))
        assert excinfo.value.next_retry_delay.total_seconds() == 7.5

    async def test_flag_off_returns_the_envelope(self, wired, broadcaster, execute_node, settings):
        settings.temporal_plugin_failure_retries = False
        execute_node.return_value = _failure("missing field", "NodeUserError", retryable=False)

        result = await _run(_ReadOnlyStub, _env())

        assert result["success"] is False
        assert result["error"] == "missing field"
        assert [s for s, _ in _statuses(broadcaster)] == ["executing", "error"]


class TestBroadcastContract:
    async def test_final_failure_broadcasts_error_once_with_plugin_message(
        self, wired, broadcaster, execute_node
    ):
        execute_node.return_value = _failure("missing field", "NodeUserError", retryable=False)
        with pytest.raises(ApplicationError):
            await _run(_ReadOnlyStub, _env())

        statuses = _statuses(broadcaster)
        errors = [data for status, data in statuses if status == "error"]
        assert len(errors) == 1, "the boundary must not double-broadcast a structured failure"
        assert errors[0]["error"] == "missing field"
        assert errors[0]["attempt"] == 1
        assert errors[0]["max_attempts"] == 3

    async def test_non_final_failure_keeps_the_node_executing(self, wired, broadcaster, execute_node):
        """An attempt Temporal will retry must not flash the node red; the
        failure rides the existing ``executing`` status as ``last_error``."""
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)
        with pytest.raises(ApplicationError):
            await _run(_ReadOnlyStub, _env(attempt=2))

        statuses = _statuses(broadcaster)
        assert [s for s, _ in statuses] == ["executing", "executing"]
        assert statuses[1][1]["last_error"] == "upstream 503"
        assert statuses[1][1]["attempt"] == 2
        assert statuses[1][1]["max_attempts"] == 3

    async def test_executing_broadcast_max_attempts_falls_back_to_effective_policy(
        self, wired, broadcaster, execute_node
    ):
        await _run(_ReadOnlyStub, _env(policy=None))
        assert _statuses(broadcaster)[0][1]["max_attempts"] == 3
        broadcaster.update_node_status.reset_mock()
        await _run(_MutatingStub, _env(policy=None), _context(_MutatingStub))
        assert _statuses(broadcaster)[0][1]["max_attempts"] == 1

    async def test_infra_exception_broadcasts_once_and_reraises_unchanged(
        self, wired, broadcaster, execute_node
    ):
        infra = RuntimeError("connection pool exhausted")
        execute_node.side_effect = infra
        with pytest.raises(RuntimeError) as excinfo:
            await _run(_ReadOnlyStub, _env())

        assert excinfo.value is infra
        errors = [data for status, data in _statuses(broadcaster) if status == "error"]
        assert len(errors) == 1
        assert errors[0]["error"] == "RuntimeError: connection pool exhausted"

    async def test_infra_application_error_reraised_identically(self, wired, broadcaster, execute_node):
        infra = ApplicationError("database connection lost", type="InfrastructureDatabaseError")
        execute_node.side_effect = infra
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env())

        assert excinfo.value is infra
        errors = [data for status, data in _statuses(broadcaster) if status == "error"]
        assert len(errors) == 1
        assert "database connection lost" in errors[0]["error"]


class TestSelfCap:
    async def test_unbounded_policy_at_effective_cap_is_final(self, wired, broadcaster, execute_node):
        """A tool activity scheduled before this change carries no policy
        (Temporal default: unlimited). The plugin cap still ends it."""
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(policy=None, attempt=3))
        assert excinfo.value.non_retryable is True

    async def test_unbounded_policy_below_cap_stays_retryable(self, wired, broadcaster, execute_node):
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(policy=None, attempt=2))
        assert excinfo.value.non_retryable is False

    async def test_maximum_attempts_zero_is_treated_as_unbounded(self, wired, broadcaster, execute_node):
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(policy=RetryPolicy(maximum_attempts=0), attempt=3))
        assert excinfo.value.non_retryable is True

    async def test_scheduled_policy_wider_than_plugin_cap_is_bounded_by_the_plugin(
        self, wired, broadcaster, execute_node
    ):
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_ReadOnlyStub, _env(policy=RetryPolicy(maximum_attempts=5), attempt=3))
        assert excinfo.value.non_retryable is True

    async def test_mutating_node_is_final_on_its_first_attempt(self, wired, broadcaster, execute_node):
        """Pre-patch MachinaWorkflow histories scheduled mutating nodes with
        three attempts; the plugin's one-attempt cap wins regardless."""
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_MutatingStub, _env(policy=RetryPolicy(maximum_attempts=3), attempt=1), _context(_MutatingStub))
        assert excinfo.value.non_retryable is True

    async def test_class_declared_policy_wins_over_annotations(self, wired, broadcaster, execute_node):
        execute_node.return_value = _failure("upstream 503", "HTTPStatusError", retryable=True)
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_DeclaredStub, _env(policy=None, attempt=4), _context(_DeclaredStub))
        assert excinfo.value.non_retryable is False
        assert _statuses(broadcaster)[0][1]["max_attempts"] == 5

    async def test_pre_body_refusal_past_the_cap(self, wired, broadcaster, execute_node):
        """A re-dispatch after a crash or timeout must not re-run a node
        capped at one attempt; the refusal happens before any side effect."""
        with pytest.raises(ApplicationError) as excinfo:
            await _run(_MutatingStub, _env(policy=RetryPolicy(maximum_attempts=3), attempt=2), _context(_MutatingStub))

        assert excinfo.value.type == RETRY_ATTEMPTS_EXHAUSTED
        assert excinfo.value.non_retryable is True
        execute_node.assert_not_awaited()
        assert [s for s, _ in _statuses(broadcaster)] == ["error"]

    async def test_pre_body_refusal_is_off_with_the_flag(self, wired, broadcaster, execute_node, settings):
        settings.temporal_plugin_failure_retries = False
        result = await _run(_MutatingStub, _env(attempt=2), _context(_MutatingStub))
        assert result["success"] is True
        execute_node.assert_awaited_once()


class TestContextForwarding:
    async def test_attempt_and_idempotency_key_reach_extras(self, wired, broadcaster, execute_node):
        await _run(_ReadOnlyStub, _env(attempt=2, activity_id="n-1", run_id="run-1"))

        extras = execute_node.await_args.kwargs["extras"]
        assert extras["activity_attempt"] == 2
        assert extras["activity_idempotency_key"] == "run-1-n-1"

    async def test_existing_extras_keys_still_forwarded(self, wired, broadcaster, execute_node):
        await _run(
            _ReadOnlyStub,
            _env(),
            _context(tool_args={"query": "x"}, tool_call_id="call-1", root_execution_id="root-1"),
        )
        extras = execute_node.await_args.kwargs["extras"]
        assert extras["tool_args"] == {"query": "x"}
        assert extras["tool_call_id"] == "call-1"
        assert extras["root_execution_id"] == "root-1"

    async def test_node_context_exposes_attempt_and_key(self):
        from services.plugin.context import NodeContext

        ctx = NodeContext.from_legacy(
            "n-1",
            "_testActivityBoundary",
            {"activity_attempt": 3, "activity_idempotency_key": "run-1-n-1"},
        )
        assert ctx.attempt == 3
        assert ctx.idempotency_key == "run-1-n-1"
        bare = NodeContext.from_legacy("n-1", "_testActivityBoundary", {})
        assert bare.attempt == 1
        assert bare.idempotency_key is None
