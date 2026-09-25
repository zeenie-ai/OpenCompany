"""Edge conditions must mean the same thing on every execution path.

Temporal keeps each node's whole activity result, ``{success, result, ...}``,
and evaluates edge conditions against it, so a condition written for the
shipped path reads ``result.<field>`` (tests/temporal/test_conditional_edges.py
locks that). The in-process executor kept only the inner result, so the same
condition read ``None`` there, and the sequential fallback evaluated no
conditions at all. The approval gate relies on ``result.approved`` gating the
outbound send: without parity a discarded draft could still be sent whenever
execution did not route through Temporal.

``services.execution.conditions.evaluate_edge_condition`` is the shared rule:
``success`` / ``result`` / ``result.*`` read the envelope, anything else the
node's own result.
"""

from __future__ import annotations

import asyncio
import time
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

import nodes  # noqa: F401 -- populate plugin registry

from services.execution.conditions import evaluate_edge_condition
from services.execution.executor import WorkflowExecutor
from services.workflow import WorkflowService

APPROVED = {"field": "result.approved", "operator": "is_true"}
DISCARDED = {"field": "result.approved", "operator": "is_false"}


def _gate_graph():
    """A gate whose ``approved`` output picks exactly one of two branches."""
    nodes_ = [
        {"id": "start-1", "type": "start", "data": {"label": "Start"}},
        {"id": "gate-1", "type": "pythonExecutor", "data": {"label": "Gate"}},
        {"id": "send-1", "type": "pythonExecutor", "data": {"label": "Send"}},
        {"id": "log-1", "type": "pythonExecutor", "data": {"label": "Log discarded"}},
    ]
    edges = [
        {"id": "e0", "source": "start-1", "target": "gate-1"},
        {"id": "e1", "source": "gate-1", "target": "send-1", "data": {"condition": APPROVED}},
        {"id": "e2", "source": "gate-1", "target": "log-1", "data": {"condition": DISCARDED}},
    ]
    return nodes_, edges


# ---------------------------------------------------------------------------
# The shared rule


class TestEvaluateEdgeCondition:
    def test_result_fields_read_the_envelope(self):
        inner = {"approved": True}
        assert evaluate_edge_condition(
            APPROVED, envelope={"success": True, "result": inner}, inner=inner
        )

    def test_other_fields_read_the_inner_result(self):
        inner = {"approved": True}
        condition = {"field": "approved", "operator": "is_true"}
        assert evaluate_edge_condition(condition, envelope={"success": True, "result": inner}, inner=inner)

    def test_success_reads_the_envelope_not_an_inner_key_of_the_same_name(self):
        condition = {"field": "success", "operator": "is_true"}
        assert evaluate_edge_condition(
            condition, envelope={"success": True, "result": {"success": False}}, inner={"success": False}
        )

    def test_a_source_that_did_not_complete_matches_nothing(self):
        assert not evaluate_edge_condition(APPROVED, envelope={}, inner={})
        assert not evaluate_edge_condition(DISCARDED, envelope={}, inner={})

    def test_no_condition_always_follows(self):
        assert evaluate_edge_condition({}, envelope={}, inner={})


# ---------------------------------------------------------------------------
# In-process WorkflowExecutor


class TestInProcessExecutor:
    def _evaluate(self, outputs, target, condition):
        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        ctx = SimpleNamespace(outputs=outputs)
        edge = {"source": "gate-1", "target": target, "data": {"condition": condition}}
        return executor._evaluate_incoming_conditions(ctx, target, [edge])

    def test_result_prefixed_condition_matches_the_inner_output(self):
        outputs = {"gate-1": {"approved": True}}
        assert self._evaluate(outputs, "send-1", APPROVED) is True
        assert self._evaluate(outputs, "log-1", DISCARDED) is False

    def test_discarded_draft_takes_the_other_branch(self):
        outputs = {"gate-1": {"approved": False}}
        assert self._evaluate(outputs, "send-1", APPROVED) is False
        assert self._evaluate(outputs, "log-1", DISCARDED) is True

    def test_skipped_source_matches_nothing(self):
        assert self._evaluate({}, "send-1", APPROVED) is False


# ---------------------------------------------------------------------------
# Sequential fallback (Temporal off, Redis off)


async def _run_sequential(nodes_, edges, results_by_node):
    service = WorkflowService.__new__(WorkflowService)
    service._settings = {"stop_on_error": False}
    ran: list[str] = []
    statuses: list[tuple[str, str]] = []

    async def fake_execute_node(self, **kwargs):
        ran.append(kwargs["node_id"])
        return {"success": True, "result": results_by_node.get(kwargs["node_id"], {})}

    async def callback(node_id, status, _data):
        statuses.append((node_id, status))

    service.execute_node = MethodType(fake_execute_node, service)
    result = await service._execute_sequential(nodes_, edges, "session-1", callback, time.time(), "workflow-1")
    return ran, statuses, result


class TestSequentialPath:
    @pytest.mark.asyncio
    async def test_approved_draft_reaches_send_only(self):
        ran, statuses, result = await _run_sequential(*_gate_graph(), {"gate-1": {"approved": True}})

        assert "send-1" in ran
        assert "log-1" not in ran, "pre-fix the sequential path ignored conditions and ran both branches"
        assert ("log-1", "skipped") in statuses
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_discarded_draft_never_reaches_send(self):
        ran, _statuses, _result = await _run_sequential(*_gate_graph(), {"gate-1": {"approved": False}})

        assert "send-1" not in ran
        assert "log-1" in ran

    @pytest.mark.asyncio
    async def test_skipping_is_not_transitive(self):
        nodes_, edges = _gate_graph()
        nodes_.append({"id": "tail-1", "type": "pythonExecutor", "data": {"label": "Tail"}})
        edges.append({"id": "e3", "source": "log-1", "target": "tail-1"})

        ran, _statuses, _result = await _run_sequential(nodes_, edges, {"gate-1": {"approved": True}})

        assert "log-1" not in ran
        assert "tail-1" in ran

    @pytest.mark.asyncio
    async def test_a_node_runs_after_its_conditional_parent(self):
        """BFS order ran ``target`` (reached first via ``start``) before
        ``source``, so a condition reading ``source`` saw nothing."""
        nodes_ = [
            {"id": "start-1", "type": "start", "data": {}},
            {"id": "mid-1", "type": "pythonExecutor", "data": {}},
            {"id": "source-1", "type": "pythonExecutor", "data": {}},
            {"id": "target-1", "type": "pythonExecutor", "data": {}},
        ]
        edges = [
            {"source": "start-1", "target": "target-1"},
            {"source": "start-1", "target": "mid-1"},
            {"source": "mid-1", "target": "source-1"},
            {
                "source": "source-1",
                "target": "target-1",
                "data": {"condition": {"field": "result.ok", "operator": "is_true"}},
            },
        ]

        ran, _statuses, _result = await _run_sequential(nodes_, edges, {"source-1": {"ok": True}})

        assert ran.index("source-1") < ran.index("target-1")

    @pytest.mark.asyncio
    async def test_a_linear_chain_keeps_its_order(self):
        nodes_ = [
            {"id": "start-1", "type": "start", "data": {}},
            {"id": "a-1", "type": "pythonExecutor", "data": {}},
            {"id": "b-1", "type": "pythonExecutor", "data": {}},
        ]
        edges = [{"source": "start-1", "target": "a-1"}, {"source": "a-1", "target": "b-1"}]

        ran, _statuses, _result = await _run_sequential(nodes_, edges, {})

        assert ran == ["start-1", "a-1", "b-1"]


# ---------------------------------------------------------------------------
# Temporal MachinaWorkflow (unchanged; locked here so all three agree)


@pytest.fixture
def temporal_scheduled(monkeypatch):
    from temporalio import workflow as temporal_workflow

    from services.temporal.workflow import MachinaWorkflow

    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)
    scheduled: list[str] = []
    gate_output: dict = {}

    def fake_start_activity(_name, **kwargs):
        ctx = kwargs["args"][0]
        scheduled.append(ctx["node_id"])
        fut = asyncio.get_event_loop().create_future()
        result = gate_output if ctx["node_id"] == "gate-1" else {}
        fut.set_result({"success": True, "node_id": ctx["node_id"], "result": result})
        return fut

    async def fake_execute_activity(*_args, **_kwargs):
        return None

    monkeypatch.setattr(temporal_workflow, "start_activity", fake_start_activity)
    monkeypatch.setattr(temporal_workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        MachinaWorkflow,
        "_resolve_dispatch",
        lambda self, node_type, **_kwargs: {"kind": "activity", "name": f"node.{node_type}.v1", "queue": None},
    )
    return scheduled, gate_output


async def _run_temporal(execution_id):
    from services.temporal.workflow import MachinaWorkflow

    nodes_, edges = _gate_graph()
    return await asyncio.wait_for(
        MachinaWorkflow().run(
            {
                "nodes": nodes_,
                "edges": edges,
                "session_id": "test",
                "workflow_id": "wf-parity",
                "execution_id": execution_id,
            }
        ),
        timeout=5.0,
    )


class TestTemporalPath:
    @pytest.mark.asyncio
    async def test_approved_draft_reaches_send_only(self, temporal_scheduled):
        scheduled, gate_output = temporal_scheduled
        gate_output.update({"approved": True})

        await _run_temporal("wf-parity-approved")

        assert "send-1" in scheduled
        assert "log-1" not in scheduled

    @pytest.mark.asyncio
    async def test_discarded_draft_never_reaches_send(self, temporal_scheduled):
        scheduled, gate_output = temporal_scheduled
        gate_output.update({"approved": False})

        await _run_temporal("wf-parity-discarded")

        assert "send-1" not in scheduled
        assert "log-1" in scheduled
