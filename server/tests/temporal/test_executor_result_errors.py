"""``TemporalExecutor.execute_workflow`` reports the orchestrator's errors.

MachinaWorkflow returns ``errors`` (a list of ``{node_id, error}``); the
executor used to read a singular ``error`` key that never existed, so
every failed run came back with an empty error list.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.temporal.executor import TemporalExecutor

pytestmark = pytest.mark.unit


def _executor(workflow_result: dict) -> TemporalExecutor:
    client = SimpleNamespace(execute_workflow=AsyncMock(return_value=workflow_result))
    return TemporalExecutor(client=client, task_queue="test-queue")


async def test_failed_run_reports_each_node_error():
    executor = _executor(
        {
            "success": False,
            "outputs": {},
            "execution_trace": [],
            "errors": [{"node_id": "py-1", "error": "credential expired", "error_type": "NodeUserError"}],
        }
    )

    result = await executor.execute_workflow(workflow_id="wf-1", nodes=[], edges=[])

    assert result["success"] is False
    assert result["errors"] == ["py-1: credential expired"]


async def test_successful_run_reports_no_errors():
    executor = _executor({"success": True, "outputs": {}, "execution_trace": ["py-1"], "errors": None})

    result = await executor.execute_workflow(workflow_id="wf-1", nodes=[], edges=[])

    assert result["success"] is True
    assert result["errors"] == []
