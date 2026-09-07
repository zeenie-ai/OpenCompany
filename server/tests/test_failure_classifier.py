"""Source-side retryability classification.

``BaseNode._execute_body`` stamps a ``retryable`` verdict into every
failure envelope from the real exception, and ``NodeExecutor`` classifies
whatever escapes the plugin the same way. The Temporal activity boundary
only reads the verdict, so this is where the rules are locked.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from services.node_executor import ExecutionResult
from services.plugin import ActionNode, NodeUserError, Operation
from services.plugin.context import NodeContext
from services.plugin.retryability import (
    NON_RETRYABLE_ENVELOPE_TYPES,
    RETRYABLE_HTTP_4XX,
    classify_retryable,
    explicit_retryable,
    retry_after_of,
)

pytestmark = pytest.mark.unit


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://example.test/resource")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


class _Params(BaseModel):
    needed: str


def _validation_error() -> ValidationError:
    try:
        _Params.model_validate({})
    except ValidationError as e:
        return e
    raise AssertionError("expected a ValidationError")


class _RetryableByAttribute(Exception):
    retryable = True


class _PermanentByAttribute(Exception):
    retryable = False


def _chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


class TestClassifierTable:
    @pytest.mark.parametrize(
        "exc, error_type, expected",
        [
            (NodeUserError("missing field"), "NodeUserError", False),
            (_validation_error(), "ValidationError", False),
            (PermissionError("denied"), "PermissionDeniedError", False),
            (None, "InvalidParametersError", False),
            (None, "OutputValidationError", False),
            (None, "Cancelled", False),
            (_http_error(400), "HTTPStatusError", False),
            (_http_error(401), "HTTPStatusError", False),
            (_http_error(403), "HTTPStatusError", False),
            (_http_error(404), "HTTPStatusError", False),
            (_http_error(422), "HTTPStatusError", False),
            (_http_error(408), "HTTPStatusError", True),
            (_http_error(425), "HTTPStatusError", True),
            (_http_error(429), "HTTPStatusError", True),
            (_http_error(500), "HTTPStatusError", True),
            (_http_error(502), "HTTPStatusError", True),
            (_http_error(503), "HTTPStatusError", True),
            (httpx.ReadTimeout("slow"), "ReadTimeout", True),
            (httpx.ConnectTimeout("slow"), "ConnectTimeout", True),
            (httpx.ConnectError("refused"), "ConnectError", True),
            (RuntimeError("boom"), "RuntimeError", True),
            (KeyError("missing"), "KeyError", True),
            (None, "Error", True),
        ],
    )
    def test_table(self, exc, error_type, expected):
        assert classify_retryable(exc, error_type=error_type) is expected

    def test_retryable_4xx_set_is_exactly_the_transient_statuses(self):
        assert RETRYABLE_HTTP_4XX == frozenset({408, 425, 429})

    def test_non_retryable_names_are_a_subset_of_the_temporal_lists(self):
        """The source-side set must never admit a name the server-side
        lists treat as retryable, or the two layers disagree."""
        from services.plugin.scaling import RetryPolicy
        from services.temporal._retry_policies import NON_RETRYABLE_ERROR_TYPES

        plugin_list = set(RetryPolicy().non_retryable_error_types)
        expected = {
            "NodeUserError",
            "ValidationError",
            "PermissionDeniedError",
            "InvalidParametersError",
            "OutputValidationError",
            "Cancelled",
        }
        assert expected <= plugin_list
        assert {"NodeUserError", "OutputValidationError"} <= set(NON_RETRYABLE_ERROR_TYPES)
        assert "NodeUserError" in NON_RETRYABLE_ENVELOPE_TYPES


class TestExplicitVerdict:
    def test_attribute_on_exception_wins_over_name(self):
        exc = NodeUserError("rate limited")
        exc.retryable = True
        assert classify_retryable(exc, error_type="NodeUserError") is True

    def test_attribute_on_cause_wins(self):
        """``services/llm/unifier.py`` raises NodeUserError from an LLMError
        that knows whether the provider failure was transient."""
        exc = _chained(NodeUserError("provider busy"), _RetryableByAttribute("429"))
        assert classify_retryable(exc, error_type="NodeUserError") is True
        exc = _chained(RuntimeError("wrapped"), _PermanentByAttribute("bad key"))
        assert classify_retryable(exc, error_type="RuntimeError") is False

    def test_direct_attribute_beats_cause_attribute(self):
        exc = _chained(_RetryableByAttribute("outer"), _PermanentByAttribute("inner"))
        assert explicit_retryable(exc) is True

    def test_http_status_on_the_cause_chain_is_honoured(self):
        exc = _chained(RuntimeError("wrapped"), _http_error(404))
        assert classify_retryable(exc, error_type="RuntimeError") is False
        exc = _chained(RuntimeError("wrapped"), _http_error(503))
        assert classify_retryable(exc, error_type="RuntimeError") is True

    def test_cause_walk_is_bounded(self):
        head = RuntimeError("0")
        current = head
        for index in range(1, 12):
            nxt = RuntimeError(str(index))
            current.__cause__ = nxt
            current = nxt
        current.__cause__ = head  # cycle
        assert classify_retryable(head, error_type="RuntimeError") is True

    def test_retry_after_from_seconds_and_timedelta(self):
        exc = RuntimeError("busy")
        exc.retry_after = 12
        assert retry_after_of(exc) == timedelta(seconds=12)
        exc = _chained(NodeUserError("busy"), RuntimeError("inner"))
        exc.__cause__.retry_after = timedelta(seconds=3)
        assert retry_after_of(exc) == timedelta(seconds=3)
        assert retry_after_of(RuntimeError("no hint")) is None


def _node(raiser, output=None):
    class _Stub(ActionNode, abstract=True):
        type = "_testClassifierNode"
        display_name = "stub"

        @Operation("run")
        async def run(self, ctx, params):
            raiser()
            return output

    return _Stub()


async def _body(node):
    return await node._execute_body(
        node_id="n1",
        parameters={},
        context=NodeContext(node_id="n1", node_type=node.type, raw={}),
        start_time=0.0,
    )


class TestExecuteBodyStampsTheVerdict:
    @pytest.mark.parametrize(
        "exc, error_type, retryable",
        [
            (NodeUserError("missing field"), "NodeUserError", False),
            (_chained(NodeUserError("busy"), _RetryableByAttribute("429")), "NodeUserError", True),
            (RuntimeError("server bug"), "RuntimeError", True),
            (_http_error(404), "HTTPStatusError", False),
            (_http_error(503), "HTTPStatusError", True),
            (PermissionError("denied"), "PermissionDeniedError", False),
        ],
    )
    async def test_operation_exception(self, exc, error_type, retryable):
        def _raise():
            raise exc

        result = await _body(_node(_raise))
        assert result["success"] is False
        assert result["error_type"] == error_type
        assert result["retryable"] is retryable

    async def test_retry_after_hint_is_stamped(self):
        cause = _RetryableByAttribute("429")
        cause.retry_after = 9.0

        def _raise():
            raise _chained(NodeUserError("busy"), cause)

        result = await _body(_node(_raise))
        assert result["retry_after_seconds"] == 9.0

    async def test_unknown_operation_is_non_retryable(self):
        """Only multi-operation nodes read ``parameters.operation``; a
        single-op node ignores it by design."""

        class _TwoOps(ActionNode, abstract=True):
            type = "_testClassifierTwoOps"
            display_name = "stub"

            @Operation("first")
            async def first(self, ctx, params):
                return {}

            @Operation("second")
            async def second(self, ctx, params):
                return {}

        node = _TwoOps()
        result = await node._execute_body(
            node_id="n1",
            parameters={"operation": "nope"},
            context=NodeContext(node_id="n1", node_type=node.type, raw={}),
            start_time=0.0,
        )
        assert result["error_type"] == "InvalidParametersError"
        assert result["retryable"] is False

    async def test_output_contract_violation_is_non_retryable(self):
        from typing import Optional

        class _Out(BaseModel):
            count: Optional[int] = None

        node = _node(lambda: None, output={"count": "not a number"})
        type(node).Output = _Out
        result = await _body(node)
        assert result["error_type"] == "OutputValidationError"
        assert result["retryable"] is False

    async def test_payload_over_the_temporal_limit_is_non_retryable(self, monkeypatch):
        """The size guard raises NodeUserError from inside ``_wrap_success``;
        it must classify like any other user-correctable failure instead of
        escaping to NodeExecutor as an untyped, retryable error."""
        import services.plugin.base as base_module

        monkeypatch.setattr(base_module, "TEMPORAL_PAYLOAD_ERROR_BYTES", 64)
        monkeypatch.setattr(base_module, "TEMPORAL_PAYLOAD_WARN_BYTES", 32)
        node = _node(lambda: None, output={"blob": "x" * 500})
        result = await _body(node)
        assert result["success"] is False
        assert result["error_type"] == "NodeUserError"
        assert result["retryable"] is False
        assert "KB" in result["error"]

    async def test_success_envelope_carries_no_verdict(self):
        result = await _body(_node(lambda: None, output={"ok": True}))
        assert result["success"] is True
        assert "retryable" not in result


class TestNodeExecutorEnvelope:
    def test_failure_result_carries_type_and_verdict(self):
        d = ExecutionResult(False, "n1", "x", error="boom", error_type="RuntimeError", retryable=True).to_dict()
        assert d["error_type"] == "RuntimeError"
        assert d["retryable"] is True

    def test_untyped_failure_omits_the_keys(self):
        d = ExecutionResult(False, "n1", "x", error="boom").to_dict()
        assert "error_type" not in d
        assert "retryable" not in d

    def test_success_result_omits_the_keys(self):
        d = ExecutionResult(True, "n1", "x", result={"a": 1}, error_type="ignored", retryable=False).to_dict()
        assert "error_type" not in d
        assert "retryable" not in d
