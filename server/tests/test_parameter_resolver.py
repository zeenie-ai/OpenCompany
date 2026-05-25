"""Contract tests for ``services.parameter_resolver.ParameterResolver``.

Locks in the behaviour every template-using workflow depends on:
``{{nodeName.field}}`` resolution, nested-path navigation, structlog
compatibility, and type preservation for whole-value templates. The
structlog test is the important one — a stdlib ``logger.isEnabledFor``
call in this module silently crashed every dynamic-parameter execution
until commit 2026-04-24 fixed it to ``logger.is_enabled_for``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.parameter_resolver import ParameterResolver


pytestmark = pytest.mark.asyncio


def _make_resolver(outputs: dict) -> ParameterResolver:
    """Build a resolver wired to a canned {source_id: output_dict} map."""
    db = MagicMock()
    db.get_node_parameters = AsyncMock(return_value={})

    async def get_output(session_id: str, source_id: str, _output_name: str):
        return outputs.get(source_id)

    return ParameterResolver(db, get_output)


async def test_resolves_whole_value_template_and_preserves_type():
    resolver = _make_resolver({"trigger_1": {"count": 5}})
    nodes = [{"id": "trigger_1", "type": "chatTrigger", "data": {"label": "Trigger"}}]
    edges = []
    resolved = await resolver.resolve({"count": "{{trigger.count}}"}, "target", nodes, edges, "s1")
    # Whole-value template preserves native type (int, not str).
    assert resolved["count"] == 5


async def test_resolves_interpolated_template_to_string():
    resolver = _make_resolver({"trigger_1": {"name": "world"}})
    nodes = [{"id": "trigger_1", "type": "chatTrigger", "data": {"label": "Trigger"}}]
    resolved = await resolver.resolve({"greeting": "hello {{trigger.name}}!"}, "target", nodes, [], "s1")
    assert resolved["greeting"] == "hello world!"


async def test_missing_key_replaces_with_empty_string():
    resolver = _make_resolver({"trigger_1": {"text": "ok"}})
    nodes = [{"id": "trigger_1", "type": "chatTrigger", "data": {"label": "Trigger"}}]
    resolved = await resolver.resolve({"value": "{{trigger.bogus}}"}, "target", nodes, [], "s1")
    assert resolved["value"] == ""


async def test_structlog_isEnabledFor_regression():
    """Regression guard: the resolver used to call ``logger.isEnabledFor``
    which only exists on stdlib loggers; structlog's BoundLogger raises
    AttributeError. That silently broke every dynamic-parameter execution
    (node returned ``null`` / an unhelpful error envelope)."""
    resolver = _make_resolver({"trigger_1": {"text": "hi"}})
    nodes = [{"id": "trigger_1", "type": "chatTrigger", "data": {"label": "Trigger"}}]
    # Forces entry into _resolve_templates with at least one {{}} param.
    resolved = await resolver.resolve({"msg": "{{trigger.text}}"}, "target", nodes, [], "s1")
    assert resolved["msg"] == "hi"


async def test_nested_navigation_with_array_index():
    resolver = _make_resolver(
        {
            "upstream_1": {
                "messages": [{"text": "first"}, {"text": "second"}],
            }
        }
    )
    nodes = [{"id": "upstream_1", "type": "httpRequest", "data": {"label": "Upstream"}}]
    resolved = await resolver.resolve({"pick": "{{upstream.messages[1].text}}"}, "target", nodes, [], "s1")
    assert resolved["pick"] == "second"


async def test_no_template_values_pass_through_unchanged():
    resolver = _make_resolver({})
    resolved = await resolver.resolve(
        {"literal": "items.0.text", "flag": True, "n": 3},
        "target",
        [],
        [],
        "s1",
    )
    # No '{{' in value ⇒ resolver must not touch it.
    assert resolved == {"literal": "items.0.text", "flag": True, "n": 3}


async def test_nested_dict_and_list_params_recurse():
    resolver = _make_resolver({"src_1": {"name": "Alice"}})
    nodes = [{"id": "src_1", "type": "httpRequest", "data": {"label": "Src"}}]
    resolved = await resolver.resolve(
        {
            "config": {"greet": "hi {{src.name}}"},
            "people": ["{{src.name}}", "static"],
        },
        "target",
        nodes,
        [],
        "s1",
    )
    assert resolved["config"]["greet"] == "hi Alice"
    assert resolved["people"] == ["Alice", "static"]
