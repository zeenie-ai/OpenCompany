"""Test workflow_ops builder helpers.

Locks the wire format that the frontend applier (lib/workflowOps.ts)
consumes -- if any of these change, the matching applyOperations
branch in TS must change too.
"""

from __future__ import annotations

import pytest

from services import workflow_ops


# --- empty -------------------------------------------------------------------


def test_empty_returns_no_operations():
    assert workflow_ops.empty() == {"operations": []}


# --- add_node ----------------------------------------------------------------


def test_add_node_minimal_omits_optional_fields():
    op = workflow_ops.add_node("ref1", "httpRequest")
    assert op == {
        "type": "add_node",
        "client_ref": "ref1",
        "node_type": "httpRequest",
        "parameters": {},
    }
    # No label/position keys when omitted -- frontend applier defaults them.
    assert "label" not in op
    assert "position" not in op


def test_add_node_with_label_and_parameters():
    op = workflow_ops.add_node(
        "ref1",
        "masterSkill",
        {"skillsConfig": {"http-request-skill": {"enabled": True}}},
        label="Master Skill",
    )
    assert op["label"] == "Master Skill"
    assert op["parameters"] == {"skillsConfig": {"http-request-skill": {"enabled": True}}}


def test_add_node_with_anchored_position():
    pos = workflow_ops.anchored("agent-1", offset_x=-60, offset_y=220)
    op = workflow_ops.add_node("ms", "masterSkill", position=pos)
    assert op["position"] == {
        "anchor_node_id": "agent-1",
        "offset": {"x": -60, "y": 220},
    }


def test_add_node_with_absolute_position():
    op = workflow_ops.add_node(
        "n1",
        "httpRequest",
        position={"x": 100, "y": 200},
    )
    assert op["position"] == {"x": 100, "y": 200}


# --- add_edge ----------------------------------------------------------------


def test_add_edge_minimal():
    op = workflow_ops.add_edge("source-1", "target-1")
    assert op == {
        "type": "add_edge",
        "source": "source-1",
        "target": "target-1",
    }
    assert "source_handle" not in op
    assert "target_handle" not in op


def test_add_edge_with_handles():
    op = workflow_ops.add_edge(
        "src",
        "tgt",
        source_handle="output-tool",
        target_handle="input-skill",
    )
    assert op["source_handle"] == "output-tool"
    assert op["target_handle"] == "input-skill"


def test_add_edge_with_client_refs():
    op = workflow_ops.add_edge({"client_ref": "ms"}, "agent-1")
    assert op["source"] == {"client_ref": "ms"}
    assert op["target"] == "agent-1"


# --- set_node_parameters -----------------------------------------------------


def test_set_node_parameters():
    op = workflow_ops.set_node_parameters("node-1", {"skillsConfig": {}})
    assert op == {
        "type": "set_node_parameters",
        "node_id": "node-1",
        "parameters": {"skillsConfig": {}},
    }


# --- delete_node / delete_edge -----------------------------------------------


def test_delete_node():
    assert workflow_ops.delete_node("n1") == {"type": "delete_node", "node_id": "n1"}


def test_delete_edge():
    assert workflow_ops.delete_edge("e1") == {"type": "delete_edge", "edge_id": "e1"}


# --- move_node ---------------------------------------------------------------


def test_move_node_with_absolute_position():
    op = workflow_ops.move_node("n1", {"x": 50, "y": 100})
    assert op == {
        "type": "move_node",
        "node_id": "n1",
        "position": {"x": 50, "y": 100},
    }


def test_move_node_with_anchored_position():
    op = workflow_ops.move_node(
        "n1",
        workflow_ops.anchored("anchor-1", offset_x=10, offset_y=20),
    )
    assert op["position"]["anchor_node_id"] == "anchor-1"


# --- replace_node ------------------------------------------------------------


def test_replace_node_defaults_preserve_edges_true():
    op = workflow_ops.replace_node("old-id", "newType", {"foo": "bar"})
    assert op == {
        "type": "replace_node",
        "node_id": "old-id",
        "node_type": "newType",
        "parameters": {"foo": "bar"},
        "preserve_edges": True,
    }


def test_replace_node_can_drop_edges():
    op = workflow_ops.replace_node("old-id", "newType", preserve_edges=False)
    assert op["preserve_edges"] is False


def test_replace_node_with_label():
    op = workflow_ops.replace_node("old-id", "newType", label="New Label")
    assert op["label"] == "New Label"


# --- anchored helper ---------------------------------------------------------


def test_anchored_default_zero_offset():
    pos = workflow_ops.anchored("anchor-1")
    assert pos == {
        "anchor_node_id": "anchor-1",
        "offset": {"x": 0, "y": 0},
    }


def test_anchored_with_fallback():
    pos = workflow_ops.anchored("anchor-1", fallback={"x": 0, "y": 0})
    assert pos["fallback"] == {"x": 0, "y": 0}


# --- batch shape -------------------------------------------------------------


def test_full_batch_round_trip():
    """A typical auto-skill batch: spawn MS + wire it to an agent."""
    batch = {
        "operations": [
            workflow_ops.add_node(
                "ms",
                "masterSkill",
                {"skillsConfig": {"http-request-skill": {"enabled": True}}},
                label="Master Skill",
                position=workflow_ops.anchored("agent-1", offset_x=-60, offset_y=220),
            ),
            workflow_ops.add_edge(
                {"client_ref": "ms"},
                "agent-1",
                source_handle="output-tool",
                target_handle="input-skill",
            ),
        ],
    }

    assert len(batch["operations"]) == 2
    add_node_op, add_edge_op = batch["operations"]

    assert add_node_op["type"] == "add_node"
    assert add_node_op["client_ref"] == "ms"
    assert add_edge_op["source"] == {"client_ref": "ms"}
    assert add_edge_op["target"] == "agent-1"


@pytest.mark.parametrize(
    "factory,kwargs,expected_type",
    [
        (workflow_ops.add_node, {"client_ref": "r", "node_type": "t"}, "add_node"),
        (workflow_ops.add_edge, {"source": "s", "target": "t"}, "add_edge"),
        (workflow_ops.set_node_parameters, {"node_id": "n", "parameters": {}}, "set_node_parameters"),
        (workflow_ops.delete_node, {"node_id": "n"}, "delete_node"),
        (workflow_ops.delete_edge, {"edge_id": "e"}, "delete_edge"),
        (workflow_ops.move_node, {"node_id": "n", "position": {"x": 0, "y": 0}}, "move_node"),
        (workflow_ops.replace_node, {"node_id": "n", "node_type": "t"}, "replace_node"),
    ],
)
def test_every_builder_sets_type_field(factory, kwargs, expected_type):
    """Each builder must stamp the discriminant `type` field correctly."""
    op = factory(**kwargs)
    assert op["type"] == expected_type
