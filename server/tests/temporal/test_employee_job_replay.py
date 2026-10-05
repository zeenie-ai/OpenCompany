"""Real Temporal SDK histories for reviewed jobs, failure and cancellation."""
from __future__ import annotations
import asyncio
import subprocess
import sys
from pathlib import Path
from uuid import uuid4
from temporalio import activity, workflow
from temporalio.client import WorkflowHistory, WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker, Replayer
from services.temporal.employee_job_workflow import EmployeeJobWorkflow

@workflow.defn(name="AgentWorkflow", sandboxed=False)
class StubLead:
    @workflow.run
    async def run(self, context: dict) -> dict:
        if context.get("mode") == "cancel":
            await workflow.wait_condition(lambda: False)
        if context.get("mode") == "failure":
            return {"success": False, "error": "lead failed"}
        return {"success": True, "result": {"response": "Reviewed final result", "team_id": "team", "execution_id": context["employee_job_id"]}}

async def replay_gate():
    deliveries, failures = [], []
    @activity.defn(name="employee.job.deliver")
    async def deliver(context: dict) -> dict:
        deliveries.append(context)
        assert context["outputs"]["origin"]["recipient"] == "original"
        assert context["outputs"]["lead"]["response"] == "Reviewed final result"
        return {"delivered": True}
    @activity.defn(name="employee.job.failed")
    async def fail(context: dict) -> dict:
        failures.append(context)
        return {"saved": True}
    histories = []
    queue = "employee-job-replay"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(env.client, task_queue=queue, workflows=[EmployeeJobWorkflow, StubLead], activities=[deliver, fail]):
            for mode in ("success", "failure", "cancel"):
                identifier = "job-replay-" + uuid4().hex
                context = {"node_id": "lead", "workflow_id": "graph", "employee_job_id": identifier, "mode": mode,
                           "outputs": {"origin": {"recipient": "original"}}}
                handle = await env.client.start_workflow(EmployeeJobWorkflow.run, context, id=identifier, task_queue=queue)
                if mode == "cancel":
                    # Wait until the stub lead is running before cancellation.
                    for _ in range(100):
                        history = await handle.fetch_history()
                        if any(event.HasField("child_workflow_execution_started_event_attributes") for event in history.events):
                            break
                        await asyncio.sleep(0.02)
                    await handle.cancel()
                    try:
                        await handle.result()
                    except WorkflowFailureError:
                        pass
                else:
                    result = await handle.result()
                    assert result.get("delivered") if mode == "success" else result.get("success") is False
                histories.append(await handle.fetch_history())
    assert len(deliveries) == 1
    assert len(failures) == 2
    assert any(context.get("cancelled") for context in failures)
    for history in histories:
        copied = WorkflowHistory.from_json(history.workflow_id, history.to_json())
        result = await Replayer(workflows=[EmployeeJobWorkflow, StubLead]).replay_workflow(copied, raise_on_replay_failure=False)
        assert result.replay_failure is None, result.replay_failure

def test_employee_job_histories_replay():
    completed = subprocess.run([sys.executable, "-m", "tests.temporal.test_employee_job_replay"], cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-4000:]

if __name__ == "__main__":
    asyncio.run(replay_gate())
