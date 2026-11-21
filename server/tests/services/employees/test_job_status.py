"""Talk sees bounded durable evidence, and runtime crashes stay distinct."""
import pytest
from models.database import AgentTeam, TeamTask
from models.employees import EmployeeJob
from services.employees.jobs import bind_job_team, fail_before_assignment, get_job_status, job_progress


def test_explicit_no_assignment_failure_is_preserved_without_leaking_details():
    from temporalio.exceptions import ApplicationError
    from services.employees.failures import lead_failure
    cause = ApplicationError("private detail", type="EmployeeLeadNoAssignments")
    wrapper = ApplicationError("Activity task failed", type="Wrapper")
    wrapper.__cause__ = cause
    assert lead_failure(wrapper) == {"code": "lead_no_assignments", "message": "The lead completed without assigning work to a teammate."}
    assert lead_failure({"error_type": "lead_no_assignments"})["code"] == "lead_no_assignments"


async def install(db, *, workflow="wf", lead="lead", identifier="job", team="team", state="working", delivery=None):
    async with db.reserved_session() as session:
        session.add(AgentTeam(id=team, workflow_id=workflow, team_lead_node_id=lead, execution_id=identifier))
        session.add(EmployeeJob(id=identifier, workflow_id=workflow, lead_node_id=lead, origin_execution_id=identifier,
            team_id=team, state=state, mission="private instructions", source={"private": "secret"},
            delivery=delivery or {}, result="private unfinished result"))
        await session.commit()


async def test_job_status_is_scoped_bounded_and_does_not_claim_unassigned_work_started(real_database):
    await install(real_database)
    await install(real_database, workflow="other", identifier="foreign", team="other-team")
    status = (await get_job_status(real_database, workflow_id="wf", lead_node_id="lead"))["job"]
    assert status["job_id"] == "job" and status["task_count"] == 0
    assert "no teammate task" in status["message"]
    assert "private" not in str(status) and "secret" not in str(status)
    assert (await get_job_status(real_database, workflow_id="wf", lead_node_id="lead", job_id="foreign"))["job"] is None
    assert (await get_job_status(real_database, workflow_id="wf", lead_node_id="different"))["job"] is None
    await real_database.create_durable_team_task(id="task", team_id="team", workflow_id="wf", title="private", created_by="lead", status="running")
    status = (await get_job_status(real_database, workflow_id="wf", lead_node_id="lead"))["job"]
    assert status["tasks_by_status"] == {"running": 1} and status["task_count"] == 1


async def test_runtime_failure_remains_terminal_and_visible_to_talk_and_ui(real_database, monkeypatch):
    monkeypatch.setattr("services.employees.events.employee_changed_now", lambda _: None)
    await install(real_database)
    payload = {"employee_job_id": "job", "workflow_id": "wf", "node_id": "lead", "team_id": "team",
               "failure_code": "lead_payload_too_large", "failure_message": "secret exception"}
    assert (await fail_before_assignment(real_database, payload))["saved"]
    assert not (await fail_before_assignment(real_database, payload))["saved"]
    status = (await get_job_status(real_database, workflow_id="wf", lead_node_id="lead", job_id="job"))["job"]
    assert status["state"] == "failed" and status["failure_code"] == "lead_payload_too_large"
    assert "size limit" in status["message"] and "secret" not in str(status)
    assert "size limit" in (await job_progress(real_database, "wf"))["message"]
    assert await real_database.create_durable_team_task(id="late", team_id="team", title="Late", created_by="lead") is None
    await bind_job_team(real_database, workflow_id="wf", lead_node_id="lead", execution_id="job", team_id="team")
    assert (await get_job_status(real_database, workflow_id="wf", lead_node_id="lead"))["job"]["state"] == "failed"


@pytest.mark.parametrize("protection", ["task", "delivery", "approval"])
async def test_failure_cannot_abandon_existing_work_or_delivery(real_database, protection):
    await install(real_database, delivery={"inflight": "send"} if protection == "delivery" else {})
    if protection == "task":
        await real_database.create_durable_team_task(id="task", team_id="team", title="Work", created_by="lead", status="running")
    if protection == "approval":
        from models.approvals import ApprovalRequest
        async with real_database.reserved_session() as session:
            session.add(ApprovalRequest(id="approval", idempotency_key="approval", workflow_id="wf", owner_id="owner", node_id="gate", execution_id="job", status="pending"))
            await session.commit()
    assert not (await fail_before_assignment(real_database, {"employee_job_id": "job", "failure_code": "lead_execution_failed"}))["saved"]
    assert (await get_job_status(real_database, workflow_id="wf", lead_node_id="lead"))["job"]["state"] == ("delivery_needs_review" if protection == "delivery" else "working")
    async with real_database.get_session() as session:
        assert (await session.get(EmployeeJob, "job")).state == "working"


async def test_delivered_job_is_reported_even_when_home_has_no_active_progress(real_database):
    await install(real_database, state="delivered")
    assert await job_progress(real_database, "wf") is None
    assert (await get_job_status(real_database, workflow_id="wf", lead_node_id="lead"))["job"]["state"] == "delivered"
