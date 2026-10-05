"""Real SQLite tests for reviewed, origin-bound employee delivery."""
from __future__ import annotations
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from models.employees import EmployeeJob
from services.employees.jobs import deliver_job, bind_job_team
from services.plugin import NodeContext

@pytest.fixture
async def database():
    name = "tests._job_db_" + uuid4().hex
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[3] / "core/database.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    db_path = Path.cwd() / (".job-test-" + uuid4().hex + ".db")
    db = module.Database(SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}", database_echo=False, database_pool_size=5, database_max_overflow=5))
    await db.startup()
    try:
        yield db
    finally:
        await db.shutdown()
        sys.modules.pop(name, None)
        for path in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
            path.unlink(missing_ok=True)

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
        await session.commit()
    return job

def context(response="Reviewed result"):
    return NodeContext(node_id="boundary", node_type="employeeJob", workflow_id="7", execution_id="review", outputs={"lead": {"response": response, "team_id": "team", "execution_id": "origin"}})

async def saved(database):
    async with database.get_session() as session:
        return await session.get(EmployeeJob, "job")

@pytest.mark.parametrize("status", ["queued", "running", "submitted", "skipped"])
async def test_unreviewed_or_skipped_tasks_never_publish(database, runtime, status):
    await install(database)
    runtime.tasks.return_value = [{"status": status}]
    result = await deliver_job(database, context("Assignment acknowledged"), lead_node_id="lead")
    assert result["delivered"] is False
    runtime.execute.assert_not_awaited()

@pytest.mark.parametrize("status", ["failed", "cancelled"])
async def test_terminal_task_failure_does_not_publish(database, runtime, status):
    await install(database)
    runtime.tasks.return_value = [{"status": status}]
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
