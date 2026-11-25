"""Deterministic driver-park ordering during an admitted Pi round's completion."""

from __future__ import annotations

import asyncio

import pytest

from services.temporal import pi_workflow
from services.temporal.pi_protocol import canonical_digest


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("inside_model_action", [True, False])
async def test_parked_driver_reconstructs_receipts_before_stop_without_resume(monkeypatch, inside_model_action):
    """The old driver parks after completion starts, with only one SDK slot."""
    value = pi_workflow.PiCodingAgentWorkflow({"execution_control_revision": 0})
    control = value._execution_control
    scope = {"workflow_id": "agent", "generation_id": "0", "node_id": "coding"}
    value._pi_request = {"scope": scope, "turn": 0, "messages": [],
        "context_digest": canonical_digest([]), "config_digest": "config", "binding_digest": "bindings"}
    identity = "turn:0:model"
    payload = value._operation_payload(purpose="assistant")
    value._pi.begin(0, {identity: {"kind": "model", "payload": payload}})
    value._pi.register(0)
    value._pi.submit({"id": identity, "kind": "model", "payload": {**payload, "epoch": 1}})
    value._pi.complete(identity, {"kind": "final", "content": "done"})
    messages = [{"role": "assistant", "content": "done"}]
    old_driver = asyncio.get_running_loop().create_future()
    value._pi_driver = old_driver
    receipt_started = asyncio.Event()
    release_receipt = asyncio.Event()
    checkpoint_waiting = asyncio.Event()
    driver_counts = {"active": 1, "max": 1, "starts": 0}
    driver_tasks = []

    async def wait_condition(predicate):
        checkpoint_waiting.set()
        await until(predicate)

    async def receipt_driver():
        try:
            epoch = (await value.register_driver({"turn": 0}))["epoch"]
            # Reconstruction attaches to a durable result; it cannot request a
            # second model effect or a previously unadmitted tool position.
            admitted = await value.submit_operation({"id": identity, "kind": "model",
                "payload": {**payload, "epoch": epoch}})
            assert admitted["status"] == "completed"
            assert value.operation_status({"id": identity, "epoch": epoch})["result"] == {
                "kind": "final", "content": "done"}
            assert control.state == "paused" and value._pi_receipt_checkpointing
            receipt_started.set()
            await release_receipt.wait()
            return {"status": "completed", "epoch": epoch, "scope": scope, "turn": 0,
                "config_digest": "config", "binding_digest": "bindings", "consumed_operations": [identity],
                "projection_digest": canonical_digest(messages), "snapshot": {"pi_blob": "saved-round"}}
        finally:
            driver_counts["active"] -= 1

    def start_activity(name, request, **options):
        assert name == "coding.pi_drive_turn" and request is value._pi_request
        assert old_driver.done() and driver_counts["active"] == 0
        assert control.state == "paused"
        driver_counts["active"] += 1
        driver_counts["max"] = max(driver_counts["max"], driver_counts["active"])
        driver_counts["starts"] += 1
        task = asyncio.create_task(receipt_driver())
        driver_tasks.append(task)
        return task

    monkeypatch.setattr(pi_workflow.workflow, "patched", lambda _: True)
    monkeypatch.setattr(pi_workflow.workflow, "cancellation_reason", lambda: None)
    monkeypatch.setattr(pi_workflow.workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(pi_workflow.workflow, "start_activity", start_activity)
    if inside_model_action:
        await control.begin_action()
    await control.set_control_state({"state": "paused", "revision": 1})

    async def complete_round():
        try:
            await value._complete_runtime_turn(messages)
        finally:
            if inside_model_action:
                control.end_action()

    completion = asyncio.create_task(complete_round())
    stop = next_action = None
    try:
        await until(lambda: value._pi_checkpointing)
        assert not old_driver.done(), "Force the parked result after completion entered its await"
        stop = asyncio.create_task(value.wait_for_checkpoint({"state": "paused", "revision": 1}))
        if inside_model_action:
            await asyncio.wait_for(checkpoint_waiting.wait(), 2)
        else:
            await until(lambda: value._pi_stop_pending and value._pi.parked)
        assert not stop.done()
        driver_counts["active"] -= 1
        old_driver.set_result({"status": "parked"})
        await asyncio.wait_for(receipt_started.wait(), 2)
        assert value._pi_checkpointing and not stop.done()
        next_action = asyncio.create_task(control.begin_action())
        await asyncio.sleep(0)
        assert not next_action.done(), "Receipt reconstruction must keep business admission closed"
        release_receipt.set()
        await asyncio.wait_for(completion, 2)
        stopped = await asyncio.wait_for(stop, 2)
        assert stopped["checkpoint"] and stopped["pi_driver_checkpoint"] and stopped["state"] == "paused"
        assert stopped["pi_driver_epoch"] == 3
        assert value.operation_status({"id": identity, "epoch": 2})["status"] == "park"
        assert value._pi_context["_pi_checkpoint"]["reference"] == {"pi_blob": "saved-round"}
        assert value._pi_stop_pending and not value._pi_checkpointing
        assert driver_counts == {"active": 0, "max": 1, "starts": 1}
        assert not next_action.done()
        await control.set_control_state({"state": "running", "revision": 2, "producers_held": False})
        await asyncio.wait_for(next_action, 2)
        control.end_action()
        assert control.status()["checkpoint"]
    finally:
        tasks = [completion, stop, next_action, *driver_tasks]
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not None), return_exceptions=True)


@pytest.mark.asyncio
async def test_unpatched_history_keeps_parked_driver_resume_boundary(monkeypatch):
    value = pi_workflow.PiCodingAgentWorkflow({"execution_control_revision": 0})
    value._pi_request = {"scope": {"workflow_id": "agent"}, "messages": [],
        "config_digest": "config", "binding_digest": "bindings"}
    value._pi.begin(0, {})
    value._pi.register(0)
    old_driver = asyncio.get_running_loop().create_future()
    value._pi_driver = old_driver
    starts = []
    resumed = asyncio.Event()

    async def wait_condition(predicate):
        await until(predicate)

    def start_activity(name, request, **options):
        assert resumed.is_set() and value._execution_control.state == "running"
        starts.append(name)
        result = asyncio.get_running_loop().create_future()
        result.set_result({"status": "completed", "epoch": 1, "scope": request["scope"], "turn": 0,
            "config_digest": "config", "binding_digest": "bindings", "consumed_operations": [],
            "projection_digest": canonical_digest([]), "snapshot": {"pi_blob": "legacy-round"}})
        return result

    monkeypatch.setattr(pi_workflow.workflow, "patched", lambda _: False)
    monkeypatch.setattr(pi_workflow.workflow, "cancellation_reason", lambda: None)
    monkeypatch.setattr(pi_workflow.workflow, "wait_condition", wait_condition)
    monkeypatch.setattr(pi_workflow.workflow, "start_activity", start_activity)
    await value._execution_control.set_control_state({"state": "paused", "revision": 1})
    completion = asyncio.create_task(value._complete_runtime_turn([]))
    try:
        await until(lambda: value._pi_checkpointing)
        old_driver.set_result({"status": "parked"})
        await until(lambda: not value._pi_checkpointing)
        assert not completion.done() and not starts
        resumed.set()
        await value._execution_control.set_control_state({"state": "running", "revision": 2, "producers_held": False})
        await asyncio.wait_for(completion, 2)
        assert starts == ["coding.pi_drive_turn"]
        assert value._pi_checkpoint["reference"] == {"pi_blob": "legacy-round"}
    finally:
        if not completion.done():
            completion.cancel()
        await asyncio.gather(completion, return_exceptions=True)
