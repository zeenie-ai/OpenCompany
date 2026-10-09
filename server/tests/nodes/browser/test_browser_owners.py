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
    with patch("nodes.browser._handlers.resolve_browser_node", AsyncMock()):
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
            patch("nodes.browser._handlers.resolve_browser_node", AsyncMock()), \
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


async def test_ambiguous_active_browser_bindings_fail_closed(database):
    db, _ = database
    db.get_node_parameters = AsyncMock(return_value={})
    async with db.get_session() as session:
        first = await session.get(BrowserProfileOwner, "profile")
        first.task_id, first.workflow_id, first.browser_node_id = "task", "wf", "browser"
        session.add(BrowserProfileOwner(profile_id="other", principal_id="owner", owner_id="alpha",
            task_id="other-task", workflow_id="wf", browser_node_id="browser"))
        await session.commit()
    with patch("nodes.browser._handlers.resolve_browser_node", AsyncMock()), pytest.raises(NodeUserError, match="ambiguous"):
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
