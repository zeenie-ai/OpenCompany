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
from unittest.mock import AsyncMock, MagicMock

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

    @pytest.mark.asyncio
    async def test_create_schedule_idempotent_on_already_running(self):
        """Re-deploy: Temporal raises ScheduleAlreadyRunningError; helper
        swallows it so the deploy path keeps a deterministic id without
        the caller needing to special-case retries."""
        from temporalio.client import ScheduleAlreadyRunningError

        from services.temporal.schedules import create_cron_schedule

        client = MagicMock()
        client.create_schedule = AsyncMock(side_effect=ScheduleAlreadyRunningError())

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
