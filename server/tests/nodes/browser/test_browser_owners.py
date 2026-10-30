"""Shared owner/task claims against SQLAlchemy; no browser processes."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.browser_owners import BrowserOwner, BrowserProfileOwner
from models.browser_profiles import BrowserProfileRow
from services import browser_owners as owners
from services.plugin.base import NodeUserError
from nodes.browser._session import ProfileController


async def native_reset_client(scope, *, status=None, workflow_type="AgentWorkflow"):
    from temporalio.api.enums.v1 import EventType
    from temporalio.api.history.v1 import HistoryEvent, WorkflowExecutionStartedEventAttributes
    from temporalio.api.common.v1 import Payloads
    from temporalio.client import WorkflowExecutionStatus
    from temporalio.converter import DataConverter

    converter = DataConverter.default
    opening = HistoryEvent(event_id=1, event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED,
        workflow_execution_started_event_attributes=WorkflowExecutionStartedEventAttributes(
            input=Payloads(payloads=await converter.encode([scope]))))
    async def history(**kwargs):
        assert kwargs == {"page_size": 1}
        yield opening
    handle = SimpleNamespace(describe=AsyncMock(return_value=SimpleNamespace(
        workflow_type=workflow_type, run_id="terminated-run",
        status=status or WorkflowExecutionStatus.TERMINATED)), fetch_history_events=history)
    return SimpleNamespace(get_workflow_handle=Mock(return_value=handle), data_converter=converter)


def authorized_browser_node():
    """resolve_browser_node's (graph, node) for tests that skip the saved-graph checks."""
    return AsyncMock(return_value=({}, {"id": "browser", "data": {"label": "Browser"}}))


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for table in (BrowserOwner.__table__, BrowserProfileOwner.__table__, BrowserProfileRow.__table__):
            await connection.run_sync(table.create)
    db = SimpleNamespace(engine=engine, get_session=async_sessionmaker(engine, expire_on_commit=False))
    async with db.get_session() as session:
        session.add(BrowserOwner(owner_id="alpha", runtime_epoch=owners.RUNTIME_EPOCH, base_url="http://alpha:8000", heartbeat_at=time.time()))
        session.add(BrowserProfileOwner(profile_id="profile", principal_id="owner", owner_id="alpha"))
        session.add(BrowserProfileRow(id="profile", owner_id="owner", name="Work"))
        await session.commit()
    config = SimpleNamespace(distributed_mode=True, browser_replica_id="alpha", browser_replica_url="http://alpha:8000", browser_router_secret="test-forward-secret", secret_key="test-shared-secret")
    controller = ProfileController("profile", "Work")
    runtime = SimpleNamespace(controller_for=lambda _: controller, controller=lambda _: controller)
    with patch.object(owners, "settings", return_value=config), patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime):
        yield db, controller
    await engine.dispose()


async def test_task_claim_blocks_other_tasks_and_cleanup_is_token_matched(database):
    db, controller = database
    binding = await owners.bind_profile(db, "profile", "owner")
    claimed = await owners.claim_browser_task(db, binding, "owner", "first")
    assert claimed["binding"]["owner_id"] == "alpha"
    with pytest.raises(NodeUserError, match="BrowserBusy"):
        await owners.claim_browser_task(db, binding, "owner", "second")
    assert (await owners.release_browser_task(db, binding, "second"))["released"] is False
    assert controller.task_id == "first"
    assert (await owners.release_browser_task(db, binding, "first"))["released"] is True
    await owners.claim_browser_task(db, binding, "owner", "second")
    assert controller.task_id == "second"


@pytest.mark.parametrize("workflow_type", ["AgentWorkflow", "MachinaWorkflow"])
async def test_generation_reset_retires_terminated_native_claim_and_interrupts_stuck_step(database, workflow_type):
    from services.browser_reset import cleanup_generation_browser_tasks
    from nodes.browser.browser import BrowserNode

    db, controller = database
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf", "node_id": "browser"}
    task_id = "native-agent-workflow"
    await owners.claim_browser_task(db, binding, "owner", task_id)
    session = SimpleNamespace(profile_id="profile", session_id="session")
    runtime = SimpleNamespace(controller=lambda _: controller, find_session=lambda *_: session,
        running=lambda _: running)
    interrupted = asyncio.Event()
    async def interrupt():
        interrupted.set()
    cli = SimpleNamespace(interrupt=AsyncMock(side_effect=interrupt), suspend_for_credentials=AsyncMock(return_value=True))
    running = SimpleNamespace(cli=cli)
    async def stuck_step():
        async with controller._op_lock:
            await interrupted.wait()
    step = asyncio.create_task(stuck_step())
    await asyncio.sleep(0)
    client = await native_reset_client({"workflow_id": "wf", "generation": 7}, workflow_type=workflow_type)
    control = SimpleNamespace(workflow_id="wf", generation=7, graph_snapshot={"nodes": []})
    with patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime), \
            patch("nodes.browser._runtime.peek_browser_runtime", return_value=runtime):
        assert await BrowserNode.reset_execution_state(node_id="browser", workflow_id="wf",
            execution_id="deployment", generation=7, graph={}, database=db) == {"reset": False, "managed_by_agent": True}
        assert await cleanup_generation_browser_tasks(db, client, control) == 1
        assert await cleanup_generation_browser_tasks(db, client, control) == 0
    await step
    cli.interrupt.assert_awaited_once()
    cli.suspend_for_credentials.assert_awaited_once()
    client.get_workflow_handle.assert_any_call(task_id, run_id="terminated-run")
    assert controller.task_id is None and controller.needs_observation
    async with db.get_session() as db_session:
        assert (await db_session.get(BrowserProfileOwner, "profile")).task_id is None


@pytest.mark.parametrize("scope", [
    {"workflow_id": "wf", "generation": 0},  # Independent Workspace task.
    {"workflow_id": "wf", "generation": 8},
    {"workflow_id": "other", "generation": 7},
    {"workflow_id": "wf", "generation": 7, "_browser_task_id": "unrelated-token"},
])
async def test_generation_reset_preserves_claims_outside_recorded_scope(database, scope):
    from services.browser_reset import cleanup_generation_browser_tasks

    db, controller = database
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf"}
    await owners.claim_browser_task(db, binding, "owner", "task")
    client = await native_reset_client(scope)
    with patch.object(owners, "cleanup_browser_task", AsyncMock()) as cleanup:
        assert await cleanup_generation_browser_tasks(db, client, SimpleNamespace(workflow_id="wf", generation=7)) == 0
    cleanup.assert_not_awaited()
    assert controller.task_id == "task"


async def test_generation_reset_keeps_claim_when_native_execution_remains_running(database):
    from services.browser_reset import cleanup_generation_browser_tasks
    from temporalio.client import WorkflowExecutionStatus

    db, controller = database
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf"}
    await owners.claim_browser_task(db, binding, "owner", "task")
    client = await native_reset_client({"workflow_id": "wf", "generation": 7}, status=WorkflowExecutionStatus.RUNNING)
    with patch.object(owners, "cleanup_browser_task", AsyncMock()) as cleanup, \
            pytest.raises(RuntimeError, match="still_running"):
        await cleanup_generation_browser_tasks(db, client, SimpleNamespace(workflow_id="wf", generation=7))
    cleanup.assert_not_awaited()
    assert controller.task_id == "task"


@pytest.mark.parametrize("receipt", [{"released": False}, {"released": True}])
async def test_generation_reset_requires_durable_owner_cleanup_receipt(database, receipt):
    from services.browser_reset import cleanup_generation_browser_tasks

    db, controller = database
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf"}
    await owners.claim_browser_task(db, binding, "owner", "task")
    client = await native_reset_client({"workflow_id": "wf", "generation": 7})
    with patch.object(owners, "cleanup_browser_task", AsyncMock(return_value=receipt)), \
            pytest.raises(RuntimeError, match="unacknowledged"):
        await cleanup_generation_browser_tasks(db, client, SimpleNamespace(workflow_id="wf", generation=7))
    assert controller.task_id == "task"


async def test_reset_owner_cleanup_never_interrupts_replacement_task(database):
    db, controller = database
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf"}
    await owners.claim_browser_task(db, binding, "owner", "replacement")
    cli = SimpleNamespace(interrupt=AsyncMock(), suspend_for_credentials=AsyncMock(return_value=True))
    runtime = SimpleNamespace(controller=lambda _: controller, running=lambda _: SimpleNamespace(cli=cli))
    with patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime):
        assert await owners.cleanup_browser_task(db, binding, "terminated-task", reset=True) == {"released": False}
    cli.interrupt.assert_not_awaited()
    assert controller.task_id == "replacement"


async def test_reset_owner_cleanup_waits_for_shielded_browser_start(database):
    db, controller = database
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf"}
    await owners.claim_browser_task(db, binding, "owner", "task")
    starting = asyncio.get_running_loop().create_future()
    cli = SimpleNamespace(interrupt=AsyncMock(), suspend_for_credentials=AsyncMock(return_value=True))
    runtime = SimpleNamespace(controller=lambda _: controller, _starting={"profile": starting},
        running=lambda _: SimpleNamespace(cli=cli) if starting.done() else None)
    with patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime):
        cleanup = asyncio.create_task(owners.cleanup_browser_task(db, binding, "task", reset=True))
        await asyncio.sleep(0.01)
        assert not cleanup.done() and controller.task_id == "task"
        starting.set_result(None)
        assert await cleanup == {"released": True}
    cli.interrupt.assert_awaited_once()
    cli.suspend_for_credentials.assert_awaited_once()


async def test_remote_reset_cleanup_forwards_exact_task_and_reset_intent(database):
    db, _ = database
    binding = await owners.bind_profile(db, "profile", "owner")
    forward = AsyncMock(return_value={"released": True})
    with patch.object(owners, "replica_id", return_value="other-replica"), \
            patch("nodes.browser._routing.forward_command", forward):
        assert await owners.cleanup_browser_task(db, binding, "native-task", reset=True) == {"released": True}
    forward.assert_awaited_once_with(binding, "owner", "cleanup_task",
        {"profile_id": "profile", "task_id": "native-task", "reset": True})


async def test_owner_command_preserves_authenticated_reset_cleanup_intent(database):
    import json
    from nodes.browser import _routing

    db, _ = database
    binding = await owners.bind_profile(db, "profile", "owner")
    body = json.dumps({"command": "cleanup_task", "data": {
        "profile_id": "profile", "task_id": "native-task", "reset": True}}).encode()
    request = SimpleNamespace(body=AsyncMock(return_value=body), headers={
        "X-Browser-Forward": owners.sign_forward("owner", "POST", _routing.COMMAND_PATH, body)})
    cleanup = AsyncMock(return_value={"released": True})
    with patch.object(_routing, "get_database", return_value=db), \
            patch.object(_routing, "command_binding", AsyncMock(return_value=binding)), \
            patch.object(owners, "cleanup_browser_task", cleanup):
        assert await _routing.owner_command(request) == {"released": True}
    cleanup.assert_awaited_once_with(db, {**binding, "principal_id": "owner"}, "native-task", reset=True)


async def test_direct_browser_reset_interrupts_unclaimed_terminated_activity_before_releasing_lease(database):
    from nodes.browser.browser import BrowserNode

    db, controller = database
    session = SimpleNamespace(profile_id="profile", session_id="session")
    controller.lease_session = session
    interrupted = asyncio.Event()
    cli = SimpleNamespace(interrupt=AsyncMock(side_effect=lambda: interrupted.set()),
        suspend_for_credentials=AsyncMock(return_value=True))
    runtime = SimpleNamespace(controller=lambda _: controller, find_session=lambda *_: session,
        running=lambda _: SimpleNamespace(cli=cli))
    async def step():
        async with controller._op_lock:
            await interrupted.wait()
    activity = asyncio.create_task(step())
    await asyncio.sleep(0)
    with patch("nodes.browser._runtime.peek_browser_runtime", return_value=runtime):
        assert await BrowserNode.reset_execution_state(node_id="browser", workflow_id="wf",
            execution_id="deployment", generation=7, graph={}, database=db) == {"reset": True}
    await activity
    assert controller.lease_session is None
    cli.interrupt.assert_awaited_once()
    cli.suspend_for_credentials.assert_awaited_once()


async def test_unavailable_owner_is_never_reassigned(database):
    db, _ = database
    async with db.get_session() as session:
        row = await session.get(BrowserOwner, "alpha")
        row.heartbeat_at = 0
        await session.commit()
    with patch.object(owners, "replica_id", return_value="beta"):
        binding = await owners.bind_profile(db, "profile", "owner")
    assert binding["owner_id"] == "alpha" and binding["available"] is False


@pytest.mark.parametrize("available", [False, True])
async def test_new_profile_preparation_uses_backend_registration_without_registering_worker(database, available):
    db, _ = database
    async with db.get_session() as session:
        backend = await session.get(BrowserOwner, "alpha")
        backend.runtime_epoch = "backend-runtime"
        backend.heartbeat_at = time.time() if available else 0
        session.add(BrowserProfileRow(id="new-profile", owner_id="owner", name="New"))
        await session.commit()
    register = AsyncMock(side_effect=AssertionError("Orchestration workers must not register browser runtimes"))
    with patch.object(owners, "RUNTIME_EPOCH", "generic-worker-runtime"), patch.object(owners, "register_browser_owner", register):
        binding = await owners.bind_profile(db, "new-profile", "owner")
        assert binding["owner_id"] == "alpha"
        assert binding["runtime_epoch"] == "backend-runtime"
        assert binding["available"] is available
        with patch.object(owners, "replica_id", return_value="beta"):
            assert (await owners.bind_profile(db, "new-profile", "owner"))["owner_id"] == "alpha"
    register.assert_not_awaited()
    async with db.get_session() as session:
        assert (await session.get(BrowserOwner, "alpha")).runtime_epoch == "backend-runtime"
        assert (await session.get(BrowserProfileOwner, "new-profile")).owner_id == "alpha"


async def test_new_profile_requires_preexisting_backend_registration(database):
    db, _ = database
    register = AsyncMock()
    with patch.object(owners, "replica_id", return_value="unregistered"), \
            patch.object(owners, "register_browser_owner", register), \
            pytest.raises(NodeUserError, match="owner is not registered"):
        await owners.bind_profile(db, "new-profile", "owner")
    register.assert_not_awaited()
    async with db.get_session() as session:
        assert await session.get(BrowserProfileOwner, "new-profile") is None


async def test_fenced_owner_cannot_claim(database):
    db, _ = database
    binding = await owners.bind_profile(db, "profile", "owner")
    async with db.get_session() as session:
        row = await session.get(BrowserOwner, "alpha")
        row.runtime_epoch = "replacement-runtime"
        await session.commit()
    with pytest.raises(NodeUserError, match="fenced"):
        await owners.claim_browser_task(db, binding, "owner", "task")


async def test_claim_restores_current_gate_instead_of_stale_frozen_binding(database):
    db, controller = database
    binding = await owners.bind_profile(db, "profile", "owner")
    await owners.set_sensitive(db, "profile", None, True)
    claimed = await owners.claim_browser_task(db, binding, "owner", "task")
    assert claimed["binding"]["sensitive_login"] is True
    assert controller.sensitive_login is True


async def test_busy_live_operation_rolls_back_durable_task_claim(database):
    db, controller = database
    binding = await owners.bind_profile(db, "profile", "owner")
    await controller._op_lock.acquire()
    try:
        with pytest.raises(NodeUserError, match="BrowserBusy"):
            await owners.claim_browser_task(db, binding, "owner", "task")
    finally:
        controller._op_lock.release()
    async with db.get_session() as session:
        assert (await session.get(BrowserProfileOwner, "profile")).task_id is None


async def test_viewer_routing_keeps_active_frozen_profile_after_parameter_edit(database):
    db, _ = database
    db.get_node_parameters = AsyncMock(return_value={"profile_id": "changed"})
    async with db.get_session() as session:
        session.add(BrowserProfileRow(id="changed", owner_id="owner", name="Changed"))
        session.add(BrowserProfileOwner(profile_id="changed", principal_id="owner", owner_id="alpha"))
        await session.commit()
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf", "node_id": "browser"}
    await owners.claim_browser_task(db, binding, "owner", "task")
    with patch("nodes.browser._handlers.resolve_browser_node", authorized_browser_node()):
        assert (await owners.routing_for_node(db, "wf", "browser", "owner"))["profile_id"] == "profile"
        await owners.release_browser_task(db, binding, "stale")
        assert (await owners.routing_for_node(db, "wf", "browser", "owner"))["profile_id"] == "profile"
        await owners.release_browser_task(db, binding, "task")
        assert (await owners.routing_for_node(db, "wf", "browser", "owner"))["profile_id"] == "changed"


@pytest.mark.parametrize("distributed", [False, True])
async def test_recovered_viewer_session_uses_active_profile_in_local_and_distributed_mode(database, distributed):
    from nodes.browser import _handlers
    from nodes.browser._runtime import BrowserRuntime

    db, _ = database
    db.get_node_parameters = AsyncMock(return_value={"profile_id": "changed"})
    async with db.get_session() as session:
        row = await session.get(BrowserProfileOwner, "profile")
        row.task_id, row.workflow_id, row.browser_node_id = "recovered-task", "wf", "browser"
        session.add(BrowserProfileRow(id="changed", owner_id="owner", name="Changed"))
        await session.commit()
    runtime = BrowserRuntime()  # Recovered owner has no in-memory node sessions.
    config = SimpleNamespace(distributed_mode=distributed, browser_replica_id="alpha")
    authorized_node = ({}, {"id": "browser", "data": {"label": "Browser"}})
    with patch.object(owners, "settings", return_value=config), \
            patch.object(_handlers, "get_database", return_value=db), \
            patch.object(_handlers, "resolve_browser_node", AsyncMock(return_value=authorized_node)) as authorize, \
            patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime):
        _, recovered_session = await _handlers._session_for_node("owner", "wf", "browser", create=True)
    assert recovered_session.profile_id == "profile"
    authorize.assert_awaited_with("owner", "wf", "browser")
    assert runtime.find_session("wf", "browser") is recovered_session


async def test_inprocess_delegated_claim_survives_stale_reset_and_cleans_up_with_its_own_token(database):
    from nodes.agent.browser_agent import BrowserAgentNode, BrowserAgentParams
    from nodes.agent._specialized import SpecializedAgentBase
    from nodes.browser._runtime import BrowserRuntime
    from services.plugin.context import NodeContext

    db, _ = database
    db.get_node_parameters = AsyncMock(return_value={"profile_id": "profile"})
    graph = {"owner_id": "owner", "nodes": [{"id": "agent", "type": "browser_agent"},
             {"id": "browser", "type": "browser"}], "edges": [
        {"source": "browser", "target": "agent", "targetHandle": "input-tools"}]}
    context = NodeContext.from_legacy("agent", "browser_agent", {**graph, "workflow_id": "wf",
        "user_id": "owner", "execution_id": "old-execution", "parent_task_id": "delegated-child"})
    started, finish = asyncio.Event(), asyncio.Event()
    async def reason(*_):
        started.set()
        await finish.wait()
        return {"response": "Complete"}
    runtime = BrowserRuntime()
    with patch("services.plugin.deps.get_database", return_value=db), \
            patch("nodes.browser._handlers.resolve_browser_node", authorized_browser_node()), \
            patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime), \
            patch.object(SpecializedAgentBase, "execute_op", reason):
        child = asyncio.create_task(BrowserAgentNode().execute_op(context, BrowserAgentParams()))
        try:
            await asyncio.wait_for(started.wait(), 2)
            assert runtime.controller("profile").task_id == "delegated-child:agent"
            reset = dict(node_id="agent", workflow_id="wf", execution_id="old-execution",
                         generation=1, graph=graph, database=db)
            assert await BrowserAgentNode.reset_execution_state(**reset) == {"released": False}
            async with db.get_session() as session:
                assert (await session.get(BrowserProfileOwner, "profile")).task_id == "delegated-child:agent"
            finish.set()
            await child
            assert runtime.controller("profile").task_id is None
            binding = await owners.routing_for_node(db, "wf", "browser", "owner")
            await owners.claim_browser_task(db, binding, "owner", "new-execution:agent")
            assert await BrowserAgentNode.reset_execution_state(**reset) == {"released": False}
            assert runtime.controller("profile").task_id == "new-execution:agent"
            await owners.cleanup_browser_task(db, binding, "new-execution:agent")
        finally:
            finish.set()
            await child


@pytest.mark.parametrize("browsers", [[], ["browser", "browser-2"]])
async def test_reset_of_browser_agent_without_exactly_one_browser_tool_is_a_noop(database, browsers):
    from nodes.agent.browser_agent import BrowserAgentNode

    db, _ = database
    graph = {"owner_id": "owner",
             "nodes": [{"id": "agent", "type": "browser_agent"}, *({"id": b, "type": "browser"} for b in browsers)],
             "edges": [{"source": b, "target": "agent", "targetHandle": "input-tools"} for b in browsers]}
    assert await BrowserAgentNode.reset_execution_state(node_id="agent", workflow_id="wf",
        execution_id="2:execution:5", generation=5, graph=graph, database=db) == {"reset": False}


async def test_the_browser_checks_its_claim_with_the_task_token_not_the_run_id(database):
    from nodes.browser.browser import BrowserNode, BrowserParams
    from services.plugin.context import NodeContext

    db, controller = database
    binding = await owners.bind_profile(db, "profile", "owner")
    await owners.claim_browser_task(db, binding, "owner", "agent-workflow")

    class PastOwnership(Exception):
        pass

    runtime = SimpleNamespace(controller_for=lambda _: controller, controller=lambda _: controller,
                              base_policy=Mock(side_effect=PastOwnership))

    def call(**scope):
        return NodeContext.from_legacy("browser", "browser", {"workflow_id": "wf", "user_id": "owner",
            "execution_id": "wf:execution:3", "_browser_owner": binding, **scope})

    with patch("services.plugin.deps.get_database", return_value=db), \
            patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime):
        with pytest.raises(PastOwnership):
            await BrowserNode().dispatch(call(_browser_task_id="agent-workflow"), BrowserParams(operation="snapshot"))
        with pytest.raises(NodeUserError, match="BrowserBusy"):
            await BrowserNode().dispatch(call(), BrowserParams(operation="snapshot"))


async def test_ambiguous_active_browser_bindings_fail_closed(database):
    db, _ = database
    db.get_node_parameters = AsyncMock(return_value={})
    async with db.get_session() as session:
        first = await session.get(BrowserProfileOwner, "profile")
        first.task_id, first.workflow_id, first.browser_node_id = "task", "wf", "browser"
        session.add(BrowserProfileOwner(profile_id="other", principal_id="owner", owner_id="alpha",
            task_id="other-task", workflow_id="wf", browser_node_id="browser"))
        await session.commit()
    with patch("nodes.browser._handlers.resolve_browser_node", authorized_browser_node()), pytest.raises(NodeUserError, match="ambiguous"):
        await owners.routing_for_node(db, "wf", "browser", "owner")


async def test_runtime_open_restores_recovered_durable_task_and_capture_gate(database):
    from nodes.browser._runtime import BrowserRuntime
    from nodes.browser._profiles import ProfileStore
    db, _ = database
    async with db.get_session() as session:
        row = await session.get(BrowserProfileOwner, "profile")
        row.task_id, row.sensitive_login, row.needs_observation = "recovered-task", True, True
        await session.commit()
    runtime = BrowserRuntime()
    runtime._profiles["profile"] = SimpleNamespace(running=True)
    profile = await ProfileStore(db).get("owner", "profile")
    with patch("services.plugin.deps.get_database", return_value=db), patch.object(runtime, "_settings", return_value=SimpleNamespace(distributed_mode=True)):
        await runtime.open(profile)
    controller = runtime.controller("profile")
    assert controller.task_id == "recovered-task"
    assert controller.sensitive_login and controller.needs_observation


async def test_claim_applies_frozen_network_policy_before_first_model_turn(database):
    from services.netpolicy import NetPolicy
    db, controller = database
    binding = {**await owners.bind_profile(db, "profile", "owner"), "workflow_id": "wf", "node_id": "browser",
        "browser_policy": {"allowed_domains": "example.test", "allow_private_network": False}}
    runtime = SimpleNamespace(controller_for=lambda _: controller,
        register_session=lambda session: session, base_policy=lambda **args: NetPolicy(**args))
    with patch("nodes.browser._runtime.get_browser_runtime", return_value=runtime):
        await owners.claim_browser_task(db, binding, "owner", "task")
    assert controller.lease_session.node_id == "browser"
    assert controller.current_policy().allowed_domains == ("example.test",)


async def test_workflow_profile_cleanup_never_deletes_under_unavailable_owner(database):
    from nodes.browser import _cleanup_deleted_workflow_profile
    db, _ = database
    db.get_workflow = AsyncMock(return_value=None)
    async with db.get_session() as session:
        profile = await session.get(BrowserProfileRow, "profile")
        profile.kind, profile.workflow_id = "employee", "wf"
        owner = await session.get(BrowserOwner, "alpha")
        owner.heartbeat_at = 0
        await session.commit()
    remove = Mock()
    with patch.object(owners, "replica_id", return_value="beta"), patch("nodes.browser._profiles.remove_profile_files", remove):
        assert await _cleanup_deleted_workflow_profile(db, "owner", "profile", "wf") == {"success": False, "deferred": True}
    remove.assert_not_called()
    async with db.get_session() as session:
        assert await session.get(BrowserProfileRow, "profile") is not None


async def test_workflow_profile_cleanup_refuses_association_while_workflow_exists(database):
    from nodes.browser import _cleanup_deleted_workflow_profile
    db, _ = database
    db.get_workflow = AsyncMock(return_value={"id": "wf"})
    with pytest.raises(NodeUserError, match="not associated"):
        await _cleanup_deleted_workflow_profile(db, "owner", "profile", "wf")


async def test_forward_authorization_binds_principal_method_path_body_and_expiry(database):
    body = b'{"command":"browser_session"}'
    token = owners.sign_forward("owner", "POST", "/api/browser/owner/command", body)
    assert owners.verify_forward(token, "POST", "/api/browser/owner/command", body) == "owner"
    for method, path, supplied in (("GET", "/api/browser/owner/command", body), ("POST", "/different", body), ("POST", "/api/browser/owner/command", body + b" ")):
        with pytest.raises(NodeUserError, match="authorization"):
            owners.verify_forward(token, method, path, supplied)
    with patch.object(owners.time, "time", return_value=time.time() + 60):
        with pytest.raises(NodeUserError, match="authorization"):
            owners.verify_forward(token, "POST", "/api/browser/owner/command", body)
