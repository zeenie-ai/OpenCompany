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
    async def test_a_schedule_left_by_a_deleted_workflow_is_replaced(self):
        """A workflow deleted before deleting stopped it first left its
        Schedule, and a new workflow can get the same slug: its Start
        replaces the leftover instead of failing on every try."""
        from temporalio.client import ScheduleAlreadyRunningError

        from services.temporal.schedules import create_cron_schedule

        async def owned_by_six(updater):
            updater(
                types.SimpleNamespace(
                    description=types.SimpleNamespace(
                        schedule=types.SimpleNamespace(state=None, action=types.SimpleNamespace(args=())),
                        typed_search_attributes=[
                            types.SimpleNamespace(key=types.SimpleNamespace(name="EventWorkflowId"), value="6"),
                        ],
                    )
                )
            )

        args = dict(
            workflow_id="14",
            workflow_slug="Maya_1",
            node_id="cron-1",
            trigger_label="Schedule",
            cron_expression="0 * * * *",
            timezone="UTC",
            listener_data={},
        )
        client = MagicMock()
        client.create_schedule = AsyncMock(side_effect=[ScheduleAlreadyRunningError(), None])
        delete = AsyncMock()
        client.get_schedule_handle.return_value = MagicMock(update=owned_by_six, delete=delete)
        gone = AsyncMock(return_value=True)

        assert await create_cron_schedule(client, owner_gone=gone, **args) == "Maya_1-Schedule"
        gone.assert_awaited_once_with("6")
        delete.assert_awaited_once()
        assert client.create_schedule.await_count == 2

        # A workflow that still exists keeps its Schedule.
        client.create_schedule = AsyncMock(side_effect=ScheduleAlreadyRunningError())
        delete.reset_mock()
        with pytest.raises(RuntimeError, match="cron_schedule_ownership_conflict:Maya_1-Schedule"):
            await create_cron_schedule(client, owner_gone=AsyncMock(return_value=False), **args)
        delete.assert_not_awaited()

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
# cron expressions, read the way Temporal reads them
# ---------------------------------------------------------------------------

# Temporal's cron layout (ScheduleSpec.cron_string in temporalio's
# api/schedule/v1): five fields start at the minute, six add a year at the
# end, and only seven start at the second. A missing second is 0 and a
# missing year is *; values are decimal integers.
_TEMPORAL_CRON_FIELDS = {
    5: ("minute", "hour", "day_of_month", "month", "day_of_week"),
    6: ("minute", "hour", "day_of_month", "month", "day_of_week", "year"),
    7: ("second", "minute", "hour", "day_of_month", "month", "day_of_week", "year"),
}


def _as_temporal_reads(expression: str) -> Dict[str, str]:
    fields = expression.split()
    calendar = {"second": "0", "year": "*"}
    for name, value in zip(_TEMPORAL_CRON_FIELDS[len(fields)], fields):
        calendar[name] = str(int(value)) if value.isdigit() else value
    return calendar


def _fires(**fields: str) -> Dict[str, str]:
    """A calendar that matches every time, except where a field narrows it."""
    calendar = {"second": "0", "minute": "*", "hour": "*", "day_of_month": "*", "month": "*", "day_of_week": "*", "year": "*"}
    calendar.update(fields)
    return calendar


class TestCronExpression:
    """The deploy path's cron string fires when the node says it will."""

    @pytest.mark.parametrize(
        ("params", "fires"),
        [
            pytest.param({"frequency": "seconds", "interval": 30}, _fires(second="*/30"), id="every-30-seconds"),
            pytest.param({"frequency": "minutes", "interval_minutes": 5}, _fires(minute="*/5"), id="every-5-minutes"),
            pytest.param({"frequency": "minutes", "interval_minutes": 1}, _fires(), id="every-minute"),
            pytest.param({"frequency": "hours", "interval_hours": 1}, _fires(minute="0"), id="hourly"),
            pytest.param({"frequency": "hours", "interval_hours": 6}, _fires(minute="0", hour="*/6"), id="every-6-hours"),
            pytest.param({"frequency": "days", "daily_time": "09:00"}, _fires(minute="0", hour="9"), id="daily"),
            pytest.param(
                {"frequency": "weeks", "weekday": "1", "weekly_time": "22:00"},
                _fires(minute="0", hour="22", day_of_week="1"),
                id="weekly",
            ),
            pytest.param(
                {"frequency": "months", "month_day": "15", "monthly_time": "08:00"},
                _fires(minute="0", hour="8", day_of_month="15"),
                id="monthly",
            ),
        ],
    )
    def test_fires_when_the_node_says(self, params, fires):
        from services.deployment.triggers import TriggerManager

        assert _as_temporal_reads(TriggerManager.build_cron_expression(params)) == fires

    def test_node_defaults_fire_every_five_minutes(self):
        from nodes.scheduler.cron_scheduler import CronSchedulerParams
        from services.deployment.triggers import TriggerManager

        expression = TriggerManager.build_cron_expression(CronSchedulerParams().model_dump())

        assert _as_temporal_reads(expression) == _fires(minute="*/5")

    def test_the_panel_shows_the_fields_the_schedule_reads(self):
        """For each frequency, the panel shows exactly the fields its Schedule
        is built from. The timezone, shown for all of them, is read beside
        the cron string rather than by the builder."""
        from typing import get_args

        from nodes.scheduler.cron_scheduler import CronSchedulerParams
        from services.deployment.triggers import TriggerManager

        class Recording(dict):
            def __init__(self, **params):
                super().__init__(**params)
                self.read = set()

            def get(self, key, default=None):
                self.read.add(key)
                return super().get(key, default)

        fields = CronSchedulerParams.model_json_schema()["properties"]
        for frequency in get_args(CronSchedulerParams.model_fields["frequency"].annotation):
            params = Recording(frequency=frequency)
            TriggerManager.build_cron_expression(params)
            shown = {
                name
                for name, field in fields.items()
                if not field.get("hidden")
                and frequency in field.get("displayOptions", {}).get("show", {}).get("frequency", [frequency])
            }

            assert shown - {"frequency", "timezone"} == params.read - {"frequency"}, frequency

    @pytest.mark.parametrize(
        ("trigger", "fires"),
        [
            pytest.param({"every": "hour"}, _fires(minute="0"), id="hour"),
            pytest.param({"every": "day", "at": "09:00"}, _fires(minute="0", hour="9"), id="day"),
            pytest.param({"every": "week", "day": "friday", "at": "18:00"}, _fires(minute="0", hour="18", day_of_week="5"), id="week"),
            pytest.param({"every": "month", "day": "15", "at": "08:00"}, _fires(minute="0", hour="8", day_of_month="15"), id="month"),
        ],
    )
    def test_home_schedule_hires_fire_when_asked(self, trigger, fires):
        from datetime import datetime, timezone

        from services.deployment.triggers import TriggerManager
        from services.employees.builder import schedule_params
        from services.employees.hire_request import HireTrigger

        params = schedule_params(
            HireTrigger(kind="schedule", **trigger),
            "UTC",
            datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        )

        assert _as_temporal_reads(TriggerManager.build_cron_expression(params)) == fires


def _in_range(ranges, value: int) -> bool:
    """Temporal's ScheduleRange: an end below the start is the start, a step of 0 is 1."""
    return any(
        r.start <= value <= max(r.start, r.end) and (value - r.start) % (r.step or 1) == 0 for r in ranges
    )


def _calendar_fires_at(calendar, moment) -> bool:
    return (
        _in_range(calendar.second, moment.second)
        and _in_range(calendar.minute, moment.minute)
        and _in_range(calendar.hour, moment.hour)
        and _in_range(calendar.day_of_month, moment.day)
        and _in_range(calendar.month, moment.month)
        and (not calendar.year or _in_range(calendar.year, moment.year))
        and _in_range(calendar.day_of_week, (moment.weekday() + 1) % 7)
    )


class TestCronScheduleSpec:
    """The two values Temporal has no syntax for become a real ScheduleSpec."""

    def test_once_is_a_schedule_with_no_times(self):
        from services.deployment.triggers import TriggerManager
        from services.temporal.schedules import CRON_ONCE, cron_schedule_spec

        expression = TriggerManager.build_cron_expression({"frequency": "once"})
        spec = cron_schedule_spec(expression, "UTC")

        assert expression == CRON_ONCE
        assert (spec.calendars, spec.cron_expressions, spec.intervals) == ([], [], [])

    @pytest.mark.asyncio
    async def test_once_runs_when_its_schedule_is_created(self):
        from services.temporal.schedules import CRON_ONCE, create_cron_schedule

        async def trigger_immediately(expression):
            client = MagicMock()
            client.create_schedule = AsyncMock()
            await create_cron_schedule(
                client,
                workflow_id="wf-1",
                workflow_slug="wf-1",
                node_id="cron-1",
                trigger_label="cron-1",
                cron_expression=expression,
                timezone="UTC",
                listener_data={},
            )
            return client.create_schedule.await_args.kwargs["trigger_immediately"]

        assert await trigger_immediately(CRON_ONCE) is True
        assert await trigger_immediately("*/5 * * * *") is False

    def test_the_last_day_of_the_month_fires_on_the_last_day_only(self):
        import calendar
        from datetime import date, datetime, time, timedelta

        from services.deployment.triggers import TriggerManager
        from services.employees.builder import schedule_params
        from services.employees.hire_request import HireTrigger
        from services.temporal.schedules import cron_schedule_spec

        expression = TriggerManager.build_cron_expression({"frequency": "months", "month_day": "L", "monthly_time": "18:00"})
        hire = schedule_params(
            HireTrigger(kind="schedule", every="month", day="L", at="18:00"),
            "UTC",
            datetime(2026, 9, 25, 12, 0),
        )
        calendars = cron_schedule_spec(expression, "UTC").calendars

        assert TriggerManager.build_cron_expression(hire) == expression
        first = date(2025, 1, 1)
        days = [first + timedelta(days=n) for n in range((date(2033, 1, 1) - first).days)]
        # 2096 is a leap year; 2100, a century year, is not.
        days += [date(year, 2, day) for year in (2096, 2100) for day in range(27, calendar.monthrange(year, 2)[1] + 1)]
        for day in days:
            is_last = day.day == calendar.monthrange(day.year, day.month)[1]
            fires = any(_calendar_fires_at(c, datetime.combine(day, time(18, 0))) for c in calendars)
            assert fires == is_last, day
        assert not any(_calendar_fires_at(c, datetime(2026, 1, 31, 9, 0)) for c in calendars)
        # A real Temporal server rejects calendar years outside 2000-2100.
        assert all(2000 <= r.start <= max(r.start, r.end) <= 2100 for c in calendars for r in c.year)


class TestScheduleDescription:
    """A deployed trigger's ``schedule`` output reads the way the node's does.

    The deploy path keeps its own copy because services never import a
    plugin folder, so this is what keeps the two from drifting.
    """

    @pytest.mark.parametrize(
        "params",
        [
            pytest.param({"frequency": "seconds", "interval": 30}, id="seconds"),
            pytest.param({"frequency": "minutes", "interval_minutes": 5}, id="minutes"),
            pytest.param({"frequency": "hours", "interval_hours": 6}, id="hours"),
            pytest.param({"frequency": "days", "daily_time": "09:00"}, id="days"),
            pytest.param({"frequency": "weeks", "weekday": "5", "weekly_time": "18:00"}, id="weeks"),
            pytest.param({"frequency": "months", "month_day": "15", "monthly_time": "08:00"}, id="months"),
            pytest.param({"frequency": "months", "month_day": "L", "monthly_time": "18:00"}, id="last-day"),
            pytest.param({"frequency": "once"}, id="once"),
        ],
    )
    def test_the_deploy_path_matches_the_node(self, params):
        from nodes.scheduler.cron_scheduler import _get_schedule_description
        from services.deployment.manager import DeploymentManager

        assert DeploymentManager._get_schedule_description(params) == _get_schedule_description(params)

    def test_the_last_day_of_the_month_is_written_out(self):
        from services.deployment.manager import DeploymentManager

        params = {"frequency": "months", "month_day": "L", "monthly_time": "18:00"}

        assert DeploymentManager._get_schedule_description(params) == "Monthly on the last day at 18:00"


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
                    "owner_gone": kw.get("owner_gone"),
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
        # A Schedule another workflow left is stale only once that workflow is deleted.
        mgr.database.get_workflow = AsyncMock(return_value=None)
        assert await call["owner_gone"]("6") is True
        mgr.database.get_workflow = AsyncMock(return_value=MagicMock(slug="Maya_1"))
        assert await call["owner_gone"]("6") is False

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
