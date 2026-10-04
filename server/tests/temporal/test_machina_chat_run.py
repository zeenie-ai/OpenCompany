"""MachinaWorkflow claims and finishes the chat run the owner's message
started (``chat_run.start`` / ``chat_run.finish``, behind
``machina-chat-run-v1``), and every node of the run carries its scope.

Two layers, like the run-record gate:

- the real ``run()`` body with activity dispatch faked: a run is claimed
  only from an event the chat service produced; a claimed run's nodes carry
  ``run_scope`` and the run is finished with the outcome; an unclaimed run
  (another chat trigger got it) runs untracked; a closed patch (a pre-patch
  history) schedules neither activity;
- an SDK replay gate against Temporal's time-skipping test server: histories
  recorded with the patch closed and open both replay, and forcing the patch
  open on the old history fails replay.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

import nodes  # noqa: F401 -- populate plugin registry

ROUTING = {"version": 1, "agent_workflow_enabled": False, "per_type_dispatch_enabled": False, "worker_pool_enabled": False}
SOURCE = "opencompany://services/chat"
TYPE = "com.opencompany.chat.message.received"


def _graph(*, source: str = SOURCE, event_type: str = TYPE, run_id: Any = "r_1", spawned: bool = True) -> Dict[str, Any]:
    trigger: Dict[str, Any] = {"id": "wf-cr:chatTrigger:1", "type": "chatTrigger", "data": {"label": "Chat"}}
    if spawned:
        data = {"message": "Book Saturday", "session_id": "wf-cr", "run_id": run_id}
        trigger.update(
            {
                "_pre_executed": True,
                "_trigger_output": {**data, "_event_envelope": {"id": str(run_id), "source": source, "type": event_type, "data": data}},
            }
        )
    return {
        "nodes": [trigger, {"id": "wf-cr:console:1", "type": "console", "data": {"label": "Log"}}],
        "edges": [{"id": "e1", "source": trigger["id"], "target": "wf-cr:console:1", "targetHandle": "input-main"}],
        "workflow_id": "wf-cr",
        # A deployed run's own session is its execution's, never the chat's.
        "session_id": "wf-cr:execution:1",
        "execution_id": "wf-cr:execution:1",
        "generation": 2,
        "_temporal_routing_v1": ROUTING,
    }


# ----- the run() body with fakes -----


@pytest.fixture()
def fakes(monkeypatch):
    from temporalio import workflow as temporal_workflow

    from services.temporal.workflow import CHAT_RUN_PATCH, MachinaWorkflow

    state = SimpleNamespace(
        activities=[],
        contexts=[],
        node_success=True,
        claimed=True,
        claim_session="wf-cr",
        open_patches={CHAT_RUN_PATCH, "machina-conditional-edges-v1", "machina-run-record-v1"},
        asked=[],
    )
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())

    def patched(patch_id):
        state.asked.append(patch_id)
        return patch_id in state.open_patches

    monkeypatch.setattr(temporal_workflow, "patched", patched)
    monkeypatch.setattr(temporal_workflow, "info", lambda: SimpleNamespace(workflow_id="wf-cr-chat-r_1", run_id="run-1"))

    def start_activity(name, **kwargs):
        context = kwargs["args"][0]
        state.contexts.append(context)
        future = asyncio.get_event_loop().create_future()
        future.set_result({"success": state.node_success, "node_id": context["node_id"], "result": {}, "error": None if state.node_success else "boom"})
        return future

    async def execute_activity(name, payload=None, **_kwargs):
        state.activities.append((name, payload))
        if name == "chat_run.start":
            if not state.claimed:
                return {"claimed": False}
            return {"claimed": True, **({"session_id": state.claim_session} if state.claim_session else {})}
        return None

    monkeypatch.setattr(temporal_workflow, "start_activity", start_activity)
    monkeypatch.setattr(temporal_workflow, "execute_activity", execute_activity)
    monkeypatch.setattr(
        MachinaWorkflow,
        "_resolve_dispatch",
        lambda self, node_type, **_kwargs: {"kind": "activity", "name": f"node.{node_type}.v1", "queue": None},
    )
    return state


async def _run(graph):
    from services.temporal.workflow import MachinaWorkflow

    return await asyncio.wait_for(MachinaWorkflow().run(graph), timeout=5.0)


def _names(state) -> List[str]:
    return [name for name, _ in state.activities]


def _payloads(state, name) -> List[Dict[str, Any]]:
    return [payload for activity, payload in state.activities if activity == name]


async def test_a_chat_message_run_is_claimed_scoped_and_finished(fakes):
    await _run(_graph())
    assert _payloads(fakes, "chat_run.start") == [{"run_id": "r_1", "temporal_workflow_id": "wf-cr-chat-r_1", "temporal_run_id": "run-1"}]
    assert _payloads(fakes, "chat_run.finish") == [
        {"run_id": "r_1", "temporal_workflow_id": "wf-cr-chat-r_1", "temporal_run_id": "run-1", "success": True}
    ]
    assert [context["run_scope"] for context in fakes.contexts] == [{"run_id": "r_1", "session_id": "wf-cr"}]
    # Claimed before the first node is scheduled; finished before the run is recorded.
    names = _names(fakes)
    assert names.index("chat_run.finish") < names.index("workflow_runs.record_completion")


async def test_a_claim_recorded_without_the_chats_session_keeps_the_old_scope(fakes):
    fakes.claim_session = None
    await _run(_graph())
    assert [context["run_scope"] for context in fakes.contexts] == [{"run_id": "r_1", "session_id": "wf-cr:execution:1"}]


async def test_a_failed_run_finishes_with_its_error(fakes):
    fakes.node_success = False
    await _run(_graph())
    [finish] = _payloads(fakes, "chat_run.finish")
    assert finish["success"] is False and finish["error"] == "boom"


async def test_an_unclaimed_run_runs_untracked(fakes):
    fakes.claimed = False
    await _run(_graph())
    assert _payloads(fakes, "chat_run.finish") == []
    assert all("run_scope" not in context for context in fakes.contexts)


@pytest.mark.parametrize(
    "graph",
    [
        _graph(source="opencompany://nodes/webhook"),
        _graph(event_type="com.opencompany.webhook.received"),
        _graph(run_id=None),
        _graph(run_id=42),
        _graph(spawned=False),
    ],
)
async def test_a_run_id_is_trusted_only_from_the_chat_service(fakes, graph):
    from services.temporal.workflow import CHAT_RUN_PATCH

    await _run(graph)
    assert "chat_run.start" not in _names(fakes)
    assert CHAT_RUN_PATCH not in fakes.asked


async def test_a_pre_patch_history_schedules_neither_activity(fakes):
    from services.temporal.workflow import CHAT_RUN_PATCH

    fakes.open_patches.discard(CHAT_RUN_PATCH)
    await _run(_graph())
    assert "chat_run.start" not in _names(fakes) and "chat_run.finish" not in _names(fakes)
    assert all("run_scope" not in context for context in fakes.contexts)


def test_every_worker_registers_the_activities():
    source = (Path(__file__).parents[2] / "services" / "temporal" / "worker.py").read_text(encoding="utf-8")
    assert source.count("*CHAT_RUN_ACTIVITIES") == 3
    from services.chat.activities import CHAT_RUN_ACTIVITIES

    names = sorted(getattr(fn, "__temporal_activity_definition").name for fn in CHAT_RUN_ACTIVITIES)
    assert names == ["chat_run.finish", "chat_run.start"]


def test_the_scope_reaches_every_node_path():
    """``run_scope`` must survive each hop a node's context takes: the
    per-type activity extras, the legacy activity's message, the agent
    workflow's inherited keys, and the internal socket's execute handler."""
    import inspect

    from routers import websocket as ws_router
    from services.plugin import base
    from services.temporal import activities, agent_workflow

    assert "run_scope" in agent_workflow._INHERITED_SCOPE_KEYS
    assert '"run_scope"' in inspect.getsource(base)
    assert '"run_scope"' in inspect.getsource(activities.NodeExecutionActivities)
    handler = inspect.getsource(ws_router.handle_execute_node)
    assert '"run_scope"' in handler and "/ws/internal" in handler


async def test_the_activities_go_through_the_ledger(monkeypatch):
    import core.container as container_module
    import services.chat.ledger as ledger

    from services.chat.activities import finish_chat_run_activity, start_chat_run_activity

    calls = []

    async def start_run(database, **kwargs):
        calls.append(("start", kwargs))
        return SimpleNamespace(state="running", session_id="wf-cr")

    async def finish_run(database, **kwargs):
        calls.append(("finish", kwargs))
        return None

    monkeypatch.setattr(ledger, "start_run", start_run)
    monkeypatch.setattr(ledger, "finish_run", finish_run)
    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: "db"))
    assert await start_chat_run_activity({"run_id": "r", "temporal_workflow_id": "w", "temporal_run_id": "t"}) == {"claimed": True, "session_id": "wf-cr"}
    assert await finish_chat_run_activity({"run_id": "r", "temporal_workflow_id": "w", "temporal_run_id": "t", "success": True}) == {"state": None}
    assert calls[0] == ("start", {"run_id": "r", "temporal_workflow_id": "w", "temporal_run_id": "t"})
    assert calls[1][1]["success"] is True and calls[1][1]["error"] is None


# ----- the SDK replay gate (run in a subprocess, like the run-record gate) -----

TASK_QUEUE = "machina-chat-run-replay-gate"


async def _replay_gate() -> None:
    from temporalio import activity
    from temporalio import workflow as temporal_workflow
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowHistory
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, Worker

    from services.temporal.workflow import CHAT_RUN_PATCH, MachinaWorkflow

    contexts: List[Dict[str, Any]] = []

    @activity.defn(name="execute_node_activity")
    async def execute_node(context: Dict[str, Any]) -> Dict[str, Any]:
        contexts.append(context)
        return {"success": True, "node_id": context["node_id"], "result": {"logged": True}}

    @activity.defn(name="store_node_output_activity")
    async def store_output(_payload: Dict[str, Any]) -> None:
        return None

    @activity.defn(name="workflow_control.pause_on_failure")
    async def pause(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"paused": False}

    @activity.defn(name="workflow_runs.record_completion")
    async def record(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"recorded": True}

    @activity.defn(name="chat_run.start")
    async def start(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"claimed": True, "session_id": "wf-cr"}

    @activity.defn(name="chat_run.finish")
    async def finish(_payload: Dict[str, Any]) -> Dict[str, Any]:
        return {"state": "finished"}

    real_patched = temporal_workflow.patched

    def scheduled(history: WorkflowHistory) -> List[str]:
        return [
            event.activity_task_scheduled_event_attributes.activity_type.name
            for event in history.events
            if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        ]

    async def run_once(environment, *, patch_open: bool) -> WorkflowHistory:
        if not patch_open:
            temporal_workflow.patched = lambda patch_id: False if patch_id == CHAT_RUN_PATCH else real_patched(patch_id)
        try:
            handle = await environment.client.start_workflow(MachinaWorkflow.run, _graph(), id=f"machina-cr-{uuid4()}", task_queue=TASK_QUEUE)
            result = await handle.result()
            assert result["success"] is True
            return await handle.fetch_history()
        finally:
            temporal_workflow.patched = real_patched

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(
            environment.client,
            task_queue=TASK_QUEUE,
            workflows=[MachinaWorkflow],
            activities=[execute_node, store_output, pause, record, start, finish],
        ):
            before = await run_once(environment, patch_open=False)
            assert all("run_scope" not in context for context in contexts)
            contexts.clear()
            after = await run_once(environment, patch_open=True)
            assert [context.get("run_scope") for context in contexts] == [{"run_id": "r_1", "session_id": "wf-cr"}]

    assert "chat_run.start" not in scheduled(before) and "chat_run.finish" not in scheduled(before)
    assert "chat_run.start" in scheduled(after) and "chat_run.finish" in scheduled(after)

    replayer = Replayer(workflows=[MachinaWorkflow])
    for history in (before, after):
        captured = WorkflowHistory.from_json(history.workflow_id, history.to_json())
        replay = await replayer.replay_workflow(captured, raise_on_replay_failure=False)
        assert replay.replay_failure is None, replay.replay_failure

    # Forcing only this patch open on the old history must fail replay: the
    # gate is what keeps old histories working. Every other patch stays real.
    temporal_workflow.patched = lambda patch_id: True if patch_id == CHAT_RUN_PATCH else real_patched(patch_id)
    try:
        captured = WorkflowHistory.from_json(before.workflow_id, before.to_json())
        replay = await Replayer(workflows=[MachinaWorkflow]).replay_workflow(captured, raise_on_replay_failure=False)
        assert replay.replay_failure is not None
    finally:
        temporal_workflow.patched = real_patched


def test_histories_before_and_after_the_patch_replay() -> None:
    """Run the SDK gate in a clean process with valid Windows I/O handles."""
    completed = subprocess.run(
        [sys.executable, "-m", "tests.temporal.test_machina_chat_run"],
        cwd=Path(__file__).parents[2],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-3000:]


if __name__ == "__main__":
    asyncio.run(_replay_gate())
