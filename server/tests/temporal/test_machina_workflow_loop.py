"""Regression: MachinaWorkflow's scheduling loop must re-evaluate
readiness after auto-completing a non-pre-executed trigger.

Live-test finding (Wave 16 verification, 2026-07-09): on the direct
canvas-Run path the ``start`` node is NOT ``_pre_executed`` — it hits
the skip branch inside the scheduling loop, which completes it without
putting anything into ``running``. The old exit check (``if not
running: break``) then terminated the workflow before the next
``_find_ready_nodes`` pass could pick up the just-unblocked downstream
nodes: a ``start -> pythonExecutor`` graph returned
``success=false`` with only the start node in the trace and ZERO
activities scheduled. The deployed path never sees this because
triggers are pre-executed before the loop.

Runs the real ``MachinaWorkflow.run`` body with ``start_activity`` /
``logger`` monkeypatched — no Temporal cluster required (same pattern
as test_trigger_listener_workflow.py).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import nodes  # noqa: F401 -- populate plugin registry


@pytest.fixture(autouse=True)
def _patch_workflow_logger(monkeypatch):
    from temporalio import workflow as temporal_workflow

    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())


def _graph():
    nodes_ = [
        {"id": "start-1", "type": "start", "data": {"label": "Start"}},
        {"id": "py-1", "type": "pythonExecutor", "data": {"label": "Py"}},
    ]
    edges = [
        {
            "id": "e1",
            "source": "start-1",
            "target": "py-1",
            "sourceHandle": "output-main",
            "targetHandle": "input-main",
        }
    ]
    return nodes_, edges


class TestSkippedTriggerUnblocksDownstream:
    @pytest.mark.asyncio
    async def test_start_graph_executes_downstream(self, monkeypatch):
        from temporalio import workflow as temporal_workflow

        from services.temporal.workflow import MachinaWorkflow

        scheduled: list[str] = []

        def fake_start_activity(name, **kwargs):
            ctx = kwargs["args"][0]
            scheduled.append(ctx["node_id"])
            fut = asyncio.get_event_loop().create_future()
            fut.set_result(
                {
                    "success": True,
                    "node_id": ctx["node_id"],
                    "result": {"answer": 42},
                }
            )
            return fut

        async def fake_execute_activity(*args, **kwargs):
            return None

        monkeypatch.setattr(temporal_workflow, "start_activity", fake_start_activity)
        monkeypatch.setattr(temporal_workflow, "execute_activity", fake_execute_activity)
        # Keep dispatch resolution out of scope — the loop mechanics are
        # under test, not the resolver (covered by test_dispatch.py).
        monkeypatch.setattr(
            MachinaWorkflow,
            "_resolve_dispatch",
            lambda self, node_type: {"kind": "activity", "name": f"node.{node_type}.v1", "queue": None},
        )

        wf = MachinaWorkflow()
        nodes_, edges = _graph()
        result = await wf.run(
            {
                "nodes": nodes_,
                "edges": edges,
                "session_id": "test",
                "workflow_id": "wf-loop-test",
                "execution_id": "wf-loop-test-run1",
            }
        )

        assert scheduled == ["py-1"], (
            "The downstream pythonExecutor must be scheduled after the "
            "start trigger is auto-completed. Pre-fix the loop broke out "
            "before re-evaluating readiness and nothing was scheduled."
        )
        assert result["success"] is True
        assert result["execution_trace"] == ["start-1", "py-1"]
        assert result["outputs"]["start-1"]["skipped_trigger"] is True
        assert result["outputs"]["py-1"]["success"] is True

    @pytest.mark.asyncio
    async def test_trigger_only_graph_still_terminates(self, monkeypatch):
        """A graph that is ONLY a trigger must not loop forever after the
        fix — one auto-complete pass, then the next pass has nothing
        ready and nothing running -> clean exit."""
        from temporalio import workflow as temporal_workflow

        from services.temporal.workflow import MachinaWorkflow

        async def fake_execute_activity(*args, **kwargs):
            return None

        monkeypatch.setattr(temporal_workflow, "execute_activity", fake_execute_activity)

        wf = MachinaWorkflow()
        result = await asyncio.wait_for(
            wf.run(
                {
                    "nodes": [{"id": "start-1", "type": "start", "data": {}}],
                    "edges": [],
                    "session_id": "test",
                    "workflow_id": "wf-loop-test2",
                    "execution_id": "wf-loop-test2-run1",
                }
            ),
            timeout=5.0,
        )
        assert result["execution_trace"] == ["start-1"]
        assert result["success"] is True
