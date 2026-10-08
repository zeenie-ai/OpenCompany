"""Native Update validation, Update-only rollover, and messaging patch replay."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from temporalio import workflow
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from services.temporal.workflow_control_workflow import WorkflowControlWorkflow


_PATCH = "controller-messaging-v1"


def _history_pressure(controller: WorkflowControlWorkflow) -> bool:
    # The time-skipping test server does not expose production dynamic config
    # for a low accepted-Update limit. Use a deterministic pressure hook for
    # the original run, and keep the same hook installed during replay. All
    # messages, their acknowledgements, and Continue-As-New still use the SDK.
    return not workflow.info().continued_run_id and controller._membership_epoch >= 3


def _has_patch(history) -> bool:
    return any(_PATCH.encode() in payload.data
        for event in history.events
        if event.event_type == EventType.EVENT_TYPE_MARKER_RECORDED
        for payloads in event.marker_recorded_event_attributes.details.values()
        for payload in payloads.payloads)


def _accepted_ids(histories) -> set[str]:
    return {event.workflow_execution_update_accepted_event_attributes.accepted_request.meta.update_id
        for history in histories for event in history.events
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_ACCEPTED}


def _completed_ids(histories) -> set[str]:
    return {event.workflow_execution_update_completed_event_attributes.meta.update_id
        for history in histories for event in history.events
        if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_UPDATE_COMPLETED}


async def _chain_histories(client, handle):
    histories = []
    run_id = handle.first_execution_run_id
    while True:
        history = await client.get_workflow_handle(handle.id, run_id=run_id).fetch_history()
        histories.append(history)
        continued = next((event.workflow_execution_continued_as_new_event_attributes
            for event in history.events
            if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW), None)
        if continued is None:
            return histories
        run_id = continued.new_execution_run_id


async def _reject_update(handle, method: str, payload: dict, update_id: str, error_type: str):
    try:
        await asyncio.wait_for(handle.execute_update(method, payload, id=update_id), timeout=15)
    except WorkflowUpdateFailedError as exc:
        assert isinstance(exc.cause, ApplicationError)
        assert exc.cause.type == error_type, repr(exc.cause)
    else:
        raise AssertionError(f"Malformed Update {update_id} was accepted")


async def _wait_for_rollover(handle):
    for _ in range(300):
        description = await handle.describe()
        if description.run_id != handle.first_execution_run_id:
            return description.run_id
        await asyncio.sleep(0.02)
    raise AssertionError("Register/unregister Update traffic did not roll the controller over")


def _registration(workflow_id: str) -> dict:
    return {"generation": 7, "workflow_id": workflow_id,
        "first_execution_run_id": str(uuid4())}


async def _run_messaging_gate():
    original_pressure = WorkflowControlWorkflow._history_pressure
    WorkflowControlWorkflow._history_pressure = _history_pressure
    try:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            client = environment.client
            queue = "controller-messaging-" + uuid4().hex
            controller_id = "controller-" + uuid4().hex
            successful_ids: set[str] = set()
            rejected_ids = {"bad-revision", "bad-epoch", "bad-checkpoint"}
            async with Worker(client, task_queue=queue, workflows=[WorkflowControlWorkflow],
                    max_cached_workflows=0, identity="messaging-before-rollover"):
                handle = await client.start_workflow("WorkflowControlWorkflow", {
                    "execution_control_version": 1, "generation": 7,
                    "controller_workflow_id": controller_id,
                    "state": "paused", "revision": 1,
                    "participant_state": "paused", "participant_revision": 1,
                }, id=controller_id, task_queue=queue)
                await _reject_update(handle, "set_control_state", {
                    "state": "running", "revision": "malformed", "generation": 7,
                }, "bad-revision", "InvalidWorkflowControlRevision")
                await _reject_update(handle, "set_control_state", {
                    "state": "running", "revision": 2, "generation": 7,
                    "producers_held": False, "expected_membership_epoch": "malformed",
                }, "bad-epoch", "InvalidExecutionMembershipEpoch")
                await _reject_update(handle, "wait_for_checkpoint", {
                    "state": "paused", "revision": "malformed", "generation": 7,
                }, "bad-checkpoint", "InvalidWorkflowControlRevision")

                # A rejected validator leaves the execution usable and its
                # acknowledged state untouched. Enrollment alone then wakes
                # an otherwise idle controller's actual rollover path.
                stopped = await handle.execute_update("set_control_state", {
                    "state": "paused", "revision": 1, "generation": 7,
                }, id="valid-after-rejection")
                successful_ids.add("valid-after-rejection")
                assert stopped["state"] == "paused" and stopped["revision"] == 1
                keeper = _registration("keeper-" + uuid4().hex)
                temporary = _registration("temporary-" + uuid4().hex)
                registered = await handle.execute_update("register_execution", keeper, id="register-keeper")
                successful_ids.add("register-keeper")
                assert registered["membership_epoch"] == 1
                registered = await handle.execute_update("register_execution", temporary, id="register-temporary")
                successful_ids.add("register-temporary")
                assert registered["membership_epoch"] == 2
                unregistered = await handle.execute_update("unregister_execution", temporary, id="unregister-temporary")
                successful_ids.add("unregister-temporary")
                assert unregistered["membership_epoch"] == 3
                assert unregistered["live_roots"] == {keeper["workflow_id"]: {
                    "workflow_id": keeper["workflow_id"],
                    "first_execution_run_id": keeper["first_execution_run_id"],
                }}
                await _wait_for_rollover(handle)

            # A new worker reconstructs the carried entity state from history;
            # no Signal, polling trigger, or business Activity caused rollover.
            async with Worker(client, task_queue=queue, workflows=[WorkflowControlWorkflow],
                    max_cached_workflows=0, identity="messaging-after-rollover"):
                carried = await handle.execute_update("wait_for_checkpoint", None, id="carried-checkpoint")
                successful_ids.add("carried-checkpoint")
                assert carried["state"] == "paused" and carried["revision"] == 1
                assert carried["membership_epoch"] == 3
                assert carried["live_roots"] == unregistered["live_roots"]
                assert carried["checkpoint"] is True
                assert (await handle.execute_update("set_control_state", "resume", id="paused-legacy-resume"))["state"] == "paused"
                successful_ids.add("paused-legacy-resume")
                held = await handle.execute_update("set_control_state", {
                    "state": "running", "revision": 2, "generation": 7,
                    "producers_held": True,
                }, id="hold-resume")
                successful_ids.add("hold-resume")
                assert held["state"] == "paused" and held["participant_state"] == "running"
                legacy = await handle.execute_update("set_control_state", "resume", id="held-legacy-resume")
                successful_ids.add("held-legacy-resume")
                assert legacy["state"] == "paused" and legacy["producers_held"] is True
                assert legacy["participant_revision"] == 2
                review = await handle.execute_update("pause_admissions", True, id="held-review-drain")
                successful_ids.add("held-review-drain")
                assert review["state"] == "paused" and review["producers_held"] is True
                released = await handle.execute_update("set_control_state", {
                    "state": "running", "revision": 2, "generation": 7,
                    "producers_held": False, "expected_membership_epoch": 3,
                }, id="release-resume")
                successful_ids.add("release-resume")
                assert released["state"] == "running" and released["producers_held"] is False
                await handle.signal("reset")
                await handle.result()
                histories = await _chain_histories(client, handle)

            assert len(histories) == 2
            assert all(_has_patch(history) for history in histories)
            assert not (_accepted_ids(histories) & rejected_ids)
            assert _accepted_ids(histories) == successful_ids
            assert _completed_ids(histories) == successful_ids
            assert not any(event.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_FAILED
                for history in histories for event in history.events)
            assert not any(event.event_type in {
                EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED,
                EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED,
            } for history in histories for event in history.events)
            # The only Signal is the terminal cleanup after both runs finish
            # their messaging checks; Update-only traffic drove continuation.
            assert sum(event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
                for history in histories for event in history.events) == 1
            for history in histories:
                replay = await Replayer(workflows=[WorkflowControlWorkflow]).replay_workflow(history)
                assert replay.replay_failure is None
    finally:
        WorkflowControlWorkflow._history_pressure = original_pressure


async def _run_pre_marker_replay():
    original_pressure = WorkflowControlWorkflow._history_pressure
    original_patched = workflow.patched
    WorkflowControlWorkflow._history_pressure = _history_pressure
    workflow.patched = lambda patch_id: False if patch_id == _PATCH else original_patched(patch_id)
    histories = []
    try:
        async with await WorkflowEnvironment.start_time_skipping() as environment:
            client = environment.client
            queue = "controller-messaging-legacy-" + uuid4().hex
            async with Worker(client, task_queue=queue, workflows=[WorkflowControlWorkflow],
                    max_cached_workflows=0, identity="messaging-pre-marker"):
                for version in (0, 1):
                    controller_id = "controller-" + uuid4().hex
                    handle = await client.start_workflow("WorkflowControlWorkflow", {
                        "execution_control_version": version, "generation": 7,
                        "controller_workflow_id": controller_id,
                        "state": "paused", "revision": 1,
                        "participant_state": "paused", "participant_revision": 1,
                    }, id=controller_id, task_queue=queue)
                    if version == 1:
                        keeper = _registration("legacy-keeper-" + uuid4().hex)
                        temporary = _registration("legacy-temporary-" + uuid4().hex)
                        await handle.execute_update("register_execution", keeper)
                        await handle.execute_update("register_execution", temporary)
                        status = await handle.execute_update("unregister_execution", temporary)
                        assert status["membership_epoch"] == 3
                    # Pre-marker v1 retains its recorded legacy command path.
                    # Fresh v1's stronger producer guard applies only when
                    # the new marker has been recorded, as tested above.
                    resumed = await handle.execute_update("set_control_state", "resume")
                    assert resumed["state"] == "running"
                    await handle.execute_update("pause_admissions", False)
                    assert (await handle.describe()).run_id == handle.first_execution_run_id
                    await handle.signal("reset")
                    await handle.result()
                    histories.append(await handle.fetch_history())
            workflow.patched = original_patched
            assert not any(_has_patch(history) for history in histories)
            for history in histories:
                replay = await Replayer(workflows=[WorkflowControlWorkflow]).replay_workflow(history)
                assert replay.replay_failure is None
    finally:
        WorkflowControlWorkflow._history_pressure = original_pressure
        workflow.patched = original_patched


def test_controller_native_messaging_validation_rollover_and_replay():
    completed = subprocess.run([sys.executable, "-m", "tests.temporal.test_controller_messaging_integration"],
        cwd=Path(__file__).parents[2], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=150, check=False)
    assert completed.returncode == 0, completed.stdout.decode(errors="replace")[-12000:]


if __name__ == "__main__":
    asyncio.run(_run_messaging_gate())
    asyncio.run(_run_pre_marker_replay())
