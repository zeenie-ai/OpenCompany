"""Contract tests for the Vertex managed-agent plugins.

vertex_managed_agent drives Google's Interactions API (mocked here at
the ``create_interaction_and_wait`` / ``build_genai_client`` seam in the
node's module namespace):

- prompt fallback + empty-prompt NodeUserError
- memory bridge: stored chain ids flow into previous_interaction_id /
  environment and new ids persist back onto the simpleMemory params
- requires_action loop: declared function tools dispatch through
  ``execute_tool`` and answer with call_id-matched function_result inputs
- stale-chain recovery: env-expired error wipes ids and retries fresh
- cloud-tool usage detection feeds the minting helper

vertex_agent_admin maps its four operations onto ``client.aio.agents``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.nodes._mocks import patched_broadcaster, patched_container

pytestmark = pytest.mark.node_contract


# ============================================================================
# Helpers
# ============================================================================


def _edge(source: str, target: str, target_handle: str) -> dict:
    return {"source": source, "target": target, "targetHandle": target_handle}


def _node(node_id: str, node_type: str, label: str | None = None) -> dict:
    return {"id": node_id, "type": node_type, "data": {"label": label or node_type}}


def _interaction(
    *,
    status: str = "completed",
    interaction_id: str = "ix-1",
    environment_id: str = "env_CAE1",
    output_text: str = "cloud response",
    steps: list | None = None,
    usage: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=interaction_id,
        status=status,
        environment_id=environment_id,
        output_text=output_text,
        steps=steps or [],
        usage=usage,
    )


def _fc_step(name: str, call_id: str, arguments: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        type="function_call", name=name, id=call_id, arguments=arguments or {}
    )


def _fr_step(call_id: str) -> SimpleNamespace:
    return SimpleNamespace(type="function_result", call_id=call_id)


_NODE_MODULE = "nodes.agent.vertex_managed_agent"
_ADMIN_MODULE = "nodes.agent.vertex_agent_admin"


def _patched_interactions(side_effect):
    """Patch the node's SDK seam: fake client + canned interactions."""
    return (
        patch(f"{_NODE_MODULE}.build_genai_client", return_value=MagicMock()),
        patch(
            f"{_NODE_MODULE}.create_interaction_and_wait",
            AsyncMock(side_effect=side_effect),
        ),
    )


def _wire_async_broadcasts(broadcaster: MagicMock) -> None:
    broadcaster.broadcast_agent_progress = AsyncMock(return_value=None)
    broadcaster.broadcast = AsyncMock(return_value=None)


class FakeGenaiError(Exception):
    """Stands in for the SDK error hierarchy (module-name matched)."""


FakeGenaiError.__module__ = "google.genai._gaos.lib.compat_errors"


# ============================================================================
# vertex_managed_agent
# ============================================================================


class TestVertexManagedAgent:
    async def test_happy_path_single_turn(self, harness):
        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "hello", "project_id": "test-proj"},
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["response"] == "cloud response"
        assert payload["interaction_id"] == "ix-1"
        assert payload["environment_id"] == "env_CAE1"
        assert payload["status"] == "completed"
        assert payload["provider"] == "gemini"

        # Fresh run: remote sandbox, no chaining.
        _, kwargs = create_mock.call_args
        assert kwargs["environment"] == "remote"
        assert "previous_interaction_id" not in kwargs
        assert kwargs["agent"] == "antigravity-preview-05-2026"
        assert kwargs["store"] is True

    async def test_empty_prompt_without_input_is_user_error(self, harness):
        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch,
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "", "project_id": "test-proj"},
            )

        assert result["success"] is False
        assert result.get("error_type") == "NodeUserError"

    async def test_prompt_falls_back_to_input_message(self, harness):
        agent_id = "vx-1"
        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
        ):
            _wire_async_broadcasts(bc)
            ctx = harness.build_context(
                nodes=[_node(agent_id, "vertex_managed_agent"), _node("t1", "chatTrigger")],
                edges=[_edge("t1", agent_id, "input-main")],
            )
            ctx["outputs"] = {"t1": {"message": "from trigger"}}
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "", "project_id": "test-proj"},
                node_id=agent_id,
                context=ctx,
            )

        harness.assert_envelope(result, success=True)
        _, kwargs = create_mock.call_args
        assert kwargs["input"] == "from trigger"

    async def test_memory_bridge_chains_and_persists(self, harness):
        agent_id = "vx-1"
        mem_id = "mem-1"
        saved: dict = {}

        harness.database.get_node_parameters = AsyncMock(
            return_value={
                "session_id": "",
                "window_size": 5,
                "memory_content": "# Conversation History\n",
                "vertex_interaction_id": "ix-prev",
                "vertex_environment_id": "env_prev",
            }
        )

        async def capture_save(node_id, params):
            saved[node_id] = params
            return True

        harness.database.save_node_parameters = AsyncMock(side_effect=capture_save)

        client_patch, create_patch = _patched_interactions(
            [_interaction(interaction_id="ix-new", environment_id="env_new")]
        )
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "remember me", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[_node(agent_id, "vertex_managed_agent"), _node(mem_id, "simpleMemory")],
                edges=[_edge(mem_id, agent_id, "input-memory")],
            )

        harness.assert_envelope(result, success=True)
        _, kwargs = create_mock.call_args
        assert kwargs["previous_interaction_id"] == "ix-prev"
        assert kwargs["environment"] == "env_prev"

        persisted = saved[mem_id]
        assert persisted["vertex_interaction_id"] == "ix-new"
        assert persisted["vertex_environment_id"] == "env_new"
        assert "remember me" in persisted["memory_content"]
        assert "cloud response" in persisted["memory_content"]

    async def test_requires_action_dispatches_tool_and_answers(self, harness):
        agent_id = "vx-1"
        tool_id = "tool-1"

        fake_tool = SimpleNamespace(name="fake_tool", description="d", args_schema=None)
        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(
            return_value=(
                fake_tool,
                {"node_type": "duckduckgoSearch", "node_id": tool_id, "parameters": {}, "label": "ddg"},
            )
        )
        execute_tool_mock = AsyncMock(return_value={"answer": 42})

        turns = [
            _interaction(
                status="requires_action",
                interaction_id="ix-1",
                steps=[
                    _fc_step("provision_sandbox", "c0"),
                    _fr_step("c0"),
                    _fc_step("fake_tool", "c1", {"query": "x"}),
                ],
            ),
            _interaction(status="completed", interaction_id="ix-2"),
        ]
        client_patch, create_patch = _patched_interactions(turns)
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
            patch("services.handlers.tools.execute_tool", execute_tool_mock),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "use the tool", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[
                    _node(agent_id, "vertex_managed_agent"),
                    _node(tool_id, "duckduckgoSearch"),
                ],
                edges=[_edge(tool_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["turns"] == 2

        # Tools declared on both creates.
        first_kwargs = create_mock.call_args_list[0].kwargs
        declared = first_kwargs["tools"]
        assert declared == [
            {
                "type": "function",
                "name": "fake_tool",
                "description": "d",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

        # Only OUR pending call executed (provision_sandbox skipped).
        execute_tool_mock.assert_awaited_once()
        name_arg, args_arg, config_arg = execute_tool_mock.await_args.args
        assert name_arg == "fake_tool"
        assert args_arg == {"query": "x"}
        assert config_arg["parent_node_id"] == agent_id

        # Follow-up create answers with the matched call_id.
        second_kwargs = create_mock.call_args_list[1].kwargs
        assert second_kwargs["previous_interaction_id"] == "ix-1"
        assert second_kwargs["input"] == [
            {
                "type": "function_result",
                "name": "fake_tool",
                "call_id": "c1",
                "result": {"answer": 42},
            }
        ]

    async def test_stale_chain_wipes_and_retries_fresh(self, harness):
        agent_id = "vx-1"
        mem_id = "mem-1"

        harness.database.get_node_parameters = AsyncMock(
            return_value={
                "vertex_interaction_id": "ix-stale",
                "vertex_environment_id": "env_stale",
                "memory_content": "# Conversation History\n",
            }
        )

        calls: list = []

        async def create_side_effect(client, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise FakeGenaiError("Error 404: environment not found")
            return _interaction(interaction_id="ix-fresh", environment_id="env_fresh")

        client_patch = patch(f"{_NODE_MODULE}.build_genai_client", return_value=MagicMock())
        create_patch = patch(
            f"{_NODE_MODULE}.create_interaction_and_wait",
            AsyncMock(side_effect=create_side_effect),
        )
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch,
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[_node(agent_id, "vertex_managed_agent"), _node(mem_id, "simpleMemory")],
                edges=[_edge(mem_id, agent_id, "input-memory")],
            )

        harness.assert_envelope(result, success=True)
        assert calls[0]["previous_interaction_id"] == "ix-stale"
        assert "previous_interaction_id" not in calls[1]
        assert calls[1]["environment"] == "remote"
        assert result["result"]["interaction_id"] == "ix-fresh"

    async def test_cloud_tool_usage_feeds_minting(self, harness):
        agent_id = "vx-1"
        steps = [
            _fc_step("run_command", "c1"),
            _fr_step("c1"),
            SimpleNamespace(type="google_search_call", id="c2", name=None),
        ]
        client_patch, create_patch = _patched_interactions([_interaction(steps=steps)])
        mint_mock = AsyncMock(return_value=[])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch,
            patch(f"{_NODE_MODULE}._ops.ensure_cloud_tool_nodes", mint_mock),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                context={
                    "workflow_id": "wf-1",
                    "nodes": [_node(agent_id, "vertex_managed_agent")],
                    "edges": [],
                    "outputs": {},
                },
            )

        harness.assert_envelope(result, success=True)
        mint_mock.assert_awaited_once()
        used = mint_mock.await_args.kwargs["used"]
        assert used == {
            "fn:run_command": "run_command",
            "type:google_search_call": "Google Search",
        }
        assert sorted(result["result"]["cloud_tools_used"]) == [
            "Google Search",
            "run_command",
        ]

    async def test_visualize_off_skips_minting(self, harness):
        steps = [_fc_step("run_command", "c1")]
        client_patch, create_patch = _patched_interactions([_interaction(steps=steps)])
        mint_mock = AsyncMock(return_value=[])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch,
            patch(f"{_NODE_MODULE}._ops.ensure_cloud_tool_nodes", mint_mock),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj", "visualize_cloud_tools": False},
                context={"workflow_id": "wf-1", "nodes": [], "edges": [], "outputs": {}},
            )

        harness.assert_envelope(result, success=True)
        mint_mock.assert_not_awaited()


# ============================================================================
# ensure_cloud_tool_nodes (minting helper)
# ============================================================================


class TestCloudToolMinting:
    def _workflow(self, nodes=None, edges=None):
        return SimpleNamespace(
            data={"nodes": list(nodes or []), "edges": list(edges or [])},
            name="wf",
            slug="wf_1",
            description=None,
        )

    async def test_mints_persists_then_broadcasts_and_pulses(self):
        from nodes.agent.vertex_managed_agent import _ops

        database = MagicMock()
        database.get_workflow = AsyncMock(return_value=self._workflow())
        database.save_workflow = AsyncMock(return_value=True)
        database.save_node_parameters = AsyncMock(return_value=True)
        broadcaster = MagicMock()
        broadcaster.broadcast = AsyncMock()
        broadcaster.update_node_status = AsyncMock()

        with (
            patch.object(_ops, "get_database", return_value=database),
            patch.object(_ops, "get_status_broadcaster", return_value=broadcaster),
        ):
            pulsed = await _ops.ensure_cloud_tool_nodes(
                workflow_id="wf-1",
                agent_node_id="vx-1",
                used={"fn:run_command": "run_command"},
            )

        assert len(pulsed) == 1
        minted_id = pulsed[0]
        assert minted_id.startswith("vertexCloudTool-")

        # Persisted node + edge with the minted id.
        save_kwargs = database.save_workflow.await_args.kwargs
        persisted_nodes = save_kwargs["data"]["nodes"]
        persisted_edges = save_kwargs["data"]["edges"]
        assert persisted_nodes[0]["id"] == minted_id
        assert persisted_nodes[0]["type"] == "vertexCloudTool"
        assert persisted_edges[0]["source"] == minted_id
        assert persisted_edges[0]["targetHandle"] == "input-tools"

        # Persist happens before the ops broadcast.
        broadcast_payload = broadcaster.broadcast.await_args.args[0]
        assert broadcast_payload["type"] == "workflow_ops_apply"
        ops = broadcast_payload["data"]["operations"]
        assert ops[0]["type"] == "add_node"
        assert ops[0]["minted_id"] == minted_id
        assert ops[1]["type"] == "add_edge"

        # executing -> success pulse on the minted node.
        statuses = [c.args[1] for c in broadcaster.update_node_status.await_args_list]
        assert statuses == ["executing", "success"]

    async def test_dedupes_existing_node_by_label(self):
        from nodes.agent.vertex_managed_agent import _ops

        existing_nodes = [
            {
                "id": "vertexCloudTool-1-aaa",
                "type": "vertexCloudTool",
                "data": {"label": "run_command"},
            }
        ]
        existing_edges = [
            {
                "source": "vertexCloudTool-1-aaa",
                "target": "vx-1",
                "targetHandle": "input-tools",
            }
        ]
        database = MagicMock()
        database.get_workflow = AsyncMock(
            return_value=self._workflow(existing_nodes, existing_edges)
        )
        database.save_workflow = AsyncMock(return_value=True)
        database.save_node_parameters = AsyncMock(return_value=True)
        broadcaster = MagicMock()
        broadcaster.broadcast = AsyncMock()
        broadcaster.update_node_status = AsyncMock()

        with (
            patch.object(_ops, "get_database", return_value=database),
            patch.object(_ops, "get_status_broadcaster", return_value=broadcaster),
        ):
            pulsed = await _ops.ensure_cloud_tool_nodes(
                workflow_id="wf-1",
                agent_node_id="vx-1",
                used={"fn:run_command": "run_command"},
            )

        assert pulsed == ["vertexCloudTool-1-aaa"]
        database.save_workflow.assert_not_awaited()
        broadcaster.broadcast.assert_not_awaited()
        # Existing node still pulses.
        assert broadcaster.update_node_status.await_count == 2


# ============================================================================
# vertex_agent_admin
# ============================================================================


class TestVertexAgentAdmin:
    def _client_with_agents(self):
        client = MagicMock()
        client.aio.agents.create = AsyncMock(
            return_value=SimpleNamespace(model_dump=lambda **kw: {"id": "my-agent"})
        )
        client.aio.agents.list = AsyncMock(
            return_value=SimpleNamespace(agents=[{"id": "a1"}, {"id": "a2"}])
        )
        client.aio.agents.get = AsyncMock(return_value={"id": "my-agent", "tools": []})
        client.aio.agents.delete = AsyncMock(return_value=None)
        return client

    async def test_create_maps_params_to_sdk(self, harness):
        client = self._client_with_agents()
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            patch(f"{_ADMIN_MODULE}.build_genai_client", return_value=client),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_agent_admin",
                {
                    "operation": "create",
                    "project_id": "test-proj",
                    "agent_id": "my-agent",
                    "description": "demo",
                    "system_instruction": "be helpful",
                    "tools": ["code_execution", "google_search"],
                },
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["agent"] == {"id": "my-agent"}
        kwargs = client.aio.agents.create.await_args.kwargs
        assert kwargs["id"] == "my-agent"
        assert kwargs["base_agent"] == "antigravity-preview-05-2026"
        assert kwargs["description"] == "demo"
        assert kwargs["system_instruction"] == "be helpful"
        assert kwargs["tools"] == [
            {"type": "code_execution"},
            {"type": "google_search"},
        ]

    async def test_list_returns_agents(self, harness):
        client = self._client_with_agents()
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            patch(f"{_ADMIN_MODULE}.build_genai_client", return_value=client),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_agent_admin",
                {"operation": "list", "project_id": "test-proj"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["count"] == 2
        assert result["result"]["agents"][0]["id"] == "a1"

    async def test_delete_requires_agent_id(self, harness):
        client = self._client_with_agents()
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            patch(f"{_ADMIN_MODULE}.build_genai_client", return_value=client),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_agent_admin",
                {"operation": "delete", "project_id": "test-proj"},
            )

        assert result["success"] is False
        assert result.get("error_type") == "NodeUserError"
        client.aio.agents.delete.assert_not_awaited()

    async def test_delete_happy_path(self, harness):
        client = self._client_with_agents()
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            patch(f"{_ADMIN_MODULE}.build_genai_client", return_value=client),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_agent_admin",
                {"operation": "delete", "project_id": "test-proj", "agent_id": "my-agent"},
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["deleted"] is True
        client.aio.agents.delete.assert_awaited_once_with(id="my-agent")


# ============================================================================
# memory clear integration (chain ids wiped)
# ============================================================================


class TestChainIdHelpers:
    async def test_save_chain_ids_none_pops_keys(self):
        from nodes.agent.vertex_managed_agent import VertexManagedAgentNode

        params_store = {
            "vertex_interaction_id": "ix-old",
            "vertex_environment_id": "env-old",
            "memory_content": "# Conversation History\n",
        }
        database = MagicMock()
        database.get_node_parameters = AsyncMock(return_value=dict(params_store))
        saved = {}

        async def capture(node_id, params):
            saved.update(params)
            return True

        database.save_node_parameters = AsyncMock(side_effect=capture)

        await VertexManagedAgentNode._save_chain_ids(database, "mem-1", None, None)

        assert "vertex_interaction_id" not in saved
        assert "vertex_environment_id" not in saved
