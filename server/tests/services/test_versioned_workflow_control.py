"""Durable transition intent and compatibility of generation-level control."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.deployment import handlers
from services.deployment import execution_control


def control(state="pausing"):
    return SimpleNamespace(id="control:1", workflow_id="canvas", generation=1, revision=3,
        root_execution_id="generation-root", controller_workflow_id="controller", controller_run_id="first",
        status=state, resource_manifest={"execution_control_version": 1})


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
    service = SimpleNamespace(transition=AsyncMock())
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
    result, _ = await handlers._finish_versioned_transition(SimpleNamespace(transition=stable, database=object()), row)
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
