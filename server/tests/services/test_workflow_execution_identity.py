"""Execution-correlation contracts shared by workflow entry points."""

from __future__ import annotations

import time
from types import MethodType

import pytest

from services.workflow import WorkflowService


@pytest.mark.asyncio
async def test_sequential_workflow_uses_one_generated_id_for_every_node():
    service = WorkflowService.__new__(WorkflowService)
    service._settings = {"stop_on_error": False}

    observed: list[str] = []

    async def fake_execute_node(self, **kwargs):
        observed.append(kwargs["execution_id"])
        return {"success": True, "result": {"node_id": kwargs["node_id"]}}

    service.execute_node = MethodType(fake_execute_node, service)
    nodes = [
        {"id": "start-1", "type": "start", "data": {}},
        {"id": "console-1", "type": "console", "data": {}},
    ]
    edges = [{"source": "start-1", "target": "console-1"}]

    result = await service._execute_sequential(
        nodes,
        edges,
        "session-1",
        None,
        time.time(),
        "workflow-1",
    )

    assert result["execution_id"]
    assert observed == [result["execution_id"], result["execution_id"]]
