"""``agent.prepare_payload`` and the model the owner picked in Home's chat
(services/chat/choice.py): it applies to the agent that answers the chat run
only, it replaces the node's own model and key (so the chosen provider's key
is checked), and the LLM step reads the effort. The step never sends a
node's own key to another provider.

Calls the activity bodies directly, collaborators patched by string path
(see test_prepare_payload_pressure.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from services.temporal.agent_activities import _resolve_activity_api_key, prepare_agent_payload

pytestmark = pytest.mark.unit

_CONTEXT = {
    "node_id": "agent-1",
    "node_type": "aiAgent",
    "workflow_id": "graph-1",
    "session_id": "graph-1",
    "generation": 1,
    "run_scope": {"run_id": "r_1", "session_id": "graph-1"},
}
_NODE = {"provider": "openai", "model": "gpt-6-sol", "api_key": "sk-node", "prompt": "hello"}


@pytest.fixture
def prepare(monkeypatch):
    """Patch prepare_agent_payload's collaborators; tests steer ``state``."""
    state = SimpleNamespace(
        options={},
        answers=True,
        connected={"anthropic"},
        run_error=None,
        keys_checked=[],
    )

    async def has_valid_key(provider):
        state.keys_checked.append(provider)
        return provider in state.connected

    database = SimpleNamespace(
        get_node_parameters=AsyncMock(return_value=dict(_NODE)),
        get_user_settings=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "core.container.container",
        SimpleNamespace(
            database=lambda: database,
            auth_service=lambda: SimpleNamespace(has_valid_key=has_valid_key),
            ai_service=lambda: SimpleNamespace(),
            workflow_service=lambda: SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(
        "core.config.Settings",
        lambda: SimpleNamespace(
            max_concurrent_subagents=3,
            max_delegation_depth=2,
            agent_recursion_limit=200,
            tool_result_max_chars=100_000,
        ),
    )
    monkeypatch.setattr("services.llm.config.resolve_max_tokens", lambda *_a: 1_000)
    monkeypatch.setattr("services.llm.config.resolve_temperature", lambda *_a: 0.2)

    async def connections(*_args, **_kwargs):
        return None, [], [], {}, None

    async def get_run(_database, run_id):
        if state.run_error is not None:
            raise state.run_error
        return SimpleNamespace(run_id=run_id, options=dict(state.options))

    async def chat_turn(_database, _stream, *, system_message, prompt):
        return system_message, prompt

    async def run_attachments(_database, _stream):
        return []

    monkeypatch.setattr("services.employees.permissions.assert_runtime_access", AsyncMock())
    monkeypatch.setattr("services.plugin.edge_walker.collect_agent_connections", connections)
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: None)
    monkeypatch.setattr("services.chat.ledger.get_run", get_run)
    monkeypatch.setattr(
        "services.chat.stream.chat_stream_for",
        lambda context: {"run_id": "r_1", "session_id": "graph-1"} if state.answers else None,
    )
    monkeypatch.setattr("services.chat.guide.chat_turn", chat_turn)
    monkeypatch.setattr("services.chat.guide.run_attachments", run_attachments)
    return state


@pytest.mark.asyncio
async def test_the_answering_agent_runs_on_the_picked_model(prepare):
    prepare.options = {"model": "anthropic::claude-sonnet-5-5", "effort": "high"}

    payload = await prepare_agent_payload(dict(_CONTEXT))

    assert (payload["provider"], payload["model"], payload["effort"]) == ("anthropic", "claude-sonnet-5-5", "high")
    # The node's own key is OpenAI's: the chosen provider's key is checked.
    assert prepare.keys_checked == ["anthropic"]


@pytest.mark.asyncio
async def test_a_picked_model_whose_provider_has_no_key_is_refused(prepare):
    prepare.options = {"model": "anthropic::claude-sonnet-5-5"}
    prepare.connected = set()

    with pytest.raises(ApplicationError) as raised:
        await prepare_agent_payload(dict(_CONTEXT))

    assert raised.value.type == "MissingAgentProviderCredential"
    assert "anthropic" in raised.value.message


@pytest.mark.asyncio
async def test_other_agents_in_the_run_keep_their_own_model(prepare):
    prepare.options = {"model": "anthropic::claude-sonnet-5-5", "effort": "high"}
    prepare.answers = False

    payload = await prepare_agent_payload(dict(_CONTEXT))

    assert (payload["provider"], payload["model"], payload["effort"]) == ("openai", "gpt-6-sol", None)
    assert prepare.keys_checked == []


@pytest.mark.asyncio
async def test_without_a_model_the_agent_keeps_its_own_at_the_effort_picked(prepare):
    prepare.options = {"effort": "low", "web": False}

    payload = await prepare_agent_payload(dict(_CONTEXT))

    assert (payload["provider"], payload["model"], payload["effort"]) == ("openai", "gpt-6-sol", "low")


@pytest.mark.asyncio
async def test_a_run_that_cannot_be_read_fails_the_step(prepare):
    prepare.run_error = RuntimeError("database down")

    with pytest.raises(RuntimeError, match="database down"):
        await prepare_agent_payload(dict(_CONTEXT))


@pytest.mark.asyncio
async def test_the_step_sends_a_nodes_own_key_only_to_its_own_provider(monkeypatch):
    database = SimpleNamespace(get_node_parameters=AsyncMock(return_value={"provider": "openai", "api_key": "sk-node"}))
    auth = SimpleNamespace(resolve_api_key=AsyncMock(return_value=None), get_api_key=AsyncMock(return_value=None))
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: database, auth_service=lambda: auth))

    assert await _resolve_activity_api_key({"provider": "openai", "node_id": "agent-1", "user_id": "owner"}) == "sk-node"
    with pytest.raises(ApplicationError) as raised:
        await _resolve_activity_api_key({"provider": "anthropic", "node_id": "agent-1", "user_id": "owner"})
    assert raised.value.type == "MissingAgentProviderCredential"
