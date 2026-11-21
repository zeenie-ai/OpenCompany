"""Exercise durable wake delivery with the real store and a fake Temporal client."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from temporalio.common import SearchAttributeKey, TypedSearchAttributes, WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.testing import ActivityEnvironment

from models.agent_instances import AgentDelivery, AgentInstance
from services.agent_instances import runtime
from services.temporal.agent_instance_workflow import dispatch_wait


@pytest.fixture
async def wake_setup(real_database, monkeypatch):
    control = {
        "workflow_id": "wf",
        "generation": 3,
        "execution_id": "execution",
        "root_execution_id": "root",
        "execution_control_version": 1,
        "controller_workflow_id": "workflow-control-wf-g3",
    }
    async with real_database.reserved_session() as session:
        session.add(AgentInstance(
            id="instance", workflow_id="wf", generation=3, team_id="team", job_id="job",
            template_node_id="lead", config={"runtime_context": {**control, "nodes": []}},
        ))
        session.add(AgentDelivery(
            id="delivery", recipient_id="instance", team_id="team", generation=3,
            sequence=1, content="Resume this job", fingerprint="delivery-fingerprint",
        ))
        await session.commit()
    handle = SimpleNamespace(result=AsyncMock(return_value={"success": True, "response": "done"}))
    client = SimpleNamespace(start_workflow=AsyncMock(return_value=handle))
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(temporal_task_queue="test-tasks"))
    monkeypatch.setattr("core.container.container", SimpleNamespace(
        database=lambda: real_database,
        temporal_client=lambda: SimpleNamespace(client=client),
    ))
    return SimpleNamespace(database=real_database, control=control, handle=handle, client=client)


@pytest.mark.parametrize("workflow_id", ["wf", ""])
async def test_wake_starts_the_existing_instance_identity_with_typed_visibility(wake_setup, workflow_id):
    setup = wake_setup
    if not workflow_id:
        async with setup.database.reserved_session() as session:
            instance = await session.get(AgentInstance, "instance")
            instance.workflow_id = workflow_id
            await session.commit()

    handle = await runtime.wake_instance(
        "instance", client=setup.client, database=setup.database,
        context={"execution_id": "current-execution", "instance_id": "cannot-replace-owner"},
    )

    assert handle is setup.handle
    setup.client.start_workflow.assert_awaited_once()
    args, options = setup.client.start_workflow.await_args
    assert args == ("AgentInstanceSessionWorkflow", {
        **setup.control, "execution_id": "current-execution", "instance_id": "instance",
    })
    assert options["id"] == "agent-instance:instance"
    assert options["task_queue"] == "test-tasks"
    assert options["id_reuse_policy"] == WorkflowIDReusePolicy.ALLOW_DUPLICATE
    assert options["id_conflict_policy"] == WorkflowIDConflictPolicy.USE_EXISTING
    assert options["start_signal"] == "wake"
    assert options["start_signal_args"] == [{}]
    if workflow_id:
        attributes = options["search_attributes"]
        assert isinstance(attributes, TypedSearchAttributes)
        assert attributes[SearchAttributeKey.for_keyword("EventWorkflowId")] == workflow_id
    else:
        assert "search_attributes" not in options


@pytest.mark.parametrize("state", ["closed", "halted"])
async def test_wake_does_not_restart_a_closed_or_halted_instance(wake_setup, state):
    setup = wake_setup
    async with setup.database.reserved_session() as session:
        instance = await session.get(AgentInstance, "instance")
        instance.state = state
        await session.commit()

    assert await runtime.wake_instance("instance", client=setup.client, database=setup.database) is None
    setup.client.start_workflow.assert_not_awaited()


async def test_reconciliation_retries_the_real_wake_path_without_consuming_queued_delivery(wake_setup):
    setup = wake_setup
    setup.client.start_workflow.side_effect = [RuntimeError("temporary transport failure"), setup.handle]
    await runtime.wake_instances({"instance_wake": ["instance"]}, client=setup.client, database=setup.database)

    assert await runtime.reconcile_pending(client=setup.client, database=setup.database) == 1
    assert setup.client.start_workflow.await_count == 2
    assert all(call.kwargs["id"] == "agent-instance:instance" for call in setup.client.start_workflow.await_args_list)
    async with setup.database.get_session() as session:
        delivery = await session.get(AgentDelivery, "delivery")
        instance = await session.get(AgentInstance, "instance")
        assert delivery.state == "queued"
        assert instance.active_run_id is None


async def test_dispatch_wait_uses_the_real_wake_path_and_returns_session_result(wake_setup):
    setup = wake_setup
    result = await ActivityEnvironment().run(dispatch_wait, {"instance_id": "instance"})

    assert result == {"success": True, "response": "done", "instance_managed": True}
    setup.client.start_workflow.assert_awaited_once()
    setup.handle.result.assert_awaited_once()
