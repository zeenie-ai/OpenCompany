"""1b.13: a bug in workflow code fails the task and records no exit cleanup.

A plain exception from workflow code fails the workflow task, so the run
stays open and a fixed release replays it. Cleanup recorded on the way out
would break that replay, and a per-event handler that swallowed the bug would
hide it. ``services/temporal/_exits.is_workflow_bug`` names those exceptions.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio import workflow
from temporalio.exceptions import ApplicationError, CancelledError as TemporalCancelledError, WorkflowAlreadyStartedError

from services.temporal._exits import is_workflow_bug


class _Continued(BaseException):
    """Stands in for continue-as-new, which the SDK raises as a BaseException."""


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("bug"),
        ValueError("bug"),
        KeyError("bug"),
        AssertionError("bug"),
        workflow.NondeterminismError("bug"),
        workflow.ReadOnlyContextError("bug"),
    ],
)
def test_a_plain_exception_is_a_bug(exc):
    assert is_workflow_bug(exc)


@pytest.mark.parametrize(
    "exc",
    [
        ApplicationError("failure"),
        WorkflowAlreadyStartedError("id", "type"),
        TemporalCancelledError("cancelled"),
        TimeoutError(),
        asyncio.TimeoutError(),
        asyncio.CancelledError(),
        _Continued(),
        GeneratorExit(),
    ],
)
def test_failures_cancellation_and_rollover_are_not_bugs(exc):
    assert not is_workflow_bug(exc)


def test_nothing_configures_failure_exception_types():
    # The pure rule mirrors the SDK only while no workflow, worker or plugin
    # adds failure types; one that did would turn those bugs into failures.
    from pathlib import Path

    import nodes  # noqa: F401  # plugins register their Temporal plugins on import
    from services.temporal.plugin_registry import temporal_plugins
    from tests._source_tree import server_python_files

    offenders = [
        path
        for path in server_python_files("tests", "scripts")
        if "failure_exception_types" in Path(path).read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
    assert all(plugin.workflow_failure_exception_types is None for plugin in temporal_plugins())


@pytest.fixture
def no_handler_wait(monkeypatch):
    monkeypatch.setattr(workflow, "wait_condition", AsyncMock())
    monkeypatch.setattr(workflow, "all_handlers_finished", lambda: True)


def _control():
    return SimpleNamespace(bind=lambda _data: None, enabled=True, register_root=AsyncMock(), unregister_root=AsyncMock())


@pytest.mark.parametrize(
    ("exc", "cleaned_up"),
    [(RuntimeError("bug"), False), (ApplicationError("failure"), True), (asyncio.CancelledError(), True)],
)
async def test_a_finally_only_run_skips_its_cleanup_for_a_bug(no_handler_wait, exc, cleaned_up):
    from services.temporal.workflow import MachinaWorkflow

    run = MachinaWorkflow()
    run._execution_control = _control()
    run._run_graph = AsyncMock(side_effect=exc)
    with pytest.raises(type(exc)):
        await run.run({})
    assert run._execution_control.unregister_root.await_count == (1 if cleaned_up else 0)


@pytest.mark.parametrize(
    ("exc", "cleaned_up"),
    [(RuntimeError("bug"), False), (ApplicationError("failure"), True)],
)
async def test_a_run_with_arms_records_no_job_failure_for_a_bug(monkeypatch, exc, cleaned_up):
    from services.temporal.agent_workflow import AgentWorkflow
    from tests.services.employees.test_runtime_v2 import admitted

    context, _ = admitted()
    context["employee_job_id"] = "job"
    agent = AgentWorkflow()
    agent._run_impl = AsyncMock(side_effect=exc)
    execute = AsyncMock(return_value={"saved": True})
    monkeypatch.setattr("services.temporal.agent_workflow.workflow.patched", lambda _: True)
    monkeypatch.setattr("services.temporal.agent_workflow.workflow.execute_activity", execute)
    with pytest.raises(type(exc)):
        await agent.run(context)
    assert [call.args[0] for call in execute.await_args_list] == (["employee.job.failed"] if cleaned_up else [])


@pytest.mark.parametrize(
    ("exc", "stopped"),
    [(RuntimeError("bug"), False), (ApplicationError("conflict", type="PiProtocolConflict"), True), (_Continued(), True)],
)
async def test_pi_stops_its_driver_only_without_a_bug(exc, stopped):
    from services.temporal.pi_workflow import PiCodingAgentWorkflow

    context = {"node_type": "coding_agent", "pi_harness_version": 1, "node_id": "coder"}
    pi = PiCodingAgentWorkflow(context)
    pi._execution_control = SimpleNamespace(bind=lambda _data: None, enabled=False)
    pi._run_impl = AsyncMock(side_effect=exc)
    pi._clear_failed_skills = AsyncMock()
    pi._emit_phase = AsyncMock()
    pi._stop_driver = AsyncMock()
    if isinstance(exc, ApplicationError):
        # A protocol conflict still ends the turn as a failure result.
        assert (await pi.run(context))["error_type"] == "PiProtocolConflict"
    else:
        with pytest.raises(type(exc)):
            await pi.run(context)
    assert pi._stop_driver.await_count == (1 if stopped else 0)
