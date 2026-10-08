"""Control-handler contract tests for acknowledged Temporal lifecycle changes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.deployment import handlers


@pytest.fixture(autouse=True)
def no_workspace_tasks(monkeypatch):
    # These lifecycle contracts use ordinary graph nodes. Workspace-specific
    # admission and cleanup are covered in test_workspace_task_reset.py.
    monkeypatch.setattr(handlers, "_workspace_runtime_status", AsyncMock(return_value={}))
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", AsyncMock(return_value={"present": False}))


def _control(
    status: str,
    revision: int,
    *,
    terminal_reason: str | None = None,
):
    return SimpleNamespace(
        id="workflow-control:wf:1",
        workflow_id="wf",
        generation=1,
        execution_id="execution-1",
        root_execution_id="execution-1",
        data_scope_id="execution-1",
        controller_workflow_id="workflow-control-wf-g1",
        controller_run_id="controller-run-1",
        status=status,
        revision=revision,
        created_at=None,
        updated_at=None,
        terminal_reason=terminal_reason,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latest", "expected_revision", "state", "revision"),
    [
        (_control("running", 2), 0, "running", 2),
        (_control("reset", 4), 3, "ready", 4),
        (None, 3, "never_started", 0),
    ],
)
async def test_stale_start_returns_current_status_without_starting_or_error_trace(
    monkeypatch, caplog, latest, expected_revision, state, revision,
):
    service = SimpleNamespace(
        database=SimpleNamespace(
            get_workflow_control_by_idempotency_key=AsyncMock(return_value=None),
            get_latest_workflow_control=AsyncMock(return_value=latest),
        ),
        begin_generation=AsyncMock(),
    )
    start_controller = AsyncMock()
    deploy = AsyncMock()
    snapshot = {"workflow_id": "wf", "state": state, "revision": revision}
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_control_payload", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(handlers, "_with_runtime_counts", AsyncMock(return_value=snapshot))
    monkeypatch.setattr(handlers, "_start_controller", start_controller)
    monkeypatch.setattr(handlers, "handle_deploy_workflow", deploy)

    result = await handlers.handle_start_workflow(
        {"workflow_id": "wf", "expected_revision": expected_revision,
         "idempotency_key": "stale-start"},
        None,
    )

    assert result == {"success": False, "error": "control_revision_conflict", **snapshot}
    service.begin_generation.assert_not_awaited()
    start_controller.assert_not_awaited()
    deploy.assert_not_awaited()
    assert not any(record.levelno >= 40 or record.exc_info for record in caplog.records)


@pytest.mark.asyncio
async def test_start_waits_for_deployment_setup_before_publishing_running(monkeypatch):
    starting = _control("starting", 0)
    starting_with_run = _control("starting", 1)
    running = _control("running", 2)
    order: list[str] = []

    async def transition(_control_value, **kwargs):
        order.append(f"db:{kwargs['status']}")
        return (
            starting_with_run
            if kwargs["status"] == "starting"
            else running
        )

    service = SimpleNamespace(
        database=SimpleNamespace(
            get_workflow_control_by_idempotency_key=AsyncMock(return_value=None),
            get_latest_workflow_control=AsyncMock(return_value=None),
            update_workflow_run_data_scope=AsyncMock(return_value=True),
        ),
        begin_generation=AsyncMock(return_value=(starting, True)),
        transition=AsyncMock(side_effect=transition),
    )

    async def broadcast(control, **_kwargs):
        order.append(f"broadcast:{control.status}:{control.revision}")
        return {"state": control.status, "revision": control.revision}

    async def deployment_setup(_workflow_id):
        order.append("deployment:ready")
        return {"success": True}

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)
    monkeypatch.setattr(
        handlers,
        "_start_controller",
        AsyncMock(return_value="controller-run-1"),
    )
    monkeypatch.setattr(
        handlers,
        "handle_deploy_workflow",
        AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr(handlers, "_await_deployment_setup", deployment_setup)
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(return_value={"state": "running", "revision": 0}),
    )

    result = await handlers.handle_start_workflow(
        {
            "workflow_id": "wf",
            "nodes": [{"id": "start-1", "type": "start", "data": {}}],
            "edges": [],
            "expected_revision": 0,
            "idempotency_key": "start-request-1",
        },
        None,
    )

    assert result["success"] is True
    assert result["state"] == "running"
    assert order == [
        "broadcast:starting:0",
        "db:starting",
        "broadcast:starting:1",
        "deployment:ready",
        "db:running",
        "broadcast:running:2",
    ]


@pytest.mark.asyncio
async def test_start_post_commit_broadcast_failure_does_not_tear_down_running(
    monkeypatch,
):
    starting = _control("starting", 0)
    starting_with_run = _control("starting", 1)
    running = _control("running", 2)

    async def transition(_control_value, **kwargs):
        if kwargs["status"] == "starting":
            return starting_with_run
        return running

    async def broadcast(control, **_kwargs):
        if control.status == "running":
            raise RuntimeError("status broadcast unavailable")
        return {"state": control.status, "revision": control.revision}

    service = SimpleNamespace(
        database=SimpleNamespace(
            get_workflow_control_by_idempotency_key=AsyncMock(
                return_value=None,
            ),
            get_latest_workflow_control=AsyncMock(return_value=None),
            update_workflow_run_data_scope=AsyncMock(return_value=True),
        ),
        begin_generation=AsyncMock(return_value=(starting, True)),
        transition=AsyncMock(side_effect=transition),
        fail=AsyncMock(),
    )
    signal_controller = AsyncMock()
    terminate_generation = AsyncMock()

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)
    monkeypatch.setattr(
        handlers,
        "_start_controller",
        AsyncMock(return_value="controller-run-1"),
    )
    monkeypatch.setattr(
        handlers,
        "handle_deploy_workflow",
        AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr(
        handlers,
        "_await_deployment_setup",
        AsyncMock(return_value={"success": True}),
    )
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(return_value={"state": "running", "revision": 0}),
    )
    monkeypatch.setattr(handlers, "_signal_controller", signal_controller)
    monkeypatch.setattr(
        handlers,
        "_terminate_generation_workflows",
        terminate_generation,
    )

    result = await handlers.handle_start_workflow(
        {
            "workflow_id": "wf",
            "nodes": [{"id": "start-1", "type": "start", "data": {}}],
            "edges": [],
            "expected_revision": 0,
            "idempotency_key": "start-request-1",
        },
        None,
    )

    assert result == {
        "success": False,
        "error": "status broadcast unavailable",
    }
    assert service.transition.await_count == 2
    assert service.transition.await_args.kwargs["status"] == "running"
    service.fail.assert_not_awaited()
    signal_controller.assert_not_awaited()
    terminate_generation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("control", "expected_error"),
    [
        (_control("starting", 1), "workflow_start_pending"),
        (
            _control(
                "failed",
                2,
                terminal_reason="deployment_setup_failed",
            ),
            "deployment_setup_failed",
        ),
    ],
)
async def test_duplicate_start_reports_non_successful_durable_outcome(
    monkeypatch,
    control,
    expected_error,
):
    service = SimpleNamespace(
        database=SimpleNamespace(
            get_workflow_control_by_idempotency_key=AsyncMock(
                return_value=control,
            ),
        ),
    )
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_reconcile_control",
        AsyncMock(return_value=(control, None)),
    )
    monkeypatch.setattr(
        handlers,
        "_control_payload",
        AsyncMock(
            return_value={
                "state": control.status,
                "revision": control.revision,
            }
        ),
    )

    result = await handlers.handle_start_workflow(
        {
            "workflow_id": "wf",
            "idempotency_key": "start-request-1",
        },
        None,
    )

    assert result == {
        "success": False,
        "error": expected_error,
        "idempotent": True,
        "state": control.status,
        "revision": control.revision,
    }


@pytest.mark.asyncio
async def test_concurrent_duplicate_start_reports_pending_winner(monkeypatch):
    starting = _control("starting", 1)
    service = SimpleNamespace(
        database=SimpleNamespace(
            get_workflow_control_by_idempotency_key=AsyncMock(
                return_value=None,
            ),
            get_latest_workflow_control=AsyncMock(return_value=None),
        ),
        begin_generation=AsyncMock(return_value=(starting, False)),
    )
    start_controller = AsyncMock()
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_reconcile_control",
        AsyncMock(return_value=(starting, None)),
    )
    monkeypatch.setattr(
        handlers,
        "_control_payload",
        AsyncMock(return_value={"state": "starting", "revision": 1}),
    )
    monkeypatch.setattr(handlers, "_start_controller", start_controller)

    result = await handlers.handle_start_workflow(
        {
            "workflow_id": "wf",
            "expected_revision": 0,
            "idempotency_key": "start-request-1",
        },
        None,
    )

    assert result["success"] is False
    assert result["error"] == "workflow_start_pending"
    assert result["idempotent"] is True
    start_controller.assert_not_awaited()


@pytest.mark.asyncio
async def test_controller_state_change_waits_for_acknowledged_update(monkeypatch):
    handle = SimpleNamespace(
        execute_update=AsyncMock(return_value={
            "state": "paused",
            "revision": 3,
            "queued_events": 2,
        }),
    )
    monkeypatch.setattr(handlers, "_controller_handle", lambda _control: handle)

    result = await handlers._update_controller_state(
        _control("pausing", 2),
        "paused",
        update_id="request-1:paused",
    )

    assert result["state"] == "paused"
    handle.execute_update.assert_awaited_once_with(
        "set_control_state",
        "paused",
        id="request-1:paused",
    )


@pytest.mark.asyncio
async def test_reconcile_completes_interrupted_pause_from_temporal(monkeypatch):
    from core import container as container_module

    pausing = _control("pausing", 3)
    paused = _control("paused", 4)
    service = SimpleNamespace(
        transition=AsyncMock(return_value=paused),
        database=SimpleNamespace(get_latest_workflow_control=AsyncMock()),
    )
    update = AsyncMock(return_value={
        "state": "paused",
        "revision": 1,
        "queued_events": 0,
    })
    broadcast = AsyncMock(return_value={})
    workflow_service = SimpleNamespace(
        pause_deployment=MagicMock(),
        update_trigger_pause_status=AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(return_value={"state": "running", "revision": 0}),
    )
    monkeypatch.setattr(handlers, "_update_controller_state", update)
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)
    monkeypatch.setattr(handlers, "_set_cron_pause", AsyncMock(return_value=1))
    monkeypatch.setattr(
        handlers,
        "_signal_generation_workflows",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        container_module.container,
        "workflow_service",
        lambda: workflow_service,
    )

    reconciled, temporal_status = await handlers._reconcile_control(
        service,
        pausing,
    )

    assert reconciled is paused
    assert temporal_status["state"] == "paused"
    update.assert_awaited_once_with(
        pausing,
        "paused",
        update_id="reconcile:workflow-control:wf:1:3:paused",
    )
    service.transition.assert_awaited_once_with(
        pausing,
        expected_revision=3,
        from_statuses={"pausing"},
        status="paused",
    )
    broadcast.assert_awaited_once_with(
        paused,
        controller_status=temporal_status,
        extra={
            "signalled_executions": 2,
            "paused_schedules": 1,
            "paused_triggers": 1,
        },
    )


@pytest.mark.asyncio
async def test_pause_response_is_final_only_after_temporal_ack(monkeypatch):
    from core import container as container_module

    running = _control("running", 2)
    pausing = _control("pausing", 3)
    paused = _control("paused", 4)
    order: list[str] = []
    update_calls: list[tuple[object, str, str]] = []

    async def transition(control, **kwargs):
        order.append(f"db:{kwargs['status']}")
        return pausing if kwargs["status"] == "pausing" else paused

    service = SimpleNamespace(
        database=SimpleNamespace(
            get_latest_workflow_control=AsyncMock(return_value=running),
        ),
        transition=AsyncMock(side_effect=transition),
    )
    workflow_service = SimpleNamespace(
        pause_deployment=MagicMock(side_effect=lambda _workflow_id: order.append("local:pause")),
        resume_deployment=AsyncMock(),
        update_trigger_pause_status=AsyncMock(return_value=1),
        get_deployment_status=MagicMock(return_value={
            "active_runs": 0,
            "queued_events": 0,
        }),
    )

    async def update_controller(control, requested_state, *, update_id):
        order.append("temporal:ack")
        update_calls.append((control, requested_state, update_id))
        return {"state": "paused", "revision": 1, "queued_events": 0}

    async def broadcast(control, **_kwargs):
        order.append(f"broadcast:{control.status}")
        return {
            "state": control.status,
            "revision": control.revision,
            "can_pause": control.status == "running",
            "can_resume": control.status == "paused",
        }

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_reconcile_control",
        AsyncMock(return_value=(running, {"state": "running", "revision": 0})),
    )
    monkeypatch.setattr(handlers, "_update_controller_state", update_controller)
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)
    monkeypatch.setattr(handlers, "_set_cron_pause", AsyncMock(return_value=1))
    monkeypatch.setattr(
        handlers,
        "_signal_generation_workflows",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        container_module.container,
        "workflow_service",
        lambda: workflow_service,
    )

    result = await handlers.handle_pause_workflow(
        {
            "workflow_id": "wf",
            "expected_revision": 2,
            "idempotency_key": "request-1",
        },
        None,
    )

    assert result["success"] is True
    assert result["state"] == "paused"
    assert order == [
        "db:pausing",
        "broadcast:pausing",
        "local:pause",
        "temporal:ack",
        "db:paused",
        "broadcast:paused",
    ]
    assert update_calls == [
        (
            pausing,
            "paused",
            "workflow-control:wf:1:3:request-1:paused",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "operation",
        "initial_status",
        "transitional_status",
        "requested_state",
        "signal_name",
        "cron_paused",
    ),
    [
        ("pause", "running", "pausing", "paused", "pause", True),
        ("resume", "paused", "resuming", "running", "resume", False),
    ],
)
async def test_stable_state_is_not_published_when_strict_fanout_fails(
    monkeypatch,
    operation,
    initial_status,
    transitional_status,
    requested_state,
    signal_name,
    cron_paused,
):
    from core import container as container_module

    initial = _control(initial_status, 2)
    transitional = _control(transitional_status, 3)
    service = SimpleNamespace(
        database=SimpleNamespace(
            get_latest_workflow_control=AsyncMock(return_value=initial),
        ),
        transition=AsyncMock(return_value=transitional),
    )
    workflow_service = SimpleNamespace(
        pause_deployment=MagicMock(),
        resume_deployment=AsyncMock(return_value=0),
        update_trigger_pause_status=AsyncMock(return_value=1),
    )
    set_cron_pause = AsyncMock(return_value=1)
    fanout = AsyncMock(
        side_effect=RuntimeError("workflow_signal_failed:1"),
    )

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_reconcile_control",
        AsyncMock(
            return_value=(
                initial,
                {"state": initial_status, "revision": 0},
            )
        ),
    )
    monkeypatch.setattr(
        handlers,
        "_update_controller_state",
        AsyncMock(
            return_value={
                "state": requested_state,
                "revision": 1,
                "queued_events": 0,
            }
        ),
    )
    monkeypatch.setattr(
        handlers,
        "_broadcast_control",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(handlers, "_set_cron_pause", set_cron_pause)
    monkeypatch.setattr(
        handlers,
        "_signal_generation_workflows",
        fanout,
    )
    monkeypatch.setattr(
        container_module.container,
        "workflow_service",
        lambda: workflow_service,
    )

    handler = (
        handlers.handle_pause_workflow
        if operation == "pause"
        else handlers.handle_resume_workflow
    )
    result = await handler(
        {
            "workflow_id": "wf",
            "expected_revision": 2,
            "idempotency_key": "request-1",
        },
        None,
    )

    assert result == {
        "success": False,
        "error": "workflow_signal_failed:1",
    }
    service.transition.assert_awaited_once_with(
        initial,
        expected_revision=2,
        from_statuses={initial_status},
        status=transitional_status,
    )
    set_cron_pause.assert_awaited_once_with(
        "wf",
        paused=cron_paused,
        strict=True,
    )
    fanout.assert_awaited_once_with(
        transitional,
        signal_name,
        strict=True,
    )


@pytest.mark.asyncio
async def test_pause_temporal_failure_restores_running_projection(monkeypatch):
    from core import container as container_module

    running = _control("running", 2)
    pausing = _control("pausing", 3)
    service = SimpleNamespace(
        database=SimpleNamespace(
            get_latest_workflow_control=AsyncMock(return_value=running),
        ),
        transition=AsyncMock(return_value=pausing),
    )
    workflow_service = SimpleNamespace(
        pause_deployment=MagicMock(),
        resume_deployment=AsyncMock(return_value=0),
    )
    restore = AsyncMock(return_value=running)

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_reconcile_control",
        AsyncMock(return_value=(running, {"state": "running", "revision": 0})),
    )
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(return_value={}))
    monkeypatch.setattr(
        handlers,
        "_update_controller_state",
        AsyncMock(
            side_effect=handlers.TemporalControlUnavailable(
                "temporal_control_unavailable"
            )
        ),
    )
    monkeypatch.setattr(
        handlers,
        "_restore_control_after_failed_update",
        restore,
    )
    monkeypatch.setattr(
        container_module.container,
        "workflow_service",
        lambda: workflow_service,
    )

    result = await handlers.handle_pause_workflow(
        {"workflow_id": "wf", "expected_revision": 2},
        None,
    )

    assert result == {
        "success": False,
        "error": "temporal_control_unavailable",
    }
    workflow_service.resume_deployment.assert_awaited_once_with("wf")
    restore.assert_awaited_once_with(
        service,
        pausing,
        transitional_state="pausing",
        stable_state="running",
    )


@pytest.mark.asyncio
async def test_unknown_update_outcome_stays_transitional_for_reconciliation(
    monkeypatch,
):
    from core import container as container_module

    running = _control("running", 2)
    pausing = _control("pausing", 3)
    service = SimpleNamespace(
        database=SimpleNamespace(
            get_latest_workflow_control=AsyncMock(return_value=running),
        ),
        transition=AsyncMock(return_value=pausing),
    )
    workflow_service = SimpleNamespace(
        pause_deployment=MagicMock(),
        resume_deployment=AsyncMock(return_value=0),
    )
    restore = AsyncMock()

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_reconcile_control",
        AsyncMock(return_value=(running, {"state": "running", "revision": 0})),
    )
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(return_value={}))
    monkeypatch.setattr(
        handlers,
        "_update_controller_state",
        AsyncMock(side_effect=TimeoutError("update outcome unknown")),
    )
    monkeypatch.setattr(
        handlers,
        "_restore_control_after_failed_update",
        restore,
    )
    monkeypatch.setattr(
        container_module.container,
        "workflow_service",
        lambda: workflow_service,
    )

    result = await handlers.handle_pause_workflow(
        {"workflow_id": "wf", "expected_revision": 2},
        None,
    )

    assert result == {"success": False, "error": "update outcome unknown"}
    workflow_service.resume_deployment.assert_not_awaited()
    restore.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_counts_sum_controller_and_local_queues(monkeypatch):
    from core import container as container_module

    workflow_service = SimpleNamespace(
        get_deployment_status=MagicMock(
            return_value={
                "active_runs": 4,
                "queued_events": 3,
            }
        ),
    )
    monkeypatch.setattr(
        container_module.container,
        "workflow_service",
        lambda: workflow_service,
    )

    result = await handlers._with_runtime_counts(
        {"queued_count": 2},
        "wf",
    )

    assert result["active_count"] == 4
    assert result["in_flight_count"] == 4
    assert result["queued_count"] == 5


@pytest.mark.asyncio
async def test_reset_cleanup_failure_leaves_control_resetting(monkeypatch):
    running = _control("running", 2)
    resetting = _control("resetting", 3)
    cleanup_order: list[str] = []
    database = SimpleNamespace(
        get_latest_workflow_control=AsyncMock(return_value=running),
        update_workflow_run_data_scope=AsyncMock(return_value=True),
    )
    service = SimpleNamespace(
        database=database,
        transition=AsyncMock(return_value=resetting),
    )

    async def delete_schedules_impl(*_args, **_kwargs):
        cleanup_order.append("delete_schedules")
        return 0

    async def cancel_deployment_impl(*_args, **_kwargs):
        cleanup_order.append("cancel_local")
        return {"success": True}

    async def terminate_impl(*_args, **_kwargs):
        cleanup_order.append("terminate")
        raise RuntimeError("workflow_termination_failed:1")

    def close_local_admission(_workflow_id):
        cleanup_order.append("close_local_admission")

    async def signal_controller(*_args, **_kwargs):
        cleanup_order.append("close_controller")

    delete_schedules = AsyncMock(side_effect=delete_schedules_impl)
    cancel_deployment = AsyncMock(side_effect=cancel_deployment_impl)
    terminate = AsyncMock(side_effect=terminate_impl)

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_broadcast_control",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        handlers,
        "_close_local_admission",
        close_local_admission,
    )
    monkeypatch.setattr(
        handlers,
        "_signal_controller",
        AsyncMock(side_effect=signal_controller),
    )
    monkeypatch.setattr(
        handlers,
        "_terminate_generation_workflows",
        terminate,
    )
    monkeypatch.setattr(
        handlers,
        "_delete_cron_schedules",
        delete_schedules,
    )
    monkeypatch.setattr(
        handlers,
        "handle_cancel_deployment",
        cancel_deployment,
    )

    result = await handlers.handle_reset_workflow(
        {"workflow_id": "wf", "expected_revision": 2},
        None,
    )

    assert result == {
        "success": False,
        "error": "workflow_termination_failed:1",
    }
    assert service.transition.await_count == 1
    first_transition = service.transition.await_args
    assert first_transition.args == (running,)
    assert first_transition.kwargs["expected_revision"] == 2
    assert first_transition.kwargs["status"] == "resetting"
    assert cleanup_order == [
        "close_local_admission",
        "close_controller",
        "delete_schedules",
        "cancel_local",
        "terminate",
    ]
    delete_schedules.assert_awaited_once_with("wf", strict=True)
    cancel_deployment.assert_awaited_once_with(
        {"workflow_id": "wf"},
        None,
    )
    terminate.assert_awaited_once_with(resetting, strict=True)
    database.update_workflow_run_data_scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_local_cleanup_failure_leaves_control_resetting(
    monkeypatch,
):
    resetting = _control("resetting", 3)
    database = SimpleNamespace(
        get_latest_workflow_control=AsyncMock(return_value=resetting),
        update_workflow_run_data_scope=AsyncMock(return_value=True),
    )
    service = SimpleNamespace(
        database=database,
        transition=AsyncMock(),
    )

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_broadcast_control",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(handlers, "_signal_controller", AsyncMock())
    monkeypatch.setattr(
        handlers,
        "_terminate_generation_workflows",
        AsyncMock(return_value=2),
    )
    monkeypatch.setattr(
        handlers,
        "_delete_cron_schedules",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        handlers,
        "handle_cancel_deployment",
        AsyncMock(
            return_value={
                "success": False,
                "error": "listener teardown failed",
            }
        ),
    )

    result = await handlers.handle_reset_workflow(
        {"workflow_id": "wf"},
        None,
    )

    assert result == {
        "success": False,
        "error": (
            "workflow_local_cleanup_failed:listener teardown failed"
        ),
    }
    service.transition.assert_not_awaited()
    database.update_workflow_run_data_scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_reset_is_idempotent_without_repeating_cleanup(
    monkeypatch,
):
    reset = _control("reset", 4)
    database = SimpleNamespace(
        get_latest_workflow_control=AsyncMock(return_value=reset),
        update_workflow_run_data_scope=AsyncMock(),
    )
    service = SimpleNamespace(
        database=database,
        transition=AsyncMock(),
    )
    close_local_admission = MagicMock()
    signal_controller = AsyncMock()
    delete_schedules = AsyncMock()
    cancel_deployment = AsyncMock()
    terminate = AsyncMock()
    control_payload = AsyncMock(
        return_value={
            "state": "ready",
            "revision": 4,
            "generation": 1,
        }
    )

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_close_local_admission",
        close_local_admission,
    )
    monkeypatch.setattr(handlers, "_signal_controller", signal_controller)
    monkeypatch.setattr(
        handlers,
        "_delete_cron_schedules",
        delete_schedules,
    )
    monkeypatch.setattr(
        handlers,
        "handle_cancel_deployment",
        cancel_deployment,
    )
    monkeypatch.setattr(
        handlers,
        "_terminate_generation_workflows",
        terminate,
    )
    monkeypatch.setattr(handlers, "_control_payload", control_payload)

    result = await handlers.handle_reset_workflow(
        {
            "workflow_id": "wf",
            "expected_revision": 4,
            "idempotency_key": "reset-request-1",
        },
        None,
    )

    assert result == {
        "success": True,
        "idempotent": True,
        "state": "ready",
        "revision": 4,
        "generation": 1,
    }
    control_payload.assert_awaited_once_with(reset)
    service.transition.assert_not_awaited()
    close_local_admission.assert_not_called()
    signal_controller.assert_not_awaited()
    delete_schedules.assert_not_awaited()
    cancel_deployment.assert_not_awaited()
    terminate.assert_not_awaited()
    database.update_workflow_run_data_scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_retry_resumes_cleanup_before_marking_ready(monkeypatch):
    from services.deployment import runtime_state
    from services import status_broadcaster

    resetting = _control("resetting", 3)
    reset = _control("reset", 4)
    database = SimpleNamespace(
        get_latest_workflow_control=AsyncMock(return_value=resetting),
        update_workflow_run_data_scope=AsyncMock(return_value=True),
    )
    service = SimpleNamespace(
        database=database,
        transition=AsyncMock(return_value=reset),
    )
    terminate = AsyncMock(return_value=2)
    delete_schedules = AsyncMock(return_value=3)
    cancel_deployment = AsyncMock(
        return_value={
            "success": False,
            "message": "Workflow wf is not deployed",
        }
    )
    archive_nodes = AsyncMock(
        return_value={
            "archived_nodes": 2,
            "reset_nodes": ["agent-1"],
        }
    )
    broadcaster = SimpleNamespace(broadcast=AsyncMock())

    async def broadcast_control(control, **_kwargs):
        return {
            "state": "ready" if control.status == "reset" else control.status,
            "revision": control.revision,
        }

    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(
        handlers,
        "_broadcast_control",
        AsyncMock(side_effect=broadcast_control),
    )
    monkeypatch.setattr(handlers, "_signal_controller", AsyncMock())
    monkeypatch.setattr(
        handlers,
        "_terminate_generation_workflows",
        terminate,
    )
    monkeypatch.setattr(
        handlers,
        "_delete_cron_schedules",
        delete_schedules,
    )
    monkeypatch.setattr(
        handlers,
        "handle_cancel_deployment",
        cancel_deployment,
    )
    monkeypatch.setattr(
        runtime_state,
        "archive_and_reset_node_state",
        archive_nodes,
    )
    monkeypatch.setattr(
        status_broadcaster,
        "get_status_broadcaster",
        lambda: broadcaster,
    )

    result = await handlers.handle_reset_workflow(
        {"workflow_id": "wf"},
        None,
    )

    assert result["success"] is True
    assert result["state"] == "ready"
    assert result["revision"] == 4
    assert result["terminated_executions"] == 2
    assert result["deleted_schedules"] == 3
    assert result["local_cleanup_completed"] is False
    terminate.assert_awaited_once_with(resetting, strict=True)
    delete_schedules.assert_awaited_once_with("wf", strict=True)
    cancel_deployment.assert_awaited_once_with(
        {"workflow_id": "wf"},
        None,
    )
    database.update_workflow_run_data_scope.assert_awaited_once()
    archive_nodes.assert_awaited_once_with(
        resetting,
        database,
        broadcaster,
    )
    service.transition.assert_awaited_once()
    final_transition = service.transition.await_args
    assert final_transition.args == (resetting,)
    assert final_transition.kwargs["expected_revision"] == 3
    assert final_transition.kwargs["from_statuses"] == {"resetting"}
    assert final_transition.kwargs["status"] == "reset"


def test_generation_fanout_uses_temporal_root_tree():
    query = handlers._generation_visibility_query(_control("running", 2))

    assert query == (
        "(RootWorkflowId='workflow-control-wf-g1' OR "
        "EventWorkflowId='wf') "
        "AND ExecutionStatus='Running'"
    )


@pytest.mark.asyncio
async def test_generation_fanout_expands_tagged_standalone_root_trees():
    queries: list[str] = []
    controller = SimpleNamespace(
        id="workflow-control-wf-g1",
        run_id="controller-run",
        root_id="workflow-control-wf-g1",
    )
    cron_graph = SimpleNamespace(
        id="cron-graph",
        run_id="graph-run",
        root_id="cron-firing-root",
    )
    cron_agent = SimpleNamespace(
        id="cron-agent",
        run_id="agent-run",
        root_id="cron-firing-root",
    )

    def list_workflows(*, query):
        queries.append(query)

        async def iterate():
            values = (
                [controller, cron_graph]
                if query.startswith("(RootWorkflowId=")
                else [controller, cron_graph, cron_agent]
            )
            for value in values:
                yield value

        return iterate()

    client = SimpleNamespace(list_workflows=list_workflows)

    executions = await handlers._list_generation_workflows(
        client,
        _control("running", 2),
    )

    assert {
        execution.id
        for execution in executions
    } == {
        "workflow-control-wf-g1",
        "cron-graph",
        "cron-agent",
    }
    assert queries[0] == (
        "(RootWorkflowId='workflow-control-wf-g1' OR "
        "EventWorkflowId='wf') "
        "AND ExecutionStatus='Running'"
    )
    assert queries[1] == (
        "RootWorkflowId IN "
        "('cron-firing-root', 'workflow-control-wf-g1') "
        "AND ExecutionStatus='Running'"
    )


# ============================================================================
# A controller execution that no longer exists
#
# Temporal deletes closed executions once the namespace retention window
# passes (24h on the dev server). A generation's controller closes when that
# generation is reset, so every control row eventually names an execution
# Temporal has forgotten. Two things must hold: terminal generations must not
# be probed at all, and a *live* generation whose controller vanished must
# converge instead of waiting for something that will never answer.
# ============================================================================


class _Gone(Exception):
    """Shaped like the Temporal error, matched by _temporal_target_already_gone."""

    def __str__(self) -> str:
        return (
            'workflow execution not found for workflow ID '
            '"workflow-control-wf-g1" and run ID "run-1"'
        )


def _reconcile_service(**overrides):
    database = SimpleNamespace(**overrides.pop("database", {}))
    return SimpleNamespace(database=database, **overrides)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["reset", "failed"])
async def test_terminal_generation_is_never_probed(monkeypatch, terminal_status):
    """The reported symptom: a doomed RPC on every status read, forever."""
    probe = AsyncMock(return_value={"state": "running"})
    monkeypatch.setattr(handlers, "_query_controller_state", probe)

    control = _control(terminal_status, 6)
    result, controller_status = await handlers._reconcile_control(
        _reconcile_service(), control
    )

    probe.assert_not_awaited()
    assert result is control
    assert controller_status is None


@pytest.mark.asyncio
async def test_live_generation_with_missing_controller_pauses_by_default(monkeypatch):
    """WORKFLOW_CONTROL_MISSING_CONTROLLER=pause (default): a killed
    controller leaves the generation user-resumable — Resume rebuilds it —
    instead of forcing a Reset that archives conversation state."""
    paused = _control("paused", 7)
    transition = AsyncMock(return_value=paused)
    fail = AsyncMock()
    broadcast = AsyncMock(return_value={})

    monkeypatch.setattr(handlers, "_missing_controller_policy", lambda: "pause")
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(side_effect=handlers.ControllerExecutionMissing("gone")),
    )
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)

    control = _control("pausing", 6)
    result, controller_status = await handlers._reconcile_control(
        _reconcile_service(fail=fail, transition=transition), control
    )

    assert result is paused
    assert controller_status is None
    assert transition.await_args.kwargs["status"] == "paused"
    fail.assert_not_awaited()
    broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_already_paused_generation_with_missing_controller_is_left_alone(
    monkeypatch,
):
    """Paused is already the recoverable posture; Resume performs the rebuild."""
    transition = AsyncMock()
    fail = AsyncMock()
    monkeypatch.setattr(handlers, "_missing_controller_policy", lambda: "pause")
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(side_effect=handlers.ControllerExecutionMissing("gone")),
    )

    control = _control("paused", 6)
    result, _ = await handlers._reconcile_control(
        _reconcile_service(fail=fail, transition=transition), control
    )

    assert result is control
    transition.assert_not_awaited()
    fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_generation_with_missing_controller_fails_under_legacy_policy(
    monkeypatch,
):
    """WORKFLOW_CONTROL_MISSING_CONTROLLER=fail preserves the Reset-only
    behaviour; otherwise the row stays non-'reset' forever and blocks Start."""
    failed = _control("failed", 7, terminal_reason="controller_execution_missing")
    fail = AsyncMock(return_value=failed)
    broadcast = AsyncMock(return_value={})

    monkeypatch.setattr(handlers, "_missing_controller_policy", lambda: "fail")
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(side_effect=handlers.ControllerExecutionMissing("gone")),
    )
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)

    control = _control("pausing", 6)
    result, controller_status = await handlers._reconcile_control(
        _reconcile_service(fail=fail), control
    )

    assert result is failed
    assert controller_status is None
    assert fail.await_args.args[1] == "controller_execution_missing"
    broadcast.assert_awaited_once()


@pytest.mark.asyncio
async def test_starting_with_missing_controller_still_fails_under_pause_policy(
    monkeypatch,
):
    """Nothing durable runs yet mid-start; Reset + Start rebuilds cleanly."""
    failed = _control("failed", 7, terminal_reason="controller_execution_missing")
    fail = AsyncMock(return_value=failed)
    monkeypatch.setattr(handlers, "_missing_controller_policy", lambda: "pause")
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(side_effect=handlers.ControllerExecutionMissing("gone")),
    )
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(return_value={}))

    control = _control("starting", 6)
    result, _ = await handlers._reconcile_control(
        _reconcile_service(fail=fail), control
    )

    assert result is failed
    assert fail.await_args.args[1] == "controller_execution_missing"


@pytest.mark.asyncio
async def test_resetting_is_not_auto_failed(monkeypatch):
    """Reset owns its own retry; auto-failing here could race a live reset."""
    fail = AsyncMock()
    monkeypatch.setattr(
        handlers,
        "_query_controller_state",
        AsyncMock(side_effect=handlers.ControllerExecutionMissing("gone")),
    )

    control = _control("resetting", 5)
    result, _ = await handlers._reconcile_control(
        _reconcile_service(fail=fail), control
    )

    fail.assert_not_awaited()
    assert result is control


@pytest.mark.asyncio
async def test_a_transient_query_failure_does_not_fail_the_generation(monkeypatch):
    """'Unreachable' must stay recoverable -- only 'gone' is terminal."""
    fail = AsyncMock()
    monkeypatch.setattr(
        handlers, "_query_controller_state", AsyncMock(return_value=None)
    )

    control = _control("running", 4)
    result, controller_status = await handlers._reconcile_control(
        _reconcile_service(fail=fail), control
    )

    fail.assert_not_awaited()
    assert result is control
    assert controller_status is None


@pytest.mark.asyncio
async def test_missing_execution_is_classified_as_gone_not_as_an_outage():
    """Locks the string match against the real Temporal error text."""
    assert handlers._temporal_target_already_gone(_Gone()) is True
    assert handlers._temporal_target_already_gone(RuntimeError("deadline")) is False


@pytest.mark.asyncio
async def test_query_raises_missing_rather_than_returning_none(monkeypatch):
    """The distinction the whole fix rests on."""
    handle = SimpleNamespace(query=AsyncMock(side_effect=_Gone()))
    monkeypatch.setattr(handlers, "_controller_handle", lambda _c: handle)

    with pytest.raises(handlers.ControllerExecutionMissing):
        await handlers._query_controller_state(_control("running", 1))


@pytest.mark.asyncio
async def test_query_still_returns_none_for_a_real_outage(monkeypatch):
    handle = SimpleNamespace(query=AsyncMock(side_effect=RuntimeError("deadline")))
    monkeypatch.setattr(handlers, "_controller_handle", lambda _c: handle)

    assert await handlers._query_controller_state(_control("running", 1)) is None
