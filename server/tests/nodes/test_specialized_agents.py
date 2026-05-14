"""Contract tests for specialized agent nodes.

Covers:
- 13 generic specialized agents routed to handle_chat_agent
  (android_agent, coding_agent, web_agent, task_agent, social_agent,
  travel_agent, tool_agent, productivity_agent, payments_agent,
  consumer_agent, autonomous_agent, orchestrator_agent, ai_employee)
- 2 dedicated-handler agents (rlm_agent, claude_code_agent)

These freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/specialized_agents/`. A refactor that breaks
any of these indicates the docs (and the user-visible contract) need to be
updated too.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.nodes._mocks import patched_broadcaster, patched_container, patched_subprocess


pytestmark = pytest.mark.node_contract


# 13 specialized agents that all route to handle_chat_agent.
GENERIC_SPECIALIZED_AGENTS = [
    "android_agent",
    "coding_agent",
    "web_agent",
    "task_agent",
    "social_agent",
    "travel_agent",
    "tool_agent",
    "productivity_agent",
    "payments_agent",
    "consumer_agent",
    "autonomous_agent",
    "orchestrator_agent",
    "ai_employee",
]


TEAM_LEAD_AGENTS = ["orchestrator_agent", "ai_employee"]


# ============================================================================
# Generic specialized agents -- parametrized so we do not repeat 13 near
# identical test bodies.
# ============================================================================


class TestGenericSpecializedAgents:
    """All 13 agents share handle_chat_agent; we verify the dispatch contract
    and the forwarded parameters, not the LLM behaviour."""

    @pytest.mark.parametrize("node_type", GENERIC_SPECIALIZED_AGENTS)
    async def test_dispatch_routes_to_execute_chat_agent(self, harness, node_type):
        # ai_service.execute_chat_agent is already an AsyncMock on the harness.
        with patched_container(auth_api_keys={"openai": "tk"}):
            result = await harness.execute(
                node_type,
                {"provider": "openai", "model": "gpt-4o", "prompt": "hello"},
            )

        harness.assert_envelope(result, success=True)
        assert harness.ai_service.execute_chat_agent.await_count == 1
        # Plugin calls execute_chat_agent(node_id, **kwargs) where kwargs["parameters"] holds the param dict.
        args, kwargs = harness.ai_service.execute_chat_agent.await_args
        assert args[0].startswith(f"test_{node_type}_")
        assert kwargs["parameters"]["provider"] == "openai"
        assert kwargs["parameters"]["model"] == "gpt-4o"
        assert kwargs["parameters"]["prompt"] == "hello"

    @pytest.mark.parametrize("node_type", GENERIC_SPECIALIZED_AGENTS)
    async def test_envelope_payload_shape(self, harness, node_type):
        # Override the mock response so we can assert the propagation.
        harness.ai_service.execute_chat_agent = AsyncMock(
            return_value={
                "success": True,
                "result": {
                    "response": f"hi from {node_type}",
                    "model": "gpt-4o",
                    "provider": "openai",
                },
            }
        )

        with patched_container(auth_api_keys={"openai": "tk"}):
            result = await harness.execute(
                node_type,
                {"provider": "openai", "model": "gpt-4o", "prompt": "x"},
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["response"] == f"hi from {node_type}"
        assert payload["model"] == "gpt-4o"
        assert payload["provider"] == "openai"

    async def test_auto_prompt_fallback_from_input_main(self, harness):
        """Empty prompt should fall back to connected input-main output."""
        harness.ai_service.execute_chat_agent = AsyncMock(
            return_value={"success": True, "result": {"response": "ok"}}
        )

        nodes = [
            {"id": "trigger-1", "type": "chatTrigger"},
            {"id": "agent-1", "type": "coding_agent"},
        ]
        edges = [
            {"source": "trigger-1", "target": "agent-1", "targetHandle": "input-main"},
        ]
        ctx = harness.build_context(
            nodes=nodes,
            edges=edges,
            extra={"outputs": {"trigger-1": {"message": "upstream prompt"}}},
        )

        with patched_container(auth_api_keys={"openai": "tk"}):
            await harness.execute(
                "coding_agent",
                {"provider": "openai", "model": "gpt-4o", "prompt": ""},
                node_id="agent-1",
                context=ctx,
            )

        _, kwargs = harness.ai_service.execute_chat_agent.await_args
        assert kwargs["parameters"]["prompt"] == "upstream prompt"

    async def test_task_completion_strips_tools(self, harness):
        """When task_data.status is completed, tool_data is stripped to []."""
        harness.ai_service.execute_chat_agent = AsyncMock(
            return_value={"success": True, "result": {"response": "done"}}
        )

        # Wire a tool and a task trigger to the agent.
        nodes = [
            {"id": "tool-1", "type": "calculatorTool"},
            {"id": "task-1", "type": "taskTrigger"},
            {"id": "agent-1", "type": "coding_agent"},
        ]
        edges = [
            {"source": "tool-1", "target": "agent-1", "targetHandle": "input-tools"},
            {"source": "task-1", "target": "agent-1", "targetHandle": "input-task"},
        ]
        ctx = harness.build_context(
            nodes=nodes,
            edges=edges,
            extra={
                "outputs": {
                    "task-1": {
                        "task_id": "t-1",
                        "status": "completed",
                        "agent_name": "Child",
                        "result": "child result",
                    }
                }
            },
        )

        with patched_container(auth_api_keys={"openai": "tk"}):
            await harness.execute(
                "coding_agent",
                {"provider": "openai", "model": "gpt-4o", "prompt": "do"},
                node_id="agent-1",
                context=ctx,
            )

        _, kwargs = harness.ai_service.execute_chat_agent.await_args
        # Tools should be stripped (kwarg value is None or []).
        assert not kwargs.get("tool_data")

    @pytest.mark.parametrize("node_type", TEAM_LEAD_AGENTS)
    async def test_team_lead_collects_teammates_as_tools(self, harness, node_type):
        """orchestrator_agent / ai_employee append teammates to tool_data."""
        harness.ai_service.execute_chat_agent = AsyncMock(
            return_value={"success": True, "result": {"response": "team"}}
        )

        nodes = [
            {
                "id": "mate-1",
                "type": "coding_agent",
                "data": {"label": "Mate Coder"},
            },
            {"id": "lead-1", "type": node_type},
        ]
        edges = [
            {
                "source": "mate-1",
                "target": "lead-1",
                "targetHandle": "input-teammates",
            },
        ]
        ctx = harness.build_context(nodes=nodes, edges=edges)

        with patched_container(auth_api_keys={"openai": "tk"}):
            await harness.execute(
                node_type,
                {"provider": "openai", "model": "gpt-4o", "prompt": "delegate"},
                node_id="lead-1",
                context=ctx,
            )

        _, kwargs = harness.ai_service.execute_chat_agent.await_args
        tool_data = kwargs.get("tool_data") or []
        assert any(
            t.get("node_id") == "mate-1" and t.get("node_type") == "coding_agent"
            for t in tool_data
        ), f"expected teammate in tool_data, got {tool_data}"

    async def test_non_team_lead_ignores_teammates_handle(self, harness):
        """A non-team-lead (e.g. coding_agent) does not expand input-teammates."""
        harness.ai_service.execute_chat_agent = AsyncMock(
            return_value={"success": True, "result": {"response": "ok"}}
        )

        nodes = [
            {"id": "mate-1", "type": "web_agent", "data": {"label": "mate"}},
            {"id": "agent-1", "type": "coding_agent"},
        ]
        edges = [
            {
                "source": "mate-1",
                "target": "agent-1",
                "targetHandle": "input-teammates",
            },
        ]
        ctx = harness.build_context(nodes=nodes, edges=edges)

        with patched_container(auth_api_keys={"openai": "tk"}):
            await harness.execute(
                "coding_agent",
                {"provider": "openai", "model": "gpt-4o", "prompt": "x"},
                node_id="agent-1",
                context=ctx,
            )

        _, kwargs = harness.ai_service.execute_chat_agent.await_args
        tool_data = kwargs.get("tool_data") or []
        # No teammate expansion happened because coding_agent is not a team lead.
        assert not any(t.get("node_id") == "mate-1" for t in tool_data)

    async def test_failure_envelope_propagates(self, harness):
        harness.ai_service.execute_chat_agent = AsyncMock(
            return_value={"success": False, "error": "model unavailable"}
        )

        with patched_container(auth_api_keys={"openai": "tk"}):
            result = await harness.execute(
                "coding_agent",
                {"provider": "openai", "model": "gpt-4o", "prompt": "x"},
            )

        harness.assert_envelope(result, success=False)
        assert result["error"] == "model unavailable"


# ============================================================================
# RLM Agent
# ============================================================================


class TestRLMAgent:
    def _wire_rlm_service(self, harness, response=None):
        rlm = MagicMock(name="RLMService")
        rlm.execute = AsyncMock(
            return_value=response
            or {
                "success": True,
                "result": {
                    "response": "final answer",
                    "model": "gpt-4o",
                    "provider": "openai",
                    "iterations": 3,
                },
            }
        )
        harness.ai_service.rlm_service = rlm
        return rlm

    async def test_happy_path_delegates_to_rlm_service(self, harness):
        rlm = self._wire_rlm_service(harness)

        with patched_container(auth_api_keys={"openai": "tk"}), patched_broadcaster():
            result = await harness.execute(
                "rlm_agent",
                {
                    "provider": "openai",
                    "model": "gpt-4o",
                    "prompt": "solve this",
                    "maxIterations": 5,
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["response"] == "final answer"
        assert payload["iterations"] == 3
        assert rlm.execute.await_count == 1

    async def test_missing_rlm_service_surfaces_as_failure(self, harness):
        bad = MagicMock(spec=[])
        harness.ai_service.rlm_service = bad

        with patched_container(auth_api_keys={"openai": "tk"}), patched_broadcaster():
            result = await harness.execute(
                "rlm_agent",
                {"provider": "openai", "model": "gpt-4o", "prompt": "x"},
            )

        harness.assert_envelope(result, success=False)

    async def test_auto_prompt_fallback(self, harness):
        rlm = self._wire_rlm_service(harness)

        nodes = [
            {"id": "trigger-1", "type": "chatTrigger"},
            {"id": "rlm-1", "type": "rlm_agent"},
        ]
        edges = [
            {"source": "trigger-1", "target": "rlm-1", "targetHandle": "input-main"},
        ]
        ctx = harness.build_context(
            nodes=nodes,
            edges=edges,
            extra={"outputs": {"trigger-1": {"text": "fallback prompt"}}},
        )

        with patched_container(auth_api_keys={"openai": "tk"}), patched_broadcaster():
            await harness.execute(
                "rlm_agent",
                {"provider": "openai", "model": "gpt-4o", "prompt": ""},
                node_id="rlm-1",
                context=ctx,
            )

        args, _ = rlm.execute.await_args
        assert args[1]["prompt"] == "fallback prompt"


# ============================================================================
# Claude Code Agent
# ============================================================================


class TestClaudeCodeAgent:
    """The claude_code_agent plugin now goes through `AICliService.run_batch`
    (multi-task batch). Single-prompt input gets adapted to a one-task batch
    for back-compat. These tests mock the new service path."""

    def _wire_cli_service(
        self,
        *,
        response: str = "cli response",
        session_id: str = "sess-abc",
        cost_usd: float = 0.012,
        success: bool = True,
        error: str | None = None,
    ):
        """Mock `AICliService` with a `run_batch` that returns a one-task BatchResult."""
        from services.cli_agent.protocol import (
            BatchResult,
            CanonicalUsage,
            SessionResult,
        )

        async def fake_run_batch(provider, *, tasks, **kwargs):
            tasks_list = list(tasks)
            results = []
            for t in tasks_list:
                results.append(SessionResult(
                    task_id=t.task_id or "t_test",
                    session_id=session_id,
                    provider=provider,
                    prompt=t.prompt,
                    response=response,
                    cost_usd=cost_usd,
                    duration_ms=1234,
                    num_turns=2,
                    canonical_usage=CanonicalUsage(input_tokens=10, output_tokens=5),
                    success=success,
                    error=error,
                ))
            n_succeeded = sum(1 for r in results if r.success)
            return BatchResult(
                tasks=results,
                n_tasks=len(results),
                n_succeeded=n_succeeded,
                n_failed=len(results) - n_succeeded,
                total_cost_usd=cost_usd if all(r.success for r in results) else None,
                wall_clock_ms=1500,
                provider=provider,
                timestamp="2026-05-04T00:00:00Z",
            )

        svc = MagicMock(name="AICliService")
        svc.run_batch = AsyncMock(side_effect=fake_run_batch)
        svc.cancel_workflow = AsyncMock(return_value=0)
        svc.cancel_node = AsyncMock(return_value=0)
        return svc

    async def test_happy_path_routes_through_run_batch(self, harness):
        svc = self._wire_cli_service()

        with patched_container(auth_api_keys={}), patched_broadcaster(), patch(
            "services.cli_agent.service.get_ai_cli_service",
            return_value=svc,
        ):
            result = await harness.execute(
                "claude_code_agent",
                {
                    "prompt": "write a hello world script",
                    "model": "claude-sonnet-4-6",
                },
            )

        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["response"] == "cli response"
        assert payload["session_id"] == "sess-abc"
        assert payload["provider"] == "claude"
        assert payload["n_tasks"] == 1
        assert payload["n_succeeded"] == 1

        # AICliService.run_batch was called with provider="claude" + 1 task
        call = svc.run_batch.await_args
        assert call.args[0] == "claude"
        tasks = list(call.kwargs["tasks"])
        assert len(tasks) == 1
        assert tasks[0].prompt == "write a hello world script"
        assert tasks[0].model == "claude-sonnet-4-6"

    async def test_max_budget_usd_propagates_via_task_spec(self, harness):
        """Per-task max_budget_usd must reach the ClaudeTaskSpec."""
        svc = self._wire_cli_service()

        with patched_container(auth_api_keys={}), patched_broadcaster(), patch(
            "services.cli_agent.service.get_ai_cli_service",
            return_value=svc,
        ):
            await harness.execute(
                "claude_code_agent",
                {
                    "tasks": [
                        {"prompt": "x", "provider": "claude", "max_budget_usd": 7.5},
                    ],
                },
            )

        call = svc.run_batch.await_args
        tasks = list(call.kwargs["tasks"])
        assert tasks[0].max_budget_usd == 7.5

    async def test_no_prompt_returns_failure(self, harness):
        """No prompt + no tasks must short-circuit before constructing a batch."""
        svc = self._wire_cli_service()

        with patched_container(auth_api_keys={}), patched_broadcaster(), patch(
            "services.cli_agent.service.get_ai_cli_service",
            return_value=svc,
        ):
            result = await harness.execute(
                "claude_code_agent",
                {"prompt": ""},
            )

        harness.assert_envelope(result, success=False)
        assert "prompt" in result["error"].lower()
        # AICliService must not have been engaged.
        assert svc.run_batch.await_count == 0

    async def test_run_batch_failure_becomes_envelope(self, harness):
        """When AICliService.run_batch raises, the handler surfaces the error."""
        svc = MagicMock(name="AICliService")
        svc.run_batch = AsyncMock(side_effect=RuntimeError("cli exit 1: boom"))

        with patched_container(auth_api_keys={}), patched_broadcaster(), patch(
            "services.cli_agent.service.get_ai_cli_service",
            return_value=svc,
        ):
            result = await harness.execute(
                "claude_code_agent",
                {"prompt": "do something"},
            )

        harness.assert_envelope(result, success=False)
        assert "boom" in result["error"]

    async def test_auto_prompt_fallback_from_input_main(self, harness):
        svc = self._wire_cli_service()

        nodes = [
            {"id": "src-1", "type": "chatTrigger"},
            {"id": "cc-1", "type": "claude_code_agent"},
        ]
        edges = [
            {"source": "src-1", "target": "cc-1", "targetHandle": "input-main"},
        ]
        ctx = harness.build_context(
            nodes=nodes,
            edges=edges,
            extra={"outputs": {"src-1": {"message": "upstream text"}}},
        )

        with patched_container(auth_api_keys={}), patched_broadcaster(), patch(
            "services.cli_agent.service.get_ai_cli_service",
            return_value=svc,
        ):
            await harness.execute(
                "claude_code_agent",
                {"prompt": ""},
                node_id="cc-1",
                context=ctx,
            )

        call = svc.run_batch.await_args
        tasks = list(call.kwargs["tasks"])
        assert tasks[0].prompt == "upstream text"
