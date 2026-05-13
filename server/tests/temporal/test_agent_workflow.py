"""F4.B infrastructure tests for ``AgentWorkflow`` + agent activities.

Smoke-level coverage so the worker bootstraps cleanly and the activity
shapes match what ``AgentWorkflow`` expects to schedule. Full
end-to-end testing of the agent loop (LLM step → tool dispatch →
persist → compaction) requires a Temporal test cluster + real plugin
classes — that lives in test_agent_workflow_integration.py once the
canary agent migration lands. This file locks the static contracts:

- AgentWorkflow class is decorated with ``@workflow.defn``.
- Three activities are decorated with ``@activity.defn`` and carry the
  expected ``node`` names.
- ``collect_agent_activities()`` returns them in a stable order.
- The orchestrator's worker registration imports both without error.
"""

from __future__ import annotations

import pytest


class TestAgentWorkflowDefinition:
    """``AgentWorkflow`` must be a valid Temporal workflow definition
    so workers can register it."""

    def test_class_is_workflow_defn(self):
        from services.temporal.agent_workflow import AgentWorkflow

        # ``@workflow.defn`` attaches metadata as ``__temporal_workflow_definition``.
        defn = getattr(AgentWorkflow, "__temporal_workflow_definition", None)
        assert defn is not None, "AgentWorkflow missing @workflow.defn"
        assert defn.name == "AgentWorkflow"

    def test_class_is_sandboxed_false(self):
        """Workflow needs to import frozen registry dicts deterministically
        (for tool type → activity name resolution). Sandboxing must be off
        — same as MachinaWorkflow."""
        from services.temporal.agent_workflow import AgentWorkflow

        defn = getattr(AgentWorkflow, "__temporal_workflow_definition")
        assert defn.sandboxed is False, (
            "AgentWorkflow must be sandboxed=False so it can read "
            "services.node_registry deterministically"
        )


class TestAgentActivities:
    """The three agent activities must register under stable names so
    ``AgentWorkflow`` can schedule them by string."""

    def test_execute_llm_step_registered(self):
        from services.temporal.agent_activities import execute_llm_step

        defn = getattr(execute_llm_step, "__temporal_activity_definition", None)
        assert defn is not None
        assert defn.name == "agent.execute_llm_step.v1"

    def test_persist_agent_turn_registered(self):
        from services.temporal.agent_activities import persist_agent_turn

        defn = getattr(persist_agent_turn, "__temporal_activity_definition")
        assert defn.name == "agent.persist_turn.v1"

    def test_compact_agent_memory_registered(self):
        from services.temporal.agent_activities import compact_agent_memory

        defn = getattr(compact_agent_memory, "__temporal_activity_definition")
        assert defn.name == "agent.compact_memory.v1"

    def test_collect_returns_all_five(self):
        """Each successive sprint added one F4.B agent activity:
        infra (3) → per-agent-wiring +prepare_payload (4) → CloudEvents
        cleanup +broadcast_progress (5). All five must register so the
        AgentWorkflow loop can schedule them by name."""
        from services.temporal.agent_activities import collect_agent_activities

        activities = collect_agent_activities()
        names = sorted(
            getattr(a, "__temporal_activity_definition").name for a in activities
        )
        assert names == [
            "agent.broadcast_progress.v1",
            "agent.compact_memory.v1",
            "agent.execute_llm_step.v1",
            "agent.persist_turn.v1",
            "agent.prepare_payload.v1",
        ]

    def test_prepare_payload_registered(self):
        from services.temporal.agent_activities import prepare_agent_payload

        defn = getattr(prepare_agent_payload, "__temporal_activity_definition")
        assert defn.name == "agent.prepare_payload.v1"

    def test_broadcast_progress_registered(self):
        from services.temporal.agent_activities import broadcast_agent_progress

        defn = getattr(broadcast_agent_progress, "__temporal_activity_definition")
        assert defn.name == "agent.broadcast_progress.v1"


class TestWorkerWiring:
    """Worker registration must include AgentWorkflow + activities so the
    orchestrator can schedule them once the flag flips on. We can't
    spin up a real Temporal client here, but we can verify the
    registration list is built without import errors."""

    def test_agent_workflow_importable_from_worker(self):
        """The worker module imports AgentWorkflow at registration time.
        If that import fails (circular dep, missing symbol, etc.) the
        whole Temporal worker bootstrap dies — catch it here."""
        # Just importing is enough; ImportError would surface in the test
        # output.
        from services.temporal.worker import TemporalWorkerManager  # noqa: F401
        from services.temporal.agent_workflow import AgentWorkflow  # noqa: F401
        from services.temporal.agent_activities import collect_agent_activities  # noqa: F401


class TestPayloadShape:
    """Static checks on the workflow's payload contract — keeps the
    seams visible to anyone refactoring the input pipeline. If a
    required key disappears, this test surfaces it before runtime."""

    REQUIRED_KEYS = (
        "node_id",
        "node_type",
        "provider",
        "model",
        "api_key",
        "system_message",
        "user_prompt",
        "tools",
        "max_iterations",
    )

    def test_required_keys_documented(self):
        """The README-style payload comment in ``AgentWorkflow.run``'s
        docstring must list every required key. Drift = unreadable
        docs + broken callers. Cross-check against an explicit
        constant here so the docstring can't quietly shrink."""
        from services.temporal.agent_workflow import AgentWorkflow

        docstring = AgentWorkflow.run.__doc__ or ""
        missing = [k for k in self.REQUIRED_KEYS if f'"{k}"' not in docstring]
        assert not missing, (
            f"AgentWorkflow.run docstring missing payload keys: {missing}. "
            "If you renamed a field, update both the docstring and the body."
        )
