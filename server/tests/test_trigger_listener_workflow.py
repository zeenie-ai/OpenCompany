"""Wave 12 C1 canary: tests for :class:`TriggerListenerWorkflow`.

Unit-style tests that exercise the workflow class as a regular Python
object — no live Temporal cluster required. ``workflow.logger`` calls
are patched to a MagicMock so the handlers run outside a workflow
event loop. ``workflow.start_child_workflow`` is patched to a recorder
in the child-spawn tests.

Tested invariants:

C1a — ``on_event`` signal dedups by ``event.id`` (same contract as
      :meth:`MachinaWorkflow.on_event`).
C1b — Malformed envelopes (missing ``id``) are dropped silently
      with one warning log.
C1c — ``_build_run_graph`` produces the correct filtered (nodes, edges)
      pair: trigger node carries ``_pre_executed=True`` + the event
      payload; sibling triggers carry ``_pre_executed=True`` +
      ``{not_triggered: True}``; downstream nodes are reachable;
      cross-graph nodes are excluded.
C1d — Spawned child workflows use a stable
      ``<workflow_slug>-<trigger_label>-<event.id>`` ID (Wave 14) so the
      Temporal Web UI surfaces the workflow + firing trigger by name
      while server-side dedup catches producer retries.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

# Stub the root `cli` package (lives at project root, outside server/) so
# services.temporal.__init__ → _runtime.py → from cli.tcp import
# probe_tcp_port doesn't crash collection.
if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


@pytest.fixture(autouse=True)
def _patch_workflow_logger(monkeypatch):
    """Workflow handlers call ``workflow.logger.<level>`` which requires a
    Temporal event loop. Replace with a MagicMock so handlers run
    synchronously in unit tests."""
    from temporalio import workflow as temporal_workflow

    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())
    monkeypatch.setattr(temporal_workflow, "patched", lambda _patch_id: True)


# ---------------------------------------------------------------------------
# C1a + C1b — signal handler
# ---------------------------------------------------------------------------


class TestSignalHandlerDedup:
    """Same dedup + drop-malformed contract as MachinaWorkflow.on_event."""

    def test_init_empty_state(self):
        from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow

        wf = TriggerListenerWorkflow()
        assert wf._seen_event_ids == set()
        assert wf._matched_events == []
        assert wf._processed_count == 0

    @pytest.mark.asyncio
    async def test_on_event_dedups_by_id(self):
        from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow

        wf = TriggerListenerWorkflow()
        payload = {"id": "evt-1", "type": "com.opencompany.webhook.received", "data": {}}

        await wf.on_event(payload)
        await wf.on_event(payload)  # duplicate

        assert wf._seen_event_ids == {"evt-1"}
        assert len(wf._matched_events) == 1

    @pytest.mark.asyncio
    async def test_on_event_drops_malformed_envelope(self):
        from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow

        wf = TriggerListenerWorkflow()
        await wf.on_event({"type": "com.opencompany.webhook.received"})  # no 'id'
        assert wf._matched_events == []
        assert wf._seen_event_ids == set()

    @pytest.mark.asyncio
    async def test_on_event_preserves_fifo_order(self):
        from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow

        wf = TriggerListenerWorkflow()
        for n in range(3):
            await wf.on_event({"id": f"evt-{n}", "type": "x", "data": {"n": n}})

        ids = [e["id"] for e in wf._matched_events]
        assert ids == ["evt-0", "evt-1", "evt-2"]


# ---------------------------------------------------------------------------
# C1c — _build_run_graph
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str) -> Dict[str, Any]:
    return {"id": node_id, "type": node_type, "data": {}}


def _edge(src: str, tgt: str, target_handle: str = "input-main") -> Dict[str, Any]:
    return {"source": src, "target": tgt, "targetHandle": target_handle}


class TestBuildRunGraph:
    """Filtered-graph builder reproduces DeploymentManager._spawn_run semantics."""

    def test_trigger_marked_pre_executed_with_output(self):
        from services.temporal.trigger_listener_workflow import _build_run_graph

        nodes: List[Dict[str, Any]] = [
            _node("wh-1", "webhookTrigger"),
            _node("agent-1", "aiAgent"),
        ]
        edges = [_edge("wh-1", "agent-1")]
        output_payload = {"method": "POST", "body": '{"hello": "world"}'}

        filtered_nodes, filtered_edges = _build_run_graph(
            trigger_node_id="wh-1",
            trigger_output=output_payload,
            nodes=nodes,
            edges=edges,
        )

        wh = next(n for n in filtered_nodes if n["id"] == "wh-1")
        assert wh["_pre_executed"] is True
        assert wh["_trigger_output"] == output_payload
        # Downstream included.
        ids = {n["id"] for n in filtered_nodes}
        assert ids == {"wh-1", "agent-1"}
        assert len(filtered_edges) == 1

    def test_sibling_triggers_excluded_from_run_graph(self):
        """Mirrors DeploymentManager._get_downstream_nodes: trigger nodes
        are independent event listeners, NOT pulled into another trigger's
        execution run. Each trigger fires its own run when its own event
        arrives — n8n pattern. The defensive ``_pre_executed=not_triggered``
        branch in MachinaWorkflow only fires for the edge case where a
        non-firing trigger somehow lands in the filtered graph; that
        doesn't happen under the canonical downstream-collection."""
        from services.temporal.trigger_listener_workflow import _build_run_graph

        nodes = [
            _node("wh-1", "webhookTrigger"),
            _node("wh-2", "webhookTrigger"),
            _node("agent-1", "aiAgent"),
        ]
        # Both triggers feed the same agent. wh-1 fires; wh-2 is a sibling.
        edges = [_edge("wh-1", "agent-1"), _edge("wh-2", "agent-1")]

        filtered_nodes, _ = _build_run_graph(
            trigger_node_id="wh-1",
            trigger_output={"path": "/hook"},
            nodes=nodes,
            edges=edges,
        )

        ids = {n["id"] for n in filtered_nodes}
        # wh-2 is NOT in the filtered run.
        assert ids == {"wh-1", "agent-1"}

    def test_cross_graph_nodes_excluded(self):
        from services.temporal.trigger_listener_workflow import _build_run_graph

        # wh-1 → agent-1; orphan-1 → orphan-2 (separate component).
        nodes = [
            _node("wh-1", "webhookTrigger"),
            _node("agent-1", "aiAgent"),
            _node("orphan-1", "aiAgent"),
            _node("orphan-2", "console"),
        ]
        edges = [
            _edge("wh-1", "agent-1"),
            _edge("orphan-1", "orphan-2"),
        ]

        filtered_nodes, filtered_edges = _build_run_graph(
            trigger_node_id="wh-1",
            trigger_output={},
            nodes=nodes,
            edges=edges,
        )

        ids = {n["id"] for n in filtered_nodes}
        assert ids == {"wh-1", "agent-1"}
        # Orphan edge dropped.
        for edge in filtered_edges:
            assert edge["source"] in ids
            assert edge["target"] in ids

    def test_config_node_included_via_input_memory_handle(self):
        from services.temporal.trigger_listener_workflow import _build_run_graph

        # Memory node connects to agent via input-memory; must be pulled
        # in despite being upstream of the trigger.
        nodes = [
            _node("wh-1", "webhookTrigger"),
            _node("agent-1", "aiAgent"),
            _node("mem-1", "simpleMemory"),
        ]
        edges = [
            _edge("wh-1", "agent-1"),
            _edge("mem-1", "agent-1", target_handle="input-memory"),
        ]

        filtered_nodes, _ = _build_run_graph(
            trigger_node_id="wh-1",
            trigger_output={},
            nodes=nodes,
            edges=edges,
        )

        assert {n["id"] for n in filtered_nodes} == {"wh-1", "agent-1", "mem-1"}

    def test_legacy_tool_handle_is_included_for_triggered_lead(self):
        from services.temporal.trigger_listener_workflow import _build_run_graph

        nodes = [
            _node("task-trigger", "taskTrigger"),
            _node("lead", "orchestrator_agent"),
            _node("todos", "writeTodos"),
        ]
        edges = [
            _edge("task-trigger", "lead", target_handle="input-task"),
            {"source": "todos", "target": "lead", "target_handle": "input-tools"},
        ]

        filtered_nodes, filtered_edges = _build_run_graph(
            trigger_node_id="task-trigger", trigger_output={}, nodes=nodes, edges=edges,
        )

        assert {node["id"] for node in filtered_nodes} == {"task-trigger", "lead", "todos"}
        assert any(edge["source"] == "todos" for edge in filtered_edges)

    def test_stops_at_downstream_trigger_nodes(self):
        """Downstream collection stops at trigger nodes (n8n pattern):
        each trigger is an independent event listener. taskTrigger fires
        when its own event (``task_completed``) arrives; collection
        from the upstream webhook trigger doesn't traverse through it.
        Children of the downstream trigger don't appear in this run's
        filtered graph either."""
        from services.temporal.trigger_listener_workflow import _build_run_graph

        nodes = [
            _node("wh-1", "webhookTrigger"),
            _node("agent-1", "aiAgent"),
            _node("task-1", "taskTrigger"),
            _node("downstream-of-task", "console"),
        ]
        edges = [
            _edge("wh-1", "agent-1"),
            _edge("agent-1", "task-1"),
            _edge("task-1", "downstream-of-task"),
        ]

        filtered_nodes, _ = _build_run_graph(
            trigger_node_id="wh-1",
            trigger_output={},
            nodes=nodes,
            edges=edges,
        )

        ids = {n["id"] for n in filtered_nodes}
        # Collection stops at task-1 (downstream trigger).
        assert ids == {"wh-1", "agent-1"}


# ---------------------------------------------------------------------------
# C1d — _spawn_child_run uses stable workflow ID derived from event.id
# ---------------------------------------------------------------------------


class TestSpawnChildRun:
    """Child workflows are started with deterministic IDs so server-side
    dedup catches producer retries across listener restarts."""

    @pytest.mark.asyncio
    async def test_child_id_derived_from_event_id(self, monkeypatch):
        from services.temporal import trigger_listener_workflow as tlw

        recorded: List[Dict[str, Any]] = []

        async def fake_start_child_workflow(workflow_name, **kwargs):
            recorded.append({"name": workflow_name, **kwargs})
            return MagicMock()

        async def fake_execute_activity(*args, **kwargs):
            # Status-broadcast activities — no-op for the spawn test.
            return None

        from temporalio import workflow as temporal_workflow

        monkeypatch.setattr(
            temporal_workflow,
            "start_child_workflow",
            fake_start_child_workflow,
        )
        monkeypatch.setattr(
            temporal_workflow,
            "execute_activity",
            fake_execute_activity,
        )

        wf = tlw.TriggerListenerWorkflow()
        event = {
            "id": "evt-42",
            "type": "com.opencompany.webhook.received",
            "data": {"path": "/hook", "method": "POST"},
        }
        listener_data = {
            "workflow_id": "wf-abc",
            "trigger_node_id": "wh-1",
            "node_type": "webhookTrigger",
            "event_type": "com.opencompany.webhook.received",
            "filter_params": {},
            "nodes": [_node("wh-1", "webhookTrigger"), _node("agent-1", "aiAgent")],
            "edges": [_edge("wh-1", "agent-1")],
            "session_id": "sess-1",
        }

        await wf._spawn_child_run(event, listener_data)

        assert len(recorded) == 1
        call = recorded[0]
        assert call["name"] == "MachinaWorkflow"
        # Wave 14: ``<workflow_slug>-<trigger_label>-<event_id>``.
        # workflow_slug falls back to workflow_id when listener_data
        # doesn't carry it; trigger_label falls back to trigger_node_id.
        assert call["id"] == "wf-abc-wh-1-evt-42"
        # Trigger output carries both the user data AND the envelope for
        # downstream introspection.
        child_args = call["args"][0]
        wh = next(n for n in child_args["nodes"] if n["id"] == "wh-1")
        assert wh["_pre_executed"] is True
        assert wh["_trigger_output"]["path"] == "/hook"
        assert wh["_trigger_output"]["_event_envelope"]["id"] == "evt-42"

    @pytest.mark.asyncio
    async def test_spawn_uses_latest_saved_graph_tools(self, monkeypatch):
        from services.temporal import trigger_listener_workflow as tlw
        from temporalio import workflow as temporal_workflow

        recorded: List[Dict[str, Any]] = []
        latest_nodes = [
            _node("task-trigger", "taskTrigger"),
            _node("lead", "orchestrator_agent"),
            _node("todos", "writeTodos"),
        ]
        latest_edges = [
            _edge("task-trigger", "lead", "input-task"),
            _edge("todos", "lead", "input-tools"),
        ]

        async def fake_execute_activity(name, *args, **kwargs):
            if name == "load_persisted_workflow_graph_activity":
                return {"found": True, "nodes": latest_nodes, "edges": latest_edges}
            return None

        async def fake_start_child_workflow(name, **kwargs):
            recorded.append({"name": name, **kwargs})
            return MagicMock()

        monkeypatch.setattr(temporal_workflow, "execute_activity", fake_execute_activity)
        monkeypatch.setattr(temporal_workflow, "start_child_workflow", fake_start_child_workflow)

        snapshot_nodes = [_node("task-trigger", "taskTrigger"), _node("lead", "orchestrator_agent")]
        snapshot_edges = [_edge("task-trigger", "lead", "input-task")]
        await tlw.TriggerListenerWorkflow()._spawn_child_run(
            {"id": "evt-new-tool", "type": "task", "data": {"task_id": "task-1", "status": "completed"}},
            {
                "workflow_id": "workflow-1", "trigger_node_id": "task-trigger",
                "nodes": snapshot_nodes, "edges": snapshot_edges,
            },
        )

        child_graph = recorded[0]["args"][0]
        assert {node["id"] for node in child_graph["nodes"]} == {"task-trigger", "lead", "todos"}
        assert any(edge["source"] == "todos" for edge in child_graph["edges"])

    @pytest.mark.asyncio
    async def test_spawn_failure_does_not_kill_listener(self, monkeypatch):
        """An exception inside _spawn_child_run propagates back to the
        main loop, which catches it — the listener stays alive. This
        test asserts the exception surfaces (the loop's catch is in
        run(), not in _spawn_child_run)."""
        from services.temporal import trigger_listener_workflow as tlw

        async def boom(*args, **kwargs):
            raise RuntimeError("temporal unreachable")

        async def fake_execute_activity(*args, **kwargs):
            return None

        from temporalio import workflow as temporal_workflow

        monkeypatch.setattr(
            temporal_workflow,
            "start_child_workflow",
            boom,
        )
        monkeypatch.setattr(
            temporal_workflow,
            "execute_activity",
            fake_execute_activity,
        )

        wf = tlw.TriggerListenerWorkflow()
        event = {"id": "evt-1", "type": "x", "data": {}}
        listener_data = {
            "workflow_id": "wf-1",
            "trigger_node_id": "wh-1",
            "nodes": [_node("wh-1", "webhookTrigger")],
            "edges": [],
        }

        with pytest.raises(RuntimeError, match="temporal unreachable"):
            await wf._spawn_child_run(event, listener_data)
