from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from models.agent_instances import AgentDelivery
from models.database import AgentTeam, TeamMember, TeamTask
from models.employees import EmployeeJob
from services.agent_instances.service import AgentInstanceService
from services.employees.review_recovery import retry_review


@pytest.fixture
async def review_job(real_database, monkeypatch):
    db = real_database
    async with db.reserved_session() as session:
        session.add(AgentTeam(id="review-team", workflow_id="wf", team_lead_node_id="lead", execution_id="job"))
        session.add(TeamMember(team_id="review-team", agent_node_id="worker", agent_type="aiAgent", role="teammate"))
        session.add(EmployeeJob(id="job", workflow_id="wf", origin_execution_id="job", lead_node_id="lead", team_id="review-team",
            state="review_blocked", source={"user_id": "owner", "generation": 1, "agent_lifecycle_version": 1}))
        await session.commit()
    service = AgentInstanceService(db)
    scope = {"workflow_id": "wf", "generation": 1, "team_id": "review-team", "job_id": "job", "execution_id": "job", "lead_node_id": "lead"}
    lead = await service.ensure_lead(scope=scope, template={"node_id": "lead"}, request_id="lead")
    scope["agent_instance_id"] = lead["id"]
    task = await service.execute("spawn", scope=scope, arguments={"instructions": "Keep this work"}, request_id="spawn",
        template={"node_id": "worker", "node_type": "aiAgent"})
    await service.halt_instance(lead["id"], reason="invalid_request_limit", request_id="halt")
    wake = AsyncMock()
    monkeypatch.setattr("services.agent_instances.runtime.wake_instances", wake)
    monkeypatch.setattr("services.employees.events.employee_changed_now", lambda _: None)
    return db, service, lead, task, wake


async def test_owner_retry_is_idempotent_and_does_not_recreate_worker_tasks(review_job):
    db, service, lead, task, wake = review_job
    args = {"workflow_id": "wf", "job_id": "job", "owner_id": "owner", "request_key": "owner-click"}
    first = await retry_review(db, **args)
    assert first["success"]
    assert await retry_review(db, **args) == first
    assert (await service.get_instance(lead["id"]))["state"] != "halted"
    async with db.get_session() as session:
        tasks = (await session.execute(select(TeamTask).where(TeamTask.team_id == "review-team"))).scalars().all()
        deliveries = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == lead["id"]))).scalars().all()
    assert len(tasks) == 1 and tasks[0].id == task["task"]["id"]
    assert tasks[0].current_attempt == 0
    assert len(deliveries) == 1
    wake.assert_awaited()


async def test_other_owner_cannot_resume_the_job(review_job):
    db, service, lead, _, wake = review_job
    result = await retry_review(db, workflow_id="wf", job_id="job", owner_id="someone-else", request_key="x")
    assert result == {"success": False, "error": "not_found"}
    assert (await service.get_instance(lead["id"]))["state"] == "halted"
    wake.assert_not_awaited()


async def test_message_only_worker_turn_uses_fenced_instance_authority(review_job):
    from services.employees.job_source import authorize_employee_job_source
    from services.employees.team_runtime import employee_job_parameters
    db, service, lead, task, _ = review_job
    async with db.reserved_session() as session:
        job = await session.get(EmployeeJob, "job")
        job.source = {**job.source, "team_plan": {"lead_node_id": "lead", "members": [{"node_id": "worker"}]},
                      "parameters": {"worker": {"model": "later-job-default"}}}
        await session.commit()
    await retry_review(db, workflow_id="wf", job_id="job", owner_id="owner", request_key="recover")
    agent = task["agent"]["id"]
    first = await service.claim_run(agent, request_id="task-run")
    await service.checkpoint(agent, first["run_id"], messages=[*first["messages"], {"role": "assistant", "content": "Done"}],
        expected_revision=0, delivery_ids=first["deliveries"], request_id="saved")
    await service.complete_run(agent, first["run_id"], result={"response": "Done"}, request_id="done")
    scope = {"workflow_id": "wf", "generation": 1, "team_id": "review-team", "job_id": "job", "execution_id": "job",
             "lead_node_id": "lead", "agent_instance_id": lead["id"]}
    await service.execute("send_message", scope=scope, arguments={"agent": agent, "message": "Explain your result."}, request_id="followup")
    followup = await service.claim_run(agent, request_id="peer-run")
    assert followup["task"] is None
    context = {"employee_job_id": "job", "workflow_id": "wf", "generation": 1, "node_id": "worker",
               "agent_instance_id": agent, "agent_run_id": followup["run_id"]}
    assert (await authorize_employee_job_source(db, context)).id == "job"
    # The admitted instance's empty configuration wins over later job defaults.
    assert (await employee_job_parameters(db, context))["worker"] == {}
    with pytest.raises(PermissionError, match="no longer owns"):
        await authorize_employee_job_source(db, {**context, "agent_run_id": first["run_id"]})
    with pytest.raises(PermissionError):
        await authorize_employee_job_source(db, {**context, "team_task_id": task["task"]["id"]})


@pytest.mark.parametrize("socket", [SimpleNamespace(scope={"path": "/ws/internal"}, state=SimpleNamespace(user_id="owner")),
                                    SimpleNamespace(scope={"path": "/ws"}, state=SimpleNamespace(user_id=None))])
async def test_internal_and_unauthenticated_sockets_cannot_retry(socket, monkeypatch):
    from services.employees.handlers import handle_retry_employee_review
    retry = AsyncMock()
    monkeypatch.setattr("services.employees.review_recovery.retry_review", retry)
    response = await handle_retry_employee_review({"workflow_id": "wf", "job_id": "job", "idempotency_key": "x"}, socket)
    assert not response["success"]
    retry.assert_not_awaited()
