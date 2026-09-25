"""MachinaWorkflow records each finished trigger-spawned run
(``workflow_runs.record_completion``, behind ``machina-run-record-v1``).

Two layers, like the conditional-edges and agent replay gates:

- the real ``run()`` body with activity dispatch faked: the record goes out
  for deployment runs only, carries the Temporal identity and the outcome,
  and is not scheduled when the patch is closed (a pre-patch history);
- an SDK replay gate against Temporal's time-skipping test server: a
  history recorded with the patch closed replays cleanly under the real
  code, a patched one replays too, and forcing the patch open on the old
  history fails replay (so the gate is what keeps old histories working).
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


def _graph(*, spawned: bool) -> Dict[str, Any]:
    trigger = {"id": "wf-rr:chatTrigger:1", "type": "chatTrigger", "data": {"label": "Chat"}}
    if spawned:
        trigger.update({"_pre_executed": True, "_trigger_output": {"message": "hello"}})
    return {
        "nodes": [trigger, {"id": "wf-rr:console:1", "type": "console", "data": {"label": "Log"}}],
        "edges": [{"id": "e1", "source": trigger["id"], "target": "wf-rr:console:1", "targetHandle": "input-main"}],
        "workflow_id": "wf-rr",
        "session_id": "s",
        "execution_id": "wf-rr:execution:1",
        "generation": 3,
        "_temporal_routing_v1": ROUTING,
    }


# ----- the run() body with fakes -----


@pytest.fixture()
def fakes(monkeypatch):
    from temporalio import workflow as temporal_workflow

    from services.temporal.workflow import RUN_RECORD_PATCH, MachinaWorkflow

    state = SimpleNamespace(activities=[], node_success=True, open_patches={RUN_RECORD_PATCH, "machina-conditional-edges-v1"}, asked=[])
    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())

    def patched(patch_id):
        state.asked.append(patch_id)
        return patch_id in state.open_patches

    monkeypatch.setattr(temporal_workflow, "patched", patched)
    monkeypatch.setattr(temporal_workflow, "info", lambda: SimpleNamespace(workflow_id="wf-rr-chat-evt", run_id="run-1"))

    def start_activity(name, **kwargs):
        context = kwargs["args"][0]
        future = asyncio.get_event_loop().create_future()
        future.set_result({"success": state.node_success, "node_id": context["node_id"], "result": {}, "error": None if state.node_success else "boom"})
        return future

    async def execute_activity(name, payload=None, **_kwargs):
        state.activities.append((name, payload))
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


def _records(state) -> List[Dict[str, Any]]:
    return [payload for name, payload in state.activities if name == "workflow_runs.record_completion"]


async def test_a_deployment_run_is_recorded(fakes):
    await _run(_graph(spawned=True))
    assert _records(fakes) == [{"workflow_id": "wf-rr", "run_id": "wf-rr-chat-evt:run-1", "generation": 3, "status": "success"}]


async def test_a_failed_run_is_recorded_as_failed(fakes):
    fakes.node_success = False
    await _run(_graph(spawned=True))
    assert _records(fakes)[0]["status"] == "failed"


async def test_manual_runs_are_not_recorded_and_do_not_consult_the_patch(fakes):
    from services.temporal.workflow import RUN_RECORD_PATCH

    await _run(_graph(spawned=False))
    assert _records(fakes) == []
    assert RUN_RECORD_PATCH not in fakes.asked


async def test_a_pre_patch_history_schedules_no_record(fakes):
    from services.temporal.workflow import RUN_RECORD_PATCH

    fakes.open_patches.discard(RUN_RECORD_PATCH)
    await _run(_graph(spawned=True))
    assert _records(fakes) == []


def test_every_worker_registers_the_activity():
    source = (Path(__file__).parents[2] / "services" / "temporal" / "worker.py").read_text(encoding="utf-8")
    assert source.count("record_run_completion_activity") == source.count("pause_workflow_on_failure_activity") == 6


async def test_the_activity_records_through_the_run_store(monkeypatch):
    import core.container as container_module
    import services.employees.runs as runs

    from services.temporal.activities import record_run_completion_activity

    calls = []

    async def record_run(database, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(runs, "record_run", record_run)
    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: "db"))
    result = await record_run_completion_activity({"workflow_id": "w", "run_id": "t:r", "generation": 2, "status": "success"})
    assert result == {"recorded": True}
    assert calls == [{"workflow_id": "w", "run_id": "t:r", "status": "success", "runtime": "temporal", "generation": 2}]

    async def broken(database, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(runs, "record_run", broken)
    assert (await record_run_completion_activity({"workflow_id": "w", "run_id": "t:r"}))["recorded"] is False


# ----- the SDK replay gate (run in a subprocess, like the agent gate) -----

TASK_QUEUE = "machina-run-record-replay-gate"


async def _replay_gate() -> None:
    from temporalio import activity
    from temporalio import workflow as temporal_workflow
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowHistory
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, Worker

    from services.temporal.workflow import RUN_RECORD_PATCH, MachinaWorkflow

    @activity.defn(name="execute_node_activity")
    async def execute_node(context: Dict[str, Any]) -> Dict[str, Any]:
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

    real_patched = temporal_workflow.patched

    def scheduled(history: WorkflowHistory) -> List[str]:
        return [
            event.activity_task_scheduled_event_attributes.activity_type.name
            for event in history.events
            if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
        ]

    async def run_once(environment, *, record_patch_open: bool) -> WorkflowHistory:
        if not record_patch_open:
            temporal_workflow.patched = lambda patch_id: False if patch_id == RUN_RECORD_PATCH else real_patched(patch_id)
        try:
            handle = await environment.client.start_workflow(
                MachinaWorkflow.run, _graph(spawned=True), id=f"machina-rr-{uuid4()}", task_queue=TASK_QUEUE
            )
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
            activities=[execute_node, store_output, pause, record],
        ):
            before = await run_once(environment, record_patch_open=False)
            after = await run_once(environment, record_patch_open=True)

    assert "workflow_runs.record_completion" not in scheduled(before)
    assert "workflow_runs.record_completion" in scheduled(after)

    replayer = Replayer(workflows=[MachinaWorkflow])
    for history in (before, after):
        captured = WorkflowHistory.from_json(history.workflow_id, history.to_json())
        replay = await replayer.replay_workflow(captured, raise_on_replay_failure=False)
        assert replay.replay_failure is None, replay.replay_failure

    # Forcing only this patch open on the old history must fail replay (the
    # record activity it then schedules is not in that history): the gate is
    # what keeps old histories working. Every other patch stays real.
    temporal_workflow.patched = lambda patch_id: True if patch_id == RUN_RECORD_PATCH else real_patched(patch_id)
    try:
        captured = WorkflowHistory.from_json(before.workflow_id, before.to_json())
        replay = await Replayer(workflows=[MachinaWorkflow]).replay_workflow(captured, raise_on_replay_failure=False)
        assert replay.replay_failure is not None
        assert "record_completion" in str(replay.replay_failure) or "Nondeterminism" in str(replay.replay_failure)
        assert "machina-conditional-edges-v1" not in str(replay.replay_failure)
    finally:
        temporal_workflow.patched = real_patched


def test_histories_before_and_after_the_patch_replay() -> None:
    """Run the SDK gate in a clean process with valid Windows I/O handles."""
    completed = subprocess.run(
        [sys.executable, "-m", "tests.temporal.test_machina_run_record"],
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
