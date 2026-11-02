"""The controller continues as new before Temporal's per-run Update limit.

A dev server with ``history.maxTotalUpdates`` lowered to 50 must suggest
continue-as-new as the count nears the limit, so register and unregister
Updates alone roll the controller over and none is refused (RFC-0006 §12.1
rec 1). Runs only when TEMPORAL_TEST_CLI names a native Temporal CLI binary,
as test_workspace_tasks_integration does.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

_LIMIT = 50
_PAIRS = 30


async def _run_gate(cli: str) -> None:
    from temporalio.api.enums.v1 import EventType
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Replayer, Worker

    from services.temporal.workflow_control_workflow import WorkflowControlWorkflow
    from tests.temporal.test_controller_messaging_integration import (
        _accepted_ids,
        _chain_histories,
        _completed_ids,
        _registration,
    )

    async with await WorkflowEnvironment.start_local(
        dev_server_existing_path=cli,
        ip="127.0.0.1",
        port=None,
        ui=False,
        dev_server_log_level="error",
        dev_server_extra_args=["--dynamic-config-value", f"history.maxTotalUpdates={_LIMIT}"],
    ) as environment:
        client = environment.client
        queue = "controller-update-limit-" + uuid4().hex
        controller_id = "controller-" + uuid4().hex
        sent: set[str] = set()
        async with Worker(client, task_queue=queue, workflows=[WorkflowControlWorkflow]):
            handle = await client.start_workflow(
                "WorkflowControlWorkflow",
                {
                    "execution_control_version": 1,
                    "generation": 7,
                    "controller_workflow_id": controller_id,
                    "state": "paused",
                    "revision": 1,
                    "participant_state": "paused",
                    "participant_revision": 1,
                },
                id=controller_id,
                task_queue=queue,
            )
            for index in range(_PAIRS):
                root = _registration(f"root-{index}-{uuid4().hex}")
                for method, epoch in (("register_execution", 2 * index + 1), ("unregister_execution", 2 * index + 2)):
                    update_id = f"{method}-{index}"
                    status = await asyncio.wait_for(handle.execute_update(method, root, id=update_id), 15)
                    sent.add(update_id)
                    assert status["membership_epoch"] == epoch, status
            status = await handle.query("status")
            assert status["membership_epoch"] == 2 * _PAIRS and status["live_roots"] == {}, status
            await handle.signal("reset")
            await handle.result()
            histories = await _chain_histories(client, handle)

    first = histories[0].events
    assert first[-1].event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW
    assert sum(event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED for event in first) <= _LIMIT
    # The Update count rolled the run over, not its history length.
    assert len(first) < 1_000
    assert _accepted_ids(histories) == sent == _completed_ids(histories)
    assert not any(event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED for history in histories for event in history.events)
    for history in histories:
        replay = await Replayer(workflows=[WorkflowControlWorkflow]).replay_workflow(history)
        assert replay.replay_failure is None


def test_controller_continues_as_new_before_the_update_limit():
    cli = os.environ.get("TEMPORAL_TEST_CLI", "")
    if not Path(cli).is_file():
        assert os.environ.get("TEMPORAL_REQUIRE_NATIVE_REPLAY") != "1", "Native replay CI requires TEMPORAL_TEST_CLI; skipping is forbidden"
        pytest.skip("Set TEMPORAL_TEST_CLI to a native Temporal CLI binary to run this test")
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "tests.temporal.test_controller_update_limit_integration", cli],
        cwd=Path(__file__).parents[2],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=150,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


if __name__ == "__main__":
    asyncio.run(_run_gate(sys.argv[1]))
