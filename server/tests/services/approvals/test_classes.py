"""Which calls may run unasked (services/approvals/classes.py): the table
names real node types and values their field accepts, every value a mapped
field accepts has a class, an unlisted type is send, a field the call leaves
unset takes the default of the model the call validates against, and a spec
that says a call reaches no one makes a send a write."""

from __future__ import annotations

import types
import typing

import pytest

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals.classes import CLASSES, CLASSES_WHEN, LADDER, class_of
from services.approvals.repeats import REPEATABLE, repeatable
from services.employees.apps import SIDE_EFFECTS
from services.node_registry import get_node_class, registered_node_classes
from services.plugin.approval import approval_spec
from services.plugin.trigger import TriggerNode


def _values(annotation) -> set:
    if typing.get_origin(annotation) is typing.Literal:
        return set(typing.get_args(annotation))
    return set().union(*(_values(arg) for arg in typing.get_args(annotation)))


def _accepted(node_type: str, field: str) -> set:
    """The values the field accepts: its Literal values, or the
    ``enum_values`` its schema lists (montyExecutor's capabilities)."""
    cls = get_node_class(node_type)
    models = [cls.Params] + ([cls.tool_input_model()] if hasattr(cls, "tool_input_model") else [])
    accepted: set = set()
    for model in models:
        info = model.model_fields.get(field)
        if info is not None:
            extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
            accepted |= _values(info.annotation) | set(extra.get("enum_values") or ())
    return accepted


def test_the_table_names_registered_node_types():
    assert sorted((set(CLASSES) | set(CLASSES_WHEN)) - set(registered_node_classes())) == []
    assert sorted(set(CLASSES) & set(CLASSES_WHEN)) == []


def test_each_class_is_on_the_hire_ladder():
    assert LADDER == tuple(SIDE_EFFECTS)
    used = set(CLASSES.values()) | {klass for _, values in CLASSES_WHEN.values() for klass in values.values()}
    assert used <= set(LADDER)


@pytest.mark.parametrize("node_type", sorted(CLASSES_WHEN))
def test_each_listed_value_is_one_the_field_accepts(node_type):
    field, values = CLASSES_WHEN[node_type]
    assert set(values) <= _accepted(node_type, field), sorted(set(values) - _accepted(node_type, field))


@pytest.mark.parametrize("node_type", sorted(CLASSES_WHEN))
def test_every_value_the_field_accepts_has_a_class(node_type):
    # Otherwise a new operation would be send, and run unasked under "Only
    # money and deletes" though it spends or deletes.
    field, values = CLASSES_WHEN[node_type]
    assert _accepted(node_type, field) <= set(values), sorted(_accepted(node_type, field) - set(values))


def test_a_read_may_run_again():
    # Triggers never run as a tool call, and a second run of an agent repeats
    # every tool call it made, so neither is in REPEATABLE.
    classes = registered_node_classes()
    exempt = {name for name, cls in classes.items() if issubclass(cls, TriggerNode) or cls.component_kind == "agent"}
    reads = {name for name, klass in CLASSES.items() if klass == "read"}
    assert sorted(reads - exempt - REPEATABLE) == []
    for node_type, (field, values) in CLASSES_WHEN.items():
        for value, klass in values.items():
            if klass == "read":
                assert repeatable(get_node_class(node_type), {field: value}), (node_type, value)


def test_what_a_spec_holds_today_asks():
    # Every call a spec holds today asks under the default mode. The browser
    # restricts instead of holding; its classes are checked below.
    for node_type, cls in registered_node_classes().items():
        spec = approval_spec(cls)
        if spec is None or spec.restrict_while_asking:
            continue
        if node_type in CLASSES:
            assert CLASSES[node_type] in ("send", "money"), node_type
            continue
        rule = CLASSES_WHEN.get(node_type)
        if rule is None:
            continue  # unlisted: send
        field, values = rule
        assert field == spec.operation_field, node_type
        for value in spec.operations or values:
            assert values.get(value, "send") in ("send", "money"), (node_type, value)


@pytest.mark.parametrize(
    ("node_type", "data", "tool_call", "expected"),
    [
        ("googleGmail", {}, False, "send"),  # its default operation sends
        ("googleGmail", {"operation": "search"}, False, "read"),
        ("googleGmail", {"operation": ["search", "send"]}, False, "send"),  # a list: its worst member
        ("googleGmail", {"operation": "forward"}, False, "send"),  # not a value it lists
        ("httpRequest", {}, False, "send"),  # a GET fetches a URL (D11)
        ("httpRequest", {"method": "DELETE"}, False, "money"),
        ("browser", {}, True, "read"),  # a model's call starts with a snapshot
        ("browser", {}, False, "send"),  # the node navigates
        ("browser", {"operation": "request_user"}, True, "read"),
        ("browser", {"operation": "click"}, True, "send"),  # restricted while asking, not held
        ("googleCalendar", {"operation": "create", "attendees": "a@example.com"}, False, "send"),
        ("googleCalendar", {"operation": "create", "attendees": "a@example.com", "send_updates": "none"}, False, "write"),
        ("googleCalendar", {"operation": "delete", "send_updates": "none"}, False, "money"),  # a delete stays a delete
        ("msCalendar", {"operation": "create"}, False, "write"),  # no attendees, no invites
        ("dodoPaymentsAction", {}, False, "read"),  # list_payments
        ("dodoPaymentsAction", {"operation": "call", "endpoint": "payments.list"}, False, "money"),  # any endpoint
        ("awsAction", {"operation": "call", "api_operation": "describe_vpcs"}, False, "money"),
        ("stripeAction", {"command": "customers list"}, False, "money"),
        ("twitterSend", {}, False, "send"),  # a tweet
        ("twitterSend", {"action": "delete"}, False, "money"),
        ("emailRead", {"operation": "flag"}, False, "money"),  # "Deleted" deletes
        ("whatsappBusinessMedia", {"operation": "delete"}, True, "money"),
        ("montyExecutor", {"code": "1"}, True, "read"),
        ("montyExecutor", {"capabilities": ["workspace_read", "workspace_write"]}, True, "write"),
        ("montyExecutor", {"capabilities": ["workspace_read", "http_get"]}, True, "send"),  # it may fetch a URL
        ("pythonExecutor", {"code": "1"}, True, "send"),  # D16
        ("simpleMemory", {"operation": "recall"}, True, "read"),
        ("simpleMemory", {"operation": "remember", "content": "x"}, True, "write"),
        ("agentBuilder", {}, True, "read"),  # inspect_canvas
        ("agentBuilder", {"operation": "add_tool"}, True, "write"),
        ("taskManager", {}, True, "read"),
        ("proxyConfig", {"operation": "add_provider"}, True, "send"),
        ("fileModify", {"operation": "edit"}, False, "write"),
        ("vectorStore", {}, False, "write"),  # store
        ("dataSource", {"operation": "write"}, True, "write"),
        ("mcpConnector", {}, True, "send"),
        ("mcpConnector", {"mcp_ask": False}, True, "write"),  # its Ask first is off
        ("approvalGate", {}, False, "read"),
        ("chatTrigger", {}, False, "read"),
        ("aiAgent", {"prompt": "x"}, True, "read"),
        ("whatsappSend", {"message": "hi"}, True, "send"),
    ],
)
def test_a_call_has_its_class(node_type, data, tool_call, expected):
    assert class_of(get_node_class(node_type), data, tool_call=tool_call) == expected


def test_an_unlisted_type_is_send():
    assert class_of(types.SimpleNamespace(type="brandNewNode"), {}, tool_call=True) == "send"
