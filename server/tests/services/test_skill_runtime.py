from types import SimpleNamespace

import pytest

from services.skill_runtime import (
    SkillRuntimeError,
    execute_skill_tool,
    skill_tool_info,
    validate_connected_skills,
)


class _Broadcaster:
    def __init__(self):
        self.events = []

    async def update_node_status(self, *args, **kwargs):
        self.events.append(("status", args, kwargs))

    async def broadcast_agent_capability(self, agent_node_id, **data):
        self.events.append(("capability", agent_node_id, data))


def _descriptor(instructions="custom authoritative body"):
    return {
        "node_id": "master-1_todo-skill",
        "master_skill_node_id": "master-1",
        "node_type": "masterSkill",
        "skill_name": "todo-skill",
        "description": "Manage todos",
        "label": "Todo",
        "parameters": {"instructions": instructions},
    }


def test_duplicate_connected_names_are_blocking():
    with pytest.raises(SkillRuntimeError) as exc:
        validate_connected_skills([_descriptor(), {**_descriptor(), "master_skill_node_id": "master-2"}])
    assert exc.value.code == "DUPLICATE_CONNECTED_SKILL_NAME"


def test_standard_skills_create_one_provider_neutral_tool():
    info = skill_tool_info([_descriptor()], "agent-1")
    assert info["node_type"] == "_builtin_skill"
    assert info["parameters"]["skill_descriptors"][0]["skill_name"] == "todo-skill"


def test_standard_skill_does_not_modify_agent_system_prompt(monkeypatch):
    from services.skill_prompt import build_skill_system_prompt
    import services.skill_loader as loader_module

    monkeypatch.setattr(loader_module, "get_skill_loader", lambda: SimpleNamespace(scan_skills=lambda: {}))
    prompt, personality = build_skill_system_prompt([_descriptor()])
    assert not personality
    assert prompt == ""


def test_connected_skill_metadata_is_on_tool_description():
    info = skill_tool_info([_descriptor()], "agent-1")
    description = info["parameters"]["tool_description"]
    assert "todo-skill" in description
    assert "Manage todos" in description
    assert "Load the instructions" in description


@pytest.mark.asyncio
async def test_node_phase_updates_preserve_active_skills_until_explicit_clear():
    from services.status_broadcaster import StatusBroadcaster

    broadcaster = StatusBroadcaster()
    await broadcaster.update_node_status("agent-1", "executing", {"active_skills": [{"name": "todo-skill", "state": "loaded"}]}, "1")
    await broadcaster.update_node_status("agent-1", "executing", {"phase": "invoking_llm"}, "1")
    assert broadcaster.get_node_status("agent-1")["data"]["active_skills"][0]["name"] == "todo-skill"
    await broadcaster.update_node_status("agent-1", "success", {"active_skills": []}, "1")
    assert broadcaster.get_node_status("agent-1")["data"]["active_skills"] == []


@pytest.mark.asyncio
async def test_fast_tool_name_remains_visible_until_next_agent_turn():
    from services.status_broadcaster import StatusBroadcaster

    broadcaster = StatusBroadcaster()
    await broadcaster.update_node_status("agent-1", "executing", {"phase": "executing_tool", "tool_name": "write_todos"}, "1")
    await broadcaster.update_node_status("agent-1", "executing", {"phase": "invoking_llm"}, "1")
    assert broadcaster.get_node_status("agent-1")["data"]["last_tool_name"] == "write_todos"
    await broadcaster.update_node_status("agent-1", "executing", {"phase": "initializing", "last_tool_name": None}, "1")
    assert broadcaster.get_node_status("agent-1")["data"]["last_tool_name"] is None


@pytest.mark.asyncio
async def test_failed_tool_retains_safe_failed_capability_without_public_error_text():
    from services.status_broadcaster import StatusBroadcaster

    broadcaster = StatusBroadcaster()
    await broadcaster.update_node_status(
        "agent-1",
        "executing",
        {
            "phase": "tool_completed",
            "tool_name": "write_todos",
            "tool_failed": True,
        },
        "1",
    )
    capability = broadcaster.get_node_status("agent-1")["data"]["last_capability"]
    assert capability == {"kind": "tool", "name": "write_todos", "state": "failed"}


@pytest.mark.asyncio
async def test_normal_agent_terminal_wrapper_preserves_last_skill_usage():
    from services.status_broadcaster import StatusBroadcaster

    broadcaster = StatusBroadcaster()
    await broadcaster.update_node_status(
        "normal-agent",
        "executing",
        {"active_skills": [], "last_skills": [{"name": "memory-skill", "state": "used"}]},
        "1",
    )
    # BaseNode / per-type activity terminal payload does not know about the
    # inner agent loop's skill lifecycle.
    await broadcaster.update_node_status(
        "normal-agent",
        "success",
        {"response": "done", "execution_id": "1"},
        "1",
    )
    assert broadcaster.get_node_status("normal-agent")["data"]["last_skills"] == [
        {"name": "memory-skill", "state": "used"}
    ]
    # The next turn owns an explicit reset.
    await broadcaster.update_node_status(
        "normal-agent",
        "executing",
        {"phase": "initializing", "active_skills": [], "last_skills": [], "last_tool_name": None},
        "1",
    )
    assert broadcaster.get_node_status("normal-agent")["data"]["last_skills"] == []


@pytest.mark.asyncio
async def test_capability_summary_never_leaks_between_workflows_with_same_node_id():
    from services.status_broadcaster import StatusBroadcaster

    broadcaster = StatusBroadcaster()
    await broadcaster.update_node_status(
        "shared-agent",
        "success",
        {
            "last_skills": [{"name": "workflow-a-skill", "state": "used"}],
            "last_capability": {"kind": "skill", "name": "workflow-a-skill", "state": "used"},
        },
        "workflow-a",
    )
    await broadcaster.update_node_status(
        "shared-agent",
        "success",
        {"response": "workflow b result"},
        "workflow-b",
    )

    current = broadcaster.get_node_status("shared-agent")
    assert current["workflow_id"] == "workflow-b"
    assert "last_skills" not in current["data"]
    assert "last_capability" not in current["data"]


@pytest.mark.asyncio
async def test_customized_content_wins_and_repeat_load_is_compact(monkeypatch):
    import services.skill_loader as loader_module
    import services.status_broadcaster as broadcaster_module

    skill = SimpleNamespace(
        instructions="global body",
        references={"guide.md": "one\nneedle\nthree"},
        scripts={},
    )
    monkeypatch.setattr(loader_module, "get_skill_loader", lambda: SimpleNamespace(load_skill=lambda name: skill))
    broadcaster = _Broadcaster()
    monkeypatch.setattr(broadcaster_module, "get_status_broadcaster", lambda: broadcaster)
    config = {
        "parameters": {"skill_descriptors": [_descriptor()]},
        "workflow_id": "1", "execution_id": "9", "parent_node_id": "agent-1",
        "tool_call_id": "call-9",
    }

    first = await execute_skill_tool({"action": "load", "skill_name": "todo-skill"}, config)
    second = await execute_skill_tool({"action": "load", "skill_name": "todo-skill"}, config)
    assert first["instructions"] == "custom authoritative body"
    assert first["status"] == "loaded"
    assert second == {"status": "already_loaded", "skill_name": "todo-skill", "content_hash": first["content_hash"]}
    master_statuses = [item[1][1] for item in broadcaster.events if item[0] == "status" and item[1][0] == "master-1"]
    assert "executing" in master_statuses
    assert master_statuses[-1] == "success"

    capability_events = [item[2] for item in broadcaster.events if item[0] == "capability"]
    loading_ids = [item["event_id"] for item in capability_events if item["state"] == "loading"]
    loaded_ids = [item["event_id"] for item in capability_events if item["state"] == "loaded"]
    assert len(loading_ids) == 2 and len(set(loading_ids)) == 1
    assert len(loaded_ids) == 2 and len(set(loaded_ids)) == 1
    assert loading_ids[0] != loaded_ids[0]

    found = await execute_skill_tool(
        {"action": "search_resource", "skill_name": "todo-skill", "path": "references/guide.md", "query": "needle"},
        config,
    )
    assert found["matches"] == [{"line": 2, "text": "needle"}]


@pytest.mark.asyncio
async def test_each_master_skill_receives_only_its_own_skill_activity(monkeypatch):
    import services.skill_loader as loader_module
    import services.status_broadcaster as broadcaster_module

    first = {
        **_descriptor("first instructions"),
        "skill_name": "first-skill",
        "master_skill_node_id": "master-first",
    }
    second = {
        **_descriptor("second instructions"),
        "skill_name": "second-skill",
        "master_skill_node_id": "master-second",
    }
    monkeypatch.setattr(
        loader_module,
        "get_skill_loader",
        lambda: SimpleNamespace(load_skill=lambda _name: None),
    )
    broadcaster = _Broadcaster()
    monkeypatch.setattr(
        broadcaster_module,
        "get_status_broadcaster",
        lambda: broadcaster,
    )
    config = {
        "parameters": {"skill_descriptors": [first, second]},
        "workflow_id": "1",
        "execution_id": "master-scope-test",
        "parent_node_id": "agent-1",
    }

    await execute_skill_tool(
        {"action": "load", "skill_name": "first-skill"},
        config,
    )
    await execute_skill_tool(
        {"action": "load", "skill_name": "second-skill"},
        config,
    )

    second_master_events = [
        item[1][2]["active_skills"]
        for item in broadcaster.events
        if item[0] == "status" and item[1][0] == "master-second"
    ]
    assert second_master_events
    assert {
        item["name"] for item in second_master_events[-1]
    } == {"second-skill"}


@pytest.mark.asyncio
async def test_resource_requires_successful_load(monkeypatch):
    import services.skill_loader as loader_module
    import services.status_broadcaster as broadcaster_module

    skill = SimpleNamespace(instructions="body", references={"guide.md": "text"}, scripts={})
    monkeypatch.setattr(loader_module, "get_skill_loader", lambda: SimpleNamespace(load_skill=lambda name: skill))
    monkeypatch.setattr(broadcaster_module, "get_status_broadcaster", lambda: _Broadcaster())
    config = {"parameters": {"skill_descriptors": [_descriptor("body")]}, "workflow_id": "1",
              "execution_id": "fresh", "parent_node_id": "agent-1"}
    with pytest.raises(SkillRuntimeError) as exc:
        await execute_skill_tool({"action": "read_resource", "skill_name": "todo-skill", "path": "references/guide.md"}, config)
    assert exc.value.code == "SKILL_NOT_LOADED"
