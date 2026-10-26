"""``ApprovalWorkflow`` on Temporal's time-skipping test server: it reads the
row again when nudged, returns the owner's answer, reads again at the row's
expiry, and a cancel ends a pending row before it goes on.

Runs in a clean process (valid Windows I/O handles), like the other SDK
gates."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

TASK_QUEUE = "approval-wait-test"


async def _scenarios() -> None:
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from services.temporal.approval_workflow import ApprovalWorkflow

    reads: List[Dict[str, Any]] = []
    rows: Dict[str, Dict[str, Any]] = {}

    @activity.defn(name="approvals.read")
    async def read(payload: Dict[str, Any]) -> Dict[str, Any]:
        reads.append(payload)
        row = rows[payload["approval_id"]]
        if payload.get("cancel") and row["status"] == "pending":
            row["status"] = "cancelled"
        if row["status"] == "pending":
            return {"status": "pending", "revision": row["revision"], "expires_at": row["expires_at"]}
        return {"status": row["status"], "revision": row["revision"], **({"note": row["note"]} if row.get("note") else {})}

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(environment.client, task_queue=TASK_QUEUE, workflows=[ApprovalWorkflow], activities=[read]):

            async def wait(approval_id: str, expires_in: timedelta):
                now = await environment.get_current_time()
                rows[approval_id] = {"status": "pending", "revision": 1, "expires_at": (now + expires_in).isoformat()}
                return await environment.client.start_workflow(
                    ApprovalWorkflow.run, {"approval_id": approval_id}, id=f"approval-{approval_id}-{uuid4()}", task_queue=TASK_QUEUE
                )

            async def settled(approval_id: str) -> None:
                # The wait has read its row once and waits for a nudge.
                for _ in range(500):
                    if any(entry["approval_id"] == approval_id for entry in reads):
                        return
                    await asyncio.sleep(0.01)
                raise AssertionError(f"the wait on {approval_id} never read its row")

            # A decision, then its nudge: the owner's answer comes back.
            handle = await wait("a1", timedelta(days=7))
            await settled("a1")
            rows["a1"].update(status="discarded", revision=2, note="Call her instead.")
            await handle.signal(ApprovalWorkflow.nudge)
            assert await handle.result() == {"status": "discarded", "revision": 2, "note": "Call her instead."}
            assert [entry for entry in reads if entry["approval_id"] == "a1"] == [{"approval_id": "a1"}] * 2

            # No nudge: it reads again at the expiry, where the row ended.
            handle = await wait("a2", timedelta(hours=1))
            await settled("a2")
            rows["a2"].update(status="expired", revision=2)
            assert (await handle.result())["status"] == "expired"

            # Cancelled while it waits: the pending row ends cancelled first.
            handle = await wait("a3", timedelta(days=7))
            await settled("a3")
            await handle.cancel()
            try:
                await handle.result()
                raise AssertionError("a cancelled wait should not complete")
            except WorkflowFailureError:
                pass
            assert {"approval_id": "a3", "cancel": True} in reads
            assert rows["a3"]["status"] == "cancelled"


def test_every_worker_registers_the_wait() -> None:
    from services.approvals.activities import APPROVAL_ACTIVITIES
    from services.temporal import worker
    from services.temporal.approval_workflow import ApprovalWorkflow

    assert ApprovalWorkflow in worker._framework_workflows()
    assert "approvals.read" in {getattr(fn, "__temporal_activity_definition").name for fn in APPROVAL_ACTIVITIES}


def test_stop_leaves_the_wait_out_of_its_tree_walk() -> None:
    from types import SimpleNamespace

    from services.deployment.execution_control import _participants

    def child(name: str, workflow_type: str):
        return SimpleNamespace(workflow_id=name, run_id="r", workflow_type_name=workflow_type)

    desc = SimpleNamespace(
        raw_description=SimpleNamespace(
            pending_children=[
                child("agent", "AgentWorkflow"),
                child("approval-a1", "ApprovalWorkflow"),
            ]
        )
    )
    assert [item.workflow_id for item in _participants(desc)] == ["agent"]


def test_the_wait_on_temporal() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.temporal.test_approval_workflow"],
        cwd=Path(__file__).parents[2],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-3000:]


if __name__ == "__main__":
    asyncio.run(_scenarios())
