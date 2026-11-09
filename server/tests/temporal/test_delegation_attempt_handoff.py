"""Retry handoffs keep their own event identity and cannot finish newer work."""

from types import SimpleNamespace

import pytest
from temporalio.exceptions import ApplicationError

from models.database import AgentTeam, TeamTask
from services.agent_team import AgentTeamService
from services.temporal import agent_activities as activities
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


PAYLOAD = {
    "team_id": "team", "team_task_id": "task", "workflow_id": "wf",
    "parent_agent_node_id": "lead", "child_agent_node_id": "worker",
    "child_agent_name": "Worker", "task": "Complete the work",
    "terminal_event_id": "task:terminal", "assignment_event_id": "task:assigned",
}


@pytest.fixture
async def handoff(real_database, monkeypatch):
    service = AgentTeamService(real_database)
    monkeypatch.setattr("services.agent_team._service", service)
    async with real_database.reserved_session() as session:
        session.add(AgentTeam(id="team", workflow_id="wf", team_lead_node_id="lead", execution_id="origin"))
        await session.commit()
    await real_database.create_durable_team_task(
        id="task", team_id="team", title="Work", mission="Complete the work",
        created_by="lead", assigned_to="worker", status="queued",
    )
    owner = SimpleNamespace(workflow_id="runner-0", workflow_run_id="run-0")
    monkeypatch.setattr(activities.activity, "info", lambda: owner)
    controller = WorkflowControlWorkflow()
    controller._event_accumulator_v1 = False
    controller._triggers = {"review": {
        "workflow_type": "TriggerListenerWorkflow",
        "event_type": "com.opencompany.agent.task.completed",
    }}
    monkeypatch.setattr(controller, "_maybe_request_rollover", lambda: None)

    async def deliver(**event):
        await controller.on_event({"id": event["event_id"], "type": "com.opencompany.agent.task.completed", "data": event})

    monkeypatch.setattr("nodes.agent._events.broadcast_agent_task_completed", deliver)
    return SimpleNamespace(database=real_database, service=service, owner=owner, controller=controller)


async def start_attempt(handoff, number):
    handoff.owner.workflow_id = f"runner-{number}"
    handoff.owner.workflow_run_id = f"run-{number}"
    # Deliberately omit attempt_number, matching the current incident's
    # recorded runners. Registration supplies the immutable owner mapping.
    await activities.register_task_execution({
        **PAYLOAD, "runner_workflow_id": handoff.owner.workflow_id,
        "runner_run_id": handoff.owner.workflow_run_id,
        "child_workflow_id": f"child-{number}",
    })
    await activities.begin_agent_delegation(dict(PAYLOAD))


async def retry(handoff):
    task = await handoff.database.get_durable_team_task("team", "task")
    return await handoff.service.mutate_durable_task(
        workflow_id="wf", team_lead_node_id="lead", execution_id="origin",
        task_id="task", revision=task["revision"], operation="retry",
    )


async def test_retried_legacy_runner_delivers_a_second_deduplicated_review(handoff):
    await start_attempt(handoff, 0)
    await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "First result"}})
    # Transport retries of the same completion remain one queued review.
    await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "First result"}})
    assert [event["id"] for _, event in handoff.controller._events] == ["task:terminal"]

    queued = await retry(handoff)
    assert queued["current_attempt"] == 1
    assert queued["runner_run_id"] is None and queued["child_run_id"] is None
    await start_attempt(handoff, 1)
    await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "Revised result"}})
    await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "Revised result"}})

    events = [event for _, event in handoff.controller._events]
    assert [event["id"] for event in events] == ["task:terminal", "task:terminal:attempt:1"]
    assert [event["data"]["lifecycle_data"]["attempt_number"] for event in events] == [0, 1]
    messages = await handoff.database.get_agent_messages("team")
    assert len(messages) == 4  # Separate assignment and result per attempt.
    task = await handoff.database.get_durable_team_task("team", "task")
    assert [attempt["result"]["response"] for attempt in task["attempts"]] == ["First result", "Revised result"]


async def test_old_legacy_runner_cannot_finish_cancel_claim_or_register_the_retry(handoff):
    await start_attempt(handoff, 0)
    await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "First result"}})
    await retry(handoff)
    await start_attempt(handoff, 1)
    handoff.owner.workflow_id, handoff.owner.workflow_run_id = "runner-0", "run-0"
    answer = await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "Stale result"}})
    assert answer["status"] == "superseded"
    cancelled = await activities.cancel_agent_delegation({**PAYLOAD, "reason": "Old cancellation"})
    assert cancelled["status"] == "superseded"
    with pytest.raises(ApplicationError, match="superseded"):
        await activities.begin_agent_delegation(dict(PAYLOAD))
    with pytest.raises(ApplicationError, match="superseded"):
        await activities.register_task_execution({**PAYLOAD, "runner_workflow_id": "runner-0", "runner_run_id": "run-0"})
    task = await handoff.database.get_durable_team_task("team", "task")
    assert task["status"] == "running" and task["current_attempt"] == 1
    assert task["runner_run_id"] == "run-1" and task["result"] is None
    assert len(handoff.controller._events) == 1


async def test_database_guards_attempt_at_the_write_boundary(handoff):
    await start_attempt(handoff, 0)
    await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "First result"}})
    await retry(handoff)
    await start_attempt(handoff, 1)
    database = handoff.database
    assert not await database.complete_task("task", {"response": "Stale"}, expected_attempt=0)
    assert not await database.fail_task("task", "Stale error", expected_attempt=0)
    assert await database.claim_task("task", "worker", expected_attempt=0) is None
    assert await database.cancel_team_task("team", "task", "Stale cancellation", expected_attempt=0) is None
    assert await database.register_team_task_execution(
        team_id="team", task_id="task", attempt_number=0, runner_workflow_id="runner-0", runner_run_id="run-0",
    ) is None
    task = await database.get_durable_team_task("team", "task")
    assert task["status"] == "running" and task["result"] is None and task["error"] is None


async def test_legacy_retry_can_register_when_old_top_level_owner_was_not_cleared(handoff):
    await start_attempt(handoff, 0)
    await activities.finish_agent_delegation({**PAYLOAD, "success": True, "result": {"response": "First result"}})
    await retry(handoff)
    # Legacy retry transitions copied the old task's runner fields. Its new
    # attempt row remains unowned, which is the authority for registration.
    async with handoff.database.get_session() as session:
        task = await session.get(TeamTask, "task")
        task.runner_workflow_id, task.runner_run_id = "runner-0", "run-0"
        await session.commit()
    await start_attempt(handoff, 1)
    task = await handoff.database.get_durable_team_task("team", "task")
    assert task["runner_run_id"] == "run-1" and task["status"] == "running"

