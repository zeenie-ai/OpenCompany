from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.employees import EmployeeJob
from services.employees.delivery_resolution import continue_delivery, dispatch_resolution, recover_delivery_resolutions, resolve_delivery
from services.employees.jobs import deliver_job
from services.plugin import NodeContext


@pytest.fixture
def delivery_runtime(real_database, monkeypatch):
    execute = AsyncMock(return_value={"success": True, "result": {"sent": True}})
    start = AsyncMock()
    container = SimpleNamespace(database=lambda: real_database,
        workflow_service=lambda: SimpleNamespace(execute_node=execute),
        temporal_client=lambda: SimpleNamespace(client=SimpleNamespace(start_workflow=start)),
        settings=lambda: SimpleNamespace(temporal_task_queue="test"))
    monkeypatch.setattr("core.container.container", container)
    monkeypatch.setattr("services.employees.events.employee_changed_now", lambda _: None)
    monkeypatch.setattr("services.employees.team_runtime.team_runtime_error", lambda: None)
    monkeypatch.setattr(real_database, "get_team_tasks", AsyncMock(return_value=[{"id": "task", "status": "accepted"}]))
    return SimpleNamespace(execute=execute, start=start)


async def install(database, *, tail=True, approval=True):
    nodes = [{"id": "lead", "type": "ai_employee"}, {"id": "gate", "type": "approvalGate"},
             {"id": "send", "type": "telegramSend"}, {"id": "tail", "type": "console"}]
    source = {"user_id": "owner", "session_id": "7", "nodes": nodes,
        "edges": [{"source": "gate", "target": "send", "data": {"condition": {"field": "approved", "operator": "eq", "value": True}}}],
        "parameters": {"gate": {"ask_first": True}, "send": {"chat_id": "SAVED-RECIPIENT", "text": "{{lead.response}}"}, "tail": {}},
        "outputs": {"origin": {"chat_id": "SAVED-RECIPIENT"}}, "generation": 3}
    job = EmployeeJob(id="job", workflow_id="7", origin_execution_id="original-run", team_id="team", lead_node_id="lead",
        state="delivering", result="Reviewed answer", source=source,
        delivery={"node_ids": ["gate", "send", *(["tail"] if tail else [])], "completed": ["gate"], "inflight": "send", "claim_token": "expired-claim",
            "lease_until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), "outputs": {"gate": {"approved": approval}}})
    async with database.reserved_session() as session:
        session.add(job)
        await session.commit()
    return job


async def saved(database):
    async with database.get_session() as session:
        return await session.get(EmployeeJob, "job")


async def decide(database, decision="retry", key="request", owner="owner"):
    return await resolve_delivery(database, workflow_id="7", job_id="job", owner_id=owner, decision=decision, request_key=key)


async def test_uncertain_external_intent_never_auto_resends(real_database, delivery_runtime):
    job = await install(real_database)
    context = NodeContext(node_id="boundary", node_type="employeeJob", workflow_id="7", execution_id=job.origin_execution_id,
        outputs={"lead": {"response": job.result, "team_id": "team", "execution_id": job.origin_execution_id}})
    result = await deliver_job(real_database, context, lead_node_id="lead")
    assert result["state"] == "delivery_needs_review"
    await recover_delivery_resolutions(real_database)
    delivery_runtime.execute.assert_not_awaited()
    delivery_runtime.start.assert_not_awaited()
    assert (await saved(real_database)).delivery["inflight"] == "send"


async def test_owner_confirmed_arrival_advances_only_remaining_sinks(real_database, delivery_runtime):
    await install(real_database)
    review = await decide(real_database, "arrived")
    assert review["success"]
    assert (await saved(real_database)).delivery["completed"] == ["gate", "send"]
    result = await continue_delivery(real_database, "job", review["request_id"])
    assert result["delivered"]
    assert [call.kwargs["node_id"] for call in delivery_runtime.execute.await_args_list] == ["tail"]
    assert (await saved(real_database)).state == "delivered"


async def test_confirming_final_arrival_finishes_without_another_workflow(real_database, delivery_runtime):
    await install(real_database, tail=False)
    result = await decide(real_database, "arrived")
    assert result["state"] == "complete"
    assert (await saved(real_database)).state == "delivered"
    await recover_delivery_resolutions(real_database)
    delivery_runtime.start.assert_not_awaited()
    delivery_runtime.execute.assert_not_awaited()


async def test_retry_uses_saved_reviewed_response_recipient_and_approval(real_database, delivery_runtime):
    await install(real_database)
    review = await decide(real_database)
    result = await continue_delivery(real_database, "job", review["request_id"])
    assert result["delivered"]
    [send, tail] = delivery_runtime.execute.await_args_list
    assert send.kwargs["parameters"]["chat_id"] == "SAVED-RECIPIENT"
    assert send.kwargs["outputs"]["lead"]["response"] == "Reviewed answer"
    assert send.kwargs["outputs"]["gate"]["approved"] is True
    assert send.kwargs["execution_id"] == "job"
    assert tail.kwargs["node_id"] == "tail"


async def test_retry_preserves_declined_approval_and_does_not_send(real_database, delivery_runtime):
    await install(real_database, tail=False, approval=False)
    review = await decide(real_database)
    result = await continue_delivery(real_database, "job", review["request_id"])
    assert result["delivered"]
    delivery_runtime.execute.assert_not_awaited()


async def test_retry_request_is_idempotent_and_has_stable_temporal_identity(real_database, delivery_runtime):
    await install(real_database)
    first = await decide(real_database)
    second = await decide(real_database)
    assert first["request_id"] == second["request_id"]
    assert (await decide(real_database, "arrived"))["error"] == "request_conflict"
    assert await dispatch_resolution(real_database, "job", first["request_id"])
    assert not await dispatch_resolution(real_database, "job", first["request_id"])
    delivery_runtime.start.assert_awaited_once()
    assert delivery_runtime.start.await_args.kwargs["id"] == first["request_id"]
    from temporalio.common import WorkflowIDReusePolicy
    assert delivery_runtime.start.await_args.kwargs["id_reuse_policy"] == WorkflowIDReusePolicy.REJECT_DUPLICATE
    await continue_delivery(real_database, "job", first["request_id"])
    sends = delivery_runtime.execute.await_count
    await continue_delivery(real_database, "job", first["request_id"])
    assert delivery_runtime.execute.await_count == sends


async def test_foreign_owner_cannot_confirm_or_retry(real_database, delivery_runtime):
    await install(real_database)
    assert (await decide(real_database, owner="another-owner"))["error"] == "not_found"
    assert (await saved(real_database)).delivery["inflight"] == "send"
    delivery_runtime.execute.assert_not_awaited()


@pytest.mark.parametrize("status", ["pending", "submitted", "running", "failed", "cancelled"])
async def test_unreviewed_work_cannot_be_reconciled(real_database, delivery_runtime, status):
    await install(real_database)
    real_database.get_team_tasks.return_value = [{"id": "task", "status": status}]
    assert (await decide(real_database))["error"] == "result_not_reviewed"
    assert (await saved(real_database)).delivery["inflight"] == "send"
    delivery_runtime.execute.assert_not_awaited()


async def test_live_send_lease_cannot_be_overridden(real_database, delivery_runtime):
    await install(real_database)
    async with real_database.reserved_session() as session:
        job = await session.get(EmployeeJob, "job")
        job.delivery = {**job.delivery, "lease_until": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()}
        await session.commit()
    assert (await decide(real_database))["error"] == "not_waiting_for_review"
    assert (await saved(real_database)).delivery["inflight"] == "send"


@pytest.mark.parametrize("path,principal", [("/ws/status", None), ("/ws/internal", "owner"), ("/ws/status", "another-owner")])
async def test_socket_decisions_require_authenticated_owner(real_database, delivery_runtime, path, principal):
    from services.employees.handlers import handle_resolve_employee_delivery
    from services.authz.ws_surface import INTERNAL_SOCKET_HANDLERS

    assert "resolve_employee_delivery" not in INTERNAL_SOCKET_HANDLERS
    await install(real_database)
    assert await real_database.save_workflow("7", "Maya", "maya", {"owner_id": "owner", "nodes": [], "edges": []})
    socket = SimpleNamespace(scope={"path": path}, state=SimpleNamespace(user_id=principal))
    response = await handle_resolve_employee_delivery({"workflow_id": "7", "job_id": "job", "decision": "retry", "idempotency_key": "owner-click", "user_id": "owner"}, socket)
    assert response == {"success": False, "error": "not_found"}
    assert (await saved(real_database)).delivery["inflight"] == "send"


async def test_owner_socket_payload_cannot_change_reviewed_answer_or_recipient(real_database, delivery_runtime):
    from services.employees.handlers import handle_resolve_employee_delivery

    await install(real_database)
    assert await real_database.save_workflow("7", "Maya", "maya", {"owner_id": "owner", "nodes": [], "edges": []})
    socket = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
    response = await handle_resolve_employee_delivery({"workflow_id": "7", "job_id": "job", "decision": "retry", "idempotency_key": "owner-click",
        "response": "Assignment acknowledgement", "recipient": "UNTRUSTED-RECIPIENT", "user_id": "another-owner"}, socket)
    assert response["success"]
    await continue_delivery(real_database, "job", response["request_id"])
    send = delivery_runtime.execute.await_args_list[0].kwargs
    assert send["outputs"]["lead"]["response"] == "Reviewed answer"
    assert send["parameters"]["chat_id"] == "SAVED-RECIPIENT"
