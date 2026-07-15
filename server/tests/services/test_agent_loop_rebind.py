"""Unit tests for the mid-loop tool-rebind path in ``_run_agent_loop``.

The agent loop, after each tool call, must:

* Detect a ``operations`` field in the tool result (canvas-mutating
  tools like ``agentBuilder`` emit a ``workflow_ops`` batch there).
* Invoke the supplied ``rebind_from_operations`` callback with the ops.
* Append the returned tools to the bound LLM surface and re-call
  ``chat_model.bind_tools`` so the LLM sees the new wiring on the
  NEXT iteration (no Run-stop-Run cycle).

Driven by source-aware mocks of ``chat_model`` so the test runs
without a live LLM. The ``execute_agent`` + ``execute_chat_agent``
wirings get source-introspection coverage in
``test_agent_loop_rebind_wiring`` below — full end-to-end live testing
of those happens in the existing integration suite.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from services import ai as ai_module
from services.tool_identity import DuplicateToolNameError


# ----------------------------------------------------------------------------
# Mock chat-model harness
# ----------------------------------------------------------------------------


class _FakeBoundModel:
    """Returns scripted ``AIMessage`` responses. The response queue is
    a SHARED list reference across all bound models built from the
    same _FakeChatModel — so a rebind doesn't restart the script."""

    def __init__(self, shared_responses: List[AIMessage]):
        self._responses = shared_responses  # SHARED ref, not a copy
        self.invocations: List[List[Any]] = []

    async def ainvoke(self, messages):
        self.invocations.append(messages)
        if not self._responses:
            return AIMessage(content="done")
        return self._responses.pop(0)


class _FakeChatModel:
    """Toplevel chat model. ``bind_tools`` returns a _FakeBoundModel
    that pops from a single shared response queue."""

    def __init__(self, responses: List[AIMessage]):
        self._responses = list(responses)  # local mutable copy
        self.bind_calls: List[List[Any]] = []
        self.last_bound: _FakeBoundModel | None = None

    def bind_tools(self, tools):
        self.bind_calls.append(list(tools))
        self.last_bound = _FakeBoundModel(self._responses)
        return self.last_bound

    async def ainvoke(self, messages):
        # Only reached when no tools wired — _run_agent_loop binds tools
        # if any are present.
        if not self._responses:
            return AIMessage(content="done")
        return self._responses.pop(0)


class _FakeTool:
    """Minimal stand-in matching ``StructuredTool`` surface that
    ``chat_model.bind_tools`` accepts. Just needs a ``name``."""

    def __init__(self, name: str):
        self.name = name


# ----------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgentLoopRebind:
    async def test_rebind_callback_invoked_with_operations(self):
        """When a tool result carries ``operations``, the loop must
        invoke ``rebind_from_operations`` with that list."""
        # Iteration 1: LLM emits a tool_call. Iteration 2: returns final.
        responses = [
            AIMessage(content="", tool_calls=[{"name": "agentBuilder", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
        chat_model = _FakeChatModel(responses)
        initial_tool = _FakeTool("agentBuilder")

        async def tool_executor(name: str, args: Dict[str, Any]) -> Any:
            # Return ops so the loop triggers rebind.
            return {
                "operation": "add_tool",
                "summary": "Added 'httpRequest'.",
                "operations": [
                    {"type": "add_node", "client_ref": "new_httpRequest", "node_type": "httpRequest"},
                    {"type": "add_edge", "source": {"client_ref": "new_httpRequest"}, "target": "agent-1"},
                ],
            }

        captured_ops: List[List[Dict[str, Any]]] = []

        async def rebind_from_operations(ops: List[Dict[str, Any]]) -> List[Any]:
            captured_ops.append(ops)
            return [_FakeTool("httpRequest")]

        await ai_module._run_agent_loop(
            chat_model,
            [HumanMessage(content="go")],
            tools=[initial_tool],
            tool_executor=tool_executor,
            max_iterations=5,
            rebind_from_operations=rebind_from_operations,
        )

        assert len(captured_ops) == 1, "rebind_from_operations must be called once per tool result with operations"
        assert captured_ops[0][0]["type"] == "add_node"
        assert captured_ops[0][0]["node_type"] == "httpRequest"

    async def test_bind_tools_called_again_after_rebind(self):
        """Returned tools from ``rebind_from_operations`` must extend
        the bound surface — ``chat_model.bind_tools`` is invoked a
        second time with the larger list."""
        responses = [
            AIMessage(content="", tool_calls=[{"name": "agentBuilder", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
        chat_model = _FakeChatModel(responses)
        initial_tool = _FakeTool("agentBuilder")
        new_tool = _FakeTool("httpRequest")

        async def tool_executor(name, args):
            return {"operations": [{"type": "add_node", "node_type": "httpRequest", "client_ref": "x"}]}

        async def rebind_from_operations(ops):
            return [new_tool]

        await ai_module._run_agent_loop(
            chat_model,
            [HumanMessage(content="go")],
            tools=[initial_tool],
            tool_executor=tool_executor,
            max_iterations=5,
            rebind_from_operations=rebind_from_operations,
        )

        # bind_tools must be called twice: once at loop start, once
        # after the canvas mutation.
        assert len(chat_model.bind_calls) == 2
        # First call: only the initial tool.
        assert chat_model.bind_calls[0] == [initial_tool]
        # Second call: initial + new tool (extending, not replacing).
        assert chat_model.bind_calls[1] == [initial_tool, new_tool]

    async def test_no_rebind_when_callback_omitted(self):
        """Without ``rebind_from_operations`` (toggle off), the loop
        does NOT rebind even when ops are present — preserves the
        legacy "wait for next run" behaviour."""
        responses = [
            AIMessage(content="", tool_calls=[{"name": "agentBuilder", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
        chat_model = _FakeChatModel(responses)
        initial_tool = _FakeTool("agentBuilder")

        async def tool_executor(name, args):
            return {"operations": [{"type": "add_node", "node_type": "httpRequest", "client_ref": "x"}]}

        await ai_module._run_agent_loop(
            chat_model,
            [HumanMessage(content="go")],
            tools=[initial_tool],
            tool_executor=tool_executor,
            max_iterations=5,
            rebind_from_operations=None,
        )

        # Only the initial bind_tools call — no rebind.
        assert len(chat_model.bind_calls) == 1

    async def test_no_rebind_when_tool_returns_no_operations(self):
        """A tool that returns a regular result (no ``operations`` key)
        must not trigger a rebind even when the callback is wired."""
        responses = [
            AIMessage(content="", tool_calls=[{"name": "calculator", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
        chat_model = _FakeChatModel(responses)
        initial_tool = _FakeTool("calculator")

        async def tool_executor(name, args):
            return {"result": 42}  # No ``operations`` field.

        called = False

        async def rebind_from_operations(ops):
            nonlocal called
            called = True
            return []

        await ai_module._run_agent_loop(
            chat_model,
            [HumanMessage(content="go")],
            tools=[initial_tool],
            tool_executor=tool_executor,
            max_iterations=5,
            rebind_from_operations=rebind_from_operations,
        )

        assert not called
        assert len(chat_model.bind_calls) == 1

    async def test_duplicate_hot_rebind_is_structured_and_not_bound(self):
        """An ambiguous hot refresh is rejected before the model surface
        changes, and the conflict is returned in the originating tool result."""
        responses = [
            AIMessage(content="", tool_calls=[{"name": "agentBuilder", "args": {}, "id": "c1"}]),
            AIMessage(content="done"),
        ]
        chat_model = _FakeChatModel(responses)
        initial_tool = _FakeTool("agentBuilder")

        async def tool_executor(name, args):
            return {"operations": [{"type": "add_node", "node_type": "agentBuilder"}]}

        async def rebind_from_operations(ops):
            raise DuplicateToolNameError(
                {
                    "agentBuilder": [
                        {"node_id": "builder-a", "label": "Builder A"},
                        {"node_id": "builder-b", "label": "Builder B"},
                    ]
                }
            )

        state = await ai_module._run_agent_loop(
            chat_model,
            [HumanMessage(content="go")],
            tools=[initial_tool],
            tool_executor=tool_executor,
            max_iterations=5,
            rebind_from_operations=rebind_from_operations,
        )

        assert len(chat_model.bind_calls) == 1
        tool_message = state["messages"][2]
        assert "DuplicateToolNameError" in tool_message.content
        assert "builder-a" in tool_message.content


# ----------------------------------------------------------------------------
# Wiring source-introspection — confirm execute_agent + execute_chat_agent
# provide the rebind callback to _run_agent_loop.
# ----------------------------------------------------------------------------


class TestRebindAcceptsDualPurposePlugins:
    """The rebind path (both F4.A in-process closures and the F4.B
    ``agent.refresh_tools.v1`` activity) must build StructuredTools
    for **dual-purpose** ActionNode plugins (``usable_as_tool=True``),
    not only pure ToolNodes (``component_kind == 'tool'``).

    Without this, every ``twitterSearch`` / ``googleGmail`` /
    ``pythonExecutor`` add_tool call returns success on the LLM-visible
    surface but the tool never makes it into ``chat_model.bind_tools``
    — so the next iteration's LLM calls an unknown tool and the run
    burns iterations on retries.
    """

    def test_ai_rebind_accepts_dual_purpose(self):
        import inspect

        from services.ai import AIService

        src = inspect.getsource(AIService.execute_agent)
        # Must use the broader filter — pure tool OR dual-purpose
        # ActionNode (usable_as_tool=True), excluding chat models.
        assert "usable_as_tool" in src, (
            "execute_agent's rebind closure must look at usable_as_tool, "
            "not just component_kind=='tool'."
        )
        # Explicit ``!= 'model'`` guard so the broadening doesn't sweep
        # in chat-model plugins like openaiChatModel that carry
        # ``usable_as_tool=True`` but aren't agent tools.
        assert '"model"' in src or "'model'" in src, (
            "Rebind broadening must explicitly exclude component_kind='model'."
        )

    def test_ai_rebind_excludes_chat_models(self):
        """execute_chat_agent's rebind closure shares the same broadened
        filter — locks both call sites."""
        import inspect

        from services.ai import AIService

        src = inspect.getsource(AIService.execute_chat_agent)
        assert "usable_as_tool" in src

    def test_temporal_refresh_accepts_dual_purpose(self):
        import inspect

        from services.temporal.agent_activities import refresh_agent_tools

        src = inspect.getsource(refresh_agent_tools)
        assert "usable_as_tool" in src, (
            "agent.refresh_tools.v1 must build tools for dual-purpose "
            "plugins (twitterSearch, googleGmail, pythonExecutor, …) — "
            "the bulk of useful LLM-callable tools live there, not in "
            "pure ToolNode plugins."
        )


class TestRebindIdAlignment:
    """The rebind path must use the BE-minted ``minted_id`` (set by
    agentBuilder so the FE applier adopts it verbatim) as the new
    tool's ``node_id``. Without this the dispatcher's status
    broadcasts target a synthesized id that doesn't match any React
    Flow node → canvas doesn't glow on the rebound tool's first run.
    """

    def test_ai_rebind_prefers_minted_id(self):
        import inspect

        from services.ai import AIService

        src = inspect.getsource(AIService.execute_agent)
        assert 'op.get("minted_id")' in src, (
            "execute_agent's rebind closure must prefer op['minted_id'] over "
            "op['client_ref'] when synthesizing the tool's node_id, otherwise "
            "the canvas can't glow on the rebound tool."
        )
        src = inspect.getsource(AIService.execute_chat_agent)
        assert 'op.get("minted_id")' in src, (
            "execute_chat_agent's rebind closure must prefer op['minted_id']."
        )

    def test_temporal_refresh_tools_prefers_minted_id(self):
        import inspect

        from services.temporal.agent_activities import refresh_agent_tools

        src = inspect.getsource(refresh_agent_tools)
        assert 'op.get("minted_id")' in src, (
            "agent.refresh_tools.v1 activity must prefer op['minted_id'] when "
            "synthesizing the tool's node_id so F4.B status broadcasts align "
            "with the FE-adopted React Flow id."
        )


class TestAgentLoopRebindWiring:
    def test_execute_agent_passes_rebind_callback(self):
        from services.ai import AIService

        src = inspect.getsource(AIService.execute_agent)
        assert "_rebind_from_operations" in src, (
            "execute_agent must define a rebind closure backed by "
            "_build_tool_from_node and pass it into _run_agent_loop."
        )
        assert "rebind_from_operations=" in src, "execute_agent must wire the rebind callback into _run_agent_loop."
        assert "auto_rebind_tools_after_canvas_change" in src, (
            "execute_agent must gate the rebind on the UserSettings flag."
        )

    def test_execute_chat_agent_passes_rebind_callback(self):
        from services.ai import AIService

        src = inspect.getsource(AIService.execute_chat_agent)
        assert "_rebind_from_operations" in src
        assert "rebind_from_operations=" in src
        assert "auto_rebind_tools_after_canvas_change" in src

    def test_both_agents_inject_flag_into_tool_config(self):
        """Both agent paths must inject ``auto_rebind_tools`` into the
        per-tool config so agentBuilder's summary text reflects the
        user's preference."""
        from services.ai import AIService

        for func in (AIService.execute_agent, AIService.execute_chat_agent):
            src = inspect.getsource(func)
            assert 'config["auto_rebind_tools"] = auto_rebind_enabled' in src, (
                f"{func.__name__} must inject the auto_rebind_tools flag into the tool config."
            )
