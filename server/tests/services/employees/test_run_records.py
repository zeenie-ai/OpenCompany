"""Run records: one row per run however often it is reported, "done today"
counted from the owner's midnight, old rows pruned, and a new record
refreshing the employee."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services.employees import events, runs


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    changed = []
    monkeypatch.setattr(events, "employee_changed", changed.append)

    async def no_prune(_database):
        return None

    # Recording prunes on its own at most hourly; these tests prune by hand.
    monkeypatch.setattr(runs, "_maybe_prune", no_prune)
    runs.reset_for_tests()
    yield changed


async def test_a_run_is_recorded_once(real_database, quiet):
    assert await runs.record_run(real_database, workflow_id="w", run_id="r1", status="success", runtime="temporal")
    assert not await runs.record_run(real_database, workflow_id="w", run_id="r1", status="success", runtime="local")
    assert quiet == ["w"]


async def test_done_today_counts_successes_since_the_owner_midnight(real_database):
    zone = ZoneInfo("Asia/Kolkata")  # UTC+5:30
    now = datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)  # 08:30 in Kolkata
    local_midnight = datetime(2026, 9, 24, 18, 30, tzinfo=timezone.utc)
    rows = [
        ("before", local_midnight - timedelta(minutes=1), "success"),
        ("after", local_midnight + timedelta(minutes=1), "success"),
        ("later", now - timedelta(minutes=5), "success"),
        ("broken", now - timedelta(minutes=4), "failed"),
    ]
    for run_id, finished, status in rows:
        await runs.record_run(real_database, workflow_id="w", run_id=run_id, status=status, runtime="temporal", finished_at=finished)
    await runs.record_run(real_database, workflow_id="other", run_id="o1", status="success", runtime="local", finished_at=now)
    assert await runs.done_today(real_database, ["w", "other", "none"], zone=zone, now=now) == {"w": 2, "other": 1}


async def test_the_latest_run_and_pruning(real_database):
    now = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    await runs.record_run(real_database, workflow_id="w", run_id="old", status="success", runtime="local", finished_at=now - timedelta(days=40))
    await runs.record_run(real_database, workflow_id="w", run_id="new", status="failed", runtime="temporal", finished_at=now - timedelta(hours=1), generation=3)
    latest = await runs.latest_run(real_database, "w")
    assert latest["status"] == "failed" and latest["generation"] == 3
    assert await runs.prune_run_records(real_database, now=now) == 1
    assert await runs.delete_runs_for_workflow(real_database, "w") == 1
    assert await runs.latest_run(real_database, "w") is None


def test_midnight_is_computed_in_the_zone():
    now = datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
    assert runs.start_of_day(now, ZoneInfo("America/New_York")) == datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc)
    assert runs.owner_zone("Not/AZone").key == "UTC"
