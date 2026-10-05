"""``agent.prepare_payload`` and the conversation save: the pressure contract.

Calls the activity bodies directly. Collaborators are patched by string path:
subdirectory conftests can swap the stubbed ``core`` modules for the real
ones in a whole-suite run, and a string path resolves whichever is loaded.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.exceptions import ApplicationError

from services.temporal.agent_activities import (
    _SEED_TRANSCRIPT_MAX_BYTES,
    _save_conversation,
    prepare_agent_payload,
)
from services.temporal.agent_context_pressure import (
    CONTEXT_PRESSURE_VERSION,
    transcript_budget_bytes,
)

pytestmark = pytest.mark.unit

_CONTEXT = {
    "node_id": "agent-1",
    "node_type": "aiAgent",
    "workflow_id": "graph-1",
    "session_id": "session-1",
    "generation": 1,
}

_ENV_TOOL_LIMIT = 100_000


def _conversation_of(size_bytes: int) -> list:
    """A stored conversation whose serialized size is exactly ``size_bytes``."""
    overhead = len(json.dumps([{"role": "user", "content": ""}]).encode("utf-8"))
    return [{"role": "user", "content": "x" * (size_bytes - overhead)}]


@pytest.fixture
def prepare(monkeypatch):
    """Patch prepare_agent_payload's collaborators; tests steer ``state``."""
    state = SimpleNamespace(
        user_settings=None,
        conversation=[],
        context_descriptor=None,
        compaction_service=lambda: None,
    )
    database = SimpleNamespace(
        get_node_parameters=AsyncMock(
            return_value={
                "provider": "openai",
                "model": "test-model",
                "api_key": "sk-test",
                "prompt": "hello",
            }
        ),
        get_user_settings=AsyncMock(side_effect=lambda *_a, **_k: state.user_settings),
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
        lambda: SimpleNamespace(
            max_concurrent_subagents=3,
            max_delegation_depth=2,
            agent_recursion_limit=200,
            tool_result_max_chars=_ENV_TOOL_LIMIT,
        ),
    )
    monkeypatch.setattr(
        "services.llm.config.is_model_valid_for_provider", lambda *_a: True
    )
    monkeypatch.setattr("services.llm.config.resolve_max_tokens", lambda *_a: 1_000)
    monkeypatch.setattr("services.llm.config.resolve_temperature", lambda *_a: 0.2)

    async def connections(*_args, **_kwargs):
        return state.context_descriptor, [], [], {}, None

    async def load(*_args, **_kwargs):
        return state.conversation

    monkeypatch.setattr(
        "services.plugin.edge_walker.collect_agent_connections", connections
    )
    monkeypatch.setattr(
        "services.compaction.get_compaction_service",
        lambda: state.compaction_service(),
    )
    monkeypatch.setattr("services.agent_context.load_conversation", load)
    return state


class TestPressureControls:
    @pytest.mark.asyncio
    async def test_the_run_records_its_pressure_controls(self, prepare):
        payload = await prepare_agent_payload(dict(_CONTEXT))

        assert payload["tool_result_max_chars"] == _ENV_TOOL_LIMIT
        assert payload["transcript_budget_bytes"] == transcript_budget_bytes()
        assert payload["context_pressure_version"] == CONTEXT_PRESSURE_VERSION

    @pytest.mark.asyncio
    async def test_the_users_tool_limit_beats_the_env_value(self, prepare):
        prepare.user_settings = {"tool_result_max_chars": 25_000}

        payload = await prepare_agent_payload(dict(_CONTEXT))

        assert payload["tool_result_max_chars"] == 25_000

    @pytest.mark.asyncio
    async def test_an_unavailable_threshold_is_logged_not_silent(
        self, prepare, caplog
    ):
        def broken():
            raise RuntimeError("model registry unavailable")

        prepare.compaction_service = broken

        with caplog.at_level(logging.WARNING):
            payload = await prepare_agent_payload(dict(_CONTEXT))

        assert payload["compaction_threshold"] is None
        assert "Compaction threshold unavailable" in caplog.text


class TestSeedGuard:
    @pytest.mark.asyncio
    async def test_a_conversation_at_the_cap_still_loads(self, prepare):
        prepare.context_descriptor = {"kind": "context"}
        prepare.conversation = _conversation_of(_SEED_TRANSCRIPT_MAX_BYTES)

        payload = await prepare_agent_payload(dict(_CONTEXT))

        assert payload["conversation"] == prepare.conversation

    @pytest.mark.asyncio
    async def test_an_oversized_conversation_fails_loudly_with_a_way_out(
        self, prepare
    ):
        prepare.context_descriptor = {"kind": "context"}
        prepare.conversation = _conversation_of(_SEED_TRANSCRIPT_MAX_BYTES + 1)

        with pytest.raises(ApplicationError) as raised:
            await prepare_agent_payload(dict(_CONTEXT))

        error = raised.value
        assert error.type == "ConversationTooLarge"
        assert error.non_retryable is True
        assert "Context panel" in error.message
        assert "Tool Result Limit" in error.message
        # The Temporal path never reads a per-session compaction override.
        assert "compaction threshold" not in error.message


class TestSaveWarning:
    @pytest.fixture
    def saver(self, monkeypatch):
        save = AsyncMock()
        monkeypatch.setattr(
            "core.container.container",
            SimpleNamespace(database=lambda: SimpleNamespace()),
        )
        monkeypatch.setattr("services.agent_context.save_conversation", save)
        return save

    _PAYLOAD = {
        "conversation_key": {
            "workflow_id": "graph-1",
            "generation": 1,
            "agent_node_id": "agent-1",
        }
    }

    @pytest.mark.asyncio
    async def test_a_large_save_warns_before_the_next_firing_would_fail(
        self, saver, caplog
    ):
        sent = _conversation_of(_SEED_TRANSCRIPT_MAX_BYTES // 2 + 1)

        with caplog.at_level(logging.WARNING):
            await _save_conversation(
                self._PAYLOAD,
                sent=sent,
                assistant_wire={"role": "assistant", "content": "done"},
            )

        assert "over half" in caplog.text
        saver.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_normal_save_is_quiet(self, saver, caplog):
        with caplog.at_level(logging.WARNING):
            await _save_conversation(
                self._PAYLOAD,
                sent=[{"role": "user", "content": "hi"}],
                assistant_wire={"role": "assistant", "content": "done"},
            )

        assert "over half" not in caplog.text
        saver.assert_awaited_once()


@pytest.fixture(autouse=True)
def runtime_access_stub(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("services.employees.permissions.assert_runtime_access", AsyncMock())
