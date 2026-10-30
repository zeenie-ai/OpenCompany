"""Durable transition intent and compatibility of generation-level control."""
import asyncio
from copy import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from services.deployment import handlers
from services.deployment import execution_control


@pytest_asyncio.fixture(autouse=True)
async def drain_supervised_transitions():
    yield
    tasks = [*handlers._control_transition_tasks.values(), *handlers._boot_reconciliation_tasks.values()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    handlers._control_transition_tasks.clear()
    handlers._boot_reconciliation_tasks.clear()


def control(state="pausing"):
    return SimpleNamespace(id="control:1", workflow_id="canvas", generation=1, revision=3,
        root_execution_id="generation-root", controller_workflow_id="controller", controller_run_id="first",
        execution_id="generation-root", data_scope_id="generation-root", created_at=None, updated_at=None,
        terminal_reason=None, status=state, resource_manifest={"execution_control_version": 1})


@pytest.mark.asyncio
async def test_stop_timeout_keeps_intent_and_closed_producers(monkeypatch):
    from core.container import container
    row = control()
    runtime = SimpleNamespace(pause_deployment=lambda wid: closed.append(wid),
        update_trigger_pause_status=AsyncMock(), resume_deployment=AsyncMock())
    closed = []
    monkeypatch.setattr(container, "workflow_service", lambda: runtime)
    monkeypatch.setattr(container, "temporal_client", lambda: SimpleNamespace(client=object()))
    schedules = AsyncMock()
    monkeypatch.setattr(handlers, "_set_cron_pause", schedules)
    transition = AsyncMock(side_effect=TimeoutError("request timeout; tool still running"))
    monkeypatch.setattr(execution_control, "transition_generation", transition)
    service = SimpleNamespace(transition=AsyncMock(), database=SimpleNamespace(
        get_latest_workflow_control=AsyncMock(return_value=row)))
    with pytest.raises(TimeoutError):
        await handlers._finish_versioned_transition(service, row)
    assert row.status == "pausing"
    assert closed == ["canvas"]
    schedules.assert_awaited_once_with("canvas", paused=True, strict=True)
    runtime.resume_deployment.assert_not_awaited()
    service.transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_releases_continuations_before_producers_and_saves_timeout_window(monkeypatch):
    from core.container import container
    row = control("resuming")
    order = []
    async def release(*args, **kwargs):
        order.append("continuations")
        return {"controller_status": {}, "controlled_executions": 2}
    async def schedules(*args, **kwargs):
        order.append("schedules")
        return 1
    async def local(*args, **kwargs):
        order.append("local")
        return 0
    async def stable(target, **kwargs):
        assert kwargs["values"]["resource_manifest"]["last_resumed_at"]
        assert kwargs["values"]["resource_manifest"]["execution_control_version"] == 1
        order.append("running")
        target.status = kwargs["status"]
        return target
    monkeypatch.setattr(container, "workflow_service", lambda: SimpleNamespace(pause_deployment=lambda _: None,
        update_trigger_pause_status=AsyncMock(), resume_deployment=local))
    monkeypatch.setattr(container, "temporal_client", lambda: SimpleNamespace(client=object()))
    monkeypatch.setattr(execution_control, "transition_generation", release)
    monkeypatch.setattr(handlers, "_set_cron_pause", schedules)
    monkeypatch.setattr(handlers, "_clear_failure_streak", AsyncMock())
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(return_value={}))
    from services import status_broadcaster
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: AsyncMock())
    database = SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=row))
    result, _ = await handlers._finish_versioned_transition(SimpleNamespace(transition=stable, database=database), row)
    assert result.status == "running"
    assert order == ["continuations", "schedules", "local", "running"]


@pytest.mark.asyncio
async def test_missing_versioned_controller_cannot_rebuild(monkeypatch):
    start = AsyncMock()
    monkeypatch.setattr(handlers, "_start_controller", start)
    with pytest.raises(handlers.ControllerExecutionMissing):
        await handlers._rebuild_missing_controller(object(), control("paused"))
    start.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_generation_guard_precedes_reconciliation(monkeypatch):
    service = SimpleNamespace(database=SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=control("running"))))
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    reconcile = AsyncMock()
    monkeypatch.setattr(handlers, "_reconcile_control", reconcile)
    result = await handlers.handle_pause_workflow({"workflow_id": "canvas", "expected_root_execution_id": "other"}, None)
    assert result["error"] == "control_generation_conflict"
    reconcile.assert_not_awaited()


def lifecycle(monkeypatch, state="running"):
    """An immutable-snapshot control store and observable lifecycle effects."""
    from core.container import container
    from services import status_broadcaster, browser_reset
    from services.deployment import runtime_state

    row = control(state)
    row.revision = 2 if state == "running" else 3
    row.execution_id = row.data_scope_id = "generation-root"
    record = SimpleNamespace(row=row, broadcasts=[], effects=[])
    database = SimpleNamespace(
        get_latest_workflow_control=AsyncMock(side_effect=lambda _: copy(record.row)),
        update_workflow_run_data_scope=AsyncMock(return_value=True),
    )

    async def transition(target, *, expected_revision, from_statuses, status, values=None):
        if record.row.revision != expected_revision or record.row.status not in from_statuses:
            raise ValueError("control_revision_conflict")
        record.row = copy(record.row)
        record.row.revision += 1
        record.row.status = status
        for key, value in (values or {}).items():
            setattr(record.row, key, value)
        return copy(record.row)

    service = SimpleNamespace(database=database, transition=AsyncMock(side_effect=transition))

    async def payload(target, **kwargs):
        return {"state": "ready" if target.status == "reset" else target.status,
                "revision": target.revision, "generation": target.generation}

    async def broadcast(target, **kwargs):
        record.broadcasts.append((target.status, target.revision))
        return await payload(target)

    runtime = SimpleNamespace(
        pause_deployment=lambda _: record.effects.append("close_local"),
        update_trigger_pause_status=AsyncMock(return_value=1), resume_deployment=AsyncMock(return_value=0),
    )
    record.service, record.runtime = service, runtime
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(container, "workflow_service", lambda: runtime)
    monkeypatch.setattr(container, "temporal_client", lambda: SimpleNamespace(client=object()))
    monkeypatch.setattr(handlers, "_control_payload", payload)
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)
    monkeypatch.setattr(handlers, "_query_controller_state", AsyncMock(return_value={"state": state}))
    monkeypatch.setattr(handlers, "_set_cron_pause", AsyncMock(return_value=1))
    monkeypatch.setattr(handlers, "_delete_cron_schedules", AsyncMock(return_value=1))
    monkeypatch.setattr(handlers, "_signal_controller", AsyncMock())
    monkeypatch.setattr(handlers, "_terminate_generation_workflows", AsyncMock(return_value=3))
    monkeypatch.setattr("services.approvals.waiting.cancel_waits", AsyncMock(return_value=0))
    monkeypatch.setattr(handlers, "_reset_workspace_tasks", AsyncMock(return_value={"cancelled": 0}))
    monkeypatch.setattr(handlers, "handle_cancel_deployment", AsyncMock(return_value={"success": True}))
    monkeypatch.setattr(handlers, "_clear_failure_streak", AsyncMock())
    monkeypatch.setattr(browser_reset, "cleanup_generation_browser_tasks", AsyncMock(return_value=1))
    monkeypatch.setattr(runtime_state, "archive_and_reset_node_state",
                        AsyncMock(return_value={"archived_nodes": [], "reset_nodes": []}))
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: AsyncMock())
    return record


@pytest.mark.asyncio
async def test_blocked_stop_status_returns_and_reset_supersedes_poll(monkeypatch):
    from temporalio.client import WorkflowUpdateRPCTimeoutOrCancelledError
    record = lifecycle(monkeypatch)
    admitted, blocked = asyncio.Event(), asyncio.Event()

    async def stuck(*args, on_admission, **kwargs):
        await on_admission({"state": "paused"})
        admitted.set()
        try:
            await blocked.wait()
        except asyncio.CancelledError as exc:
            # The actual SDK wraps local Update polling cancellation this way.
            raise WorkflowUpdateRPCTimeoutOrCancelledError() from exc

    temporal = AsyncMock(side_effect=stuck)
    monkeypatch.setattr(execution_control, "transition_generation", temporal)
    stop = asyncio.create_task(handlers.handle_pause_workflow(
        {"workflow_id": "canvas", "expected_revision": 2}, None))
    await asyncio.wait_for(admitted.wait(), 1)
    for _ in range(3):
        snapshot = await asyncio.wait_for(handlers.handle_get_workflow_control_status({"workflow_id": "canvas"}, None), 0.2)
        assert snapshot["state"] == "pausing"
    assert not stop.done()
    assert temporal.await_count == 1
    handlers._query_controller_state.assert_awaited_once()

    reset = await asyncio.wait_for(handlers.handle_reset_workflow(
        {"workflow_id": "canvas", "expected_revision": 3,
         "expected_root_execution_id": "generation-root"}, None), 1)
    outcome = await asyncio.wait_for(stop, 1)
    assert reset["success"] and reset["state"] == "ready"
    assert outcome["success"] and outcome["superseded"]
    assert outcome["state"] in {"resetting", "ready"}
    assert record.row.status == "reset"
    assert record.broadcasts == [("pausing", 3), ("pausing", 3), ("resetting", 4), ("reset", 5)]
    record.runtime.resume_deployment.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_cancels_waiter_and_preserves_durable_stop(monkeypatch):
    record = lifecycle(monkeypatch)
    admitted, release = asyncio.Event(), asyncio.Event()

    async def stuck(*args, **kwargs):
        admitted.set()
        await release.wait()
        return {"controller_status": {}, "controlled_executions": 3}

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=stuck))
    waiter = asyncio.create_task(handlers.handle_pause_workflow({"workflow_id": "canvas", "expected_revision": 2}, None))
    await asyncio.wait_for(admitted.wait(), 1)
    transition_task = handlers._control_transition_tasks[(record.row.id, 3)]
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not transition_task.done()
    assert record.row.status == "pausing"
    record.runtime.resume_deployment.assert_not_awaited()
    release.set()
    finished, _ = await asyncio.wait_for(asyncio.shield(transition_task), 1)
    assert finished.status == "paused" and record.row.status == "paused"


@pytest.mark.asyncio
async def test_a_stop_still_draining_answers_pending_and_the_drain_goes_on(monkeypatch):
    # 1b.15: the socket request answers within the bound; the task carries on.
    record = lifecycle(monkeypatch)
    admitted, release = asyncio.Event(), asyncio.Event()

    async def stuck(*args, **kwargs):
        admitted.set()
        await release.wait()
        return {"controller_status": {}, "controlled_executions": 3}

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=stuck))
    monkeypatch.setattr(handlers, "_TRANSITION_ANSWER_SECONDS", 0.05)
    outcome = await asyncio.wait_for(
        handlers.handle_pause_workflow({"workflow_id": "canvas", "expected_revision": 2}, None), 5)
    assert outcome["success"] and outcome["transition_pending"] and not outcome["superseded"]
    assert outcome["state"] == "pausing"
    transition_task = handlers._control_transition_tasks[(record.row.id, 3)]
    assert admitted.is_set() and not transition_task.done()
    release.set()
    finished, _ = await asyncio.wait_for(asyncio.shield(transition_task), 1)
    assert finished.status == "paused" and record.row.status == "paused"


@pytest.mark.asyncio
async def test_a_timeout_raised_by_the_transition_is_not_read_as_pending(monkeypatch):
    lifecycle(monkeypatch)
    monkeypatch.setattr(execution_control, "transition_generation",
                        AsyncMock(side_effect=TimeoutError("request timeout; tool still running")))
    outcome = await handlers.handle_pause_workflow({"workflow_id": "canvas", "expected_revision": 2}, None)
    assert outcome == {"success": False, "error": "request timeout; tool still running"}


@pytest.mark.asyncio
@pytest.mark.parametrize("intent", ["pausing", "resuming"])
async def test_late_transition_after_reset_does_not_broadcast_or_reopen(monkeypatch, intent):
    record = lifecycle(monkeypatch, intent)
    started, release = asyncio.Event(), asyncio.Event()

    async def late(*args, on_admission, **kwargs):
        started.set()
        await release.wait()
        # An admission response can arrive after another request wins Reset.
        await on_admission({"state": "paused" if intent == "pausing" else "running"})
        return {"controller_status": {}, "controlled_executions": 3}

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=late))
    transition_task = asyncio.create_task(handlers._finish_versioned_transition(record.service, copy(record.row)))
    await asyncio.wait_for(started.wait(), 1)
    record.row = copy(record.row)
    record.row.status, record.row.revision = "reset", 5
    release.set()
    finished, status = await asyncio.wait_for(transition_task, 1)
    assert finished.status == "reset" and status is None
    assert record.broadcasts == []
    record.runtime.resume_deployment.assert_not_awaited()
    assert all(call.kwargs["paused"] for call in handlers._set_cron_pause.await_args_list)


@pytest.mark.asyncio
async def test_transition_failure_after_reset_reports_current_state(monkeypatch):
    record = lifecycle(monkeypatch, "pausing")

    async def failed(*args, **kwargs):
        record.row = copy(record.row)
        record.row.status, record.row.revision = "resetting", 4
        raise RuntimeError("workflow execution was terminated by Reset")

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=failed))
    finished, status = await handlers._finish_versioned_transition(record.service, copy(record.row))
    assert finished.status == "resetting" and status is None
    assert record.broadcasts == []
    record.service.transition.assert_not_awaited()


@pytest.mark.asyncio
async def test_browser_cleanup_failure_keeps_resetting_and_scope_unarchived(monkeypatch):
    from services import browser_reset
    record = lifecycle(monkeypatch)
    cleanup = AsyncMock(side_effect=RuntimeError("browser_reset_owner_cleanup_unacknowledged"))
    monkeypatch.setattr(browser_reset, "cleanup_generation_browser_tasks", cleanup)
    result = await handlers.handle_reset_workflow({"workflow_id": "canvas", "expected_revision": 2}, None)
    assert result == {"success": False, "error": "browser_reset_owner_cleanup_unacknowledged"}
    assert record.row.status == "resetting"
    handlers._terminate_generation_workflows.assert_awaited_once()
    cleanup.assert_awaited_once()
    handlers._reset_workspace_tasks.assert_not_awaited()
    record.service.database.update_workflow_run_data_scope.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["running", "reset"])
async def test_stale_reset_generation_cannot_cleanup_current_generation(monkeypatch, state):
    record = lifecycle(monkeypatch, state)
    result = await handlers.handle_reset_workflow(
        {"workflow_id": "canvas", "expected_root_execution_id": "older-generation"}, None)
    assert result["error"] == "control_generation_conflict"
    record.service.transition.assert_not_awaited()
    handlers._reset_workspace_tasks.assert_not_awaited()
    handlers._terminate_generation_workflows.assert_not_awaited()


@pytest.mark.asyncio
async def test_generation_scoped_reset_wins_if_stop_completed_in_flight(monkeypatch):
    record = lifecycle(monkeypatch, "paused")
    record.row.revision = 4  # Stop completed after the client observed pausing:3.
    result = await handlers.handle_reset_workflow(
        {"workflow_id": "canvas", "expected_revision": 3,
         "expected_root_execution_id": "generation-root"}, None)
    assert result["success"] and result["state"] == "ready"
    assert record.service.transition.await_args_list[0].kwargs["expected_revision"] == 4


@pytest.mark.asyncio
async def test_revision_only_reset_keeps_legacy_conflict_contract(monkeypatch):
    record = lifecycle(monkeypatch, "paused")
    record.row.revision = 4
    result = await handlers.handle_reset_workflow({"workflow_id": "canvas", "expected_revision": 3}, None)
    assert result == {"success": False, "error": "control_revision_conflict"}
    handlers._terminate_generation_workflows.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_rpc_timeout_returns_pending_without_undoing_intent(monkeypatch):
    from temporalio.client import WorkflowUpdateRPCTimeoutOrCancelledError
    record = lifecycle(monkeypatch, "pausing")
    monkeypatch.setattr(execution_control, "transition_generation",
                        AsyncMock(side_effect=WorkflowUpdateRPCTimeoutOrCancelledError()))
    result = await handlers._await_versioned_transition(record.service, copy(record.row))
    assert result["success"] and result["transition_pending"]
    assert result["state"] == "pausing" and not result["superseded"]
    record.service.transition.assert_not_awaited()
    record.runtime.resume_deployment.assert_not_awaited()


@pytest.mark.asyncio
async def test_sdk_local_cancellation_is_not_recast_as_control_failure(monkeypatch):
    from temporalio.client import WorkflowUpdateRPCTimeoutOrCancelledError
    record = lifecycle(monkeypatch, "pausing")
    cancelled = WorkflowUpdateRPCTimeoutOrCancelledError()
    cancelled.__cause__ = asyncio.CancelledError()
    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=cancelled))
    with pytest.raises(asyncio.CancelledError):
        await handlers._finish_versioned_transition(record.service, copy(record.row))
    assert record.row.status == "pausing"
    record.service.transition.assert_not_awaited()
    record.runtime.resume_deployment.assert_not_awaited()


def temporal_tree(state="paused"):
    from temporalio.client import WorkflowExecutionStatus

    def description(first):
        return SimpleNamespace(status=WorkflowExecutionStatus.RUNNING, raw_description=SimpleNamespace(
            workflow_execution_info=SimpleNamespace(first_run_id=first), pending_children=[]))

    status = {"participant_revision": 3, "participant_state": state, "state": state,
              "revision": 3, "membership_epoch": 0, "producers_held": True, "checkpoint": True,
              "live_roots": {"root": {"workflow_id": "root", "first_execution_run_id": "root-first"}}}
    controller = SimpleNamespace(describe=AsyncMock(return_value=description("first")),
        query=AsyncMock(return_value=status), execute_update=AsyncMock(return_value=status))
    root = SimpleNamespace(describe=AsyncMock(return_value=description("root-first")),
        execute_update=AsyncMock(return_value=status))
    client = SimpleNamespace(get_workflow_handle=lambda wid: {"controller": controller, "root": root}[wid])
    return client, controller, root, status


@pytest.mark.asyncio
async def test_generation_resume_supersession_prevents_temporal_release():
    client, controller, root, _ = temporal_tree("running")
    current = True

    async def ensure_current():
        if not current:
            raise execution_control.GenerationTransitionSuperseded()

    async def reset_wins(status):
        nonlocal current
        current = False

    with pytest.raises(execution_control.GenerationTransitionSuperseded):
        await execution_control.transition_generation(client, control("resuming"), paused=False,
            on_admission=reset_wins, ensure_current=ensure_current)
    assert len(controller.execute_update.await_args_list) == 1
    assert len(root.execute_update.await_args_list) == 1
    assert all(call.args[1]["producers_held"] for call in
               [*controller.execute_update.await_args_list, *root.execute_update.await_args_list])


@pytest.mark.asyncio
async def test_checkpoint_failure_retires_sibling_rpc_pollers():
    client, controller, root, status = temporal_tree()
    polling, retired = asyncio.Event(), asyncio.Event()

    async def root_update(name, payload, **kwargs):
        if name == "wait_for_checkpoint":
            polling.set()
            try:
                await asyncio.Event().wait()
            finally:
                retired.set()
        return status

    async def controller_update(name, payload, **kwargs):
        if name == "wait_for_checkpoint":
            await polling.wait()
            raise RuntimeError("checkpoint transport failure")
        return status

    root.execute_update.side_effect = root_update
    controller.execute_update.side_effect = controller_update
    with pytest.raises(RuntimeError, match="checkpoint transport failure"):
        await asyncio.wait_for(execution_control.transition_generation(client, control(), paused=True), 1)
    assert retired.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("intent, handler", [
    ("pausing", handlers.handle_pause_workflow), ("resuming", handlers.handle_resume_workflow),
])
async def test_explicit_retry_joins_existing_transition(monkeypatch, intent, handler):
    record = lifecycle(monkeypatch, intent)
    started, release = asyncio.Event(), asyncio.Event()

    async def pending(*args, **kwargs):
        started.set()
        await release.wait()
        return {"controller_status": {}, "controlled_executions": 3}

    temporal = AsyncMock(side_effect=pending)
    monkeypatch.setattr(execution_control, "transition_generation", temporal)
    background = handlers._schedule_versioned_transition(record.service, copy(record.row))
    await asyncio.wait_for(started.wait(), 1)
    retry = asyncio.create_task(handler({"workflow_id": "canvas", "expected_revision": 3}, None))
    snapshot = await asyncio.wait_for(handlers.handle_get_workflow_control_status({"workflow_id": "canvas"}, None), 0.2)
    assert snapshot["state"] == intent
    await asyncio.sleep(0)
    assert not retry.done()
    release.set()
    result = await asyncio.wait_for(retry, 1)
    assert result["success"] and not result["transition_pending"]
    assert result["state"] == ("paused" if intent == "pausing" else "running")
    await background
    assert temporal.await_count == 1


@pytest.mark.asyncio
async def test_unavailable_generation_observation_cannot_hold_reset_lock(monkeypatch):
    real_payload, real_broadcast = handlers._control_payload, handlers._broadcast_control
    record = lifecycle(monkeypatch)
    record.runtime.get_deployment_status = lambda _: {"active_runs": 2}
    monkeypatch.setattr(handlers, "_control_payload", real_payload)
    monkeypatch.setattr(handlers, "_broadcast_control", real_broadcast)
    monkeypatch.setattr(handlers, "_workspace_runtime_status", AsyncMock(return_value={}))
    monkeypatch.setattr(handlers, "_CONTROL_OBSERVATION_TIMEOUT_SECONDS", 0.02)
    observing, retired = asyncio.Event(), asyncio.Event()

    async def count(*args):
        observing.set()
        try:
            await asyncio.Event().wait()
        finally:
            retired.set()

    async def pending(*args, on_admission, **kwargs):
        await on_admission({"state": "paused", "revision": 3})
        await asyncio.Event().wait()

    monkeypatch.setattr(execution_control, "count_generation_actions", count)
    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=pending))
    stop = asyncio.create_task(handlers.handle_pause_workflow({"workflow_id": "canvas", "expected_revision": 2}, None))
    await asyncio.wait_for(observing.wait(), 1)
    reset = await asyncio.wait_for(handlers.handle_reset_workflow(
        {"workflow_id": "canvas", "expected_revision": 3, "expected_root_execution_id": "generation-root"}, None), 0.5)
    assert reset["success"] and reset["state"] == "ready"
    assert retired.is_set()
    assert (await asyncio.wait_for(stop, 0.5))["superseded"]
    # Runtime fallback is explicitly observational. Never report an invented
    # zero generation count or use it as evidence of a checkpoint.
    snapshot = await real_payload(record.row, controller_status={"state": "reset"})
    assert "generation_in_flight_count" not in snapshot
    assert snapshot["in_flight_count"] == 2


@pytest.mark.asyncio
async def test_workspace_observation_timeout_omits_unavailable_counts(monkeypatch):
    from core.container import container
    from services import node_invocations
    record = lifecycle(monkeypatch, "pausing")
    record.runtime.get_deployment_status = lambda _: {"active_runs": 2}
    monkeypatch.setattr(container, "database", lambda: record.service.database)
    monkeypatch.setattr(node_invocations, "workspace_task_nodes", AsyncMock(return_value=(None, ["browser"])))
    monkeypatch.setattr(handlers, "_CONTROL_OBSERVATION_TIMEOUT_SECONDS", 0.02)
    retired = asyncio.Event()

    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            retired.set()

    monkeypatch.setattr(node_invocations, "controller_status", stalled)
    snapshot = await asyncio.wait_for(handlers._with_runtime_counts({"state": "pausing"}, "canvas"), 0.5)
    assert retired.is_set()
    assert snapshot["workspace_available"] is False
    assert "workspace_active_count" not in snapshot
    assert "generation_in_flight_count" not in snapshot
    assert snapshot["in_flight_count"] == 2


def boot_plane(monkeypatch, record, *, clean=True, policy="pause", additional=None):
    from core.container import container

    record.service.database.list_active_workflow_controls = AsyncMock(return_value=[copy(record.row), *(additional or [])])
    monkeypatch.setattr(container, "database", lambda: record.service.database)
    monkeypatch.setattr(handlers, "WorkflowControlService", lambda database: record.service)
    monkeypatch.setattr(handlers, "_consume_shutdown_state", AsyncMock(return_value=clean))
    monkeypatch.setattr(handlers, "_crash_recovery_policy", lambda: policy)
    monkeypatch.setattr(handlers, "_BOOT_RETRY_SECONDS", 0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("intent,stable", [("pausing", "paused"), ("resuming", "running")])
async def test_boot_pending_transition_restores_collectors_without_blocking_other_rows(monkeypatch, intent, stable):
    record = lifecycle(monkeypatch, intent)
    healthy = control("running")
    healthy.id, healthy.workflow_id = "healthy-control", "healthy"
    boot_plane(monkeypatch, record, additional=[healthy])
    started, release = asyncio.Event(), asyncio.Event()
    collectors = {}

    async def transition(*args, **kwargs):
        started.set()
        await release.wait()
        return {"controller_status": {}, "controlled_executions": 3}

    async def rearm(current):
        collectors[current.workflow_id] = current.status

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=transition))
    monkeypatch.setattr(handlers, "_rearm_generation", AsyncMock(side_effect=rearm))
    assert await asyncio.wait_for(handlers.reconcile_active_controls_on_boot(), 0.2) == 2
    await asyncio.wait_for(started.wait(), 0.2)
    assert collectors == {"healthy": "running"}
    boot_task = handlers._boot_reconciliation_tasks[(record.row.id, record.row.generation)]
    release.set()
    await asyncio.wait_for(asyncio.shield(boot_task), 0.5)
    assert collectors == {"healthy": "running", "canvas": stable}
    assert record.row.status == stable


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,stable", [("pause", "paused"), ("resume", "running")])
async def test_boot_interrupted_resume_obeys_unclean_shutdown_policy(monkeypatch, policy, stable):
    record = lifecycle(monkeypatch, "resuming")
    boot_plane(monkeypatch, record, clean=False, policy=policy)
    started, release = asyncio.Event(), asyncio.Event()
    collectors = []

    async def transition(*args, **kwargs):
        started.set()
        await release.wait()
        return {"controller_status": {}, "controlled_executions": 3}

    async def rearm(current):
        collectors.append(current.status)

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=transition))
    monkeypatch.setattr(handlers, "_rearm_generation", AsyncMock(side_effect=rearm))
    assert await handlers.reconcile_active_controls_on_boot() == 1
    await asyncio.wait_for(started.wait(), 0.2)
    boot_task = handlers._boot_reconciliation_tasks[(record.row.id, record.row.generation)]
    release.set()
    await asyncio.wait_for(asyncio.shield(boot_task), 0.5)
    assert collectors == [stable]
    assert record.row.status == stable
    if policy == "pause":
        assert record.row.pause_reason == "recovery"


@pytest.mark.asyncio
@pytest.mark.parametrize("new_generation", [False, True])
async def test_boot_continuation_cannot_rearm_reset_or_a_later_start(monkeypatch, new_generation):
    record = lifecycle(monkeypatch, "pausing")
    boot_plane(monkeypatch, record)
    started, release = asyncio.Event(), asyncio.Event()

    async def transition(*args, **kwargs):
        started.set()
        await release.wait()
        return {"controller_status": {}, "controlled_executions": 3}

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=transition))
    rearm = AsyncMock()
    monkeypatch.setattr(handlers, "_rearm_generation", rearm)
    await handlers.reconcile_active_controls_on_boot()
    await asyncio.wait_for(started.wait(), 0.2)
    boot_task = handlers._boot_reconciliation_tasks[(record.row.id, record.row.generation)]
    record.row = copy(record.row)
    record.row.revision = 5
    if new_generation:
        record.row.id, record.row.generation, record.row.root_execution_id = "next-control", 2, "next-root"
        record.row.status = "running"
    else:
        record.row.status = "reset"
    release.set()
    await asyncio.wait_for(asyncio.shield(boot_task), 0.5)
    rearm.assert_not_awaited()
    assert record.broadcasts == []


@pytest.mark.asyncio
async def test_boot_rearm_failure_keeps_admission_closed_and_retries_collectors(monkeypatch):
    record = lifecycle(monkeypatch, "pausing")
    boot_plane(monkeypatch, record)
    started, release = asyncio.Event(), asyncio.Event()
    effects = []

    async def transition(*args, **kwargs):
        started.set()
        await release.wait()
        return {"controller_status": {}, "controlled_executions": 3}

    async def rearm(current):
        effects.append("rearm")
        if len(effects) == 1:
            raise RuntimeError("collector startup failed")
        effects.append("restored")

    monkeypatch.setattr(execution_control, "transition_generation", AsyncMock(side_effect=transition))
    monkeypatch.setattr(handlers, "_rearm_generation", AsyncMock(side_effect=rearm))
    await handlers.reconcile_active_controls_on_boot()
    await asyncio.wait_for(started.wait(), 0.2)
    boot_task = handlers._boot_reconciliation_tasks[(record.row.id, record.row.generation)]
    release.set()
    await asyncio.wait_for(asyncio.shield(boot_task), 0.5)
    assert effects == ["rearm", "rearm", "restored"]
    assert record.row.status == "paused"
    record.runtime.resume_deployment.assert_not_awaited()
    handlers.handle_cancel_deployment.assert_not_awaited()


@pytest.mark.asyncio
async def test_boot_rearm_wait_is_bounded_and_reset_can_take_over(monkeypatch):
    record = lifecycle(monkeypatch, "pausing")
    boot_plane(monkeypatch, record)
    monkeypatch.setattr(handlers, "_BOOT_REARM_TIMEOUT_SECONDS", 0.02)
    restoring, retired = asyncio.Event(), asyncio.Event()

    async def rearm(current):
        restoring.set()
        try:
            await asyncio.Event().wait()
        finally:
            retired.set()

    monkeypatch.setattr(execution_control, "transition_generation",
                        AsyncMock(return_value={"controller_status": {}, "controlled_executions": 3}))
    monkeypatch.setattr(handlers, "_rearm_generation", rearm)
    await handlers.reconcile_active_controls_on_boot()
    await asyncio.wait_for(restoring.wait(), 0.2)
    boot_task = handlers._boot_reconciliation_tasks[(record.row.id, record.row.generation)]
    reset = await asyncio.wait_for(handlers.handle_reset_workflow({
        "workflow_id": "canvas", "expected_revision": 4,
    }, None), 0.5)
    assert reset["success"] and record.row.status == "reset"
    assert retired.is_set()
    await asyncio.gather(boot_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_generation_scoped_reset_preempts_a_blocked_boot_setup_immediately(monkeypatch):
    record = lifecycle(monkeypatch, "pausing")
    boot_plane(monkeypatch, record)
    restoring, retired = asyncio.Event(), asyncio.Event()

    async def rearm(current):
        restoring.set()
        try:
            await asyncio.Event().wait()
        finally:
            retired.set()

    monkeypatch.setattr(execution_control, "transition_generation",
                        AsyncMock(return_value={"controller_status": {}, "controlled_executions": 3}))
    monkeypatch.setattr(handlers, "_rearm_generation", rearm)
    await handlers.reconcile_active_controls_on_boot()
    await asyncio.wait_for(restoring.wait(), 0.2)
    boot_task = handlers._boot_reconciliation_tasks[(record.row.id, record.row.generation)]
    reset = await asyncio.wait_for(handlers.handle_reset_workflow({
        "workflow_id": "canvas", "expected_root_execution_id": "generation-root",
    }, None), 0.2)
    assert reset["success"] and record.row.status == "reset"
    assert retired.is_set()
    await asyncio.gather(boot_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_boot_retry_awaits_existing_setup_receipt_instead_of_claiming_it_restored(monkeypatch):
    record = lifecycle(monkeypatch, "pausing")
    record.row.graph_snapshot = {"nodes": [{"id": "trigger", "type": "webhookTrigger"}], "edges": []}
    boot_plane(monkeypatch, record)
    monkeypatch.setattr(handlers, "_BOOT_REARM_TIMEOUT_SECONDS", 0.02)
    deployed = False
    setup_started, release_setup, gated = asyncio.Event(), asyncio.Event(), asyncio.Event()
    previous_setup = handlers._deployment_tasks.pop("canvas", None)

    async def setup():
        setup_started.set()
        await release_setup.wait()
        return {"success": True}

    async def deploy(*args, **kwargs):
        nonlocal deployed
        deployed = True
        handlers._deployment_tasks["canvas"] = asyncio.create_task(setup())
        return {"success": True}

    def pause(_workflow_id):
        if deployed:
            gated.set()

    record.runtime.is_workflow_deployed = lambda _: deployed
    record.runtime.pause_deployment = pause
    monkeypatch.setattr(handlers, "handle_deploy_workflow", AsyncMock(side_effect=deploy))
    monkeypatch.setattr(execution_control, "transition_generation",
                        AsyncMock(return_value={"controller_status": {}, "controlled_executions": 3}))
    try:
        await handlers.reconcile_active_controls_on_boot()
        boot_task = handlers._boot_reconciliation_tasks[(record.row.id, record.row.generation)]
        await asyncio.wait_for(setup_started.wait(), 0.2)
        await asyncio.wait_for(gated.wait(), 0.2)
        setup_task = handlers._deployment_tasks["canvas"]
        assert not setup_task.done() and not boot_task.done()
        handlers.handle_cancel_deployment.assert_not_awaited()
        release_setup.set()
        await asyncio.wait_for(asyncio.shield(boot_task), 0.5)
        handlers.handle_deploy_workflow.assert_awaited_once()
        assert setup_task.done() and record.row.status == "paused"
        record.runtime.update_trigger_pause_status.assert_awaited_with("canvas", paused=True)
    finally:
        setup_task = handlers._deployment_tasks.pop("canvas", None)
        if setup_task is not None and not setup_task.done():
            setup_task.cancel()
            await asyncio.gather(setup_task, return_exceptions=True)
        if previous_setup is not None:
            handlers._deployment_tasks["canvas"] = previous_setup
