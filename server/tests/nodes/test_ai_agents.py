"""Contract tests for ai_agents nodes: aiAgent, chatAgent, simpleMemory.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/ai_agents/`. A refactor that breaks any of
these indicates the docs (and the user-visible contract) need to be updated.

Invocation notes
================
- aiAgent / chatAgent go through NodeExecutor dispatch; the harness binds
  `handle_ai_agent` / `handle_chat_agent` with stubbed `ai_service` and
  `database`. We drive the connection-collection logic by building real
  `nodes` + `edges` lists and asserting what the AsyncMock service receives.
- simpleMemory is exercised through dispatch too; it uses the in-memory
  `services.memory_store` module (NOT the markdown path used by agents),
  so we also test the module-global state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


pytestmark = pytest.mark.node_contract


# ============================================================================
# Helpers
# ============================================================================


def _edge(source: str, target: str, target_handle: str) -> dict:
    return {
        "source": source,
        "target": target,
        "targetHandle": target_handle,
    }


def _node(node_id: str, node_type: str, label: str | None = None) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "data": {"label": label or node_type},
    }


# ============================================================================
# aiAgent
# ============================================================================


class TestAIAgent:
    """handle_ai_agent + _collect_agent_connections contract."""

    async def test_happy_path_no_connections(self, harness):
        """No connected nodes -> execute_agent called with no memory/skill/tool."""
        result = await harness.execute(
            "aiAgent",
            {"prompt": "hello", "model": "gpt-test"},
        )

        harness.assert_envelope(result, success=True)
        assert result["result"]["response"] == "mocked agent response"

        harness.ai_service.execute_agent.assert_awaited_once()
        _, kwargs = harness.ai_service.execute_agent.call_args
        assert kwargs["memory_data"] is None
        assert kwargs["skill_data"] is None
        assert kwargs["tool_data"] is None
        assert kwargs["broadcaster"] is not None

    async def test_memory_connection_forwarded_with_auto_session(self, harness):
        """simpleMemory wired to input-memory -> memory_data forwarded.

        When the memory node's session_id is empty, the session falls back to
        the agent's own node_id (documented auto-derivation).
        """
        agent_id = "agent-1"
        mem_id = "mem-1"

        harness.database.get_node_parameters = AsyncMock(
            return_value={
                "session_id": "",  # triggers auto-derivation
                "window_size": 7,
                "memory_content": "# Conversation History\n\n### **Human** (t)\nhi\n",
                "long_term_enabled": True,
                "retrieval_count": 5,
                "embedding_provider": "ollama",
                "embedding_endpoint": "http://localhost:11434",
            }
        )

        nodes = [_node(agent_id, "aiAgent"), _node(mem_id, "simpleMemory")]
        edges = [_edge(mem_id, agent_id, "input-memory")]

        result = await harness.execute(
            "aiAgent",
            {"prompt": "p", "model": "m"},
            node_id=agent_id,
            nodes=nodes,
            edges=edges,
        )

        harness.assert_envelope(result, success=True)
        _, kwargs = harness.ai_service.execute_agent.call_args
        mem = kwargs["memory_data"]
        assert mem is not None
        assert mem["node_id"] == mem_id
        assert mem["session_id"] == agent_id  # auto-derived
        assert mem["window_size"] == 7
        assert mem["long_term_enabled"] is True
        assert mem["retrieval_count"] == 5
        assert mem["embedding_provider"] == "ollama"
        assert mem["embedding_model"] == "nomic-embed-text"
        assert mem["embedding_endpoint"] == "http://localhost:11434"
        assert "### **Human**" in mem["memory_content"]

    async def test_memory_explicit_session_overrides_auto(self, harness):
        agent_id = "agent-x"
        mem_id = "mem-x"
        harness.database.get_node_parameters = AsyncMock(
            return_value={"session_id": "shared-session", "window_size": 3, "memory_content": ""}
        )

        nodes = [_node(agent_id, "aiAgent"), _node(mem_id, "simpleMemory")]
        edges = [_edge(mem_id, agent_id, "input-memory")]

        await harness.execute(
            "aiAgent",
            {"prompt": "p"},
            node_id=agent_id,
            nodes=nodes,
            edges=edges,
        )

        _, kwargs = harness.ai_service.execute_agent.call_args
        assert kwargs["memory_data"]["session_id"] == "shared-session"

    async def test_tool_connection_forwarded(self, harness):
        """A tool node wired to input-tools becomes a tool_data entry."""
        agent_id = "agent-2"
        tool_id = "calc-1"

        harness.database.get_node_parameters = AsyncMock(return_value={"tool_name": "calculator"})

        nodes = [_node(agent_id, "aiAgent"), _node(tool_id, "calculatorTool", "Calc")]
        edges = [_edge(tool_id, agent_id, "input-tools")]

        await harness.execute(
            "aiAgent",
            {"prompt": "compute", "model": "m"},
            node_id=agent_id,
            nodes=nodes,
            edges=edges,
        )

        _, kwargs = harness.ai_service.execute_agent.call_args
        tool_data = kwargs["tool_data"]
        assert tool_data is not None
        assert len(tool_data) == 1
        assert tool_data[0]["node_id"] == tool_id
        assert tool_data[0]["node_type"] == "calculatorTool"
        assert tool_data[0]["label"] == "Calc"

    async def test_master_skill_expands_enabled_skills_only(self, harness):
        """masterSkill with enabled=True entries is expanded, disabled skipped."""
        import sys
        import types
        from unittest.mock import patch

        agent_id = "agent-3"
        skill_id = "skill-1"

        harness.database.get_node_parameters = AsyncMock(
            return_value={
                "skills_config": {
                    "python-skill": {"enabled": True, "instructions": "Use python."},
                    "shell-skill": {"enabled": False, "instructions": "ignored"},
                }
            }
        )

        # Stub skill loader so the fallback path isn't taken (instructions are present in DB)
        fake_loader_mod = types.ModuleType("services.skill_loader")
        fake_loader_mod.get_skill_loader = MagicMock(return_value=MagicMock())

        nodes = [_node(agent_id, "aiAgent"), _node(skill_id, "masterSkill")]
        edges = [_edge(skill_id, agent_id, "input-skill")]

        with patch.dict(sys.modules, {"services.skill_loader": fake_loader_mod}):
            await harness.execute(
                "aiAgent",
                {"prompt": "p"},
                node_id=agent_id,
                nodes=nodes,
                edges=edges,
            )

        _, kwargs = harness.ai_service.execute_agent.call_args
        skill_data = kwargs["skill_data"]
        assert skill_data is not None
        # The Master Skill's required Assistant ``skill`` entry is always
        # present so every connected agent can learn the progressive-loading
        # contract through the same skill surface.  User-disabled entries are
        # still excluded.
        names = {item["skill_name"] for item in skill_data}
        assert names == {"python-skill", "skill"}
        assert "shell-skill" not in names
        assert skill_data[0]["parameters"]["instructions"] == "Use python."

    async def test_task_completion_strips_all_tools(self, harness):
        """When an input-task edge delivers a completed task, tool_data is cleared.

        Documented as a CRITICAL FIX in the handler: binding tools while telling
        the LLM "do not use tools" confused Gemini.
        """
        agent_id = "agent-4"
        trigger_id = "task-trig-1"
        tool_id = "tool-a"

        task_payload = {
            "task_id": "abc",
            "status": "completed",
            "agent_name": "coding_agent",
            "result": "done",
        }

        async def db_params(node_id):
            if node_id == tool_id:
                return {"toolName": "calculator"}
            return {}

        harness.database.get_node_parameters = AsyncMock(side_effect=db_params)

        nodes = [
            _node(agent_id, "aiAgent"),
            _node(trigger_id, "taskTrigger"),
            _node(tool_id, "calculatorTool"),
        ]
        edges = [
            _edge(trigger_id, agent_id, "input-task"),
            _edge(tool_id, agent_id, "input-tools"),
        ]

        ctx = harness.build_context(nodes=nodes, edges=edges)
        # Inject the completed-task payload into context.outputs where
        # _collect_agent_connections looks for input-task data.
        ctx["outputs"] = {trigger_id: task_payload}

        await harness.executor.execute(
            node_id=agent_id,
            node_type="aiAgent",
            parameters={"prompt": "original", "model": "m"},
            context=ctx,
        )

        _, kwargs = harness.ai_service.execute_agent.call_args
        # Tools stripped on task completion
        assert kwargs["tool_data"] is None

        # Prompt got the task context prepended
        sent_params = harness.ai_service.execute_agent.call_args.kwargs["parameters"]
        assert "delegated task has completed" in sent_params["prompt"]
        assert "original" in sent_params["prompt"]

    async def test_service_error_propagates_through_envelope(self, harness):
        """execute_agent can return a failure envelope; handler must pass it through."""
        harness.ai_service.execute_agent = AsyncMock(return_value={"success": False, "error": "provider 429"})

        result = await harness.execute(
            "aiAgent",
            {"prompt": "x", "model": "m"},
        )

        harness.assert_envelope(result, success=False)
        assert "provider 429" in result["error"]


# ============================================================================
# chatAgent (Zeenie)
# ============================================================================


class TestChatAgent:
    """handle_chat_agent - same collection logic as aiAgent, different service call."""

    async def test_happy_path_routes_to_execute_chat_agent(self, harness):
        result = await harness.execute(
            "chatAgent",
            {"prompt": "hi", "model": "m"},
        )

        harness.assert_envelope(result, success=True)
        harness.ai_service.execute_chat_agent.assert_awaited_once()
        # Must NOT call execute_agent
        harness.ai_service.execute_agent.assert_not_awaited()
        assert result["result"]["response"] == "mocked chat agent response"

    async def test_empty_prompt_auto_fills_from_input_main(self, harness):
        """chatAgent's key distinction: empty prompt + input-main -> use upstream output."""
        chat_id = "chat-1"
        upstream_id = "trigger-1"

        nodes = [_node(chat_id, "chatAgent"), _node(upstream_id, "chatTrigger")]
        edges = [_edge(upstream_id, chat_id, "input-main")]

        ctx = harness.build_context(nodes=nodes, edges=edges)
        ctx["outputs"] = {upstream_id: {"message": "from upstream"}}

        await harness.executor.execute(
            node_id=chat_id,
            node_type="chatAgent",
            parameters={"prompt": "", "model": "m"},  # empty prompt
            context=ctx,
        )

        sent_params = harness.ai_service.execute_chat_agent.call_args.kwargs["parameters"]
        assert sent_params["prompt"] == "from upstream"

    async def test_input_fallback_prefers_message_over_text_over_content(self, harness):
        chat_id = "chat-2"
        up_id = "up-2"
        nodes = [_node(chat_id, "chatAgent"), _node(up_id, "httpRequest")]
        edges = [_edge(up_id, chat_id, "input-main")]

        ctx = harness.build_context(nodes=nodes, edges=edges)
        # All three present - message must win
        ctx["outputs"] = {up_id: {"message": "via-message", "text": "via-text", "content": "via-content"}}

        await harness.executor.execute(
            node_id=chat_id,
            node_type="chatAgent",
            parameters={"prompt": ""},
            context=ctx,
        )

        sent_params = harness.ai_service.execute_chat_agent.call_args.kwargs["parameters"]
        assert sent_params["prompt"] == "via-message"

    async def test_non_empty_prompt_is_NOT_overridden_by_input_data(self, harness):
        """If prompt already has content, input_data must not clobber it."""
        chat_id = "chat-3"
        up_id = "up-3"
        nodes = [_node(chat_id, "chatAgent"), _node(up_id, "chatTrigger")]
        edges = [_edge(up_id, chat_id, "input-main")]

        ctx = harness.build_context(nodes=nodes, edges=edges)
        ctx["outputs"] = {up_id: {"message": "should-not-leak"}}

        await harness.executor.execute(
            node_id=chat_id,
            node_type="chatAgent",
            parameters={"prompt": "explicit"},
            context=ctx,
        )

        sent_params = harness.ai_service.execute_chat_agent.call_args.kwargs["parameters"]
        assert sent_params["prompt"] == "explicit"

    async def test_orchestrator_adds_teammates_as_delegation_tools(self, harness):
        """Team-lead types append input-teammates agents to tool_data."""
        lead_id = "lead-1"
        teammate_id = "worker-1"

        async def db_params(node_id):
            if node_id == teammate_id:
                return {"provider": "openai", "model": "gpt"}
            return {}

        harness.database.get_node_parameters = AsyncMock(side_effect=db_params)

        nodes = [
            _node(lead_id, "orchestrator_agent"),
            _node(teammate_id, "coding_agent", "CoderBot"),
        ]
        edges = [_edge(teammate_id, lead_id, "input-teammates")]

        await harness.execute(
            "orchestrator_agent",
            {"prompt": "do a thing", "model": "m"},
            node_id=lead_id,
            nodes=nodes,
            edges=edges,
        )

        _, kwargs = harness.ai_service.execute_chat_agent.call_args
        tool_data = kwargs["tool_data"]
        assert tool_data is not None and len(tool_data) == 2
        teammate_tool = next(tool for tool in tool_data if tool["node_type"] == "coding_agent")
        assert teammate_tool["label"] == "CoderBot"
        assert any(tool["node_type"] == "taskManager" and tool.get("builtin") for tool in tool_data)
        # Teammate params come along so delegation knows provider/model
        assert teammate_tool["parameters"]["model"] == "gpt"

    async def test_chat_agent_task_error_strips_tools(self, harness):
        """Same tool-strip behaviour as aiAgent on task failure."""
        chat_id = "chat-4"
        trig_id = "trig-4"
        tool_id = "tool-b"

        async def db_params(nid):
            return {"toolName": "calc"} if nid == tool_id else {}

        harness.database.get_node_parameters = AsyncMock(side_effect=db_params)

        nodes = [
            _node(chat_id, "chatAgent"),
            _node(trig_id, "taskTrigger"),
            _node(tool_id, "calculatorTool"),
        ]
        edges = [
            _edge(trig_id, chat_id, "input-task"),
            _edge(tool_id, chat_id, "input-tools"),
        ]

        ctx = harness.build_context(nodes=nodes, edges=edges)
        ctx["outputs"] = {trig_id: {"task_id": "x", "status": "error", "error": "boom", "agent_name": "web_agent"}}

        await harness.executor.execute(
            node_id=chat_id,
            node_type="chatAgent",
            parameters={"prompt": "p", "model": "m"},
            context=ctx,
        )

        _, kwargs = harness.ai_service.execute_chat_agent.call_args
        assert kwargs["tool_data"] is None
        sent_params = harness.ai_service.execute_chat_agent.call_args.kwargs["parameters"]
        assert "failed" in sent_params["prompt"].lower()


# ============================================================================
# simpleMemory
# ============================================================================


class TestSimpleMemory:
    """handle_simple_memory reads from services.memory_store (NOT markdown)."""

    @pytest.fixture(autouse=True)
    def _reset_memory_store(self):
        """Snapshot + restore the module-global session dict."""
        from services import memory_store as ms

        backup = dict(ms._sessions)
        ms._sessions.clear()
        try:
            yield ms
        finally:
            ms._sessions.clear()
            ms._sessions.update(backup)

    async def test_happy_path_empty_session(self, harness, _reset_memory_store):
        result = await harness.execute(
            "simpleMemory",
            {"session_id": "sess-a"},
        )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["session_id"] == "sess-a"
        assert payload["messages"] == []
        assert payload["message_count"] == 0
        # window_size always defaults to 100 now (no buffer/window split)
        assert payload["window_size"] == 100

    async def test_window_size_returns_last_n_messages(self, harness, _reset_memory_store):
        ms = _reset_memory_store
        for i in range(5):
            ms.add_message("sess-b", "human", f"h{i}")
            ms.add_message("sess-b", "ai", f"a{i}")

        result = await harness.execute(
            "simpleMemory",
            {"session_id": "sess-b", "window_size": 2},
        )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["window_size"] == 2
        # last 2 messages from a 10-message session
        assert payload["message_count"] == 2
        contents = [m["content"] for m in payload["messages"]]
        assert contents == ["h4", "a4"]

    async def test_default_session_when_omitted(self, harness, _reset_memory_store):
        result = await harness.execute("simpleMemory", {})

        harness.assert_envelope(result, success=True)
        # Empty session_id resolves to "default" sentinel
        assert result["result"]["session_id"] == "default"
        assert result["result"]["window_size"] == 100
