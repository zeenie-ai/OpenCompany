"""Wave 12 C3: tests for the cron Temporal-Schedule canary.

Three layers:

1. **plugin_registry contract** — Temporal SimplePlugin registration is
   idempotent, namespace-keyed, and produces a stable snapshot for the
   Temporal worker's ``plugins=[]`` argument.

2. **schedules helper** — deterministic ``cron_schedule_id`` derivation;
   :func:`create_cron_schedule` calls ``client.create_schedule`` with
   the SimplePlugin-targeting action + Search Attributes;
   :func:`delete_cron_schedules_for_deployment` queries via Visibility
   and graceful-deletes each.

3. **DeploymentManager integration** — _start_canary_cron_schedule
   builds the listener_data payload and calls into schedules.py;
   _cancel_canary_cron_schedules dispatches into the delete sweep.

4. **Plugin self-registration smoke** — importing
   ``nodes.scheduler.cron_scheduler`` populates the plugin_registry
   with the cron SimplePlugin AND the canary_registry with
   ``cronScheduler``.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, sentinel

import pytest


if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


@pytest.fixture
def fresh_plugin_registry(monkeypatch):
    """Reset plugin_registry's backing store for test isolation."""
    from services.temporal import plugin_registry as pr

    fresh = type(pr._REGISTRY)(pr._REGISTRY._name)  # type: ignore[attr-defined]
    monkeypatch.setattr(pr, "_REGISTRY", fresh)
    return pr


# ---------------------------------------------------------------------------
# plugin_registry contract
# ---------------------------------------------------------------------------


class TestPluginRegistryContract:
    """SimplePlugin registration shape."""

    def test_empty_registry_yields_empty_list(self, fresh_plugin_registry):
        assert fresh_plugin_registry.temporal_plugins() == []

    def test_register_then_snapshot(self, fresh_plugin_registry):
        from temporalio.plugin import SimplePlugin

        plugin = SimplePlugin(name="test-plugin")
        fresh_plugin_registry.register_temporal_plugin(plugin)

        snap = fresh_plugin_registry.temporal_plugins()
        assert snap == [plugin]

    def test_idempotent_same_plugin_instance(self, fresh_plugin_registry):
        from temporalio.plugin import SimplePlugin

        plugin = SimplePlugin(name="test-plugin")
        fresh_plugin_registry.register_temporal_plugin(plugin)
        fresh_plugin_registry.register_temporal_plugin(plugin)
        assert len(fresh_plugin_registry.temporal_plugins()) == 1

    def test_conflicting_name_raises(self, fresh_plugin_registry):
        from temporalio.plugin import SimplePlugin

        a = SimplePlugin(name="dup-name")
        b = SimplePlugin(name="dup-name")
        fresh_plugin_registry.register_temporal_plugin(a)
        with pytest.raises(ValueError, match="already registered"):
            fresh_plugin_registry.register_temporal_plugin(b)

    def test_distinct_names_coexist(self, fresh_plugin_registry):
        from temporalio.plugin import SimplePlugin

        p1 = SimplePlugin(name="plugin-1")
        p2 = SimplePlugin(name="plugin-2")
        fresh_plugin_registry.register_temporal_plugin(p1)
        fresh_plugin_registry.register_temporal_plugin(p2)
        assert {p.name() for p in fresh_plugin_registry.temporal_plugins()} == {
            "plugin-1",
            "plugin-2",
        }


# ---------------------------------------------------------------------------
# schedules helper
# ---------------------------------------------------------------------------


class TestScheduleIdDerivation:
    def test_deterministic_id(self):
        from services.temporal.schedules import cron_schedule_id

        # Wave 14: id = ``<workflow_slug>-<trigger_label>``.
        a = cron_schedule_id("wf-1", "cron-1")
        b = cron_schedule_id("wf-1", "cron-1")
        assert a == b == "wf-1-cron-1"

    def test_different_node_different_id(self):
        from services.temporal.schedules import cron_schedule_id

        assert cron_schedule_id("wf-1", "cron-a") != cron_schedule_id(
            "wf-1",
            "cron-b",
        )


class TestCreateCronSchedule:
    @pytest.mark.asyncio
    async def test_create_schedule_payload_shape(self):
        from services.temporal.schedules import create_cron_schedule

        client = MagicMock()
        client.create_schedule = AsyncMock()

        listener_data = {
            "workflow_id": "wf-1",
            "trigger_node_id": "cron-1",
            "node_type": "cronScheduler",
            "cron_expression": "*/5 * * * *",
        }

        # Wave 14: signature now takes workflow_id (Search Attribute) +
        # workflow_slug (id prefix) + trigger_label (id suffix).
        schedule_id = await create_cron_schedule(
            client,
            workflow_id="wf-1",
            workflow_slug="wf-1",
            node_id="cron-1",
            trigger_label="cron-1",
            cron_expression="*/5 * * * *",
            timezone="America/New_York",
            listener_data=listener_data,
        )

        assert schedule_id == "wf-1-cron-1"
        assert client.create_schedule.await_count == 1

        passed_id, passed_schedule = client.create_schedule.call_args.args
        assert passed_id == schedule_id
        assert passed_schedule.spec.cron_expressions == ["*/5 * * * *"]
        assert passed_schedule.spec.time_zone_name == "America/New_York"
        action_attributes = {
            pair.key.name: pair.value
            for pair in passed_schedule.action.typed_search_attributes
        }
        assert action_attributes == {
            "EventWorkflowId": "wf-1",
            "TriggerNodeId": "cron-1",
            "EventTriggerKind": "cron",
        }

    @pytest.mark.asyncio
    async def test_create_schedule_idempotent_on_already_running(self):
        """Re-deploy: Temporal raises ScheduleAlreadyRunningError; helper
        swallows it so the deploy path keeps a deterministic id without
        the caller needing to special-case retries."""
        from temporalio.client import ScheduleAlreadyRunningError

        from services.temporal.schedules import create_cron_schedule

        client = MagicMock()
        client.create_schedule = AsyncMock(side_effect=ScheduleAlreadyRunningError())
        update = AsyncMock()
        client.get_schedule_handle.return_value = MagicMock(update=update)

        schedule_id = await create_cron_schedule(
            client,
            workflow_id="wf-1",
            workflow_slug="wf-1",
            node_id="cron-1",
            trigger_label="cron-1",
            cron_expression="0 * * * *",
            timezone="UTC",
            listener_data={},
        )

        assert schedule_id == "wf-1-cron-1"
        client.get_schedule_handle.assert_called_once_with(schedule_id)
        update.assert_awaited_once()

        updater = update.await_args.args[0]
        existing_schedule = client.create_schedule.await_args.args[1]
        existing_attributes = (
            client.create_schedule.await_args.kwargs["search_attributes"]
        )
        update_result = updater(
            types.SimpleNamespace(
                description=types.SimpleNamespace(
                    schedule=types.SimpleNamespace(
                        state=sentinel.paused_state,
                        action=existing_schedule.action,
                    ),
                    typed_search_attributes=existing_attributes,
                ),
            )
        )
        assert update_result.schedule.state is sentinel.paused_state
        action_attributes = {
            pair.key.name: pair.value
            for pair in update_result.schedule.action.typed_search_attributes
        }
        assert action_attributes["EventWorkflowId"] == "wf-1"
        assert update_result.search_attributes == (
            update_result.schedule.action.typed_search_attributes
        )

        with pytest.raises(
            RuntimeError,
            match="cron_schedule_ownership_conflict",
        ):
            updater(
                types.SimpleNamespace(
                    description=types.SimpleNamespace(
                        schedule=types.SimpleNamespace(
                            state=sentinel.paused_state,
                            action=existing_schedule.action,
                        ),
                        typed_search_attributes=[
                            types.SimpleNamespace(
                                key=types.SimpleNamespace(
                                    name="EventWorkflowId",
                                ),
                                value="different-workflow",
                            ),
                        ],
                    ),
                )
            )

    @pytest.mark.asyncio
    async def test_cron_workflow_pause_resume_gate(self, monkeypatch):
        from nodes.scheduler.cron_scheduler import _workflow as cron_workflow

        wait_condition = AsyncMock()
        monkeypatch.setattr(
            cron_workflow.workflow,
            "wait_condition",
            wait_condition,
        )
        instance = cron_workflow.CronTriggerWorkflow()

        await instance.pause()
        assert instance._control_paused is True
        await instance._wait_until_resumed()
        wait_condition.assert_awaited_once()
        predicate = wait_condition.await_args.args[0]
        assert predicate() is False

        await instance.resume()
        assert predicate() is True


class _FakeScheduleIterator:
    """Match the temporalio SDK's ``ScheduleAsyncIterator`` shape —
    an explicit async iterator object, NOT an async generator.

    ``Client.list_schedules`` is ``async def`` in the real SDK: it
    returns a coroutine that resolves to ``ScheduleAsyncIterator``.
    Pre-fix stubs used ``async def fake(query): yield ...`` (an async
    generator function), which when called returns an async generator
    directly — making ``async for fake(...)`` work AND hiding the
    real bug that production code was doing exactly that against the
    real coroutine-returning method.
    """

    def __init__(self, ids):
        self._ids = list(ids)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._ids:
            raise StopAsyncIteration
        return MagicMock(id=self._ids.pop(0))


class TestDeleteCronSchedulesForDeployment:
    @pytest.mark.asyncio
    async def test_query_filters_by_workflow_id(self):
        from services.temporal.schedules import (
            delete_cron_schedules_for_deployment,
        )

        recorded_queries: List[str] = []
        deleted_ids: List[str] = []

        async def fake_list(query, **kwargs):
            # ``async def`` returning the iterator — matches real
            # ``Client.list_schedules`` signature. NOT ``yield``.
            recorded_queries.append(query)
            return _FakeScheduleIterator(
                ["cron-schedule-wf-1-a", "cron-schedule-wf-1-b"],
            )

        def fake_get_handle(sid):
            handle = MagicMock()

            async def fake_delete():
                deleted_ids.append(sid)

            handle.delete = fake_delete
            return handle

        client = MagicMock()
        client.list_schedules = fake_list
        client.get_schedule_handle = fake_get_handle

        count = await delete_cron_schedules_for_deployment(client, "wf-1")

        assert count == 2
        assert sorted(deleted_ids) == [
            "cron-schedule-wf-1-a",
            "cron-schedule-wf-1-b",
        ]
        # Filter on both deployment workflow_id AND the cron kind tag.
        assert len(recorded_queries) == 1
        q = recorded_queries[0]
        assert "EventWorkflowId='wf-1'" in q
        assert "EventTriggerKind='cron'" in q

    @pytest.mark.asyncio
    async def test_per_schedule_failure_does_not_block_sweep(self):
        from services.temporal.schedules import (
            delete_cron_schedules_for_deployment,
        )

        async def fake_list(query, **kwargs):
            return _FakeScheduleIterator(
                ["cron-schedule-wf-1-good", "cron-schedule-wf-1-bad"],
            )

        def fake_get_handle(sid):
            handle = MagicMock()
            if "bad" in sid:

                async def boom():
                    raise RuntimeError("simulated delete failure")

                handle.delete = boom
            else:

                async def ok():
                    return None

                handle.delete = ok
            return handle

        client = MagicMock()
        client.list_schedules = fake_list
        client.get_schedule_handle = fake_get_handle

        count = await delete_cron_schedules_for_deployment(client, "wf-1")
        # Only the good one deleted; the bad one's failure was logged
        # + skipped.
        assert count == 1

    @pytest.mark.asyncio
    async def test_strict_cleanup_raises_after_sweeping_all_schedules(self):
        from services.temporal.schedules import (
            delete_cron_schedules_for_deployment,
        )

        deleted_ids: List[str] = []

        async def fake_list(query, **kwargs):
            return _FakeScheduleIterator(
                ["cron-schedule-wf-1-bad", "cron-schedule-wf-1-good"],
            )

        def fake_get_handle(sid):
            handle = MagicMock()
            if "bad" in sid:
                handle.delete = AsyncMock(
                    side_effect=RuntimeError("simulated delete failure"),
                )
            else:
                handle.delete = AsyncMock(
                    side_effect=lambda: deleted_ids.append(sid),
                )
            return handle

        client = MagicMock()
        client.list_schedules = fake_list
        client.get_schedule_handle = fake_get_handle

        with pytest.raises(
            RuntimeError,
            match="cron_schedule_cleanup_failed:1",
        ):
            await delete_cron_schedules_for_deployment(
                client,
                "wf-1",
                strict=True,
            )

        assert deleted_ids == ["cron-schedule-wf-1-good"]

    @pytest.mark.asyncio
    async def test_strict_cleanup_raises_when_visibility_scan_fails(self):
        from services.temporal.schedules import (
            delete_cron_schedules_for_deployment,
        )

        client = MagicMock()
        client.list_schedules = AsyncMock(
            side_effect=RuntimeError("visibility unavailable"),
        )

        with pytest.raises(
            RuntimeError,
            match="cron_schedule_visibility_cleanup_failed",
        ):
            await delete_cron_schedules_for_deployment(
                client,
                "wf-1",
                strict=True,
            )

    def test_source_awaits_list_schedules_before_async_for(self):
        """Regression: ``Client.list_schedules`` is ``async def`` in
        the temporalio SDK. Bare ``async for ... in client.list_schedules(...)``
        raises ``'async for' requires an object with __aiter__ method,
        got coroutine`` at runtime — observed in prod on every deployment
        cancel before the Wave 13 follow-up.

        The fix captures the iterator via ``await`` first:
            iterator = await client.list_schedules(query=query)
            async for desc in iterator:
                ...
        """
        import inspect
        import re

        from services.temporal import schedules as schedules_mod

        src = inspect.getsource(schedules_mod.delete_cron_schedules_for_deployment)
        bare_pattern = re.compile(
            r"async\s+for\s+\w+\s+in\s+\w+\.list_schedules\s*\(",
        )
        assert not bare_pattern.search(src), (
            "delete_cron_schedules_for_deployment contains bare "
            "``async for ... in client.list_schedules(...)``. "
            "list_schedules is ``async def`` in temporalio — must "
            "``await`` to get the iterator first. See "
            "https://python.temporal.io/temporalio.client.Client.html#list_schedules"
        )


# ---------------------------------------------------------------------------
# DeploymentManager cron canary integration
# ---------------------------------------------------------------------------


def _node(node_id: str, node_type: str) -> Dict[str, Any]:
    return {"id": node_id, "type": node_type, "data": {}}


def _build_manager(workflow_id: str, nodes=None, edges=None, session_id="sess"):
    from services.deployment.manager import DeploymentManager
    from services.deployment.state import DeploymentState

    database = MagicMock()
    database.get_node_parameters = AsyncMock(return_value={})
    broadcaster = MagicMock()
    broadcaster.update_node_status = AsyncMock()

    mgr = DeploymentManager(
        database=database,
        execute_workflow_fn=AsyncMock(),
        store_output_fn=AsyncMock(),
        broadcaster=broadcaster,
    )
    mgr._deployments[workflow_id] = DeploymentState(
        deployment_id=f"deploy_{workflow_id}",
        workflow_id=workflow_id,
        is_running=True,
        nodes=nodes or [],
        edges=edges or [],
        session_id=session_id,
    )
    return mgr


class TestDeploymentCronCanaryRouting:
    @pytest.mark.asyncio
    async def test_start_canary_cron_schedule_calls_helper(self, monkeypatch):
        """The deployment helper threads the listener_data payload into
        create_cron_schedule with the right shape."""
        mgr = _build_manager(
            "wf-1",
            nodes=[_node("cron-1", "cronScheduler")],
            edges=[],
        )

        recorded: List[Dict[str, Any]] = []

        async def fake_create(client, *, workflow_id, workflow_slug, node_id, trigger_label, cron_expression, timezone, listener_data, **kw):
            recorded.append(
                {
                    "workflow_id": workflow_id,
                    "workflow_slug": workflow_slug,
                    "node_id": node_id,
                    "trigger_label": trigger_label,
                    "cron_expression": cron_expression,
                    "timezone": timezone,
                    "listener_data": listener_data,
                }
            )
            return f"{workflow_slug}-{trigger_label}"

        from services.temporal import schedules

        monkeypatch.setattr(schedules, "create_cron_schedule", fake_create)

        wrapper = MagicMock()
        wrapper.client = MagicMock()

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await mgr._start_canary_cron_schedule(
            _node("cron-1", "cronScheduler"),
            "wf-1",
            params={"cron": "*"},
            cron_expr="*/5 * * * *",
            timezone="UTC",
            frequency="minutes",
            schedule_desc="Every 5 minutes",
        )

        # Wave 14: id = ``<workflow_slug>-<trigger_label>``.
        # Slug + label both fall back to workflow_id / node.type when
        # the test doesn't pre-populate DeploymentState.workflow_slug
        # or set a custom node label.
        assert result == "wf-1-cronScheduler"
        assert len(recorded) == 1
        call = recorded[0]
        assert call["cron_expression"] == "*/5 * * * *"
        assert call["timezone"] == "UTC"
        # listener_data carries the graph snapshot + cron metadata.
        ld = call["listener_data"]
        assert ld["workflow_id"] == "wf-1"
        assert ld["trigger_node_id"] == "cron-1"
        assert ld["cron_expression"] == "*/5 * * * *"
        assert ld["schedule"] == "Every 5 minutes"

    @pytest.mark.asyncio
    async def test_start_returns_none_when_temporal_not_connected(self, monkeypatch):
        mgr = _build_manager(
            "wf-1",
            nodes=[_node("cron-1", "cronScheduler")],
            edges=[],
        )

        wrapper = MagicMock()
        wrapper.client = None

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await mgr._start_canary_cron_schedule(
            _node("cron-1", "cronScheduler"),
            "wf-1",
            params={},
            cron_expr="0 * * * *",
            timezone="UTC",
            frequency="hours",
            schedule_desc="Every hour",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_dispatches_into_delete_sweep(self, monkeypatch):
        mgr = _build_manager("wf-1")

        delete_calls: List[str] = []

        async def fake_delete(client, deployment_workflow_id):
            delete_calls.append(deployment_workflow_id)
            return 3

        from services.temporal import schedules

        monkeypatch.setattr(
            schedules,
            "delete_cron_schedules_for_deployment",
            fake_delete,
        )

        wrapper = MagicMock()
        wrapper.client = MagicMock()

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        count = await mgr._cancel_canary_cron_schedules("wf-1")
        assert count == 3
        assert delete_calls == ["wf-1"]

    @pytest.mark.asyncio
    async def test_cancel_returns_zero_when_temporal_disconnected(self, monkeypatch):
        mgr = _build_manager("wf-1")

        wrapper = MagicMock()
        wrapper.client = None

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        assert await mgr._cancel_canary_cron_schedules("wf-1") == 0


# ---------------------------------------------------------------------------
# Plugin self-registration smoke
# ---------------------------------------------------------------------------


class TestCronPluginSelfRegisters:
    def test_plugin_import_registers_simple_plugin(self):
        try:
            __import__("nodes.scheduler.cron_scheduler")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"cron_scheduler not importable: {exc}")

        from services.temporal.plugin_registry import temporal_plugins

        names = {p.name() for p in temporal_plugins()}
        assert "cron-scheduler" in names, (
            "Importing nodes.scheduler.cron_scheduler should register a "
            "SimplePlugin named 'cron-scheduler'. Check the __init__.py "
            "bottom section."
        )

    def test_plugin_import_registers_canary(self):
        try:
            __import__("nodes.scheduler.cron_scheduler")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"cron_scheduler not importable: {exc}")

        from services.deployment import canary_registry

        assert canary_registry.is_canary_trigger_type("cronScheduler"), (
            "Importing nodes.scheduler.cron_scheduler should call " "register_canary_trigger_type('cronScheduler')."
        )
@pytest.mark.asyncio
@pytest.mark.parametrize("paused", [True, False])
async def test_schedule_pause_state_follows_workflow_control(paused):
    from services.temporal.schedules import set_cron_schedules_paused

    handles = {}

    async def fake_list(query, **kwargs):
        assert "EventWorkflowId='wf-1'" in query
        return _FakeScheduleIterator(["schedule-a", "schedule-b"])

    def fake_get_handle(schedule_id):
        handle = MagicMock()
        handle.pause = AsyncMock()
        handle.unpause = AsyncMock()
        handles[schedule_id] = handle
        return handle

    client = MagicMock(list_schedules=fake_list, get_schedule_handle=fake_get_handle)
    changed = await set_cron_schedules_paused(client, "wf-1", paused=paused)

    assert changed == 2
    for handle in handles.values():
        if paused:
            handle.pause.assert_awaited_once()
            handle.unpause.assert_not_awaited()
        else:
            handle.unpause.assert_awaited_once()
            handle.pause.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tenant-namespace resolution (schedule create/sweep must target the
# deployment's own Temporal namespace, not container.temporal_client()).
# Regression for: VTask cron schedule created in ``default`` while the
# workflow was deployed in ``addval-connect`` — firings spawned runs in
# the wrong namespace (missing credentials / failed child workflows).
# ---------------------------------------------------------------------------


def _tenant_manager(workflow_id: str, ns: str = "addval-connect"):
    """Manager whose deployment state records a tenant namespace."""
    mgr = _build_manager(workflow_id)
    mgr._deployments[workflow_id].temporal_namespace = ns
    return mgr


def _patch_registry(monkeypatch, ns: str = "addval-connect"):
    """Force multi_tenant_namespaces=True + a registered tenant wrapper."""
    from core import container as container_mod

    settings = MagicMock()
    settings.multi_tenant_namespaces = True
    settings.temporal_namespace = "default"
    monkeypatch.setattr(container_mod.container, "settings", lambda: settings)

    tenant_wrapper = MagicMock()
    tenant_wrapper.client = MagicMock()
    tenant_wrapper.client.list_schedules = AsyncMock(return_value=[])
    from services.temporal import client_registry

    monkeypatch.setattr(client_registry, "_registry", {ns: tenant_wrapper})
    return tenant_wrapper


class TestTenantNamespaceScheduleRouting:
    @pytest.mark.asyncio
    async def test_start_schedule_targets_tenant_namespace(self, monkeypatch):
        """_start_canary_cron_schedule must NOT use the default-namespace
        client when the deployment state records a tenant namespace."""
        mgr = _tenant_manager("wf-1", "addval-connect")
        tenant_wrapper = _patch_registry(monkeypatch)

        from services.temporal import schedules

        fake_create = AsyncMock(return_value="sched-1")
        monkeypatch.setattr(schedules, "create_cron_schedule", fake_create)

        await mgr._start_canary_cron_schedule(
            _node("cron-1", "cronScheduler"),
            "wf-1",
            params={},
            cron_expr="*/5 * * * *",
            timezone="UTC",
            frequency="minutes",
            schedule_desc="Every 5 minutes",
        )

        # The schedule was created with the tenant namespace's client.
        assert fake_create.await_args.args[0] is tenant_wrapper.client

    @pytest.mark.asyncio
    async def test_cancel_schedule_sweep_targets_tenant_namespace(self, monkeypatch):
        mgr = _tenant_manager("wf-1", "addval-connect")
        tenant_wrapper = _patch_registry(monkeypatch)

        from services.temporal import schedules

        fake_delete = AsyncMock(return_value=1)
        monkeypatch.setattr(
            schedules, "delete_cron_schedules_for_deployment", fake_delete
        )

        count = await mgr._cancel_canary_cron_schedules("wf-1")
        assert count == 1
        assert fake_delete.await_args.args[0] is tenant_wrapper.client

    @pytest.mark.asyncio
    async def test_cancel_listeners_sweep_targets_tenant_namespace(self, monkeypatch):
        mgr = _tenant_manager("wf-1", "addval-connect")
        tenant_wrapper = _patch_registry(monkeypatch)
        tenant_wrapper.client.list_workflows = MagicMock(
            return_value=MagicMock(async_iterator=iter([]))
        )

        cancelled = await mgr._cancel_canary_listeners("wf-1")
        assert cancelled == 0
        # The Visibility query ran against the tenant namespace client.
        tenant_wrapper.client.list_workflows.assert_called_once()


class TestFailClosedScheduleMutations:
    @pytest.mark.asyncio
    async def test_set_cron_pause_fail_closed_on_unregistered_namespace(
        self, monkeypatch
    ):
        """strict=True + unregistered tenant namespace must raise instead
        of silently sweeping ``default`` (fail-open repro'd the incident)."""
        from core import container as container_mod
        from services.deployment import handlers

        settings = MagicMock()
        settings.multi_tenant_namespaces = True
        settings.temporal_namespace = "default"
        settings.temporal_enabled = True
        monkeypatch.setattr(container_mod.container, "settings", lambda: settings)
        monkeypatch.setattr(
            "services.temporal.client_registry._registry", {}
        )

        with pytest.raises(Exception) as exc_info:
            await handlers._set_cron_pause(
                "wf-1", paused=True, strict=True, namespace="addval-connect"
            )
        assert "temporal_control_unavailable" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_set_cron_pause_passes_namespace_to_sweep(self, monkeypatch):
        from core import container as container_mod
        from services.deployment import handlers
        from services.temporal import schedules

        settings = MagicMock()
        settings.multi_tenant_namespaces = True
        settings.temporal_namespace = "default"
        monkeypatch.setattr(container_mod.container, "settings", lambda: settings)

        tenant_wrapper = MagicMock()
        tenant_wrapper.client = MagicMock()
        monkeypatch.setattr(
            "services.temporal.client_registry._registry",
            {"addval-connect": tenant_wrapper},
        )

        recorded: List[Dict[str, Any]] = []

        async def fake_pause(client, workflow_id, *, paused, strict=False):
            recorded.append({"client": client, "workflow_id": workflow_id})
            return 1

        monkeypatch.setattr(schedules, "set_cron_schedules_paused", fake_pause)

        result = await handlers._set_cron_pause(
            "wf-1", paused=True, strict=True, namespace="addval-connect"
        )
        assert result == 1
        assert recorded[0]["client"] is tenant_wrapper.client


class TestBootstrapOrdering:
    def test_tenant_clients_bootstrap_before_reconcile(self):
        """_bootstrap_tenant_clients must run BEFORE _boot_reconcile:
        the reconcile pass re-arms durable generations via
        _client_for_namespace, which requires tenant clients to be
        registered already (otherwise the re-arm falls back to default)."""
        import inspect

        from services.temporal import lifecycle

        src = inspect.getsource(lifecycle.run_temporal_lifecycle)
        boot_pos = src.find("await _bootstrap_tenant_clients")
        reconcile_pos = src.find("await _boot_reconcile")
        assert boot_pos != -1, "bootstrap call missing from lifecycle"
        assert reconcile_pos != -1, "reconcile call missing from lifecycle"
        assert boot_pos < reconcile_pos, (
            "bootstrap must precede reconcile so tenant namespaces are "
            "registered before the re-arm pass runs"
        )
