"""Output-contract enforcement at the plugin serialization boundary.

Regression suite for the "Object of type ReadResult is not JSON
serializable" class of bug: a plugin operation returned a dict carrying
a raw third-party dataclass, ``_wrap_success`` passed it through
unvalidated, and the SQLAlchemy JSON column's stdlib ``json.dumps``
raised at ``session.commit()`` — silently dropping node-output
persistence (``save_node_output`` swallows the error).

Two enforcement layers are locked here:

1. ``BaseNode._serialize_result`` — dict results are validated against
   the plugin's declared ``Output`` model and dumped with
   ``mode="json"`` (the semantics FastAPI applies to ``response_model``:
   validate, coerce, serialize). Contract violations become a loud
   ``OutputValidationError`` envelope at the producer instead of silent
   corruption downstream.
2. Engine-level ``json_serializer`` on ``create_async_engine`` —
   SQLAlchemy's documented extension point for every JSON column —
   backed by ``pydantic_core.to_jsonable_python`` (official
   arbitrary-object → JSON coercion; ``fallback=str`` for unknowns).
"""

from __future__ import annotations

import time
from typing import Optional

from pydantic import BaseModel, ConfigDict


def _action_stub(output_model=None):
    """Throwaway ActionNode subclass; ``abstract=True`` skips registry
    side-effects (same mechanism ToolNode itself uses)."""
    from services.plugin import ActionNode

    class _Stub(ActionNode, abstract=True):
        type = "_testOutputContract"
        display_name = "stub"

    if output_model is not None:
        _Stub.Output = output_model
    return _Stub()


def _tool_stub(output_model=None):
    from services.plugin.tool import ToolNode

    class _Stub(ToolNode, abstract=True):
        type = "_testToolOutputContract"
        display_name = "stub"

    if output_model is not None:
        _Stub.Output = output_model
    return _Stub()


class _ContentOutput(BaseModel):
    content: Optional[str] = None
    line_count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class TestActionNodeWrapSuccess:
    def test_dict_violating_declared_field_fails_loudly(self):
        """The original bug shape: a non-string object in a declared
        ``content: Optional[str]`` field. Must become an error envelope
        (plugin bug surfaced at the producer), never a success payload
        carrying a non-serializable object."""
        node = _action_stub(_ContentOutput)
        result = node._wrap_success(
            start_time=time.time(),
            result={"content": object(), "file_path": "/x.txt"},
        )
        assert result["success"] is False
        assert result["error_type"] == "OutputValidationError"

    def test_dict_with_extra_keys_passes_and_keeps_extras(self):
        """All plugin Output models use ``extra="allow"`` + Optional
        fields — well-behaved plugins returning context keys beyond the
        declared schema are unaffected by enforcement."""
        node = _action_stub(_ContentOutput)
        result = node._wrap_success(
            start_time=time.time(),
            result={"content": "hello", "file_path": "/x.txt"},
        )
        assert result["success"] is True
        assert result["result"]["content"] == "hello"
        assert result["result"]["file_path"] == "/x.txt"

    def test_basemodel_result_dumps_json_mode(self):
        """BaseModel returns must dump with ``mode="json"`` so the
        payload is JSON-compatible (datetimes -> ISO strings, etc.)."""
        import json
        from datetime import datetime, timezone

        class _Out(BaseModel):
            when: datetime
            model_config = ConfigDict(extra="allow")

        node = _action_stub(_Out)
        result = node._wrap_success(
            start_time=time.time(),
            result=_Out(when=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        )
        assert result["success"] is True
        json.dumps(result["result"])  # must not raise
        assert isinstance(result["result"]["when"], str)

    def test_dict_without_declared_output_passes_through(self):
        """Plugins with the default ``_EmptyOutput`` keep the historical
        pass-through behavior — enforcement only applies where a
        contract is declared."""
        node = _action_stub()  # inherits _EmptyOutput
        payload = {"anything": 1, "nested": {"x": [1, 2]}}
        result = node._wrap_success(start_time=time.time(), result=payload)
        assert result["success"] is True
        assert result["result"] is payload


class TestToolNodeWrapSuccess:
    def test_flat_dict_validated_against_output(self):
        node = _tool_stub(_ContentOutput)
        result = node._wrap_success(
            start_time=time.time(),
            result={"content": "hi", "extra": True},
        )
        # ToolNode contract: flat dict, no success wrapper.
        assert "success" not in result
        assert result["content"] == "hi"
        assert result["extra"] is True

    def test_violation_becomes_error_envelope(self):
        """A contract violation flips the flat-dict success shape into
        the standard error envelope — ``interpret_result`` then reports
        failure (a dict WITH ``success`` takes base-class semantics)."""
        node = _tool_stub(_ContentOutput)
        result = node._wrap_success(
            start_time=time.time(),
            result={"content": object()},
        )
        assert result["success"] is False
        assert result["error_type"] == "OutputValidationError"
        ok, _payload, error = type(node).interpret_result(result)
        assert ok is False
        assert "Output contract violation" in (error or "")


class TestEngineJsonSerializer:
    """Lock the engine-level serializer configuration. ``core.database``
    is stubbed by conftest, so the source lock reads the file from disk."""

    def test_engine_configures_pydantic_json_serializer(self):
        from pathlib import Path

        src = (Path(__file__).parent.parent / "core" / "database.py").read_text(encoding="utf-8")
        assert "json_serializer" in src, (
            "create_async_engine must set json_serializer — SQLAlchemy's "
            "documented extension point for JSON-column serialization. "
            "Without it, stdlib json.dumps raises on any non-primitive "
            "object and node-output persistence silently drops data."
        )
        assert "to_jsonable_python" in src, (
            "The serializer must coerce via pydantic_core.to_jsonable_python "
            "(official arbitrary-object -> JSON handling), not ad-hoc "
            "default=str round-trips."
        )

    def test_serializer_expression_handles_rich_payloads(self):
        """Behavior of the exact expression used by the engine: stdlib
        ``json.dumps`` over ``to_jsonable_python(..., fallback=str)``
        must serialize dataclasses, datetimes, sets, enums, and unknown
        objects without raising."""
        import enum
        import json
        from dataclasses import dataclass
        from datetime import datetime, timezone

        from pydantic_core import to_jsonable_python

        @dataclass
        class _ReadResultLike:
            error: Optional[str] = None
            file_data: Optional[dict] = None

        class _Color(enum.Enum):
            RED = "red"

        payload = {
            "result": _ReadResultLike(file_data={"content": "x", "encoding": "utf-8"}),
            "when": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "tags": {"a", "b"},
            "color": _Color.RED,
            "unknown": object(),
        }
        out = json.dumps(to_jsonable_python(payload, fallback=str))
        decoded = json.loads(out)
        assert decoded["result"]["file_data"]["content"] == "x"
        assert decoded["when"].startswith("2026-01-01")
        assert sorted(decoded["tags"]) == ["a", "b"]
        assert decoded["color"] == "red"
        assert isinstance(decoded["unknown"], str)


class TestErrorEnvelopeCarriesTheRetryVerdict:
    def test_output_violation_is_non_retryable(self):
        node = _action_stub(_ContentOutput)
        result = node._wrap_success(start_time=time.time(), result={"content": "x", "line_count": "nope"})
        assert result["success"] is False
        assert result["error_type"] == "OutputValidationError"
        assert result["retryable"] is False

    def test_tool_output_violation_is_non_retryable(self):
        node = _tool_stub(_ContentOutput)
        result = node._wrap_success(start_time=time.time(), result={"content": "x", "line_count": "nope"})
        assert result["success"] is False
        assert result["retryable"] is False
