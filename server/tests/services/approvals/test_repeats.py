"""Which node calls may run again after an attempt broke off
(services/approvals/repeats.py): the list names real node types and values
their field accepts, an unlisted type never repeats, and a field the call
leaves unset takes the default of the model the call validates against."""

from __future__ import annotations

import typing

import pytest

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals.repeats import REPEATABLE, REPEATABLE_WHEN, repeatable
from services.node_registry import get_node_class, registered_node_classes


def _values(annotation) -> set:
    if typing.get_origin(annotation) is typing.Literal:
        return set(typing.get_args(annotation))
    return set().union(*(_values(arg) for arg in typing.get_args(annotation)))


def test_the_list_names_registered_node_types():
    assert sorted((REPEATABLE | set(REPEATABLE_WHEN)) - set(registered_node_classes())) == []


@pytest.mark.parametrize("node_type", sorted(REPEATABLE_WHEN))
def test_each_listed_value_is_one_the_field_accepts(node_type):
    cls = get_node_class(node_type)
    field, values = REPEATABLE_WHEN[node_type]
    models = [cls.Params] + ([cls.tool_input_model()] if hasattr(cls, "tool_input_model") else [])
    accepted = set().union(*(_values(model.model_fields[field].annotation) for model in models if field in model.model_fields))
    assert values <= accepted, sorted(values - accepted)


@pytest.mark.parametrize(
    ("node_type", "data", "tool_call", "expected"),
    [
        ("googleGmail", {}, False, False),  # its default operation sends
        ("googleGmail", {"operation": "search"}, False, True),
        ("googleGmail", {"operation": ["search"]}, False, False),  # not a value at all
        ("httpRequest", {}, False, True),  # GET
        ("httpRequest", {"method": "POST"}, False, False),
        ("browser", {}, True, True),  # a model's call starts with a snapshot
        ("browser", {}, False, False),  # the node navigates
        ("browser", {"operation": "request_user"}, True, True),
        ("awsAction", {"operation": "call", "api_operation": "describe_vpcs"}, False, False),
        ("approvalGate", {}, False, True),
        ("whatsappSend", {"message": "hi"}, True, False),
    ],
)
def test_a_call_repeats_only_when_listed(node_type, data, tool_call, expected):
    assert repeatable(get_node_class(node_type), data, tool_call=tool_call) is expected
