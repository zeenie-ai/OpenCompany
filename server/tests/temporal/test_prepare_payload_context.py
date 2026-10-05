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
