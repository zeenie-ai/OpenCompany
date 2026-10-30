"""Workspace admission and cancellation boundaries without device access."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services import node_invocations as service
from services.plugin import NodeUserError

SUBMISSION = "63cb635f-c758-42bc-a9b6-673655a72fa0"


@pytest.fixture
def admission(monkeypatch):
    from core import config
    from services import node_registry
    from services.authz import workflow_node
    from services.plugin import deps
    from services.deployment import handlers
    from services import status_broadcaster

    database = SimpleNamespace(get_latest_workflow_control=AsyncMock(return_value=None),
                               get_node_parameters=AsyncMock(return_value={"max_steps": 40}))
    plugin = SimpleNamespace(workspace_task=True, type="phone", version=1, task_queue="device",
                             start_to_close_timeout=timedelta(hours=3))
    monkeypatch.setattr(config, "Settings", lambda: SimpleNamespace(temporal_worker_pool_enabled=False,
                                                                    temporal_task_queue="main"))
    monkeypatch.setattr(node_registry, "get_node_class", lambda _: plugin)
    monkeypatch.setattr(deps, "get_database", lambda: database)
    graph = {"nodes": [{"id": "phone", "type": "phone"}], "edges": []}
    monkeypatch.setattr(workflow_node, "resolve_workflow_node", AsyncMock(return_value=(SimpleNamespace(name="saved"), graph, graph["nodes"][0])))
    status = AsyncMock(return_value={"epoch": 3, "resetting": False})
    update = AsyncMock(return_value={"status": "accepted"})
    monkeypatch.setattr(service, "controller_status", status)
    monkeypatch.setattr(service, "_controller_update", update)
    client = SimpleNamespace(get_workflow_handle=Mock())
    monkeypatch.setattr(service, "temporal_client", lambda: client)
    monkeypatch.setattr(handlers, "_with_runtime_counts", AsyncMock(return_value={}))
    broadcast = AsyncMock()
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: SimpleNamespace(broadcast=broadcast))
    return SimpleNamespace(database=database, status=status, update=update, broadcast=broadcast, client=client)


async def test_submit_uses_saved_context_and_admission_epoch(admission):
    result = await service.submit("owner", "wf", "phone", "Read the screen", SUBMISSION)
    payload = admission.update.await_args.args[2]
    assert payload["admission_epoch"] == 3
    assert payload["context"]["generation"] == 0
    assert payload["context"]["user_id"] == "owner"
    assert payload["context"]["node_data"]["prompt"] == "Read the screen"
    assert payload["task_queue"] == "main"
    assert result["run_id"] == service.invocation_id("owner", "wf", "phone", SUBMISSION)


@pytest.mark.parametrize("source", ["controller", "generation"])
async def test_reset_blocks_admission_from_either_durable_source(admission, source):
    if source == "controller":
        admission.status.return_value["resetting"] = True
    else:
        admission.database.get_latest_workflow_control.return_value = SimpleNamespace(status="resetting")
    with pytest.raises(NodeUserError, match="resetting"):
        await service.submit("owner", "wf", "phone", "Read", SUBMISSION)
    admission.update.assert_not_awaited()


async def test_retry_uses_same_child_but_new_update_identity(admission):
    for _ in range(2):
        await service.submit("owner", "wf", "phone", "Read", SUBMISSION)
    first, second = admission.update.await_args_list
    assert first.args[2]["context"]["execution_id"] == second.args[2]["context"]["execution_id"]
    assert first.kwargs["update_id"] != second.kwargs["update_id"]


async def test_a_submit_the_controller_has_not_answered_says_still_pending(admission):
    # 1b.15: the request waits 15 s; the same submission ID again is safe.
    from services.temporal.update_calls import UpdatePending

    admission.update.side_effect = UpdatePending("no answer in 15 s")
    with pytest.raises(NodeUserError, match="still pending"):
        await service.submit("owner", "wf", "phone", "Read", SUBMISSION)
    assert admission.update.await_args.kwargs["seconds"] == service.SUBMIT_UPDATE_SECONDS
    admission.broadcast.assert_not_awaited()


async def test_a_reset_the_controller_has_not_answered_stays_pending(admission, monkeypatch):
    from services import llm_wait
    from services.temporal.update_calls import UpdatePending

    closed = AsyncMock()
    monkeypatch.setattr(llm_wait, "close_diagnostic_waits", closed)
    admission.update.side_effect = UpdatePending("no answer in 240 s")
    with pytest.raises(UpdatePending):
        await service.reset("wf", "reset-1")
    assert admission.update.await_args.kwargs["seconds"] == service.RESET_UPDATE_SECONDS
    closed.assert_not_awaited()  # the cleanup is not known done


async def test_the_controller_update_is_bounded(monkeypatch):
    from core import config
    from services.temporal.update_calls import UpdatePending

    async def hangs(*_args, **_kwargs):
        await asyncio.sleep(60)

    monkeypatch.setattr(config, "Settings", lambda: SimpleNamespace(temporal_task_queue="main"))
    monkeypatch.setattr(service, "temporal_client", lambda: SimpleNamespace(execute_update_with_start_workflow=hangs))
    with pytest.raises(UpdatePending):
        await service._controller_update("wf", "submit", {}, update_id="submit:1", seconds=0.01)


async def test_notification_failure_does_not_undo_committed_admission(admission):
    admission.broadcast.side_effect = RuntimeError("socket unavailable")
    assert (await service.submit("owner", "wf", "phone", "Read", SUBMISSION))["status"] == "accepted"


async def test_reset_retry_resumes_latched_request_with_fresh_rpc_id(admission):
    admission.status.return_value = {"reset_request_id": "original-reset", "resetting": True}
    await service.reset("wf", "new-reset")
    await service.reset("wf", "retry-reset")
    first, second = admission.update.await_args_list
    assert first.args[2] == second.args[2] == "original-reset"
    assert first.kwargs["update_id"] != second.kwargs["update_id"]


async def test_reset_during_saved_graph_resolution_preserves_old_epoch(admission, monkeypatch):
    from services.authz import workflow_node

    resolver = workflow_node.resolve_workflow_node
    async def resolve(*args):
        saved = await resolver(*args)
        # Simulate Reset completing while the database resolves the graph.
        admission.status.return_value = {"epoch": 4, "resetting": False}
        return saved
    monkeypatch.setattr(workflow_node, "resolve_workflow_node", resolve)
    await service.submit("owner", "wf", "phone", "Read", SUBMISSION)
    assert admission.update.await_args.args[2]["admission_epoch"] == 3


async def test_cancel_waits_for_workflow_close():
    gate = asyncio.Event()
    handle = SimpleNamespace(cancel=AsyncMock(), result=AsyncMock(side_effect=gate.wait))
    pending = asyncio.create_task(service._cancel_and_wait(handle))
    await asyncio.sleep(0)
    handle.cancel.assert_awaited_once()
    assert not pending.done()
    gate.set()
    await pending


async def test_confirmed_cancellation_does_not_require_another_rpc():
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import CancelledError

    handle = SimpleNamespace(cancel=AsyncMock(),
                             result=AsyncMock(side_effect=WorkflowFailureError(cause=CancelledError())),
                             describe=AsyncMock(side_effect=RuntimeError("connection dropped")))
    await service._cancel_and_wait(handle)
    handle.describe.assert_not_awaited()


async def test_frozen_phone_node_is_not_hidden_by_editable_replacement(monkeypatch):
    from services import node_registry

    monkeypatch.setattr(node_registry, "get_node_class", lambda kind: SimpleNamespace(workspace_task=kind == "phone"))
    database = SimpleNamespace(get_workflow=AsyncMock(return_value={"nodes": [{"id": "same", "type": "ordinary"}]}))
    control = SimpleNamespace(graph_snapshot={"nodes": [{"id": "same", "type": "phone"}]})
    _, nodes = await service.workspace_task_nodes(database, "wf", control)
    assert nodes == [{"id": "same", "type": "phone"}]


async def test_legacy_reset_cancels_only_matching_roots(monkeypatch):
    matching = SimpleNamespace(query=AsyncMock(return_value={"workflow_id": "wf"}))
    other = SimpleNamespace(query=AsyncMock(return_value={"workflow_id": "other"}))
    async def executions(**_):
        for key, parent in [("mine", None), ("other", None), ("child", "controller")]:
            yield SimpleNamespace(id=key, run_id=key + "-run", parent_id=parent)
    client = SimpleNamespace(list_workflows=executions, get_workflow_handle=Mock(side_effect=lambda key, **_: {"mine": matching, "other": other}[key]))
    monkeypatch.setattr(service, "temporal_client", lambda: client)
    cancel = AsyncMock()
    monkeypatch.setattr(service, "_cancel_and_wait", cancel)
    assert await service.cancel_legacy_invocations("wf") == 1
    cancel.assert_awaited_once_with(matching)


async def test_node_executor_lets_an_activity_see_a_cancel(monkeypatch):
    # The activity wrapper reports a call that may have happened, and
    # Temporal must see a cancel, not a completed failure.
    from temporalio.testing import ActivityEnvironment

    from services import node_executor as module

    executor = module.NodeExecutor.__new__(module.NodeExecutor)
    executor._prepare_parameters = AsyncMock(return_value={})
    executor._dispatch = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(module, "get_node_class", lambda _: SimpleNamespace(workspace_task=False))
    with pytest.raises(asyncio.CancelledError):
        await ActivityEnvironment().run(executor.execute, "node", "ordinary", {}, {})


@pytest.mark.parametrize("workspace", [True, False])
async def test_node_executor_preserves_workspace_cancellation(monkeypatch, workspace):
    from services import node_executor as module

    executor = module.NodeExecutor.__new__(module.NodeExecutor)
    executor._prepare_parameters = AsyncMock(return_value={})
    executor._dispatch = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(module, "get_node_class", lambda _: SimpleNamespace(workspace_task=workspace))
    if workspace:
        with pytest.raises(asyncio.CancelledError):
            await executor.execute("phone", "device", {}, {})
    else:
        assert (await executor.execute("phone", "ordinary", {}, {}))["error"] == "Cancelled"
