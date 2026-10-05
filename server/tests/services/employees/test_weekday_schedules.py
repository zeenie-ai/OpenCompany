"""Weekday generation uses scheduler semantics rather than model judgement."""
from datetime import datetime, timezone

from nodes.scheduler.cron_scheduler import CronSchedulerParams, _get_schedule_description
from services.deployment.triggers import TriggerManager
from services.employees.builder import schedule_params
from services.employees.hire_request import HireTrigger
from services.temporal.schedules import cron_schedule_spec


def test_generated_weekdays_have_temporal_weekday_filter_in_owner_timezone():
    params = schedule_params(HireTrigger(kind="schedule", every="weekday", at="09:00"), "Asia/Kolkata", datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert params == {"frequency": "days", "daily_time": "09:00", "timezone": "Asia/Kolkata", "weekday_only": True}
    assert CronSchedulerParams.model_validate(params).weekday_only is True
    expression = TriggerManager.build_cron_expression(params)
    assert expression == "00 09 * * 1-5"
    spec = cron_schedule_spec(expression, params["timezone"])
    assert spec.cron_expressions == [expression]
    assert spec.time_zone_name == "Asia/Kolkata"
    assert _get_schedule_description(params) == "Weekdays at 09:00"


def test_default_and_owner_edited_daily_schedules_keep_existing_semantics():
    params = {"frequency": "days", "daily_time": "08:00", "timezone": "Europe/London"}
    assert CronSchedulerParams.model_validate(params).weekday_only is False
    assert TriggerManager.build_cron_expression(params) == "00 08 * * *"
    assert TriggerManager.build_cron_expression({**params, "weekday_only": False}) == "00 08 * * *"
    assert _get_schedule_description(params) == "Daily at 08:00"


def test_weekday_filter_does_not_override_explicit_weekly_owner_selection():
    params = {"frequency": "weeks", "weekday": "6", "weekly_time": "10:00", "weekday_only": True}
    assert TriggerManager.build_cron_expression(params) == "00 10 * * 6"


def test_daily_hire_does_not_implicitly_restrict_weekends():
    params = schedule_params(HireTrigger(kind="schedule", every="day", at="09:00"), "UTC", datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert "weekday_only" not in params
