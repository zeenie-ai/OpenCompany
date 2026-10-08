"""Versioned control admission, result checkpoints and controller membership."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.converter import DefaultPayloadConverter

from services.temporal import execution_control as control_module
from services.temporal.execution_control import ExecutionControl
from services.temporal.execution_control_activities import ExecutionControlActivities
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


async def _wait(predicate):
    while not predicate():
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def controller_runtime(monkeypatch):
    monkeypatch.setattr(control_module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(WorkflowControlWorkflow, "_history_pressure", lambda _: False)


@pytest.fixture
def control(monkeypatch):
    monkeypatch.setattr(control_module.workflow, "wait_condition", _wait)
    monkeypatch.setattr(control_module.workflow, "info", lambda: SimpleNamespace(workflow_id="root", first_execution_run_id="first"))
    instance = ExecutionControl()
    instance.bind({"execution_control_version": 1, "generation": 7, "controller_workflow_id": "controller"})
    return instance


async def test_stop_retains_current_result_and_blocks_next_action(control):
    tool_started, tool_finished = asyncio.Event(), asyncio.Event()
    actions, results = [], []

    async def run():
        async with control.action():
            actions.append("A")
            tool_started.set()
            await tool_finished.wait()
            results.append("A-result")
        async with control.action():
            actions.append("B")
            results.append("B-result")

    task = asyncio.create_task(run())
    await tool_started.wait()
    admission = await control.set_control_state({"state": "paused", "revision": 1})
    assert admission["active_actions"] == 1
    checkpoint = asyncio.create_task(control.wait_for_checkpoint())
    await asyncio.sleep(0)
    assert not checkpoint.done()
    tool_finished.set()
    assert (await checkpoint)["checkpoint"] is True
    assert actions == ["A"]
    assert results == ["A-result"]
    await control.set_control_state({"state": "running", "revision": 2})
    await task
    assert actions == ["A", "B"]
    assert results == ["A-result", "B-result"]


async def test_parallel_actions_drain_without_serializing(control):
    releases = [asyncio.Event(), asyncio.Event()]
    started = []

    async def action(index):
        async with control.action():
            started.append(index)
            await releases[index].wait()

    tasks = [asyncio.create_task(action(index)) for index in range(2)]
    while len(started) != 2:
        await asyncio.sleep(0)
    await control.set_control_state({"state": "paused", "revision": 1})
    checkpoint = asyncio.create_task(control.wait_for_checkpoint())
    releases[0].set()
    await tasks[0]
    assert not checkpoint.done()
    releases[1].set()
    await tasks[1]
    assert (await checkpoint)["active_actions"] == 0


async def test_setter_waits_child_start_ack_not_child_result(control):
    await control.begin_child_start()
    setter = asyncio.create_task(control.set_control_state({"state": "paused", "revision": 1}))
    await asyncio.sleep(0)
    assert control.state == "paused"
    assert not setter.done()
    control.end_child_start()
    assert (await setter)["pending_child_starts"] == 0
    assert (await control.wait_for_checkpoint())["checkpoint"] is True


async def test_bookkeeping_runs_while_stopped_and_drains(control):
    await control.set_control_state({"state": "paused", "revision": 1})
    control.begin_bookkeeping()
    checkpoint = asyncio.create_task(control.wait_for_checkpoint())
    await asyncio.sleep(0)
    assert not checkpoint.done()
    control.end_action()
    assert (await checkpoint)["checkpoint"] is True


async def test_stale_or_conflicting_revision_cannot_override_resume(control):
    await control.set_control_state({"state": "running", "revision": 5})
    await control.set_control_state({"state": "paused", "revision": 4})
    await control.set_control_state({"state": "paused", "revision": 5})
    assert control.state == "running"
    assert control.revision == 5


async def test_resume_hold_prevents_new_admission_until_released(control):
    await control.set_control_state({"state": "running", "revision": 5, "producers_held": True})
    gate = asyncio.create_task(control.wait_until_running())
    await asyncio.sleep(0)
    assert not gate.done()
    await control.set_control_state({"state": "running", "revision": 5, "producers_held": False})
    await gate


async def test_old_same_revision_admission_cannot_undo_release(control):
    await control.set_control_state({"state": "running", "revision": 5, "producers_held": True})
    await control.set_control_state({"state": "running", "revision": 5, "producers_held": False})
    await control.set_control_state({"state": "running", "revision": 5, "producers_held": True})
    assert control.producers_held is False


async def test_delayed_same_revision_registration_cannot_undo_release(control, monkeypatch):
    async def registration(*args, **kwargs):
        await control.set_control_state({"state": "running", "revision": 5, "producers_held": True})
        await control.set_control_state({"state": "running", "revision": 5, "producers_held": False})
        return {"participant_state": "running", "participant_revision": 5, "producers_held": True}
    monkeypatch.setattr(control_module.workflow, "execute_activity", registration)
    await control.register_root()
    assert control.producers_held is False


async def test_new_root_inheriting_unheld_input_still_obeys_same_revision_registration(monkeypatch):
    control = ExecutionControl()
    control.bind({"execution_control_version": 1, "generation": 7, "controller_workflow_id": "controller",
        "execution_control_revision": 5, "execution_control_state": "running", "execution_control_producers_held": False})
    monkeypatch.setattr(control_module.workflow, "info", lambda: SimpleNamespace(workflow_id="root", first_execution_run_id="first"))
    monkeypatch.setattr(control_module.workflow, "execute_activity", AsyncMock(return_value={
        "participant_state": "running", "participant_revision": 5, "producers_held": True}))
    await control.register_root()
    assert control.producers_held is True


async def test_release_phase_survives_same_workflow_rollover(control):
    await control.set_control_state({"state": "running", "revision": 5, "producers_held": False})
    restored = ExecutionControl()
    restored.bind(control.carry())
    await restored.set_control_state({"state": "running", "revision": 5, "producers_held": True})
    assert restored.producers_held is False


async def test_repeated_bind_cannot_overwrite_pre_run_update(control):
    await control.set_control_state({"state": "paused", "revision": 5})
    control.bind({"execution_control_version": 1, "generation": 7, "execution_control_state": "running", "execution_control_revision": 0})
    assert control.state == "paused"
    assert control.revision == 5


def test_first_bind_retains_initial_hold_without_revision():
    control = ExecutionControl()
    control.bind({"execution_control_version": 1, "execution_control_producers_held": True})
    assert control.producers_held is True


async def test_mismatched_generation_and_execution_chain_rejected(control):
    for payload in ({"generation": 8}, {"first_execution_run_id": "replacement"}):
        with pytest.raises(ApplicationError):
            await control.set_control_state({"state": "paused", "revision": 1, **payload})
    assert control.state == "running"


async def test_carry_preserves_control_and_root_identity(control):
    control.root_run_id = "first"
    await control.set_control_state({"state": "paused", "revision": 9, "producers_held": True})
    restored = ExecutionControl()
    restored.bind(control.carry())
    assert restored.status() == control.status()
    assert restored.root_run_id == "first"
    assert restored.carry()["generation"] == 7
    assert isinstance(restored.carry()["generation"], int)
    assert restored._registration()["generation"] == 7


@pytest.mark.parametrize(
    "extra, error_type",
    [
        ({"revision": None}, "InvalidWorkflowControlRevision"),
        ({"revision": "bad"}, "InvalidWorkflowControlRevision"),
        ({"revision": []}, "InvalidWorkflowControlRevision"),
        ({"revision": float("inf")}, "InvalidWorkflowControlRevision"),
        ({"state": []}, "InvalidWorkflowControlState"),
        ({"producers_held": "false"}, "InvalidWorkflowControlRequest"),
    ],
)
async def test_malformed_control_rejects_without_mutating_intent(control, extra, error_type):
    before = control.status()
    payload = {"state": "paused", "revision": 1, **extra}
    with pytest.raises(ApplicationError) as validator_failure:
        control.validate_control(payload)
    assert validator_failure.value.type == error_type
    with pytest.raises(ApplicationError) as handler_failure:
        await control.set_control_state(payload)
    assert handler_failure.value.type == error_type
    assert control.status() == before
    assert (await control.set_control_state({"state": "paused", "revision": 1}))["state"] == "paused"


def test_control_validator_is_read_only_and_allows_existing_checkpoint(control):
    before = control.status()
    control.validate_control({"state": "paused", "revision": 1, "generation": 7, "first_execution_run_id": "first"})
    control.validate_control(None)
    assert control.status() == before


def test_carry_preserves_string_generation_input():
    control = ExecutionControl()
    control.bind({"execution_control_version": 1, "generation": "7"})
    assert control.carry()["generation"] == "7"


async def test_root_registration_response_cannot_override_newer_resume(control, monkeypatch):
    async def delayed_registration(*args, **kwargs):
        await control.set_control_state({"state": "running", "revision": 8})
        return {"participant_state": "paused", "participant_revision": 7}

    monkeypatch.setattr(control_module.workflow, "execute_activity", delayed_registration)
    await control.register_root()
    assert control.state == "running"
    assert control.revision == 8
    assert control.root_run_id == "first"


async def test_late_registered_root_receives_held_state(control, monkeypatch):
    activity = AsyncMock(return_value={"participant_state": "running", "participant_revision": 7, "producers_held": True})
    monkeypatch.setattr(control_module.workflow, "execute_activity", activity)
    await control.register_root()
    assert control.producers_held is True
    assert activity.call_args.args[0] == "execution_control.register"


async def test_carried_root_keeps_membership_without_reregistering(control, monkeypatch):
    activity = AsyncMock()
    monkeypatch.setattr(control_module.workflow, "execute_activity", activity)
    control.root_run_id = "first"
    await control.register_root()
    activity.assert_not_awaited()


async def test_legacy_helper_emits_no_temporal_commands(monkeypatch):
    execute = AsyncMock()
    wait = AsyncMock()
    monkeypatch.setattr(control_module.workflow, "execute_activity", execute)
    monkeypatch.setattr(control_module.workflow, "wait_condition", wait)
    legacy = ExecutionControl()
    legacy.bind({})
    async with legacy.action():
        pass
    async with legacy.child_start():
        pass
    await legacy.register_root()
    await legacy.unregister_root()
    await legacy.wait_for_checkpoint()
    assert legacy.carry() == {}
    execute.assert_not_awaited()
    wait.assert_not_awaited()


def _controller():
    controller = WorkflowControlWorkflow()
    controller._seed_carried_state({"execution_control_version": 1, "generation": 7})
    return controller


async def test_root_membership_is_idempotent_and_identity_checked():
    controller = _controller()
    registration = {"workflow_id": "root", "first_execution_run_id": "first", "generation": 7}
    first = await controller.register_execution(registration)
    duplicate = await controller.register_execution(registration)
    assert first["membership_epoch"] == duplicate["membership_epoch"] == 1
    with pytest.raises(ApplicationError, match="identity"):
        await controller.register_execution({**registration, "first_execution_run_id": "replacement"})
    with pytest.raises(ApplicationError, match="generation"):
        await controller.register_execution({**registration, "generation": 8})
    stale_remove = await controller.unregister_execution({**registration, "first_execution_run_id": "replacement"})
    assert stale_remove["membership_epoch"] == 1
    removed = await controller.unregister_execution(registration)
    assert removed["membership_epoch"] == 2
    assert removed["live_roots"] == {}


async def test_controller_holds_producers_through_resume_and_late_enrollment():
    controller = _controller()
    await controller.set_control_state({"state": "paused", "revision": 2, "generation": 7})
    held = await controller.set_control_state({"state": "running", "revision": 3, "producers_held": True})
    assert held["state"] == "paused"
    assert held["participant_state"] == "running"
    assert held["producers_held"] is True
    root = await controller.register_execution({"workflow_id": "late", "first_execution_run_id": "first", "generation": 7})
    assert root["participant_revision"] == 3
    assert root["producers_held"] is True
    released = await controller.set_control_state({"state": "running", "revision": 3, "producers_held": False})
    assert released["state"] == "running"


async def test_controller_final_release_checks_membership_atomically():
    controller = _controller()
    await controller.set_control_state({"state": "running", "revision": 3, "producers_held": True})
    await controller.register_execution({"workflow_id": "late", "first_execution_run_id": "first", "generation": 7})
    retry = await controller.set_control_state({"state": "running", "revision": 3, "producers_held": False, "expected_membership_epoch": 0})
    assert retry["producers_held"] is True
    done = await controller.set_control_state({"state": "running", "revision": 3, "producers_held": False, "expected_membership_epoch": 1})
    assert done["producers_held"] is False


async def test_controller_rollover_carries_roots_target_and_revision(monkeypatch):
    from services.temporal import workflow_control_workflow as module
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "wait_condition", _wait)
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(module.workflow, "payload_converter", DefaultPayloadConverter)
    controller = _controller()
    await controller.register_execution({"workflow_id": "root", "first_execution_run_id": "first", "generation": 7})
    await controller.set_control_state({"state": "running", "revision": 4, "producers_held": True})
    carried = {}
    monkeypatch.setattr(module.workflow, "continue_as_new", lambda *, args: carried.update(args[0]))
    await controller._continue_as_new({"execution_control_version": 1, "generation": 7})
    restored = WorkflowControlWorkflow()
    restored._state = carried["state"]
    restored._seed_carried_state(carried)
    assert restored.status() == controller.status()


async def test_controller_init_does_not_reseed_over_pre_run_updates(monkeypatch):
    from services.temporal import workflow_control_workflow as module
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    original = {"execution_control_version": 1, "generation": 7, "revision": 3,
        "state": "paused", "participant_state": "running", "participant_revision": 3,
        "execution_control_producers_held": True}
    controller = WorkflowControlWorkflow(original)
    await controller.set_control_state({"state": "running", "revision": 3, "producers_held": False})
    await controller.register_execution({"workflow_id": "new", "first_execution_run_id": "first", "generation": 7})
    controller._seed_carried_state(original)
    assert controller.status()["state"] == "running"
    assert controller.status()["live_roots"]["new"]["first_execution_run_id"] == "first"


async def test_controller_rollover_rejects_complete_oversized_input(monkeypatch):
    from services.temporal import workflow_control_workflow as module
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "wait_condition", _wait)
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(module.workflow, "payload_converter", DefaultPayloadConverter)
    can = MagicMock()
    monkeypatch.setattr(module.workflow, "continue_as_new", can)
    controller = _controller()
    with pytest.raises(ApplicationError, match="continuation requires") as exc:
        await controller._continue_as_new({"execution_control_version": 1, "generation": 7, "graph": "x" * 1_900_001})
    assert exc.value.type == "ControllerContinuationTooLarge"
    assert exc.value.non_retryable is True
    can.assert_not_called()


async def test_control_activity_bridges_use_injected_client_and_acknowledged_updates():
    handle = SimpleNamespace(execute_update=AsyncMock(return_value={"ok": True}))
    client = SimpleNamespace(get_workflow_handle=MagicMock(return_value=handle))
    activities = ExecutionControlActivities(client)
    payload = {"controller_workflow_id": "controller", "generation": 7}
    assert await activities.register(payload) == {"ok": True}
    assert await activities.unregister(payload) == {"ok": True}
    assert [call.args[0] for call in handle.execute_update.await_args_list] == ["register_execution", "unregister_execution"]


async def test_paused_push_admission_defers_for_rollover(control):
    from services.temporal.workflow_control_workflow import _AdmissionDeferred
    controller = _controller()
    await controller.set_control_state({"state": "paused", "revision": 1})
    with pytest.raises(_AdmissionDeferred):
        await controller._wait_until_running()
