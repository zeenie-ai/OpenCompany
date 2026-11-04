"""Workflow Reset must acknowledge standalone Workspace cleanup before success."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.deployment import handlers


def control(status="reset", revision=4):
    return SimpleNamespace(
        id="control-wf-2", workflow_id="wf", generation=2, execution_id="execution-2",
        root_execution_id="execution-2", data_scope_id="scope-2", status=status, revision=revision,
    )


def broadcaster(monkeypatch, order=None):
    from services import status_broadcaster

    async def broadcast(message):
        if order is not None:
            order.append(message["type"])

    value = SimpleNamespace(broadcast=AsyncMock(side_effect=broadcast))
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: value)
    return value


@pytest.mark.parametrize("current", [None, control()])
async def test_direct_reset_waits_for_workspace_barrier_before_any_success_broadcast(monkeypatch, current):
    entered, release = asyncio.Event(), asyncio.Event()
    order = []
    database = SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=current))
    service = SimpleNamespace(database=database, get_status=AsyncMock(return_value={"state": "ready"}), transition=AsyncMock())

    async def reset_workspace(*_args):
        order.append("workspace:start")
        entered.set()
        await release.wait()
        order.append("workspace:done")
        return {"cancelled": 1, "reset_nodes": ["phone"], "present": True}

    workspace = AsyncMock(side_effect=reset_workspace)
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", workspace)
    monkeypatch.setattr(handlers, "_with_runtime_counts", AsyncMock(return_value={"state": "ready", "can_reset": True}))
    notices = broadcaster(monkeypatch, order)
    data = {"workflow_id": "wf", "expected_revision": current.revision if current else 0, "idempotency_key": "reset-phone-1"}
    task = asyncio.create_task(handlers.handle_reset_workflow(data, None))
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()
    notices.broadcast.assert_not_awaited()
    service.get_status.assert_not_awaited()
    service.transition.assert_not_awaited()
    release.set()
    result = await asyncio.wait_for(task, 1)
    assert result == {"success": True, "idempotent": False, "state": "ready", "can_reset": True}
    workspace.assert_awaited_once_with("wf", database, current, data)
    assert order == ["workspace:start", "workspace:done", "workflow_runtime_reset", "workflow_control_status"]
    assert notices.broadcast.await_args_list[0].args[0]["reset_nodes"] == ["phone"]


@pytest.mark.parametrize("current", [None, control()])
async def test_direct_reset_barrier_failure_does_not_publish_success(monkeypatch, current):
    database = SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=current))
    service = SimpleNamespace(database=database, get_status=AsyncMock(), transition=AsyncMock())
    workspace = AsyncMock(side_effect=RuntimeError("workspace_cleanup_failed"))
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", workspace)
    notices = broadcaster(monkeypatch)
    result = await handlers.handle_reset_workflow({"workflow_id": "wf"}, None)
    assert result == {"success": False, "error": "workspace_cleanup_failed"}
    notices.broadcast.assert_not_awaited()
    service.get_status.assert_not_awaited()
    service.transition.assert_not_awaited()


@pytest.mark.parametrize("current", [None, control()])
async def test_a_pending_workspace_cleanup_answers_pending_and_publishes_nothing(monkeypatch, current):
    # 1b.15: the Workspace controller's reset waits at most 240 s.
    from services.temporal.update_calls import UpdatePending

    database = SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=current))
    service = SimpleNamespace(database=database, get_status=AsyncMock(return_value={"state": "ready"}), transition=AsyncMock())
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", AsyncMock(side_effect=UpdatePending("no answer in 240 s")))
    monkeypatch.setattr(handlers, "_control_payload", AsyncMock(return_value={"state": "ready"}))
    monkeypatch.setattr(handlers, "_with_runtime_counts", AsyncMock(return_value={"state": "ready"}))
    notices = broadcaster(monkeypatch)
    data = {"workflow_id": "wf", "expected_revision": current.revision if current else 0}
    assert await handlers.handle_reset_workflow(data, None) == {
        "success": False, "error": "workflow_control_transition_pending", "state": "ready",
    }
    notices.broadcast.assert_not_awaited()
    service.transition.assert_not_awaited()


async def test_a_pending_workspace_cleanup_keeps_a_generation_resetting(monkeypatch):
    from services.deployment import runtime_state
    from services.temporal.update_calls import UpdatePending

    initial, resetting = control("running", 3), control("resetting", 4)
    statuses = []

    async def transition(_current, **kwargs):
        statuses.append(kwargs["status"])
        return resetting

    database = SimpleNamespace(
        get_latest_workflow_control=AsyncMock(side_effect=[initial, initial, resetting]),
        update_workflow_run_data_scope=AsyncMock(),
    )
    service = SimpleNamespace(database=database, transition=AsyncMock(side_effect=transition))
    archive = AsyncMock()
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_close_local_admission", Mock())
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(return_value={"state": "resetting"}))
    monkeypatch.setattr(handlers, "_signal_controller", AsyncMock())
    monkeypatch.setattr(handlers, "_delete_cron_schedules", AsyncMock(return_value=0))
    monkeypatch.setattr(handlers, "handle_cancel_deployment", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(handlers, "_terminate_generation_workflows", AsyncMock(return_value=0))
    monkeypatch.setattr("services.approvals.waiting.cancel_waits", AsyncMock(return_value=0))
    monkeypatch.setattr("services.employees.safe_apply.close_open_work", AsyncMock(return_value=None))
    monkeypatch.setattr("services.employees.safe_apply.cancel_applies", AsyncMock(return_value=None))
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", AsyncMock(side_effect=UpdatePending("no answer in 240 s")))
    monkeypatch.setattr(handlers, "_control_payload", AsyncMock(side_effect=lambda row, **_: {"state": row.status}))
    monkeypatch.setattr(runtime_state, "archive_and_reset_node_state", archive)
    broadcaster(monkeypatch)
    result = await handlers.handle_reset_workflow(
        {"workflow_id": "wf", "expected_revision": 3, "idempotency_key": "reset-generation"}, None)
    assert result == {"success": False, "error": "workflow_control_transition_pending", "state": "resetting"}
    assert statuses == ["resetting"]
    database.update_workflow_run_data_scope.assert_not_awaited()
    archive.assert_not_awaited()


@pytest.mark.parametrize("current", [None, control()])
async def test_ordinary_idempotent_reset_has_no_workspace_reset_broadcast(monkeypatch, current):
    database = SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=current))
    payload = {"state": "ready", "can_reset": False}
    service = SimpleNamespace(database=database, get_status=AsyncMock(return_value=payload))
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", AsyncMock(return_value={"present": False}))
    monkeypatch.setattr(handlers, "_control_payload", AsyncMock(return_value=payload))
    notices = broadcaster(monkeypatch)
    assert await handlers.handle_reset_workflow({"workflow_id": "wf"}, None) == {
        "success": True, "idempotent": True, **payload,
    }
    notices.broadcast.assert_not_awaited()


@pytest.mark.parametrize("initial_status", ["running", "resetting"])
@pytest.mark.parametrize("workspace_fails", [False, True])
async def test_generation_reset_waits_for_workspace_before_archive_and_final_reset(monkeypatch, initial_status, workspace_fails):
    from services.deployment import runtime_state

    initial = control(initial_status, 3 if initial_status == "running" else 4)
    resetting, reset = control("resetting", 4), control("reset", 5)
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def transition(_current, **kwargs):
        order.append("db:" + kwargs["status"])
        return resetting if kwargs["status"] == "resetting" else reset

    async def archive_scope(*_args, **_kwargs):
        order.append("archive:scope")
        return True

    async def archive_nodes(*_args, **_kwargs):
        order.append("archive:nodes")
        return {"archived_nodes": 1, "reset_nodes": ["phone"]}

    async def terminate(*_args, **_kwargs):
        order.append("terminate:graph")
        return 1

    async def reset_workspace(*_args):
        order.append("workspace:start")
        entered.set()
        await release.wait()
        if workspace_fails:
            raise RuntimeError("workspace_cleanup_failed")
        order.append("workspace:done")
        return {"cancelled": 2, "reset_nodes": ["phone"], "present": True}

    async def broadcast_control(current, **_kwargs):
        order.append("control:" + current.status)
        return {"state": "ready" if current.status == "reset" else current.status}

    database = SimpleNamespace(
        get_latest_workflow_control=AsyncMock(return_value=initial),
        update_workflow_run_data_scope=AsyncMock(side_effect=archive_scope),
    )
    service = SimpleNamespace(database=database, transition=AsyncMock(side_effect=transition))
    archive = AsyncMock(side_effect=archive_nodes)
    workspace = AsyncMock(side_effect=reset_workspace)
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_close_local_admission", Mock())
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(side_effect=broadcast_control))
    monkeypatch.setattr(handlers, "_signal_controller", AsyncMock())
    monkeypatch.setattr(handlers, "_delete_cron_schedules", AsyncMock(return_value=1))
    monkeypatch.setattr(handlers, "handle_cancel_deployment", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(handlers, "_terminate_generation_workflows", AsyncMock(side_effect=terminate))
    monkeypatch.setattr("services.approvals.waiting.cancel_waits", AsyncMock(return_value=0))

    async def close_open_work(*_args, **_kwargs):
        order.append("close:work")

    async def cancel_applies(*_args):
        order.append("cancel:applies")

    monkeypatch.setattr("services.employees.safe_apply.close_open_work", close_open_work)
    monkeypatch.setattr("services.employees.safe_apply.cancel_applies", cancel_applies)
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", workspace)
    monkeypatch.setattr(runtime_state, "archive_and_reset_node_state", archive)
    notices = broadcaster(monkeypatch, order)
    data = {"workflow_id": "wf", "expected_revision": initial.revision, "idempotency_key": "reset-generation"}
    task = asyncio.create_task(handlers.handle_reset_workflow(data, None))
    await asyncio.wait_for(entered.wait(), 1)
    assert not task.done()
    database.update_workflow_run_data_scope.assert_not_awaited()
    archive.assert_not_awaited()
    notices.broadcast.assert_not_awaited()
    assert all(call.kwargs["status"] != "reset" for call in service.transition.await_args_list)
    release.set()
    result = await asyncio.wait_for(task, 1)
    workspace.assert_awaited_once_with("wf", database, resetting if initial_status == "running" else initial, data)
    if workspace_fails:
        assert result == {"success": False, "error": "workspace_cleanup_failed"}
        database.update_workflow_run_data_scope.assert_not_awaited()
        archive.assert_not_awaited()
        notices.broadcast.assert_not_awaited()
        assert "db:reset" not in order and "control:reset" not in order
    else:
        assert result["success"] is True and result["state"] == "ready"
        assert order[-7:] == ["workspace:start", "workspace:done", "archive:scope", "archive:nodes",
                              "db:reset", "workflow_runtime_reset", "control:reset"]
        assert order.index("terminate:graph") < order.index("close:work") < order.index("cancel:applies") < order.index("workspace:start")
        assert notices.broadcast.await_args.args[0]["cancelled_workspace_tasks"] == 2


def invocation_services(monkeypatch, *, nodes, actor=None, available=True):
    import core.container as container_module
    from services import node_invocations

    database, client = object(), object()
    container = SimpleNamespace(database=lambda: database,
                                temporal_client=lambda: SimpleNamespace(client=client) if available else None)
    monkeypatch.setattr(container_module, "container", container)
    find_nodes = AsyncMock(return_value=({"nodes": nodes}, nodes))
    query = AsyncMock(return_value=actor)
    reset = AsyncMock(return_value={"cancelled": 1, "reset_nodes": ["phone"]})
    monkeypatch.setattr(node_invocations, "workspace_task_nodes", find_nodes)
    monkeypatch.setattr(node_invocations, "controller_status", query)
    monkeypatch.setattr(node_invocations, "reset", reset)
    return SimpleNamespace(database=database, client=client, container=container, find_nodes=find_nodes, query=query, reset=reset)


@pytest.mark.parametrize("has_nodes,has_actor", [(False, False), (True, False), (False, True), (True, True)])
async def test_reset_helper_requires_saved_workspace_nodes_or_existing_actor(monkeypatch, has_nodes, has_actor):
    nodes = [{"id": "phone", "type": "mobile_use_agent"}] if has_nodes else []
    mocks = invocation_services(monkeypatch, nodes=nodes, actor={"active_count": 1} if has_actor else None)
    current = control()
    result = await handlers._reset_workspace_tasks("wf", mocks.database, current, {"idempotency_key": "client-reset-id"})
    mocks.find_nodes.assert_awaited_once_with(mocks.database, "wf", current)
    mocks.query.assert_awaited_once_with("wf", client=mocks.client)
    if has_nodes or has_actor:
        mocks.reset.assert_awaited_once_with("wf", "client-reset-id")
        assert result == {"cancelled": 1, "reset_nodes": ["phone"], "present": True}
    else:
        mocks.reset.assert_not_awaited()
        assert result == {"cancelled": 0, "reset_nodes": [], "present": False}


@pytest.mark.parametrize("current,revision", [(None, 0), (control(), 4)])
async def test_reset_helper_generates_fresh_request_id_when_client_omits_one(monkeypatch, current, revision):
    mocks = invocation_services(monkeypatch, nodes=[{"id": "phone"}])
    await handlers._reset_workspace_tasks("wf", mocks.database, current, {"expected_revision": revision})
    await handlers._reset_workspace_tasks("wf", mocks.database, current, {"expected_revision": revision})
    assert mocks.reset.await_count == 2
    calls = mocks.reset.await_args_list
    assert all(call.args[0] == "wf" for call in calls)
    first_id, second_id = (call.args[1] for call in calls)
    assert isinstance(first_id, str) and first_id
    assert isinstance(second_id, str) and second_id
    assert first_id != second_id


async def test_reset_helper_does_not_treat_unavailable_engine_as_completed_cleanup(monkeypatch):
    mocks = invocation_services(monkeypatch, nodes=[{"id": "phone"}], available=False)
    mocks.reset.side_effect = RuntimeError("temporal_unavailable")
    with pytest.raises(RuntimeError, match="temporal_unavailable"):
        await handlers._reset_workspace_tasks("wf", mocks.database, None, {"idempotency_key": "reset-id"})
    mocks.query.assert_not_awaited()
    mocks.reset.assert_awaited_once_with("wf", "reset-id")


@pytest.mark.parametrize("available", [False, True])
@pytest.mark.parametrize("has_nodes", [False, True])
async def test_status_exposes_reset_for_direct_workspace_nodes_before_any_actor(monkeypatch, available, has_nodes):
    mocks = invocation_services(monkeypatch, nodes=[{"id": "phone"}] if has_nodes else [], available=available)
    result = await handlers._workspace_runtime_status("wf")
    assert result.get("can_reset", False) is has_nodes
    if not available:
        mocks.query.assert_not_awaited()
        if has_nodes:
            assert result["workspace_available"] is False
    elif has_nodes:
        assert result["workspace_epoch"] == 0


async def test_status_preserves_reset_affordance_when_workspace_query_fails(monkeypatch):
    mocks = invocation_services(monkeypatch, nodes=[{"id": "phone"}])
    mocks.query.side_effect = RuntimeError("engine temporarily unavailable")
    assert await handlers._workspace_runtime_status("wf") == {"can_reset": True, "workspace_available": False}


async def test_runtime_counts_enable_reset_for_direct_nodes_without_deployment(monkeypatch):
    mocks = invocation_services(monkeypatch, nodes=[{"id": "phone"}])
    mocks.container.workflow_service = lambda: SimpleNamespace(get_deployment_status=lambda _wf: {})
    result = await handlers._with_runtime_counts({"state": "ready", "can_reset": False}, "wf")
    assert result["can_reset"] is True
    assert result["active_count"] == result["in_flight_count"] == 0


async def test_workspace_resetting_actor_disables_start_and_preserves_reset_request(monkeypatch):
    invocation_services(monkeypatch, nodes=[], actor={"active_count": 2, "epoch": 3, "resetting": True,
                                                     "reset_request_id": "reset-pending"})
    result = await handlers._workspace_runtime_status("wf")
    assert result["state"] == "resetting"
    assert result["can_reset"] is True
    assert result["can_start"] is result["can_pause"] is result["can_resume"] is result["can_edit"] is False
    assert result["workspace_active_count"] == 2
    assert result["workspace_reset_request_id"] == "reset-pending"
