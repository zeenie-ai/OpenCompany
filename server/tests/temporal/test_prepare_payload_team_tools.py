"""``agent.prepare_payload`` for a team lead: each teammate's delegate entry
lists the teammate's tools as ``collect_teammate_connections`` found them, past
the owner's Generated UI switch (services/genui/switch.py). While the switch is
off, a teammate wired to Show UI carries neither the tool nor its catalogue in
the lead's delegate entry; the payload used to rebuild that list from the
edges and put both back.

Runs the real prepare_agent_payload, collect_teammate_connections and
AIService._build_tool_from_node; other collaborators are patched by string
path, as in test_prepare_payload_context.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.genui.spec import describe_catalog
from services.temporal.agent_activities import prepare_agent_payload

pytestmark = pytest.mark.unit

NODES = [
    {"id": "lead", "type": "orchestrator_agent", "data": {"label": "Lead"}},
    {"id": "helper", "type": "aiAgent", "data": {"label": "Helper"}},
    {"id": "ui", "type": "chatUi", "data": {"label": "Show UI"}},
    {"id": "search", "type": "duckduckgoSearch", "data": {"label": "Search"}},
]
EDGES = [
    {"source": "helper", "target": "lead", "targetHandle": "input-teammates"},
    {"source": "ui", "target": "helper", "targetHandle": "input-tools"},
    {"source": "search", "target": "helper", "targetHandle": "input-tools"},
]


class _Database:
    def __init__(self, settings):
        self.settings = settings

    async def get_node_parameters(self, node_id):
        if node_id == "lead":
            return {"provider": "openai", "model": "test-model", "api_key": "sk-test", "prompt": "hello"}
        return {}

    async def get_user_settings(self, user_id="default"):
        return self.settings

    async def get_workflow_control_generation(self, *_args, **_kwargs):
        return None

    async def get_tool_schema(self, _node_id):
        return None


@pytest.fixture
def lead_delegate(monkeypatch):
    """The lead's delegate entry for Helper, with the switch as ``settings`` has it."""
    from services.ai import AIService

    monkeypatch.setattr(
        "core.config.Settings",
        lambda: SimpleNamespace(max_concurrent_subagents=3, max_delegation_depth=2, agent_recursion_limit=200, tool_result_max_chars=100_000),
    )
    monkeypatch.setattr("services.llm.config.is_model_valid_for_provider", lambda *_a: True)
    monkeypatch.setattr("services.llm.config.resolve_max_tokens", lambda *_a: 1_000)
    monkeypatch.setattr("services.llm.config.resolve_temperature", lambda *_a: 0.2)

    async def lead_connections(*_args, **_kwargs):
        # The lead holds no tools of its own; its teammate comes from the
        # real collect_teammate_connections.
        return None, [], [], {}, None

    monkeypatch.setattr("services.plugin.edge_walker.collect_agent_connections", lead_connections)
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: None)
    monkeypatch.setattr("services.agent_context.load_conversation", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.employees.permissions.assert_runtime_access", AsyncMock())

    async def run(settings):
        database = _Database(settings)
        ai = AIService.__new__(AIService)
        ai.database = database
        resolver = SimpleNamespace(resolve=AsyncMock(side_effect=lambda params, *_a, **_k: params))
        monkeypatch.setattr(
            "core.container.container",
            SimpleNamespace(
                database=lambda: database,
                auth_service=lambda: SimpleNamespace(get_api_key=AsyncMock(return_value=None)),
                ai_service=lambda: ai,
                workflow_service=lambda: SimpleNamespace(_param_resolver=resolver),
            ),
        )
        payload = await prepare_agent_payload(
            {"node_id": "lead", "node_type": "orchestrator_agent", "workflow_id": "wf", "session_id": "wf", "nodes": NODES, "edges": EDGES}
        )
        delegates = [tool for tool in payload["tools"] if tool["name"].startswith("delegate_to_")]
        assert len(delegates) == 1, [tool["name"] for tool in payload["tools"]]
        return delegates[0]

    return run


async def test_with_the_switch_off_the_delegate_lists_no_show_ui(lead_delegate):
    delegate = await lead_delegate(None)

    assert [tool["node_type"] for tool in delegate["tool_info"]["child_tools"]] == ["duckduckgoSearch"]
    assert describe_catalog().strip() not in delegate["definition"]["description"]
    assert "Show UI" not in delegate["tool_info"]["capabilities"]


async def test_with_the_switch_on_the_delegate_lists_show_ui(lead_delegate):
    delegate = await lead_delegate({"generated_ui": True})

    assert [tool["node_type"] for tool in delegate["tool_info"]["child_tools"]] == ["chatUi", "duckduckgoSearch"]
    assert [tool["label"] for tool in delegate["tool_info"]["child_tools"]] == ["Show UI", "Search"]
    assert describe_catalog().strip() in delegate["definition"]["description"]
