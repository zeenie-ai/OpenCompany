"""``ApprovedToolCallWorkflow`` on Temporal's time-skipping test server: it
sends only after the Undo window, never when its claim is refused (the
owner undid or sent again), runs the node's activity exactly once even when
it fails, and records a send that broke off as ``unknown``.

Runs in a clean process (valid Windows I/O handles), like the other SDK
gates."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

TASK_QUEUE = "approved-send-test"


async def _scenarios() -> None:
    from temporalio import activity
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from services.temporal.approved_tool_call_workflow import ApprovedToolCallWorkflow

    calls: Dict[str, List[Dict[str, Any]]] = {"claim": [], "send": [], "record": []}
    behaviour: Dict[str, Any] = {"claimed": True, "send": "ok"}

    @activity.defn(name="approvals.claim_send")
    async def claim(payload: Dict[str, Any]) -> Dict[str, Any]:
        calls["claim"].append({**payload, "at": activity.info().current_attempt_scheduled_time})
        if not behaviour["claimed"]:
            return {"claimed": False, "reason": "changed"}
        return {"claimed": True, "activity": "node.fakeSend.v1", "context": {"node_id": "n", "approval_execution": {"approval_id": payload["approval_id"]}}}

    @activity.defn(name="node.fakeSend.v1")
    async def send(context: Dict[str, Any]) -> Dict[str, Any]:
        calls["send"].append(context)
        if behaviour["send"] == "raise":
            raise RuntimeError("connection reset")
        if behaviour["send"] == "refused":
            return {"success": False, "error": "Recipient not on WhatsApp"}
        return {"success": True, "result": {"message_id": "m1"}}

    @activity.defn(name="approvals.record_outcome")
    async def record(payload: Dict[str, Any]) -> Dict[str, Any]:
        calls["record"].append(payload)
        if payload.get("broke_off"):
            return {"recorded": True, "status": "failed", "outcome": "unknown"}
        if payload.get("success") is False:
            return {"recorded": True, "status": "failed", "outcome": "not_sent"}
        return {"recorded": True, "status": "sent", "outcome": "sent"}

    async with await WorkflowEnvironment.start_time_skipping() as environment:
        async with Worker(environment.client, task_queue=TASK_QUEUE, workflows=[ApprovedToolCallWorkflow], activities=[claim, send, record]):

            async def run(grace_seconds: float = 5.0) -> Dict[str, Any]:
                now = await environment.get_current_time()
                grace = (now + timedelta(seconds=grace_seconds)).astimezone(timezone.utc).isoformat()
                handle = await environment.client.start_workflow(
                    ApprovedToolCallWorkflow.run,
                    {"payload_version": 1, "approval_id": "a1", "revision": 3, "grace_until": grace},
                    id=f"approval-send-{uuid4()}",
                    task_queue=TASK_QUEUE,
                )
                return await handle.result()

            # Sent after the grace, once.
            started = await environment.get_current_time()
            result = await run()
            assert result == {"sent": True, "outcome": "sent"}, result
            assert len(calls["claim"]) == 1 and calls["claim"][0]["revision"] == 3
            claimed_at = calls["claim"][0]["at"]
            assert claimed_at - started >= timedelta(seconds=4.9), (started, claimed_at)
            assert len(calls["send"]) == 1 and len(calls["record"]) == 1

            # Claim refused (Undo, or a newer Send): nothing runs.
            for bucket in calls.values():
                bucket.clear()
            behaviour["claimed"] = False
            result = await run()
            assert result == {"sent": False, "reason": "changed"}
            assert calls["send"] == [] and calls["record"] == []

            # The node refuses: not sent, never retried.
            behaviour.update(claimed=True, send="refused")
            result = await run(0)
            assert result == {"sent": False, "outcome": "not_sent"}
            assert len(calls["send"]) == 1 and calls["record"][-1]["error"] == "Recipient not on WhatsApp"

            # The node breaks off: unknown, never retried.
            calls["send"].clear()
            behaviour["send"] = "raise"
            result = await run(0)
            assert result == {"sent": False, "outcome": "unknown"}
            assert len(calls["send"]) == 1 and calls["record"][-1]["broke_off"] is True


def test_every_worker_registers_the_workflow_and_its_activities() -> None:
    """Every Worker construction carries the send workflow and both its
    activities (the scenarios above pin the names the workflow schedules)."""
    from services.approvals.activities import APPROVAL_ACTIVITIES
    from services.temporal import worker
    from services.temporal.approved_tool_call_workflow import ApprovedToolCallWorkflow

    assert ApprovedToolCallWorkflow in worker._framework_workflows()
    source = (Path(__file__).parents[2] / "services" / "temporal" / "worker.py").read_text(encoding="utf-8")
    assert source.count("*APPROVAL_ACTIVITIES") == 3
    names = sorted(getattr(fn, "__temporal_activity_definition").name for fn in APPROVAL_ACTIVITIES)
    assert names == ["approvals.claim_send", "approvals.record_outcome"]


def test_the_send_workflow_on_temporal() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.temporal.test_approved_tool_call_workflow"],
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
