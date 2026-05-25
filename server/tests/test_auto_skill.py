"""Test the auto-add-skill policy in services/auto_skill.evaluate.

The policy decides what (if anything) happens when a tool node is
connected to (or disconnected from) an AI agent's input-tools handle.
Output is always a workflow_ops batch (`{operations: [...]}`); the
three meaningful outcomes are:

  - empty batch (event is irrelevant)
  - one set_node_parameters op (Master Skill exists; toggle the skill)
  - add_node + add_edge (no Master Skill; spawn one wired to the agent)

These tests stub the two external lookups -- get_skill (visuals.json
reverse map) and get_node_class (plugin registry agent check) -- so
they exercise the policy logic without touching real plugin imports.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services import auto_skill


# --- helpers -----------------------------------------------------------------


def _agent_class():
    """Stand-in for a registered AI agent plugin class."""
    return SimpleNamespace(component_kind="agent")


def _non_agent_class():
    return SimpleNamespace(component_kind="tool")


def _patch_lookups(*, skill: str = "http-request-skill", is_agent: bool = True):
    """Patch the two external lookups auto_skill.evaluate consults."""
    return [
        patch("services.auto_skill.get_skill", return_value=skill),
        patch(
            "services.auto_skill.get_node_class",
            return_value=_agent_class() if is_agent else _non_agent_class(),
        ),
    ]


def _enter(patches):
    return [p.__enter__() for p in patches]


def _exit(patches):
    for p in reversed(patches):
        p.__exit__(None, None, None)


def _evaluate(**overrides):
    """Default-args wrapper so each test only states what it cares about."""
    kwargs = {
        "action": "connect",
        "source_type": "httpRequest",
        "target_type": "aiAgent",
        "target_handle": "input-tools",
        "target_node_id": "agent-1",
        "master_skill_id": None,
        "master_skill_config": None,
    }
    kwargs.update(overrides)
    return auto_skill.evaluate(**kwargs)


# --- empty / no-op outcomes --------------------------------------------------


def test_wrong_target_handle_is_noop():
    """Connecting to input-skill (or anything other than input-tools) is irrelevant."""
    patches = _patch_lookups()
    _enter(patches)
    try:
        result = _evaluate(target_handle="input-skill")
        assert result == {"operations": []}
    finally:
        _exit(patches)


def test_non_agent_target_is_noop():
    patches = _patch_lookups(is_agent=False)
    _enter(patches)
    try:
        result = _evaluate()
        assert result == {"operations": []}
    finally:
        _exit(patches)


def test_unknown_target_node_class_is_noop():
    """get_node_class returns None when the type isn't registered -- treat as non-agent."""
    with (
        patch("services.auto_skill.get_skill", return_value="http-request-skill"),
        patch("services.auto_skill.get_node_class", return_value=None),
    ):
        result = _evaluate()
        assert result == {"operations": []}


def test_source_with_no_skill_mapping_is_noop():
    """A tool node that has no `skill` field in visuals.json is silently skipped."""
    patches = _patch_lookups(skill="")
    _enter(patches)
    try:
        result = _evaluate()
        assert result == {"operations": []}
    finally:
        _exit(patches)


def test_disconnect_with_no_master_skill_is_noop():
    """Removing a tool when no Master Skill is wired -- nothing to remove from."""
    patches = _patch_lookups()
    _enter(patches)
    try:
        result = _evaluate(action="disconnect", master_skill_id=None)
        assert result == {"operations": []}
    finally:
        _exit(patches)


def test_connect_without_target_node_id_is_noop():
    """If the frontend can't tell us the agent id, we can't anchor a new MS."""
    patches = _patch_lookups()
    _enter(patches)
    try:
        result = _evaluate(target_node_id=None, master_skill_id=None)
        assert result == {"operations": []}
    finally:
        _exit(patches)


# --- update existing Master Skill --------------------------------------------


def test_connect_with_existing_master_skill_returns_set_params():
    """An MS already wired -> a single set_node_parameters op enabling the skill."""
    patches = _patch_lookups(skill="http-request-skill")
    _enter(patches)
    try:
        result = _evaluate(master_skill_id="ms-1", master_skill_config={})
        assert len(result["operations"]) == 1
        op = result["operations"][0]
        assert op["type"] == "set_node_parameters"
        assert op["node_id"] == "ms-1"
        skills_config = op["parameters"]["skills_config"]
        assert "http-request-skill" in skills_config
        assert skills_config["http-request-skill"]["enabled"] is True
    finally:
        _exit(patches)


def test_disconnect_with_existing_master_skill_disables_skill():
    patches = _patch_lookups(skill="http-request-skill")
    _enter(patches)
    try:
        result = _evaluate(
            action="disconnect",
            master_skill_id="ms-1",
            master_skill_config={
                "http-request-skill": {
                    "enabled": True,
                    "instructions": "",
                    "isCustomized": False,
                }
            },
        )
        skills_config = result["operations"][0]["parameters"]["skills_config"]
        assert skills_config["http-request-skill"]["enabled"] is False
    finally:
        _exit(patches)


def test_existing_skill_customisation_is_preserved_on_toggle():
    """User-customised instructions must survive an enable/disable toggle."""
    patches = _patch_lookups(skill="http-request-skill")
    _enter(patches)
    try:
        result = _evaluate(
            action="disconnect",
            master_skill_id="ms-1",
            master_skill_config={
                "http-request-skill": {
                    "enabled": True,
                    "instructions": "Custom user instructions",
                    "isCustomized": True,
                }
            },
        )
        entry = result["operations"][0]["parameters"]["skills_config"]["http-request-skill"]
        assert entry["instructions"] == "Custom user instructions"
        assert entry["isCustomized"] is True
        assert entry["enabled"] is False  # only the enabled flag flipped
    finally:
        _exit(patches)


def test_other_skills_in_config_are_preserved():
    """Toggling one skill must not erase entries for other skills."""
    patches = _patch_lookups(skill="http-request-skill")
    _enter(patches)
    try:
        result = _evaluate(
            master_skill_id="ms-1",
            master_skill_config={
                "calculator-skill": {
                    "enabled": True,
                    "instructions": "",
                    "isCustomized": False,
                }
            },
        )
        skills_config = result["operations"][0]["parameters"]["skills_config"]
        assert "calculator-skill" in skills_config
        assert skills_config["calculator-skill"]["enabled"] is True
        assert skills_config["http-request-skill"]["enabled"] is True
    finally:
        _exit(patches)


# --- spawn new Master Skill --------------------------------------------------


def test_connect_with_no_master_skill_spawns_one():
    """No MS exists -> add_node + add_edge wiring it to the agent."""
    patches = _patch_lookups(skill="http-request-skill")
    _enter(patches)
    try:
        result = _evaluate(master_skill_id=None)
        ops = result["operations"]
        assert len(ops) == 2

        add_node, add_edge = ops
        assert add_node["type"] == "add_node"
        assert add_node["node_type"] == "masterSkill"
        assert add_node["label"] == "Master Skill"
        assert "http-request-skill" in add_node["parameters"]["skills_config"]
        assert add_node["parameters"]["skills_config"]["http-request-skill"]["enabled"] is True

        # Position is anchored to the agent so the frontend places it nearby.
        assert add_node["position"]["anchor_node_id"] == "agent-1"
        assert add_node["position"]["offset"]["y"] == 220

        # Edge wires the new MS into the agent's input-skill handle.
        assert add_edge["type"] == "add_edge"
        assert add_edge["source"] == {"client_ref": add_node["client_ref"]}
        assert add_edge["target"] == "agent-1"
        assert add_edge["target_handle"] == "input-skill"
        assert add_edge["source_handle"] == "output-tool"
    finally:
        _exit(patches)


def test_spawned_master_skill_uses_consistent_client_ref():
    """The add_edge must reference the same client_ref as the add_node."""
    patches = _patch_lookups()
    _enter(patches)
    try:
        result = _evaluate(master_skill_id=None)
        add_node, add_edge = result["operations"]
        assert add_edge["source"]["client_ref"] == add_node["client_ref"]
    finally:
        _exit(patches)


# --- internal helper ---------------------------------------------------------


def test_toggle_skill_creates_entry_with_defaults():
    """_toggle_skill on empty config creates a default SkillConfig entry."""
    result = auto_skill._toggle_skill(None, "calculator-skill", True)
    assert result == {
        "calculator-skill": {
            "enabled": True,
            "instructions": "",
            "isCustomized": False,
        }
    }


def test_toggle_skill_preserves_instructions_on_disable():
    config = {
        "x-skill": {"enabled": True, "instructions": "custom", "isCustomized": True},
    }
    result = auto_skill._toggle_skill(config, "x-skill", False)
    assert result["x-skill"]["enabled"] is False
    assert result["x-skill"]["instructions"] == "custom"
    assert result["x-skill"]["isCustomized"] is True


@pytest.mark.parametrize("action", ["connect", "disconnect"])
def test_irrelevant_event_returns_empty_for_either_action(action):
    """Whatever the action, a non-input-tools handle is always a no-op."""
    patches = _patch_lookups()
    _enter(patches)
    try:
        result = _evaluate(action=action, target_handle="input-main")
        assert result == {"operations": []}
    finally:
        _exit(patches)
