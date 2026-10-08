"""Acknowledged controller messages respect suspension and history rollover."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from temporalio.converter import DefaultPayloadConverter
from temporalio.exceptions import ApplicationError

from services.temporal import workflow_control_workflow as module
from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


def control_data(**extra):
    return {
        "execution_control_version": 1,
        "generation": 7,
        "controller_workflow_id": "controller",
        **extra,
    }


def registration():
    return {"generation": 7, "workflow_id": "root", "first_execution_run_id": "first"}


@pytest.fixture
def runtime(monkeypatch):
    patches = []

    def patched(name):
        patches.append(name)
        return True

    async def wait(predicate):
        while not predicate():
            await asyncio.sleep(0)

    monkeypatch.setattr(module.workflow, "patched", patched)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(
        workflow_id="controller", run_id="run", first_execution_run_id="controller-first",
    ))
    monkeypatch.setattr(module.workflow, "wait_condition", wait)
    monkeypatch.setattr(module.workflow, "all_handlers_finished", lambda: True)
    monkeypatch.setattr(module.workflow, "payload_converter", DefaultPayloadConverter)
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(WorkflowControlWorkflow, "_history_pressure", lambda _: False)
    return patches


@pytest.mark.parametrize("message", ["state", "legacy", "checkpoint", "register", "unregister", "pause", "replace"])
async def test_successful_updates_request_main_thread_rollover(runtime, monkeypatch, message):
    controller = WorkflowControlWorkflow(control_data())
    assert not runtime, "Workflow initialization must not issue patch commands"
    monkeypatch.setattr(controller, "_history_pressure", lambda: True)
    continue_as_new = MagicMock()
    monkeypatch.setattr(module.workflow, "continue_as_new", continue_as_new)

    if message == "state":
        await controller.set_control_state({"state": "paused", "revision": 1})
    elif message == "legacy":
        await controller.set_control_state("paused")
    elif message == "checkpoint":
        await controller.wait_for_checkpoint()
    elif message == "register":
        await controller.register_execution(registration())
    elif message == "unregister":
        await controller.unregister_execution(registration())
    elif message == "pause":
        await controller.pause_admissions()
    else:
        controller._state = "paused"
        await controller.replace_graph({"nodes": [], "edges": []})

    assert controller._can_requested is True
    assert runtime == ["controller-messaging-v1"]
    continue_as_new.assert_not_called()


async def test_registration_alone_wakes_an_idle_controller(runtime, monkeypatch):
    data = control_data()
    controller = WorkflowControlWorkflow(data)
    waiting = asyncio.Event()

    async def wait(predicate):
        if not predicate():
            waiting.set()
        while not predicate():
            await asyncio.sleep(0)

    class Continued(BaseException):
        pass

    carried = {}

    def continue_as_new(*, args):
        carried.update(args[0])
        raise Continued()

    monkeypatch.setattr(module.workflow, "wait_condition", wait)
    monkeypatch.setattr(module.workflow, "continue_as_new", continue_as_new)
    run = asyncio.create_task(controller.run(data))
    await asyncio.wait_for(waiting.wait(), 1)
    monkeypatch.setattr(controller, "_history_pressure", lambda: True)
    await controller.register_execution(registration())
    with pytest.raises(Continued):
        await asyncio.wait_for(run, 1)
    assert carried["live_roots"]["root"]["first_execution_run_id"] == "first"
    assert carried["membership_epoch"] == 1


@pytest.mark.parametrize("state,held", [("paused", False), ("running", True)])
async def test_legacy_producer_resume_cannot_release_generation_suspension(runtime, state, held):
    controller = WorkflowControlWorkflow(control_data())
    await controller.set_control_state({"state": state, "revision": 4, "producers_held": held})
    before = controller._execution_control.carry()

    result = await controller.set_control_state("running")

    assert result["state"] == "paused"
    assert result["participant_state"] == state
    assert result["producers_held"] is held
    assert controller._execution_control.carry() == before


@pytest.mark.parametrize("state,held", [("paused", False), ("running", True)])
async def test_producer_pause_cannot_drain_reviews_during_generation_suspension(runtime, state, held):
    controller = WorkflowControlWorkflow(control_data())
    controller._triggers["review"] = {
        "trigger_node_id": "review-node", "listener_args": {"node_type": "taskTrigger"},
    }
    controller._events.append(("review", {"id": "review-event"}))
    await controller.set_control_state({"state": state, "revision": 4, "producers_held": held})

    result = await controller.pause_admissions(drain_tasks=True)

    assert result["state"] == "paused"
    assert controller._drain_tasks is False
    assert controller._next_event() is None


async def test_producer_pause_keeps_normal_safe_apply_review_drain_and_restore(runtime):
    controller = WorkflowControlWorkflow(control_data())
    controller._triggers.update({
        "ordinary": {"trigger_node_id": "ordinary-node", "listener_args": {"node_type": "mailTrigger"}},
        "review": {"trigger_node_id": "review-node", "listener_args": {"node_type": "taskTrigger"}},
    })
    controller._events.extend([
        ("ordinary", {"id": "ordinary-event"}), ("review", {"id": "review-event"}),
    ])

    await controller.pause_admissions(drain_tasks=True)
    assert controller._next_event() == 1
    assert controller._execution_control.state == "running"
    assert controller._execution_control.producers_held is False
    await controller.set_control_state("running")
    assert controller.status()["state"] == "running"
    assert controller._next_event() == 0
    assert controller._drain_tasks is False


async def test_legacy_restore_cannot_reopen_a_closed_controller(runtime):
    controller = WorkflowControlWorkflow(control_data())
    await controller.reset()
    result = await controller.set_control_state("running")
    assert result["state"] == "resetting"
    assert controller._closed is True
    assert controller._can_requested is False


async def test_pre_marker_v1_retains_prior_update_and_producer_commands(runtime, monkeypatch):
    monkeypatch.setattr(module.workflow, "patched", lambda name: name != "controller-messaging-v1")
    controller = WorkflowControlWorkflow(control_data())
    monkeypatch.setattr(controller, "_history_pressure", lambda: True)
    await controller.set_control_state({"state": "paused", "revision": 4})
    await controller.pause_admissions(drain_tasks=True)
    assert controller._drain_tasks is True
    result = await controller.set_control_state("running")
    assert result["state"] == "running"
    assert result["participant_state"] == "paused"
    assert controller._can_requested is False
    assert controller._messaging_v1 is False


async def test_legacy_generation_updates_do_not_record_the_new_patch(runtime, monkeypatch):
    controller = WorkflowControlWorkflow()
    monkeypatch.setattr(controller, "_history_pressure", lambda: True)
    await controller.pause_admissions(drain_tasks=True)
    await controller.set_control_state("running")
    await controller.wait_for_checkpoint()
    assert not runtime
    assert controller._can_requested is False


@pytest.mark.parametrize("payload,error_type", [
    ({"state": "paused", "revision": []}, "InvalidWorkflowControlRevision"),
    ({"state": "paused", "revision": 1, "expected_membership_epoch": []}, "InvalidExecutionMembershipEpoch"),
    ({"state": "paused", "revision": 1, "hold_admissions": "yes"}, "InvalidWorkflowControlRequest"),
])
async def test_invalid_control_messages_rejected_without_mutation(runtime, payload, error_type):
    controller = WorkflowControlWorkflow(control_data())
    before = controller.status()
    for apply in (controller.validate_control_state, controller.set_control_state):
        with pytest.raises(ApplicationError) as exc:
            result = apply(payload)
            if asyncio.iscoroutine(result):
                await result
        assert exc.value.type == error_type
        assert controller.status() == before
    assert not runtime, "A rejected validator must issue no patch commands"


def test_checkpoint_validator_is_read_only_and_accepts_omitted_payload(runtime):
    controller = WorkflowControlWorkflow(control_data())
    controller.validate_checkpoint()
    before = controller.status()
    controller.validate_checkpoint({"state": "paused", "revision": 8})
    assert controller.status() == before
    with pytest.raises(ApplicationError):
        controller.validate_checkpoint({"state": [], "revision": 8})
    assert not runtime


async def test_registration_shape_is_rejected_in_validator_and_handler(runtime):
    controller = WorkflowControlWorkflow(control_data())
    for validate, handler in (
        (controller.validate_registration, controller.register_execution),
        (controller.validate_unregistration, controller.unregister_execution),
    ):
        with pytest.raises(ApplicationError) as exc:
            validate([])
        assert exc.value.type == "InvalidExecutionRegistration"
        with pytest.raises(ApplicationError):
            await handler([])
    assert controller.status()["live_roots"] == {}
    assert controller.status()["membership_epoch"] == 0
    assert not runtime


@pytest.mark.parametrize("graph", [
    None,
    {},
    {"nodes": []},
    {"nodes": {}, "edges": []},
    {"nodes": [{}], "edges": []},
    {"nodes": [{"id": []}], "edges": []},
    {"nodes": [{"id": "trigger"}], "edges": None},
])
async def test_invalid_graph_update_preserves_revision_and_existing_snapshots(runtime, graph):
    controller = WorkflowControlWorkflow(control_data(state="paused", revision=3, triggers={
        "listener": {
            "trigger_node_id": "trigger", "listener_args": {
                "nodes": [{"id": "trigger"}], "edges": [], "parameter_snapshot": {"old": True},
            },
        },
    }))
    before = controller.status()
    triggers = deepcopy(controller._triggers)
    for apply in (controller.validate_graph, controller.replace_graph):
        with pytest.raises(ApplicationError) as exc:
            result = apply(graph)
            if asyncio.iscoroutine(result):
                await result
        assert exc.value.type == "InvalidWorkflowGraphSnapshot"
        assert controller.status() == before
        assert controller._triggers == triggers
    assert not runtime


async def test_graph_message_preserves_existing_hashable_id_contract(runtime):
    controller = WorkflowControlWorkflow(control_data(state="paused", triggers={
        "listener": {"trigger_node_id": 7, "listener_args": {}},
    }))
    graph = {"nodes": [{"id": 7}], "edges": []}
    controller.validate_graph(graph)
    result = await controller.replace_graph(graph)
    assert result["applied"] is True
    assert controller._triggers["listener"]["listener_args"]["nodes"] == graph["nodes"]


def test_controller_registers_read_only_control_and_membership_validators():
    definitions = WorkflowControlWorkflow.__temporal_workflow_definition.updates
    for name in ("set_control_state", "wait_for_checkpoint", "register_execution", "unregister_execution", "replace_graph"):
        assert definitions[name].validator is not None
