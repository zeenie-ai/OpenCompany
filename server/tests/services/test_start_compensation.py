"""A Start that fails undoes its steps, and one whose socket drops still finishes (1b.22)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.deployment import handlers

START = {
    "workflow_id": "wf",
    "nodes": [{"id": "start-1", "type": "start", "data": {}}],
    "edges": [],
    "expected_revision": 0,
    "idempotency_key": "start-1",
}
UNDO = ["undo:cancel_local", "undo:reset", "undo:sweep", "undo:fail", "broadcast:failed"]


@pytest.fixture(autouse=True)
async def drain_starts():
    yield
    tasks = list(handlers._start_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    handlers._start_tasks.clear()


def _control(status, revision=0, *, generation=1):
    return SimpleNamespace(
        id=f"workflow-control:wf:{generation}",
        workflow_id="wf",
        generation=generation,
        execution_id="execution-1",
        root_execution_id="execution-1",
        data_scope_id="execution-1",
        controller_workflow_id="workflow-control-wf-g1",
        controller_run_id=None,
        status=status,
        revision=revision,
        created_at=None,
        updated_at=None,
        terminal_reason=None,
    )


def start_plane(monkeypatch, *, forward_error=None, setup=None):
    """Patch every step of a Start and of its undo, and record the order they run in."""
    order = []
    starting, reread = _control("starting"), _control("starting", 1)

    def recorded(name):
        async def call(*_args, **_kwargs):
            order.append(name)

        return call

    async def transition(_control_value, **kwargs):
        if kwargs["status"] == "running" and forward_error == "commit":
            raise ValueError("control_revision_conflict")
        return _control(kwargs["status"], 1 if kwargs["status"] == "starting" else 2)

    async def fail(row, reason):
        order.append("undo:fail")
        return SimpleNamespace(**{**vars(row), "status": "failed", "terminal_reason": reason})

    async def broadcast(control, **_kwargs):
        order.append(f"broadcast:{control.status}")
        return {"state": control.status, "revision": control.revision}

    service = SimpleNamespace(
        database=SimpleNamespace(
            get_workflow_control_by_idempotency_key=AsyncMock(return_value=None),
            # The Start reads the latest row once (there is none yet); the undo
            # reads it before the cancel, the sweep and the fail.
            get_latest_workflow_control=AsyncMock(side_effect=[None, reread, reread, reread]),
            update_workflow_run_data_scope=AsyncMock(return_value=True),
        ),
        begin_generation=AsyncMock(return_value=(starting, True)),
        transition=AsyncMock(side_effect=transition),
        fail=AsyncMock(side_effect=fail),
    )
    controller = (
        AsyncMock(side_effect=RuntimeError("controller unavailable"))
        if forward_error == "controller"
        else AsyncMock(return_value="controller-run-1")
    )
    deploy = {"success": False, "error": "deploy refused"} if forward_error == "deploy" else {"success": True}
    ready = {"success": False, "error": "setup refused"} if forward_error == "setup" else {"success": True}
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)
    monkeypatch.setattr(handlers, "_start_controller", controller)
    monkeypatch.setattr(handlers, "handle_deploy_workflow", AsyncMock(return_value=deploy))
    monkeypatch.setattr(handlers, "_await_deployment_setup", setup or AsyncMock(return_value=ready))
    monkeypatch.setattr(handlers, "_query_controller_state", AsyncMock(return_value={"state": "running", "revision": 0}))
    monkeypatch.setattr(handlers, "handle_cancel_deployment", recorded("undo:cancel_local"))
    monkeypatch.setattr(handlers, "_signal_controller", recorded("undo:reset"))
    monkeypatch.setattr(handlers, "_terminate_generation_workflows", recorded("undo:sweep"))
    return SimpleNamespace(order=order, service=service, reread=reread)


@pytest.mark.parametrize(
    "forward_error, error",
    [
        ("controller", "controller unavailable"),
        ("deploy", "deploy refused"),
        ("setup", "setup refused"),
        ("commit", "control_revision_conflict"),
    ],
)
async def test_a_failed_start_undoes_its_steps_last_in_first_out(monkeypatch, forward_error, error):
    plane = start_plane(monkeypatch, forward_error=forward_error)
    assert await handlers.handle_start_workflow(dict(START), None) == {"success": False, "error": error}
    assert plane.order[-5:] == UNDO
    plane.service.fail.assert_awaited_once_with(plane.reread, error)


@pytest.mark.parametrize("broken", ["cancel_local_deployment", "reset_controller", "sweep_generation", "fail_row", "announce"])
async def test_a_failing_undo_step_never_skips_the_next(monkeypatch, broken):
    calls = []

    def step(name):
        async def call(*_args, **_kwargs):
            calls.append(name)
            if name == broken:
                raise RuntimeError("Temporal is down")

        return call

    monkeypatch.setattr(handlers, "handle_cancel_deployment", step("cancel_local_deployment"))
    monkeypatch.setattr(handlers, "_signal_controller", step("reset_controller"))
    monkeypatch.setattr(handlers, "_terminate_generation_workflows", step("sweep_generation"))
    monkeypatch.setattr(handlers, "_broadcast_control", step("announce"))
    logger = MagicMock()
    monkeypatch.setattr(handlers, "logger", logger)
    control = _control("starting")
    service = SimpleNamespace(database=SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=control)), fail=step("fail_row"))
    await handlers._undo_failed_start(service, control, RuntimeError("setup refused"))
    assert calls == ["cancel_local_deployment", "reset_controller", "sweep_generation", "fail_row", "announce"]
    (warning,) = logger.warning.call_args_list
    assert warning.kwargs["step"] == broken


@pytest.mark.parametrize(
    "latest",
    [
        _control("running", 3),
        _control("resetting", 2),
        _control("reset", 4),
        _control("failed", 2),
        _control("starting", generation=2),  # a newer generation
    ],
)
async def test_the_undo_fails_only_this_generations_starting_row(monkeypatch, latest):
    steps = {name: AsyncMock() for name in ("handle_cancel_deployment", "_signal_controller", "_terminate_generation_workflows")}
    for name, step in steps.items():
        monkeypatch.setattr(handlers, name, step)
    broadcast = AsyncMock()
    monkeypatch.setattr(handlers, "_broadcast_control", broadcast)
    control = _control("starting")
    service = SimpleNamespace(database=SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=latest)), fail=AsyncMock())
    await handlers._undo_failed_start(service, control, RuntimeError("setup refused"))
    service.fail.assert_not_awaited()
    assert broadcast.await_args.args[0] is latest
    # A Reset took the row, and a newer Start may follow it: the workflow-wide
    # cancel and sweep leave it alone, while this generation's own controller
    # is still told to reset.
    steps["handle_cancel_deployment"].assert_not_awaited()
    steps["_terminate_generation_workflows"].assert_not_awaited()
    steps["_signal_controller"].assert_awaited_once_with(control, "reset")


def parked_setup(result):
    parked, release = asyncio.Event(), asyncio.Event()

    async def setup(_workflow_id):
        parked.set()
        await release.wait()
        return result

    return SimpleNamespace(setup=setup, parked=parked, release=release)


async def drop_the_socket(gate):
    """Start, wait until it parks in the setup wait, then cancel its handler as a dropped socket does."""
    request = asyncio.create_task(handlers.handle_start_workflow(dict(START), None))
    await asyncio.wait_for(gate.parked.wait(), 3)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    (start,) = handlers._start_tasks
    assert not start.done()
    return start


async def test_a_start_whose_socket_drops_still_finishes(monkeypatch):
    gate = parked_setup({"success": True})
    plane = start_plane(monkeypatch, setup=gate.setup)
    start = await drop_the_socket(gate)
    gate.release.set()
    assert (await start)["state"] == "running"
    await asyncio.sleep(0)
    assert not handlers._start_tasks
    plane.service.fail.assert_not_awaited()


async def test_a_start_whose_socket_drops_and_then_fails_undoes_itself(monkeypatch):
    gate = parked_setup({"success": False, "error": "setup refused"})
    plane = start_plane(monkeypatch, setup=gate.setup)
    logger = MagicMock()
    monkeypatch.setattr(handlers, "logger", logger)
    start = await drop_the_socket(gate)
    gate.release.set()
    with pytest.raises(RuntimeError, match="setup refused"):
        await start
    await asyncio.sleep(0)
    assert plane.order[-5:] == UNDO
    # Nobody awaits the Start any more, so its own log line is the record.
    assert any(call.args[0] == "Start ended with an error" for call in logger.warning.call_args_list)


async def test_a_clean_shutdown_cancels_a_start_still_running(monkeypatch):
    gate = parked_setup({"success": True})
    plane = start_plane(monkeypatch, setup=gate.setup)
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: SimpleNamespace(set_cache_entry=AsyncMock())))
    start = await drop_the_socket(gate)
    await handlers.mark_clean_shutdown()
    assert start.cancelled()
    assert not handlers._start_tasks
    # A cancel is no failure: the row stays starting, and the next boot converges it.
    plane.service.fail.assert_not_awaited()
