"""Temporal Activity failure boundary — regression tests.

The Temporal-facing BaseNode.as_activity() boundary must translate a
structured failed-node result into a Temporal-visible typed Activity
failure. The structured result's error_type must be preserved so the
existing Temporal RetryPolicy can classify it (e.g. NodeUserError is
non-retryable, RuntimeError is retryable).

Outside the Temporal boundary (normal BaseNode.execute /
_execute_body), the structured success=False envelope must remain —
no global exception conversion.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.plugin.base import BaseNode, NodeUserError

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal concrete node for testing as_activity()
# ---------------------------------------------------------------------------


class _MinimalNode(BaseNode):
    """Registered-enough node for the activity boundary tests."""

    type = "_test_boundary_node"
    version = 1
    display_name = "Test Boundary Node"
    group = ("tool",)


def _make_activity_context(
    *,
    node_id: str = "test-node-1",
    node_type: str = "_test_boundary_node",
    workflow_id: str = "wf-test-1",
    execution_id: str = "exec-1",
    node_data: dict | None = None,
    pre_executed: bool = False,
    trigger_output: dict | None = None,
    disabled: bool = False,
    **extra,
) -> dict:
    ctx: dict = {
        "node_id": node_id,
        "node_type": node_type,
        "workflow_id": workflow_id,
        "execution_id": execution_id,
        "node_data": node_data or {},
    }
    if pre_executed:
        ctx["pre_executed"] = True
        ctx["trigger_output"] = trigger_output or {}
    if disabled:
        ctx["node_data"]["disabled"] = True
    ctx.update(extra)
    return ctx


def _success_result(*, node_id: str = "test-node-1") -> dict:
    return {
        "success": True,
        "result": {"output": "hello"},
        "node_id": node_id,
        "node_type": "_test_boundary_node",
        "execution_id": "exec-1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _error_result(
    *,
    error_type: str = "RuntimeError",
    error: str = "something broke",
    node_id: str = "test-node-1",
) -> dict:
    return {
        "success": False,
        "error_type": error_type,
        "error": error,
        "node_id": node_id,
        "node_type": "_test_boundary_node",
        "execution_id": "exec-1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_broadcaster():
    """Create a mock broadcaster with the methods as_activity() calls."""
    b = AsyncMock()
    b.update_node_status = AsyncMock()
    b.update_node_output = AsyncMock()
    return b


def _make_mock_activity():
    """Create a mock temporalio.activity module.

    The mock must:
    - Have ``defn(name=...)`` return a decorator that preserves the
      original function (same as real ``activity.defn``).
    - Have ``heartbeat(...)`` callable without error.
    - Have ``logger`` with ``.debug/info/warning/error`` methods.
    """
    mock_activity = MagicMock()

    def _defn_factory(fn=None, *, name=None, **kwargs):
        """Mimics ``temporalio.activity.defn`` — returns the function unchanged."""
        def _decorator(func):
            return func
        if fn is not None:
            return _decorator(fn)
        return _decorator

    mock_activity.defn.side_effect = _defn_factory
    mock_activity.heartbeat = MagicMock()
    mock_activity.logger = MagicMock()
    return mock_activity


def _make_patches(broadcaster, mock_ws=None):
    """Create all needed mocks and return (mock_activity, context_manager).

    The caller MUST use the returned mock_activity to build the activity fn
    inside the patch scope, because ``as_activity()`` does
    ``from temporalio import activity`` and the closure captures whatever
    ``activity`` is at call time.
    """
    mock_activity = _make_mock_activity()
    patches = [
        patch("temporalio.activity", mock_activity),
        patch(
            "services.status_broadcaster.get_status_broadcaster",
            return_value=broadcaster,
        ),
    ]
    if mock_ws is not None:
        patches.append(patch("core.container.container"))
    return mock_activity, patches


# ---------------------------------------------------------------------------
# Test 1: SUCCESS CONTROL — successful result returns normally
# ---------------------------------------------------------------------------


class TestSuccessControl:
    """A successful Activity result continues to return normally."""

    @pytest.mark.asyncio
    async def test_successful_result_returned_normally(self):
        """When workflow_service.execute_node returns a success=True result,
        as_activity must return it as-is without raising."""
        broadcaster = _mock_broadcaster()
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(return_value=_success_result())

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            result = await activity_fn(_make_activity_context())

        assert result["success"] is True
        assert result["result"]["output"] == "hello"


# ---------------------------------------------------------------------------
# Test 2: NODEUSERERROR — failed result with error_type="NodeUserError"
# ---------------------------------------------------------------------------


class TestNodeUserErrorBoundary:
    """Given a structured failed result with error_type="NodeUserError",
    BaseNode.as_activity() must NOT return it normally. It must produce a
    Temporal-visible typed failure preserving type == "NodeUserError"."""

    @pytest.mark.asyncio
    async def test_nodeusererror_raises_application_error(self):
        from temporalio.exceptions import ApplicationError

        broadcaster = _mock_broadcaster()
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(
            return_value=_error_result(error_type="NodeUserError", error="missing field")
        )

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            with pytest.raises(ApplicationError) as excinfo:
                await activity_fn(_make_activity_context())

        assert excinfo.value.type == "NodeUserError"
        assert "missing field" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_nodeusererror_is_not_returned_as_dict(self):
        """The current (broken) behavior returns the error dict normally.
        After the fix, this must NOT be a dict return — it must raise."""
        from temporalio.exceptions import ApplicationError

        broadcaster = _mock_broadcaster()
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(
            return_value=_error_result(
                error_type="NodeUserError", error="bad input"
            )
        )

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            with pytest.raises(ApplicationError):
                await activity_fn(_make_activity_context())


# ---------------------------------------------------------------------------
# Test 3: RUNTIMEERROR — failed result with error_type="RuntimeError"
# ---------------------------------------------------------------------------


class TestRuntimeErrorBoundary:
    """Given a structured failed result with error_type="RuntimeError",
    BaseNode.as_activity() must NOT return it normally. It must produce a
    Temporal-visible typed failure preserving type == "RuntimeError"."""

    @pytest.mark.asyncio
    async def test_runtimeerror_raises_application_error(self):
        from temporalio.exceptions import ApplicationError

        broadcaster = _mock_broadcaster()
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(
            return_value=_error_result(
                error_type="RuntimeError", error="unexpected failure"
            )
        )

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            with pytest.raises(ApplicationError) as excinfo:
                await activity_fn(_make_activity_context())

        assert excinfo.value.type == "RuntimeError"
        assert "unexpected failure" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_runtimeerror_is_retryable(self):
        """RuntimeError is NOT in the non_retryable_error_types list,
        so Temporal should be able to retry it."""
        from temporalio.exceptions import ApplicationError

        broadcaster = _mock_broadcaster()
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(
            return_value=_error_result(
                error_type="RuntimeError", error="transient failure"
            )
        )

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            with pytest.raises(ApplicationError) as excinfo:
                await activity_fn(_make_activity_context())

        assert excinfo.value.non_retryable is False


# ---------------------------------------------------------------------------
# Test 4: NON-TEMPORAL CONTRACT — _execute_body wraps, doesn't raise
# ---------------------------------------------------------------------------


class TestNonTemporalEnvelopeContract:
    """The underlying BaseNode execution/error-envelope behavior remains
    structured success=False and is NOT globally converted to an exception."""

    @pytest.mark.asyncio
    async def test_execute_body_wraps_nodeusererror_in_envelope(self):
        """_execute_body catches NodeUserError and returns a structured
        envelope — it does NOT re-raise."""

        class _FailOpNode(BaseNode):
            type = "_test_fail_op"
            version = 1
            display_name = "Fail Op"
            group = ("tool",)
            _abstract = True

            from services.plugin.operation import Operation

            @Operation("run")
            async def run(self, ctx, params):
                raise NodeUserError("missing required field")

        node = _FailOpNode()
        from services.plugin.context import NodeContext

        ctx = NodeContext(
            node_id="n1",
            node_type="_test_fail_op",
            raw={"workflow_id": "wf-1"},
        )
        result = await node._execute_body(
            node_id="n1",
            parameters={},
            context=ctx,
            start_time=0,
        )
        assert isinstance(result, dict)
        assert result["success"] is False
        assert result["error_type"] == "NodeUserError"
        assert "missing required field" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_body_wraps_runtimeerror_in_envelope(self):
        """_execute_body catches generic Exception and returns a structured
        envelope — it does NOT re-raise."""

        class _FailOpNode2(BaseNode):
            type = "_test_fail_op2"
            version = 1
            display_name = "Fail Op 2"
            group = ("tool",)
            _abstract = True

            from services.plugin.operation import Operation

            @Operation("run")
            async def run(self, ctx, params):
                raise RuntimeError("server bug")

        node = _FailOpNode2()
        from services.plugin.context import NodeContext

        ctx = NodeContext(
            node_id="n1",
            node_type="_test_fail_op2",
            raw={"workflow_id": "wf-1"},
        )
        result = await node._execute_body(
            node_id="n1",
            parameters={},
            context=ctx,
            start_time=0,
        )
        assert isinstance(result, dict)
        assert result["success"] is False
        assert result["error_type"] == "RuntimeError"
        assert "server bug" in result["error"]


# ---------------------------------------------------------------------------
# Test 5: BROADCAST CONTRACT — exactly one error broadcast per invocation
# ---------------------------------------------------------------------------


class TestSingleErrorBroadcast:
    """One failed Activity invocation must not emit the same error status
    twice merely because the newly-created Temporal failure passes through
    the wrapper's outer exception handling."""

    @pytest.mark.asyncio
    async def test_single_error_broadcast_on_failed_activity(self):
        from temporalio.exceptions import ApplicationError

        broadcaster = _mock_broadcaster()
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(
            return_value=_error_result(
                error_type="NodeUserError", error="bad input"
            )
        )

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            with pytest.raises(ApplicationError):
                await activity_fn(_make_activity_context())

        error_calls = [
            call
            for call in broadcaster.update_node_status.call_args_list
            if call.args[1] == "error"
        ]
        assert len(error_calls) == 1, (
            f"Expected exactly 1 error broadcast, got {len(error_calls)}. "
            "The Temporal failure boundary must not double-broadcast."
        )

    @pytest.mark.asyncio
    async def test_no_error_broadcast_on_success(self):
        broadcaster = _mock_broadcaster()
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(return_value=_success_result())

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            await activity_fn(_make_activity_context())

        error_calls = [
            call
            for call in broadcaster.update_node_status.call_args_list
            if call.args[1] == "error"
        ]
        assert len(error_calls) == 0


class TestInfrastructureApplicationError:
    """Infrastructure ApplicationError failures retain baseline handling."""

    @pytest.mark.asyncio
    async def test_infrastructure_application_error_broadcasts_once_and_reraises_unchanged(self):
        from temporalio.exceptions import ApplicationError

        broadcaster = _mock_broadcaster()
        infrastructure_error = ApplicationError(
            "database connection lost",
            type="InfrastructureDatabaseError",
        )
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(side_effect=infrastructure_error)

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            with pytest.raises(ApplicationError) as excinfo:
                await activity_fn(_make_activity_context())

        assert excinfo.value is infrastructure_error
        assert excinfo.value.type == "InfrastructureDatabaseError"

        error_calls = [
            call
            for call in broadcaster.update_node_status.call_args_list
            if call.args[1] == "error"
        ]
        assert len(error_calls) == 1
        assert error_calls[0].args[2]["error"] == (
            "ApplicationError: InfrastructureDatabaseError: database connection lost"
        )

    @pytest.mark.asyncio
    async def test_generic_infrastructure_exception_broadcasts_once_and_reraises_unchanged(self):
        broadcaster = _mock_broadcaster()
        infrastructure_error = RuntimeError("connection pool exhausted")
        mock_ws = MagicMock()
        mock_ws.execute_node = AsyncMock(side_effect=infrastructure_error)

        mock_activity, patches = _make_patches(broadcaster, mock_ws)
        with patches[0], patches[1], patches[2] as container_cm:
            container_cm.workflow_service.return_value = mock_ws
            activity_fn = _MinimalNode.as_activity()
            with pytest.raises(RuntimeError) as excinfo:
                await activity_fn(_make_activity_context())

        assert excinfo.value is infrastructure_error
        error_calls = [
            call
            for call in broadcaster.update_node_status.call_args_list
            if call.args[1] == "error"
        ]
        assert len(error_calls) == 1
        assert error_calls[0].args[2]["error"] == (
            "RuntimeError: connection pool exhausted"
        )


# ---------------------------------------------------------------------------
# Test 6: Pre-executed / disabled paths unaffected
# ---------------------------------------------------------------------------


class TestPreExecutedAndDisabledPaths:
    """Pre-executed and disabled nodes must still return normally — they
    don't go through the error-envelope path."""

    @pytest.mark.asyncio
    async def test_pre_executed_returns_normally(self):
        broadcaster = _mock_broadcaster()

        mock_activity, patches = _make_patches(broadcaster)
        with patches[0], patches[1]:
            activity_fn = _MinimalNode.as_activity()
            ctx = _make_activity_context(
                pre_executed=True,
                trigger_output={"event": "test"},
            )
            result = await activity_fn(ctx)

        assert result["success"] is True
        assert result["pre_executed"] is True

    @pytest.mark.asyncio
    async def test_disabled_returns_normally(self):
        broadcaster = _mock_broadcaster()

        mock_activity, patches = _make_patches(broadcaster)
        with patches[0], patches[1]:
            activity_fn = _MinimalNode.as_activity()
            ctx = _make_activity_context(disabled=True)
            result = await activity_fn(ctx)

        assert result["success"] is True
        assert result["skipped"] is True
