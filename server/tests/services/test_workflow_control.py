from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from services.deployment.control import WorkflowControlService, serialize_control


@pytest.mark.asyncio
async def test_begin_generation_is_idempotent():
    db = AsyncMock()
    db.get_workflow_control_by_idempotency_key.side_effect = [None, None]
    db.get_latest_workflow_control.return_value = None
    db.create_workflow_control.side_effect = lambda control: control
    service = WorkflowControlService(db)

    control, created = await service.begin_generation(
        workflow_id="wf", nodes=[], edges=[], session_id="s", idempotency_key="request-1"
    )

    assert created is True
    assert control.generation == 1
    assert control.status == "starting"
    assert serialize_control(control)["can_pause"] is False


@pytest.mark.asyncio
async def test_concurrent_insert_rereads_idempotent_winner():
    db = AsyncMock()
    winner = AsyncMock()
    winner.generation = 1
    db.get_workflow_control_by_idempotency_key.side_effect = [None, winner]
    db.get_latest_workflow_control.return_value = None
    db.create_workflow_control.side_effect = IntegrityError("insert", {}, Exception("unique"))
    service = WorkflowControlService(db)

    control, created = await service.begin_generation(
        workflow_id="wf", nodes=[], edges=[], session_id="s", idempotency_key="same-key"
    )

    assert control is winner
    assert created is False


@pytest.mark.asyncio
async def test_transition_rejects_stale_revision():
    db = AsyncMock()
    db.transition_workflow_control.return_value = None
    service = WorkflowControlService(db)
    control = AsyncMock(id="control", revision=2)

    with pytest.raises(ValueError, match="control_revision_conflict"):
        await service.transition(
            control, expected_revision=1, from_statuses={"running"}, status="pausing"
        )


@pytest.mark.asyncio
async def test_reset_generation_is_ready_for_explicit_start():
    reset_control = AsyncMock(status="reset", generation=3)
    reset_control.workflow_id = "wf"
    reset_control.revision = 9
    reset_control.execution_id = "old-execution"
    reset_control.root_execution_id = "old-execution"
    reset_control.controller_workflow_id = "old-controller"
    reset_control.controller_run_id = "old-run"
    reset_control.created_at = None
    reset_control.updated_at = None
    reset_control.terminal_reason = "workflow_reset"
    status = serialize_control(reset_control)
    assert status["state"] == "ready"
    assert status["can_start"] is True
    assert status["can_reset"] is False

    db = AsyncMock()
    db.get_workflow_control_by_idempotency_key.return_value = None
    db.get_latest_workflow_control.return_value = reset_control
    db.create_workflow_control.side_effect = lambda control: control
    control, created = await WorkflowControlService(db).begin_generation(
        workflow_id="wf", nodes=[], edges=[], session_id="s", idempotency_key="start-after-reset"
    )
    assert created is True
    assert control.generation == 4
