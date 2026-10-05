"""Authenticated user propagation through deployment-owned execution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.deployment.manager import DeploymentManager
from services.deployment.state import DeploymentState


def _manager(*, execute_workflow=None) -> DeploymentManager:
    database = MagicMock()
    database.get_workflow = AsyncMock(
        return_value=SimpleNamespace(slug="workflow"),
    )
    broadcaster = MagicMock()
    broadcaster.update_node_status = AsyncMock()
    return DeploymentManager(
        database=database,
        execute_workflow_fn=execute_workflow or AsyncMock(),
        store_output_fn=AsyncMock(),
        broadcaster=broadcaster,
    )


def test_deployment_state_defaults_to_owner():
    state = DeploymentState(
        deployment_id="deploy-1",
        workflow_id="workflow-1",
        is_running=True,
        nodes=[],
        edges=[],
        session_id="session",
    )

    assert state.user_id == "owner"
    assert state.to_dict()["user_id"] == "owner"


@pytest.mark.asyncio
async def test_deploy_captures_authenticated_user_in_state():
    manager = _manager()
    manager._load_settings = AsyncMock()
    manager._notify = AsyncMock()

    result = await manager.deploy(
        nodes=[],
        edges=[],
        workflow_id="workflow-1",
        user_id="account-31",
    )

    assert result["success"] is True
    assert manager._deployments["workflow-1"].user_id == "account-31"


@pytest.mark.asyncio
async def test_trigger_run_preserves_deployment_user():
    execute_workflow = AsyncMock(return_value={"success": True})
    manager = _manager(execute_workflow=execute_workflow)
    manager._deployments["workflow-1"] = DeploymentState(
        deployment_id="deploy-1",
        workflow_id="workflow-1",
        is_running=True,
        nodes=[
            {"id": "trigger-1", "type": "webhookTrigger", "data": {}},
            {"id": "node-1", "type": "console", "data": {}},
        ],
        edges=[
            {
                "source": "trigger-1",
                "target": "node-1",
                "targetHandle": "input-main",
            }
        ],
        session_id="session",
        user_id="account-32",
    )

    await manager._execute_from_trigger(
        "run-1",
        "trigger-1",
        {"event_data": {"value": 1}},
        "workflow-1",
    )

    assert execute_workflow.await_args.kwargs["user_id"] == "account-32"


async def test_deploy_captures_immutable_parameter_snapshot_for_future_runs():
    manager = _manager()
    manager._load_settings = AsyncMock()
    manager._notify = AsyncMock()
    parameters = {"send": {"recipient": "original"}}
    await manager.deploy(nodes=[], edges=[], workflow_id="frozen", graph_version=2, generation=1, parameter_snapshot=parameters)
    parameters["send"]["recipient"] = "changed"
    assert manager._deployments["frozen"].parameter_snapshot["send"]["recipient"] == "original"
