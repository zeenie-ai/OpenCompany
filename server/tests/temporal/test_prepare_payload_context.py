"""``agent.prepare_payload``: only a legacy memory descriptor names a node for
``agent.persist_turn``. A Context descriptor carries a ``node_id`` too, the
Context node's own; taking it wrote legacy markdown turns into the Context
node's parameter row on every turn of a Context-connected agent.

Collaborators are patched by string path, as in
test_prepare_payload_pressure.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.temporal.agent_activities import prepare_agent_payload

pytestmark = pytest.mark.unit

_CONTEXT = {"node_id": "7:aiAgent:2", "node_type": "aiAgent", "workflow_id": "7", "session_id": "7", "generation": 3}


@pytest.fixture
def descriptor(monkeypatch):
    """The descriptor the edge walk returns; tests set ``value``."""
    state = SimpleNamespace(value=None)
    database = SimpleNamespace(
        get_node_parameters=AsyncMock(return_value={"provider": "openai", "model": "test-model", "api_key": "sk-test", "prompt": "hello"}),
        get_user_settings=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "core.container.container",
        SimpleNamespace(
            database=lambda: database,
            auth_service=lambda: SimpleNamespace(get_api_key=AsyncMock(return_value=None)),
            ai_service=lambda: SimpleNamespace(),
            workflow_service=lambda: SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(
        "core.config.Settings",
        lambda: SimpleNamespace(max_concurrent_subagents=3, max_delegation_depth=2, agent_recursion_limit=200, tool_result_max_chars=100_000),
    )
    monkeypatch.setattr("services.llm.config.is_model_valid_for_provider", lambda *_a: True)
    monkeypatch.setattr("services.llm.config.resolve_max_tokens", lambda *_a: 1_000)
    monkeypatch.setattr("services.llm.config.resolve_temperature", lambda *_a: 0.2)

    async def connections(*_args, **_kwargs):
        return state.value, [], [], {}, None

    monkeypatch.setattr("services.plugin.edge_walker.collect_agent_connections", connections)
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: None)
    monkeypatch.setattr("services.agent_context.load_conversation", AsyncMock(return_value=[]))
    return state


@pytest.mark.asyncio
async def test_a_context_descriptor_names_no_memory_node(descriptor):
    descriptor.value = {
        "kind": "context",
        "node_id": "7:context:2",
        "context_node_id": "7:context:2",
        "workflow_id": "7",
        "generation": 3,
    }

    payload = await prepare_agent_payload(dict(_CONTEXT))

    assert payload["memory_node_id"] == ""
    assert payload["memory_content"] == ""
    # The descriptor itself still travels, and keys the conversation.
    assert payload["context_descriptor"]["node_id"] == "7:context:2"
    assert payload["conversation_key"] == {"workflow_id": "7", "generation": 3, "agent_node_id": "7:aiAgent:2"}


@pytest.mark.asyncio
async def test_a_legacy_memory_descriptor_still_names_its_node(descriptor):
    descriptor.value = {"node_id": "7:simpleMemory:1", "memory_content": "# Earlier", "window_size": 4}

    payload = await prepare_agent_payload(dict(_CONTEXT))

    assert (payload["memory_node_id"], payload["memory_content"], payload["memory_window_size"]) == ("7:simpleMemory:1", "# Earlier", 4)
    assert payload["conversation_key"] is None


@pytest.fixture(autouse=True)
def runtime_access_stub(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("services.employees.permissions.assert_runtime_access", AsyncMock())


async def test_parameter_snapshot_keeps_original_mission_and_model(descriptor):
    descriptor.value = None
    snapshot = {_CONTEXT["node_id"]: {"provider": "openai", "model": "test-model", "api_key": "sk-test", "prompt": "Original admitted mission"}}
    result = await prepare_agent_payload({**_CONTEXT, "parameter_snapshot": snapshot})
    assert result["user_prompt"] == "Original admitted mission"
    assert result["parameter_snapshot"] == snapshot


async def test_direct_workspace_prompt_wins_and_never_loads_deployed_conversation(descriptor, monkeypatch):
    import core.container as container_module
    database = container_module.container.database()
    database.get_node_parameters.return_value.update(prompt="Saved chat template", system_message="Keep Browser instructions")
    descriptor.value = {"kind": "context", "node_id": "context", "workflow_id": "7", "generation": 3}
    load = AsyncMock(side_effect=AssertionError("Direct tasks cannot load deployed conversation"))
    monkeypatch.setattr("services.agent_context.load_conversation", load)
    result = await prepare_agent_payload({**_CONTEXT, "generation": 0,
        "native_workspace_version": 1, "workspace_task_prompt": "Direct task", "node_data": {"prompt": "ignored"}})
    assert result["user_prompt"] == "Direct task"
    assert result["system_message"] == "Keep Browser instructions"
    assert result["conversation_key"] is None and result["memory_node_id"] == ""
    assert "sk-test" not in repr(result)
    load.assert_not_awaited()


@pytest.mark.parametrize("native_workspace", [False, True])
async def test_browser_delegation_preserves_role_and_records_owner_policy(descriptor, monkeypatch, native_workspace):
    import core.container as container_module
    container = container_module.container
    database = container.database()
    database.get_node_parameters.return_value.update(system_message="Keep Browser instructions")
    tool = SimpleNamespace(name="browser", description="browser", args_schema={"type": "object"})
    monkeypatch.setattr(container, "ai_service", lambda: SimpleNamespace(_build_tool_from_node=AsyncMock(return_value=(tool, {}))))
    async def connections(*args, **kwargs):
        return None, [], [{"node_id": "browser", "node_type": "browser", "parameters": {"password": "CANARY"}}], {}, None
    monkeypatch.setattr("services.plugin.edge_walker.collect_agent_connections", connections)
    binding = {"profile_id": "profile", "owner_id": "owner-a", "task_queue": "owner-a-queue"}
    result = await prepare_agent_payload({**_CONTEXT, "node_type": "browser_agent", "user_id": "owner",
        **({"native_workspace_version": 1, "generation": 0} if native_workspace else {}),
        "browser_bindings": {"browser": binding}, "invocation": {"task": "Read issues", "context": "Repository A"}})
    assert result["system_message"] == "Keep Browser instructions"
    assert result["user_prompt"] == "Read issues\n\nRepository A"
    assert result["tools"][0]["activity_policy"]["task_queue"] == "owner-a-queue"
    assert result["tools"][0]["activity_policy"]["owner_routing"] is True
    assert "CANARY" not in repr(result) and "sk-test" not in repr(result)


async def test_browser_delegation_without_saved_role_uses_browser_default(descriptor, monkeypatch):
    import core.container as container_module
    from services.browser_agent_recipe import BROWSER_AGENT_ROLE
    monkeypatch.setattr("services.temporal.agent_activities._freeze_browser_tools", AsyncMock(return_value=({}, 0)))
    async def connections(*args, **kwargs):
        return None, [], [{"node_id": "browser", "node_type": "browser", "parameters": {}}], {}, None
    monkeypatch.setattr("services.plugin.edge_walker.collect_agent_connections", connections)
    tool = SimpleNamespace(name="browser", description="browser", args_schema={"type": "object"})
    monkeypatch.setattr(container_module.container, "ai_service", lambda: SimpleNamespace(_build_tool_from_node=AsyncMock(return_value=(tool, {}))))
    result = await prepare_agent_payload({**_CONTEXT, "node_type": "browser_agent",
        "node_data": {"system_message": "Delegated mission is input"},
        "invocation": {"task": "Read issues"}})
    assert result["system_message"] == BROWSER_AGENT_ROLE
    assert result["user_prompt"] == "Read issues"
