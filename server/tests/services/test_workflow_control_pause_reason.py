"""Why a deployment paused on its own: the circuit breaker, crash recovery
and a lost controller say so (``pause_reason`` / ``pause_detail``), a
manual pause and a client never set one, and running again clears it."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.deployment import control as control_module
from services.deployment import handlers


def row(status, **extra):
    return SimpleNamespace(
        id="c1",
        workflow_id="wf",
        generation=1,
        execution_id="wf:execution:1",
        root_execution_id="wf:execution:1",
        data_scope_id=None,
        controller_workflow_id=None,
        controller_run_id=None,
        status=status,
        revision=2,
        created_at=None,
        updated_at=None,
        terminal_reason=None,
        **extra,
    )


def test_serialize_control_reports_the_reason():
    payload = control_module.serialize_control(row("paused", pause_reason="failures", pause_detail="Paused after repeated errors."))
    assert payload["pause_reason"] == "failures"
    assert payload["pause_detail"] == "Paused after repeated errors."
    # Controls built without the columns still serialize.
    assert control_module.serialize_control(row("running"))["pause_reason"] is None


def test_only_known_server_reasons_are_accepted():
    assert handlers._automatic_pause_values({"_pause_reason": "failures", "_pause_detail": " d "}) == {"pause_reason": "failures", "pause_detail": "d"}
    assert handlers._automatic_pause_values({"_pause_reason": "because"}) is None
    assert handlers._automatic_pause_values({}) is None


def test_running_again_clears_a_reason_only_when_there_is_one():
    assert handlers._clear_pause_reason_kwargs(row("resuming", pause_reason="recovery", pause_detail="x")) == {
        "values": {"pause_reason": None, "pause_detail": None}
    }
    assert handlers._clear_pause_reason_kwargs(row("resuming")) == {}


def test_failure_detail_is_readable():
    assert handlers._failure_pause_detail("") == "Paused after repeated errors. Resume when it's fixed."
    assert handlers._failure_pause_detail("Boom\n   happened").endswith("Last error: Boom happened")


@pytest.fixture()
def pause_harness(monkeypatch):
    import core.container as container_module

    running = row("running")
    pausing = row("pausing")
    paused = row("paused")
    service = SimpleNamespace(
        database=SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=running)),
        transition=AsyncMock(side_effect=[pausing, paused]),
    )
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr(handlers, "_reconcile_control", AsyncMock(return_value=(running, None)))
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(return_value={}))
    monkeypatch.setattr(handlers, "_update_controller_state", AsyncMock(return_value={}))
    monkeypatch.setattr(handlers, "_set_cron_pause", AsyncMock(return_value=0))
    monkeypatch.setattr(handlers, "_signal_generation_workflows", AsyncMock(return_value=0))
    workflow_service = SimpleNamespace(pause_deployment=MagicMock(), update_trigger_pause_status=AsyncMock(return_value=0))
    monkeypatch.setattr(container_module.container, "workflow_service", lambda: workflow_service)
    import services.status_broadcaster as status_broadcaster

    monkeypatch.setattr(
        status_broadcaster,
        "get_status_broadcaster",
        lambda: SimpleNamespace(update_workflow_status=AsyncMock(), update_deployment_status=AsyncMock()),
    )
    return service


async def test_a_server_side_pause_records_its_reason(pause_harness):
    await handlers.handle_pause_workflow(
        {"workflow_id": "wf", "expected_revision": 2, "_pause_reason": "failures", "_pause_detail": "Paused after repeated errors."},
        None,
    )
    first = pause_harness.transition.await_args_list[0]
    assert first.kwargs["status"] == "pausing"
    assert first.kwargs["values"] == {"pause_reason": "failures", "pause_detail": "Paused after repeated errors."}


async def test_a_client_cannot_claim_an_automatic_pause(pause_harness):
    socket = SimpleNamespace(state=SimpleNamespace(user_id="owner"))
    await handlers.handle_pause_workflow({"workflow_id": "wf", "expected_revision": 2, "_pause_reason": "failures"}, socket)
    assert "values" not in pause_harness.transition.await_args_list[0].kwargs


async def test_the_circuit_breaker_and_recovery_pass_their_reasons(monkeypatch):
    pause = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(handlers, "handle_pause_workflow", pause)
    service = SimpleNamespace(database=SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=row("paused"))))
    await handlers._pause_for_recovery(service, row("running"), reason="unclean_shutdown")
    assert pause.await_args.args[0]["_pause_reason"] == "recovery"

    import core.container as container_module

    monkeypatch.setattr(container_module.container, "settings", MagicMock(return_value=SimpleNamespace(workflow_control_pause_on_failure=True)))
    monkeypatch.setattr(handlers, "_control_service", lambda: SimpleNamespace(database=SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=row("running")))))
    monkeypatch.setattr(handlers, "_pause_on_failure_threshold", lambda: 1)
    await handlers.pause_generation_on_failure(workflow_id="wf", reason="API key rejected")
    request = pause.await_args.args[0]
    assert request["_pause_reason"] == "failures"
    assert "API key rejected" in request["_pause_detail"]


# ---- the monthly budget --------------------------------------------------------

BUDGET_DETAIL = "Reached this month's budget of $20.00. Raise it in Settings > Billing, then resume."


def controls(monkeypatch, latest):
    """The control service answering ``latest`` as the workflow's newest row."""
    service = SimpleNamespace(database=SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=latest)))
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    return service


@pytest.fixture()
def pause(monkeypatch):
    pause = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(handlers, "handle_pause_workflow", pause)
    return pause


async def test_a_running_employee_is_paused_for_its_budget(monkeypatch, pause):
    controls(monkeypatch, row("running"))

    assert await handlers.pause_generation_for_budget(workflow_id="wf", detail=BUDGET_DETAIL) is True

    request = pause.await_args.args[0]
    assert request["_pause_reason"] == "budget"
    assert request["_pause_detail"] == BUDGET_DETAIL


@pytest.mark.parametrize("status", ["pausing", "paused"])
async def test_an_employee_already_stopping_is_left_alone(monkeypatch, pause, status):
    controls(monkeypatch, row(status))

    assert await handlers.pause_generation_for_budget(workflow_id="wf", detail=BUDGET_DETAIL) is True
    pause.assert_not_awaited()


async def test_a_run_with_no_generation_is_never_paused(monkeypatch, pause):
    controls(monkeypatch, None)

    assert await handlers.pause_generation_for_budget(workflow_id="wf", detail=BUDGET_DETAIL) is False
    pause.assert_not_awaited()


async def test_a_budget_pause_that_fails_answers_false(monkeypatch, pause):
    controls(monkeypatch, row("running"))
    pause.side_effect = RuntimeError("temporal down")

    assert await handlers.pause_generation_for_budget(workflow_id="wf", detail=BUDGET_DETAIL) is False


def test_the_budget_is_a_reason_the_server_may_give():
    assert handlers._automatic_pause_values({"_pause_reason": "budget", "_pause_detail": BUDGET_DETAIL}) == {
        "pause_reason": "budget",
        "pause_detail": BUDGET_DETAIL,
    }


@pytest.fixture()
def resume_harness(monkeypatch):
    """A workflow paused for its budget, about to be resumed. The transition
    to ``resuming`` raises, which ends the handler at the point the tests
    look at: whether the resume got that far."""
    paused = row("paused", pause_reason="budget", pause_detail=BUDGET_DETAIL)
    service = controls(monkeypatch, paused)
    service.transition = AsyncMock(side_effect=RuntimeError("the resume went ahead"))
    monkeypatch.setattr(handlers, "_reconcile_control", AsyncMock(return_value=(paused, None)))
    monkeypatch.setattr(handlers, "_control_payload", AsyncMock(return_value={}))
    return service


async def test_resume_is_refused_while_the_employee_is_over_its_budget(monkeypatch, resume_harness):
    import services.usage as usage

    monkeypatch.setattr(usage, "monthly_budget_reached", AsyncMock(return_value=20.0))

    reply = await handlers.handle_resume_workflow({"workflow_id": "wf", "expected_revision": 2}, None)

    assert reply["success"] is False
    assert reply["error"] == "monthly_budget_reached"
    assert reply["message"] == BUDGET_DETAIL
    resume_harness.transition.assert_not_awaited()


async def test_resume_goes_ahead_once_the_budget_allows_it(monkeypatch, resume_harness):
    import services.usage as usage

    monkeypatch.setattr(usage, "monthly_budget_reached", AsyncMock(return_value=None))

    reply = await handlers.handle_resume_workflow({"workflow_id": "wf", "expected_revision": 2}, None)

    resume_harness.transition.assert_awaited_once()
    assert reply.get("error") != "monthly_budget_reached"
