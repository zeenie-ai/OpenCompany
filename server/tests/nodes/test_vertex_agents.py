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
    """Patch the node's SDK seam: fake client + canned interactions.

    The node now calls ``stream_interaction`` (SSE-backed); non-live
    tests mock it to return final interactions, ignoring ``on_event``.
    """
    return (
        patch(f"{_NODE_MODULE}.build_genai_client", return_value=MagicMock()),
        patch(
            f"{_NODE_MODULE}.stream_interaction",
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
        record_mock = AsyncMock()
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
            patch("services.handlers.tools.execute_tool", execute_tool_mock),
            patch(f"{_NODE_MODULE}._ops.record_tool_output", record_mock),
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

        # Local tool node got its invocation output recorded (Output panel).
        record_mock.assert_awaited_once()
        rec = record_mock.await_args
        assert rec.args[0] == tool_id
        assert rec.args[1]["tool"] == "fake_tool"
        assert rec.args[1]["result"] == {"answer": 42}
        assert rec.args[1]["is_error"] is False

    async def test_requires_action_awaits_delegated_agent_and_answers_real_result(self, harness):
        """Sub-agent bridging: the delegate call is dispatched with the
        blocking-wait contract (delegation_wait_seconds) and the child's
        REAL answer — not a fire-and-forget task_id ack — is sent back
        to the cloud agent as the function_result."""
        agent_id = "vx-1"
        child_id = "child-1"

        delegate_tool = SimpleNamespace(name="delegate_to_ai_agent", description="d", args_schema=None)
        check_tool = SimpleNamespace(name="check_delegated_tasks", description="check", args_schema=None)

        async def build_side_effect(tool_info):
            if tool_info["node_type"] == "_builtin_check_delegated_tasks":
                return (check_tool, {"node_type": "_builtin_check_delegated_tasks", "node_id": tool_info["node_id"], "parameters": {}})
            return (delegate_tool, {"node_type": "aiAgent", "node_id": child_id, "parameters": {}, "label": "child"})

        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(side_effect=build_side_effect)
        execute_tool_mock = AsyncMock(
            return_value={
                "success": True,
                "status": "completed",
                "task_id": "delegated_x",
                "agent_name": "child",
                "result": "child answer",
            }
        )

        turns = [
            _interaction(
                status="requires_action",
                interaction_id="ix-1",
                steps=[_fc_step("delegate_to_ai_agent", "c1", {"task": "do it"})],
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
            patch(f"{_NODE_MODULE}._ops.record_tool_output", AsyncMock()),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "delegate", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[
                    _node(agent_id, "vertex_managed_agent"),
                    _node(child_id, "aiAgent"),
                ],
                edges=[_edge(child_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        assert result["result"]["turns"] == 2

        # The bridged dispatch carried the blocking-wait contract.
        execute_tool_mock.assert_awaited_once()
        name_arg, args_arg, config_arg = execute_tool_mock.await_args.args
        assert name_arg == "delegate_to_ai_agent"
        assert config_arg["delegation_wait_seconds"] == 600

        # The cloud agent got the child's real answer, not a task_id ack.
        second_kwargs = create_mock.call_args_list[1].kwargs
        fr = second_kwargs["input"][0]
        assert fr["type"] == "function_result"
        assert fr["call_id"] == "c1"
        assert fr["result"]["result"] == "child answer"
        assert fr["result"]["status"] == "completed"

    async def test_check_tool_injected_when_agent_connected(self, harness):
        agent_id = "vx-1"
        child_id = "child-1"

        delegate_tool = SimpleNamespace(name="delegate_to_ai_agent", description="d", args_schema=None)
        check_tool = SimpleNamespace(name="check_delegated_tasks", description="check", args_schema=None)

        async def build_side_effect(tool_info):
            if tool_info["node_type"] == "_builtin_check_delegated_tasks":
                return (check_tool, {"node_type": "_builtin_check_delegated_tasks", "node_id": tool_info["node_id"], "parameters": {}})
            return (delegate_tool, {"node_type": "aiAgent", "node_id": child_id, "parameters": {}, "label": "child"})

        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(side_effect=build_side_effect)

        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[
                    _node(agent_id, "vertex_managed_agent"),
                    _node(child_id, "aiAgent"),
                ],
                edges=[_edge(child_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        kwargs = create_mock.call_args.kwargs
        declared_names = [t["name"] for t in kwargs["tools"]]
        assert declared_names == ["delegate_to_ai_agent", "check_delegated_tasks"]
        # Delegation guidance injected even with no user system_instruction.
        assert "## Agent Delegation" in kwargs["system_instruction"]
        assert "delegate_to_ai_agent" in kwargs["system_instruction"]

    async def test_check_tool_not_injected_for_plain_tools(self, harness):
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

        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[_node(agent_id, "vertex_managed_agent"), _node(tool_id, "duckduckgoSearch")],
                edges=[_edge(tool_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        kwargs = create_mock.call_args.kwargs
        assert [t["name"] for t in kwargs["tools"]] == ["fake_tool"]
        assert "system_instruction" not in kwargs

    async def test_delegation_wait_clamped_to_activity_budget(self, harness):
        """A delegation wait must never blow the Temporal activity
        deadline: with ~29 min already elapsed, the wait clamps to 0 and
        the dispatch falls back to the fire-and-forget contract."""
        agent_id = "vx-1"
        child_id = "child-1"

        delegate_tool = SimpleNamespace(name="delegate_to_ai_agent", description="d", args_schema=None)
        check_tool = SimpleNamespace(name="check_delegated_tasks", description="check", args_schema=None)

        async def build_side_effect(tool_info):
            if tool_info["node_type"] == "_builtin_check_delegated_tasks":
                return (check_tool, {"node_type": "_builtin_check_delegated_tasks", "node_id": tool_info["node_id"], "parameters": {}})
            return (delegate_tool, {"node_type": "aiAgent", "node_id": child_id, "parameters": {}, "label": "child"})

        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(side_effect=build_side_effect)
        execute_tool_mock = AsyncMock(return_value={"success": True, "status": "delegated", "task_id": "t1"})

        turns = [
            _interaction(
                status="requires_action",
                interaction_id="ix-1",
                steps=[_fc_step("delegate_to_ai_agent", "c1", {"task": "t"})],
            ),
            _interaction(status="completed", interaction_id="ix-2"),
        ]
        client_patch, create_patch = _patched_interactions(turns)

        # First time.time() call is start_time (0.0); every later call
        # reports 1740s elapsed -> remaining budget is negative.
        ticks = iter([0.0])
        fake_time = MagicMock()
        fake_time.time = lambda: next(ticks, 1740.0)

        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
            patch("services.handlers.tools.execute_tool", execute_tool_mock),
            patch(f"{_NODE_MODULE}._ops.record_tool_output", AsyncMock()),
            patch(f"{_NODE_MODULE}.time", fake_time),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[_node(agent_id, "vertex_managed_agent"), _node(child_id, "aiAgent")],
                edges=[_edge(child_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        execute_tool_mock.assert_awaited_once()
        config_arg = execute_tool_mock.await_args.args[2]
        assert "delegation_wait_seconds" not in config_arg

    async def test_declared_schema_inlines_nested_refs(self, harness):
        """Nested BaseModel / Enum Params fields emit $defs + $ref under
        Pydantic v2 — the declaration must inline them, not strip $defs
        and leave dangling refs."""
        import json
        from enum import Enum

        from pydantic import BaseModel as PydanticBase

        class Color(str, Enum):
            RED = "red"
            BLUE = "blue"

        class Inner(PydanticBase):
            name: str

        class NestedToolParams(PydanticBase):
            nested: Inner
            color: Color = Color.RED

        agent_id = "vx-1"
        tool_id = "tool-1"
        fake_tool = SimpleNamespace(name="fake_tool", description="d", args_schema=NestedToolParams)
        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(
            return_value=(
                fake_tool,
                {"node_type": "duckduckgoSearch", "node_id": tool_id, "parameters": {}, "label": "ddg"},
            )
        )

        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[_node(agent_id, "vertex_managed_agent"), _node(tool_id, "duckduckgoSearch")],
                edges=[_edge(tool_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        parameters = create_mock.call_args.kwargs["tools"][0]["parameters"]
        dumped = json.dumps(parameters)
        assert "$defs" not in parameters
        assert '"$ref"' not in dumped
        assert parameters["properties"]["nested"]["type"] == "object"
        assert "red" in json.dumps(parameters["properties"]["color"])

    async def test_recursive_params_degrade_safely(self, harness):
        import json
        from typing import List as TypingList

        from pydantic import BaseModel as PydanticBase

        class TreeParams(PydanticBase):
            label: str
            children: TypingList["TreeParams"] = []

        agent_id = "vx-1"
        tool_id = "tool-1"
        fake_tool = SimpleNamespace(name="fake_tool", description="d", args_schema=TreeParams)
        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(
            return_value=(
                fake_tool,
                {"node_type": "duckduckgoSearch", "node_id": tool_id, "parameters": {}, "label": "ddg"},
            )
        )

        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[_node(agent_id, "vertex_managed_agent"), _node(tool_id, "duckduckgoSearch")],
                edges=[_edge(tool_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        parameters = create_mock.call_args.kwargs["tools"][0]["parameters"]
        dumped = json.dumps(parameters)  # must stay JSON-serializable
        assert "$defs" not in parameters
        assert '"$ref"' not in dumped

    async def test_minted_cloud_tool_nodes_not_redeclared(self, harness):
        """Display-only vertexCloudTool nodes (minted with a persisted
        input-tools edge) must not be re-declared as junk function tools
        on later runs."""
        agent_id = "vx-1"
        tool_id = "tool-1"
        minted_id = "vertexCloudTool-1-aaa"

        fake_tool = SimpleNamespace(name="fake_tool", description="d", args_schema=None)
        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(
            return_value=(
                fake_tool,
                {"node_type": "duckduckgoSearch", "node_id": tool_id, "parameters": {}, "label": "ddg"},
            )
        )

        client_patch, create_patch = _patched_interactions([_interaction()])
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[
                    _node(agent_id, "vertex_managed_agent"),
                    _node(tool_id, "duckduckgoSearch"),
                    _node(minted_id, "vertexCloudTool", label="run_command"),
                ],
                edges=[
                    _edge(tool_id, agent_id, "input-tools"),
                    _edge(minted_id, agent_id, "input-tools"),
                ],
            )

        harness.assert_envelope(result, success=True)
        # Only the real tool was built + declared; the display node was
        # gated out before the DB round-trip.
        ai_service._build_tool_from_node.assert_awaited_once()
        assert [t["name"] for t in create_mock.call_args.kwargs["tools"]] == ["fake_tool"]

    async def test_duplicate_tool_names_deduped(self, harness):
        """Two nodes resolving to the same tool name get deterministic
        _2 suffixes; the suffixed name dispatches with the SECOND node's
        config."""
        agent_id = "vx-1"

        def make_tool(node_id):
            return (
                SimpleNamespace(name="fake_tool", description="d", args_schema=None),
                {"node_type": "duckduckgoSearch", "node_id": node_id, "parameters": {}, "label": node_id},
            )

        ai_service = MagicMock(name="AIService")
        ai_service._build_tool_from_node = AsyncMock(side_effect=[make_tool("tool-1"), make_tool("tool-2")])
        execute_tool_mock = AsyncMock(return_value={"answer": 1})

        turns = [
            _interaction(
                status="requires_action",
                interaction_id="ix-1",
                steps=[_fc_step("fake_tool_2", "c1", {"query": "x"})],
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
            patch(f"{_NODE_MODULE}._ops.record_tool_output", AsyncMock()),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[
                    _node(agent_id, "vertex_managed_agent"),
                    _node("tool-1", "duckduckgoSearch"),
                    _node("tool-2", "duckduckgoSearch"),
                ],
                edges=[
                    _edge("tool-1", agent_id, "input-tools"),
                    _edge("tool-2", agent_id, "input-tools"),
                ],
            )

        harness.assert_envelope(result, success=True)
        declared_names = [t["name"] for t in create_mock.call_args_list[0].kwargs["tools"]]
        assert declared_names == ["fake_tool", "fake_tool_2"]
        execute_tool_mock.assert_awaited_once()
        config_arg = execute_tool_mock.await_args.args[2]
        assert config_arg["node_id"] == "tool-2"

    async def test_unanswerable_requires_action_surfaces_warning(self, harness):
        """When requires_action only carries calls we never declared, the
        loop stops AND says why — in the log, the output payload, and the
        node-status details."""
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

        turns = [
            _interaction(
                status="requires_action",
                interaction_id="ix-1",
                steps=[
                    _fc_step("provision_sandbox", "c0"),
                    _fc_step("mystery_fn", "c1"),
                ],
            ),
        ]
        client_patch, create_patch = _patched_interactions(turns)
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch as create_mock,
            patch("services.plugin.deps.get_ai_service", return_value=ai_service),
        ):
            _wire_async_broadcasts(bc)
            result = await harness.execute(
                "vertex_managed_agent",
                {"prompt": "p", "project_id": "test-proj"},
                node_id=agent_id,
                nodes=[_node(agent_id, "vertex_managed_agent"), _node(tool_id, "duckduckgoSearch")],
                edges=[_edge(tool_id, agent_id, "input-tools")],
            )

        harness.assert_envelope(result, success=True)
        assert create_mock.await_count == 1  # loop stopped, no follow-up turn
        payload = result["result"]
        assert payload["status"] == "requires_action"
        warning = payload["warnings"][0]
        assert "mystery_fn" in warning
        assert "fake_tool" in warning
        # Noise names are excluded from the diagnostic.
        assert "provision_sandbox" not in warning

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
            f"{_NODE_MODULE}.stream_interaction",
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

    async def test_precondition_failure_wipes_chain_and_retries_fresh(self, harness):
        """Live-verified wedge: previous_interaction_id pointing at an
        unresumable interaction 400s with 'Precondition check failed'.
        Turn 1 must wipe the stored ids and retry fresh instead of
        failing every subsequent run."""
        agent_id = "vx-1"
        mem_id = "mem-1"

        harness.database.get_node_parameters = AsyncMock(
            return_value={
                "vertex_interaction_id": "ix-wedged",
                "vertex_environment_id": "env_wedged",
                "memory_content": "# Conversation History\n",
            }
        )
        saved: dict = {}

        async def capture_save(node_id, params):
            saved[node_id] = params
            return True

        harness.database.save_node_parameters = AsyncMock(side_effect=capture_save)

        calls: list = []

        async def create_side_effect(client, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise FakeGenaiError(
                    "Error code: 400 - {'error': {'message': 'Precondition check failed.', 'code': 'invalid_request'}}"
                )
            return _interaction(interaction_id="ix-fresh", environment_id="env_fresh")

        client_patch = patch(f"{_NODE_MODULE}.build_genai_client", return_value=MagicMock())
        create_patch = patch(
            f"{_NODE_MODULE}.stream_interaction",
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
        assert calls[0]["previous_interaction_id"] == "ix-wedged"
        assert "previous_interaction_id" not in calls[1]
        assert calls[1]["environment"] == "remote"
        assert result["result"]["interaction_id"] == "ix-fresh"
        # The fresh chain got persisted (completed status is resumable).
        assert saved[mem_id]["vertex_interaction_id"] == "ix-fresh"
        assert saved[mem_id]["vertex_environment_id"] == "env_fresh"

    async def test_unresumable_final_status_keeps_previous_chain_ids(self, harness):
        """A run whose final interaction is failed/stuck must NOT persist
        that id — chaining onto it wedges every later run. Keep the last
        good pair instead."""
        agent_id = "vx-1"
        mem_id = "mem-1"

        harness.database.get_node_parameters = AsyncMock(
            return_value={
                "vertex_interaction_id": "ix-good",
                "vertex_environment_id": "env_good",
                "memory_content": "# Conversation History\n",
            }
        )
        saved: dict = {}

        async def capture_save(node_id, params):
            saved[node_id] = params
            return True

        harness.database.save_node_parameters = AsyncMock(side_effect=capture_save)

        client_patch, create_patch = _patched_interactions(
            [_interaction(status="failed", interaction_id="ix-bad", environment_id="env_bad")]
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
        assert result["result"]["status"] == "failed"
        # The wedging id was NOT persisted; the last good chain survives.
        assert saved[mem_id]["vertex_interaction_id"] == "ix-good"
        assert saved[mem_id]["vertex_environment_id"] == "env_good"

    async def test_cloud_tool_usage_feeds_minting(self, harness):
        agent_id = "vx-1"
        steps = [
            _fc_step("run_command", "c1"),
            _fr_step("c1"),
            SimpleNamespace(type="google_search_call", id="c2", name=None),
        ]
        client_patch, create_patch = _patched_interactions([_interaction(steps=steps)])
        mint_mock = AsyncMock(return_value={})
        pulse_mock = AsyncMock()
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            client_patch,
            create_patch,
            patch(f"{_NODE_MODULE}._ops.ensure_cloud_tool_nodes", mint_mock),
            patch(f"{_NODE_MODULE}._ops.pulse_node", pulse_mock),
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
        # Stream mock never invoked on_event, so everything lands in the
        # post-turn sweep.
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

    async def test_live_stream_mints_and_pulses_mid_turn(self, harness):
        """Cloud tool nodes appear WHILE streaming: mint + executing pulse
        on the call step, success pulse on the call_id-matched result
        step; noise names and non-step events are ignored; the live
        union feeds cloud_tools_used even when the final resource
        carries no steps."""
        agent_id = "vx-1"

        async def fake_stream(client, *, on_event=None, **kwargs):
            assert on_event is not None
            await on_event(
                SimpleNamespace(
                    event_type="step.start",
                    index=0,
                    step=SimpleNamespace(type="code_execution_call", id="c1", name=None),
                )
            )
            await on_event(SimpleNamespace(event_type="step.delta", index=0))
            await on_event(
                SimpleNamespace(
                    event_type="step.start",
                    index=1,
                    step=SimpleNamespace(
                        type="code_execution_result",
                        call_id="c1",
                        result={"stdout": "42"},
                        is_error=False,
                    ),
                )
            )
            await on_event(
                SimpleNamespace(
                    event_type="step.start",
                    index=2,
                    step=SimpleNamespace(
                        type="function_call", id="c9", name="provision_sandbox"
                    ),
                )
            )
            return _interaction(steps=[])  # final resource: no steps

        mint_mock = AsyncMock(return_value={"type:code_execution_call": "vct-1"})
        pulse_mock = AsyncMock()
        record_mock = AsyncMock()
        with (
            patched_container(auth_api_keys={}),
            patched_broadcaster() as bc,
            patch(f"{_NODE_MODULE}.build_genai_client", return_value=MagicMock()),
            patch(
                f"{_NODE_MODULE}.stream_interaction",
                AsyncMock(side_effect=fake_stream),
            ),
            patch(f"{_NODE_MODULE}._ops.ensure_cloud_tool_nodes", mint_mock),
            patch(f"{_NODE_MODULE}._ops.pulse_node", pulse_mock),
            patch(f"{_NODE_MODULE}._ops.record_tool_output", record_mock),
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
        # Minted once, LIVE (noise name skipped; empty final steps = no sweep).
        mint_mock.assert_awaited_once()
        assert mint_mock.await_args.kwargs["used"] == {
            "type:code_execution_call": "Code Execution"
        }
        pulses = [(c.args[0], c.args[1]) for c in pulse_mock.await_args_list]
        assert ("vct-1", "executing") in pulses
        assert ("vct-1", "success") in pulses
        assert result["result"]["cloud_tools_used"] == ["Code Execution"]
        # Invocation output recorded on the display node (Output panel).
        record_mock.assert_awaited_once()
        rec_args = record_mock.await_args
        assert rec_args.args[0] == "vct-1"
        payload = rec_args.args[1]
        assert payload["tool"] == "Code Execution"
        assert payload["result"] == {"stdout": "42"}
        assert payload["is_error"] is False


# ============================================================================
# stream_interaction (SSE helper)
# ============================================================================


class _FakeStream:
    def __init__(self, events):
        self._events = list(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


class TestStreamInteraction:
    async def test_on_event_errors_swallowed_and_full_resource_fetched(self):
        from nodes.agent._vertex import stream_interaction

        events = [
            SimpleNamespace(
                event_type="interaction.created",
                interaction=SimpleNamespace(id="ix-9"),
            ),
            SimpleNamespace(
                event_type="step.start",
                step=SimpleNamespace(type="model_output"),
            ),
        ]
        client = MagicMock()
        client.aio.interactions.create = AsyncMock(return_value=_FakeStream(events))
        final = _interaction(interaction_id="ix-9")
        client.aio.interactions.get = AsyncMock(return_value=final)

        async def bad_handler(event):
            raise RuntimeError("boom")

        interaction = await stream_interaction(
            client, on_event=bad_handler, agent="a", input="p"
        )

        assert interaction is final
        client.aio.interactions.get.assert_awaited_with("ix-9")
        create_kwargs = client.aio.interactions.create.await_args.kwargs
        assert create_kwargs["stream"] is True
        assert create_kwargs["background"] is True

    async def test_falls_back_to_poll_when_streaming_rejected(self):
        from nodes.agent._vertex import stream_interaction

        final = _interaction(interaction_id="ix-2")
        client = MagicMock()
        client.aio.interactions.create = AsyncMock(
            side_effect=[FakeGenaiError("streaming not supported"), final]
        )
        client.aio.interactions.get = AsyncMock(return_value=final)

        interaction = await stream_interaction(client, agent="a", input="p")

        assert interaction is final
        # Second create is the non-stream background fallback.
        second = client.aio.interactions.create.await_args_list[1].kwargs
        assert second.get("stream") is not True
        assert second["background"] is True

    async def test_streaming_rejection_latches_poll_for_the_client(self):
        """A rejected streaming create (enterprise 400 'Precondition
        check failed') must not be re-attempted on later turns of the
        same run: the client is latched to the poll path."""
        from nodes.agent._vertex import stream_interaction

        final = _interaction(interaction_id="ix-2")
        client = MagicMock()
        client.aio.interactions.create = AsyncMock(
            side_effect=[
                FakeGenaiError("Error code: 400 - Precondition check failed."),
                final,  # turn 1 poll fallback
                final,  # turn 2 goes straight to poll
            ]
        )
        client.aio.interactions.get = AsyncMock(return_value=final)

        await stream_interaction(client, agent="a", input="p")
        await stream_interaction(client, agent="a", input="p2")

        creates = client.aio.interactions.create.await_args_list
        assert len(creates) == 3
        # Only the very first create attempted streaming.
        assert creates[0].kwargs.get("stream") is True
        assert creates[1].kwargs.get("stream") is not True
        assert creates[2].kwargs.get("stream") is not True


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

    async def test_mints_persists_then_broadcasts(self):
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
            resolved = await _ops.ensure_cloud_tool_nodes(
                workflow_id="wf-1",
                agent_node_id="vx-1",
                used={"fn:run_command": "run_command"},
            )

        assert list(resolved) == ["fn:run_command"]
        minted_id = resolved["fn:run_command"]
        assert minted_id.startswith("vertexCloudTool-")

        # Persisted node + edge with the minted id.
        save_kwargs = database.save_workflow.await_args.kwargs
        persisted_nodes = save_kwargs["data"]["nodes"]
        persisted_edges = save_kwargs["data"]["edges"]
        assert persisted_nodes[0]["id"] == minted_id
        assert persisted_nodes[0]["type"] == "vertexCloudTool"
        assert persisted_edges[0]["source"] == minted_id
        # Tool-node convention: top output-tool handle -> agent's bottom
        # input-tools handle (side attachment was a rendering bug).
        assert persisted_edges[0]["sourceHandle"] == "output-tool"
        assert persisted_edges[0]["targetHandle"] == "input-tools"

        # Persist happens before the ops broadcast.
        broadcast_payload = broadcaster.broadcast.await_args.args[0]
        assert broadcast_payload["type"] == "workflow_ops_apply"
        ops = broadcast_payload["data"]["operations"]
        assert ops[0]["type"] == "add_node"
        assert ops[0]["minted_id"] == minted_id
        assert ops[1]["type"] == "add_edge"
        assert ops[1]["source_handle"] == "output-tool"
        assert ops[1]["target_handle"] == "input-tools"

        # Pulsing is the caller's concern now — mint emits no node_status.
        broadcaster.update_node_status.assert_not_awaited()

    async def test_pulse_node_broadcasts_status(self):
        from nodes.agent.vertex_managed_agent import _ops

        broadcaster = MagicMock()
        broadcaster.update_node_status = AsyncMock()
        with patch.object(_ops, "get_status_broadcaster", return_value=broadcaster):
            await _ops.pulse_node("vct-1", "executing", workflow_id="wf-1")

        args = broadcaster.update_node_status.await_args
        assert args.args[0] == "vct-1"
        assert args.args[1] == "executing"
        assert args.kwargs["workflow_id"] == "wf-1"

    async def test_record_tool_output_persists_then_broadcasts(self):
        from nodes.agent.vertex_managed_agent import _ops

        database = MagicMock()
        database.save_node_output = AsyncMock(return_value=True)
        broadcaster = MagicMock()
        broadcaster.update_node_output = AsyncMock()
        payload = {"tool": "run_command", "result": {"stdout": "ok"}}

        with (
            patch.object(_ops, "get_database", return_value=database),
            patch.object(_ops, "get_status_broadcaster", return_value=broadcaster),
        ):
            await _ops.record_tool_output("vct-1", payload, workflow_id="wf-1")

        # Stored under the "default" session (what the Output panel fetches).
        database.save_node_output.assert_awaited_once_with(
            "vct-1", "default", "output_0", payload
        )
        out_args = broadcaster.update_node_output.await_args
        assert out_args.args[0] == "vct-1"
        assert out_args.args[1] == payload
        assert out_args.kwargs["workflow_id"] == "wf-1"

    async def test_record_tool_output_skips_broadcast_on_persist_failure(self):
        from nodes.agent.vertex_managed_agent import _ops

        database = MagicMock()
        database.save_node_output = AsyncMock(side_effect=RuntimeError("db locked"))
        broadcaster = MagicMock()
        broadcaster.update_node_output = AsyncMock()

        with (
            patch.object(_ops, "get_database", return_value=database),
            patch.object(_ops, "get_status_broadcaster", return_value=broadcaster),
        ):
            await _ops.record_tool_output("vct-1", {"tool": "x"}, workflow_id="wf-1")

        broadcaster.update_node_output.assert_not_awaited()

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
            resolved = await _ops.ensure_cloud_tool_nodes(
                workflow_id="wf-1",
                agent_node_id="vx-1",
                used={"fn:run_command": "run_command"},
            )

        assert resolved == {"fn:run_command": "vertexCloudTool-1-aaa"}
        database.save_workflow.assert_not_awaited()
        broadcaster.broadcast.assert_not_awaited()
        broadcaster.update_node_status.assert_not_awaited()


# ============================================================================
# vertex_agent_admin
# ============================================================================


class TestVertexAgentAdmin:
    def test_palette_group_is_agent_not_tool(self):
        """("tool",) put the admin node in the AI Tools palette and
        auto-derived a bogus isConfigNode hint — but it has no
        output-tool handle, so it can never wire into input-tools."""
        from nodes.agent.vertex_agent_admin import VertexAgentAdminNode

        assert VertexAgentAdminNode.group == ("agent",)
        assert VertexAgentAdminNode.usable_as_tool is False

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
