"""Real SQLite tests for reviewed, origin-bound employee delivery."""
from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timedelta, timezone
import pytest
from models.employees import EmployeeJob
from models.database import TeamTask
from services.employees.jobs import deliver_job, bind_job_team
from services.plugin import NodeContext

@pytest.fixture
async def database(real_database):
    yield real_database

@pytest.fixture
async def runtime(database, monkeypatch):
    execute = AsyncMock(return_value={"success": True, "result": {"approved": True}})
    tasks = AsyncMock(return_value=[{"id": "task", "status": "accepted"}])
    monkeypatch.setattr(database, "get_team_tasks", tasks)
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: database, workflow_service=lambda: SimpleNamespace(execute_node=execute)))
    return SimpleNamespace(execute=execute, tasks=tasks)

async def install(database, *, sink="telegramSend", inflight=None, expired=True):
    nodes = [{"id": "lead", "type": "ai_employee"}, {"id": "gate", "type": "approvalGate"}, {"id": "send", "type": sink}]
    source = {"nodes": nodes, "edges": [{"source": "gate", "target": "send", "data": {"condition": {"field": "result.approved", "operator": "is_true"}}}],
        "parameters": {"gate": {}, "send": {"chat_id": "ORIGINAL-RECIPIENT", "text": "{{lead.response}}"}},
        "outputs": {"trigger": {"recipient": "ORIGINAL-RECIPIENT"}}, "user_id": "owner", "session_id": "7", "generation": 4}
    delivery = {"node_ids": ["gate", "send"], "completed": [], "inflight": inflight}
    if inflight:
        delivery["lease_until"] = (datetime.now(timezone.utc) + timedelta(minutes=-1 if expired else 2)).isoformat()
    job = EmployeeJob(id="job", workflow_id="7", origin_execution_id="origin", lead_node_id="lead", team_id="team", state="working", source=source, delivery=delivery, mission="Mission")
    async with database.reserved_session() as session:
        session.add(job)
        session.add(TeamTask(id="task", team_id="team", title="Work", created_by="lead", status="accepted"))
        await session.commit()
    return job

def context(response="Reviewed result"):
    return NodeContext(node_id="boundary", node_type="employeeJob", workflow_id="7", execution_id="review", outputs={"lead": {"response": response, "team_id": "team", "execution_id": "origin"}})

async def saved(database):
    async with database.get_session() as session:
        return await session.get(EmployeeJob, "job")


async def test_v2_job_keeps_admitted_plan_after_saved_metadata_rollback(database, monkeypatch):
    from models.database import WorkflowControlExecution
    from models.employees import Employee
    from services.employees.jobs import create_job
    from tests.services.employees.test_runtime_v2 import admitted
    admitted_context, plan = admitted()
    admitted_context["workflow_id"] = "7"
    admitted_context["outputs"] = admitted_context["inputs"]
    admitted_context["parameter_snapshot"] = {node["id"]: {} for node in admitted_context["nodes"]}
    admitted_context["parameter_snapshot"]["send"] = {"recipient": "original"}
    async with database.reserved_session() as session:
        session.add(Employee(id="employee", workflow_id="7", owner_id="owner", hire_state="ready", team_plan={}, node_roles={}))
        # The generation was admitted with the plan; the saved metadata rolled back since.
        session.add(WorkflowControlExecution(id="workflow-control:7:4", workflow_id="7", generation=4, execution_id="origin",
            root_execution_id="origin", data_scope_id="original-scope", status="running", idempotency_key="start",
            graph_hash="admitted", graph_snapshot={"nodes": admitted_context["nodes"], "edges": [],
                                                   "parameters": admitted_context["parameter_snapshot"]}))
        await session.commit()
    monkeypatch.setattr(database, "get_workflow", AsyncMock(return_value=SimpleNamespace(data={"nodes": [{"id": "unactivated"}], "edges": []})))
    # A Talk run holds only its own line; the job takes the generation's whole graph.
    talk_run = {**admitted_context, "nodes": [node for node in admitted_context["nodes"] if node["id"] == "talk"]}
    job = await create_job(database, NodeContext.from_legacy("talk", "chatAgent", talk_run), mission="Original work",
        lead_node_id="lead", delivery_node_ids=["chat"], dispatch=True)
    assert job.source["team_plan"] == plan
    assert job.source["generation"] == 4
    assert job.source["data_scope_id"] == "original-scope"
    assert job.source["parameters"]["send"]["recipient"] == "original"
    assert {node["id"] for node in job.source["nodes"]} == {node["id"] for node in admitted_context["nodes"]}

@pytest.mark.parametrize("status", ["queued", "running", "submitted", "skipped"])
async def test_unreviewed_or_skipped_tasks_never_publish(database, runtime, status):
    await install(database)
    async with database.reserved_session() as session:
        (await session.get(TeamTask, "task")).status = status
        await session.commit()
    result = await deliver_job(database, context("Assignment acknowledged"), lead_node_id="lead")
    assert result["delivered"] is False
    runtime.execute.assert_not_awaited()

@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_terminal_task_failure_does_not_publish(database, runtime, status):
    await install(database)
    async with database.reserved_session() as session:
        (await session.get(TeamTask, "task")).status = status
        await session.commit()
    result = await deliver_job(database, context(), lead_node_id="lead")
    assert result["state"] == status
    runtime.execute.assert_not_awaited()

async def test_gate_discard_prevents_send_and_duplicate_delivery(database, runtime):
    await install(database)
    runtime.execute.return_value = {"success": True, "result": {"approved": False}}
    first = await deliver_job(database, context(), lead_node_id="lead")
    second = await deliver_job(database, context("different response"), lead_node_id="lead")
    assert first["delivered"] and second["delivered"]
    assert runtime.execute.await_count == 1
    assert (await saved(database)).result == "Reviewed result"

async def test_original_recipient_and_outputs_survive_review(database, runtime):
    await install(database)
    assert (await deliver_job(database, context(), lead_node_id="lead"))["delivered"]
    assert runtime.execute.await_count == 2
    call = runtime.execute.await_args_list[-1]
    assert call.kwargs["parameters"]["chat_id"] == "ORIGINAL-RECIPIENT"
    assert call.kwargs["outputs"]["trigger"]["recipient"] == "ORIGINAL-RECIPIENT"

async def test_uncertain_external_send_and_live_claim_do_not_retry(database, runtime):
    await install(database, inflight="send")
    result = await deliver_job(database, context(), lead_node_id="lead")
    assert result["state"] == "delivery_needs_review"
    runtime.execute.assert_not_awaited()

async def test_concurrent_waiting_gate_uses_one_claim(database, runtime):
    await install(database)
    entered, release = asyncio.Event(), asyncio.Event()
    async def wait_gate(**kwargs):
        if kwargs.get("node_type") == "approvalGate":
            entered.set()
            await release.wait()
        return {"success": True, "result": {"approved": True}}
    runtime.execute.side_effect = wait_gate
    task = asyncio.create_task(deliver_job(database, context(), lead_node_id="lead"))
    await asyncio.wait_for(entered.wait(), 3)
    duplicate = await deliver_job(database, context(), lead_node_id="lead")
    assert duplicate["state"] == "delivering"
    release.set()
    assert (await task)["delivered"]
    assert runtime.execute.await_count == 2

async def test_job_team_binding_uses_origin_and_cannot_swap_teams(database, runtime):
    job = await install(database)
    async with database.reserved_session() as session:
        row = await session.get(EmployeeJob, job.id)
        row.team_id = None
        await session.commit()
    await bind_job_team(database, workflow_id="7", lead_node_id="lead", execution_id="wrong", team_id="other")
    assert (await saved(database)).team_id is None
    await bind_job_team(database, workflow_id="7", lead_node_id="lead", execution_id="origin", team_id="team")
    with pytest.raises(RuntimeError, match="another team"):
        await bind_job_team(database, workflow_id="7", lead_node_id="lead", execution_id="origin", team_id="other")


async def test_expired_gate_claim_reattaches_and_chat_uid_is_stable(database, runtime, monkeypatch):
    job = await install(database, sink="chatReply", inflight="gate")
    ids = []
    async def record(*args, uid, **kwargs):
        ids.append(uid)
        return True
    monkeypatch.setattr("services.chat_thread.record_chat_message", record)
    assert (await deliver_job(database, context(), lead_node_id="lead"))["delivered"]
    # Model a crash after the chat append but before its completion checkpoint.
    async with database.reserved_session() as session:
        row = await session.get(EmployeeJob, job.id)
        row.state = "delivering"
        row.delivery = {**row.delivery, "completed": ["gate"], "inflight": "send", "claim_token": "crashed", "lease_until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}
        await session.commit()
    assert (await deliver_job(database, context("changed"), lead_node_id="lead"))["delivered"]
    assert len(ids) == 2 and ids[0] == ids[1]
    assert (await saved(database)).result == "Reviewed result"


# 1b.9: reviewed delivery re-attaches to its own lease.
WAIT_INTERRUPTED = {"success": False, "error": "The worker is stopping", "error_type": "NodeWaitInterrupted"}


def attempt(number, *, workflow_id="job", activity_id="deliver"):
    from dataclasses import replace
    from temporalio.testing import ActivityEnvironment

    env = ActivityEnvironment()
    env.info = replace(env.info, workflow_id=workflow_id, activity_id=activity_id, attempt=number)
    return env


async def test_a_retry_of_the_same_delivery_reattaches_within_its_lease(database, runtime):
    from services.plugin.base import NodeWaitInterrupted

    await install(database)
    runtime.execute.return_value = WAIT_INTERRUPTED
    with pytest.raises(NodeWaitInterrupted):
        await attempt(1).run(deliver_job, database, context(), lead_node_id="lead")
    row = await saved(database)
    # The interrupted wait records nothing.
    assert row.state == "delivering"
    assert row.delivery["inflight"] == "gate" and row.delivery["completed"] == []
    assert row.delivery["lease_holder"] == "job:deliver"

    # Another delivery within the lease still waits its turn.
    other = await attempt(1, workflow_id="job:lead", activity_id="employee-reviewed-delivery").run(
        deliver_job, database, context(), lead_node_id="lead")
    assert other["state"] == "delivering"

    runtime.execute.return_value = {"success": True, "result": {"approved": True}}
    assert (await attempt(2).run(deliver_job, database, context(), lead_node_id="lead"))["delivered"]
    assert (await saved(database)).state == "delivered"


async def test_a_refused_send_fails_the_job_and_a_failed_job_is_never_delivered(database, runtime):
    await install(database)
    runtime.execute.return_value = {"success": False, "error": "Telegram refused the message"}
    assert await attempt(1).run(deliver_job, database, context(), lead_node_id="lead") == {
        "delivered": False, "state": "failed", "job_id": "job"}
    row = await saved(database)
    assert row.state == "failed"
    assert (row.delivery["inflight"], row.delivery["claim_token"], row.delivery["lease_until"]) == (None, None, None)
    sends = runtime.execute.await_count

    assert (await attempt(2).run(deliver_job, database, context(), lead_node_id="lead"))["state"] == "failed"
    assert runtime.execute.await_count == sends


async def test_a_refusal_keeps_a_resets_cancelled(database, runtime):
    await install(database)

    async def refuse_after_reset(**_kwargs):
        async with database.reserved_session() as session:
            (await session.get(EmployeeJob, "job")).state = "cancelled"
            await session.commit()
        return {"success": False, "error": "refused"}

    runtime.execute.side_effect = refuse_after_reset
    assert (await deliver_job(database, context(), lead_node_id="lead"))["state"] == "cancelled"
    assert (await saved(database)).state == "cancelled"
