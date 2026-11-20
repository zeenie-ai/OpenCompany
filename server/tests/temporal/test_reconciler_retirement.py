"""Upgrade the timer-based reconciler without creating replacement history."""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio import activity
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import CancelledError
from temporalio.service import RPCError, RPCStatusCode

from services.agent_instances.runtime import RECONCILER_ID, retire_legacy_reconciler
from services.temporal.agent_instance_workflow import AgentInstanceReconcilerWorkflow


@pytest.mark.parametrize("status, workflow_type", [
    (WorkflowExecutionStatus.COMPLETED, "AgentInstanceReconcilerWorkflow"),
    (WorkflowExecutionStatus.RUNNING, "AgentInstanceSessionWorkflow"),
])
async def test_retirement_only_targets_the_running_legacy_scanner(status, workflow_type):
    handle = AsyncMock()
    handle.describe.return_value = SimpleNamespace(status=status, workflow_type=workflow_type)
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    await retire_legacy_reconciler(client)
    client.get_workflow_handle.assert_called_once_with(RECONCILER_ID)
    handle.cancel.assert_not_awaited()


async def test_transient_lookup_failure_can_be_retried():
    client = MagicMock()
    handle = AsyncMock()
    client.get_workflow_handle.return_value = handle
    handle.describe.side_effect = RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
    with pytest.raises(RPCError):
        await retire_legacy_reconciler(client)
    handle.cancel.assert_not_awaited()


async def test_idle_scans_leave_temporal_empty_and_legacy_run_closes(real_database):
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker
    from services.agent_instances.runtime import scheduled_reconcile_pending

    cli = os.environ.get("TEMPORAL_TEST_CLI")
    if not cli or not Path(cli).is_file():
        pytest.skip("Set TEMPORAL_TEST_CLI to the pinned Temporal CLI binary")
    swept = asyncio.Event()

    @activity.defn(name="agent.instance.reconcile")
    async def legacy_pass(payload: dict) -> dict:
        swept.set()
        return {"woken": 0}

    async with await WorkflowEnvironment.start_local(dev_server_existing_path=cli) as env:
        # Fresh installs can scan and check for a previous owner without
        # creating any Temporal execution, even a short-lived one.
        client = env.client
        assert await scheduled_reconcile_pending(client=client, database=real_database) == 0
        await retire_legacy_reconciler(client)
        assert (await client.count_workflows()).count == 0

        async with Worker(client, task_queue="retire-test", workflows=[AgentInstanceReconcilerWorkflow],
                          activities=[legacy_pass]):
            old = await client.start_workflow("AgentInstanceReconcilerWorkflow", {},
                id=RECONCILER_ID, task_queue="retire-test")
            await asyncio.wait_for(swept.wait(), timeout=30)
            await retire_legacy_reconciler(client)
            with pytest.raises(WorkflowFailureError) as closed:
                await asyncio.wait_for(old.result(), timeout=30)
            assert isinstance(closed.value.cause, CancelledError)
            before = await old.describe()
            assert before.status == WorkflowExecutionStatus.CANCELED
            await retire_legacy_reconciler(client)
            await scheduled_reconcile_pending(client=client, database=real_database)
            after = await old.describe()
            assert after.run_id == before.run_id and after.history_length == before.history_length
