"""The monthly budget per employee (``services/usage/limit.py``).

Run against real SQLite. The budget is the owner's
``employee_monthly_budget_usd`` setting when above 0, else the environment's;
0 means no budget. An employee reaches it on everything booked for its
workflow since the 1st of the month in the owner's time zone.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from services.usage import monthly_budget_reached
from tests.services.usage._helpers import add_api_call, add_model_call, unavailable

NOVEMBER_20 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
NO_ENV_BUDGET = SimpleNamespace(employee_monthly_budget_usd=0.0)


async def owner_sets(database, budget: float, **settings) -> None:
    await database.save_user_settings({"employee_monthly_budget_usd": budget, **settings})


async def reached(database, workflow_id: str = "wf", settings=NO_ENV_BUDGET):
    return await monthly_budget_reached(workflow_id, database=database, settings=settings, now=NOVEMBER_20)


async def add_model_cost(database, workflow_id: str, cost: float, when: datetime) -> None:
    """One model call; the table keeps its time in UTC without a zone."""
    await add_model_call(database, workflow_id=workflow_id, total_cost=cost, created_at=when.replace(tzinfo=None))


async def add_tool_cost(database, workflow_id: str, cost: float, when: datetime) -> None:
    await add_api_call(database, workflow_id=workflow_id, cost=cost, timestamp=when)


# ---- the setting -------------------------------------------------------------


async def test_the_owners_budget_is_saved_and_read_back(database):
    await owner_sets(database, 20.0)

    assert (await database.get_user_settings())["employee_monthly_budget_usd"] == 20.0


async def test_an_owner_who_set_none_has_no_budget(database):
    await database.save_user_settings({})

    assert (await database.get_user_settings())["employee_monthly_budget_usd"] == 0


# ---- reaching it -------------------------------------------------------------


async def test_an_employee_is_never_over_while_no_budget_is_set(database):
    await add_model_cost(database, "wf", 500.0, NOVEMBER_20)

    assert await reached(database) is None


async def test_an_employee_under_its_budget_is_not_over(database):
    await owner_sets(database, 20.0)
    await add_model_cost(database, "wf", 19.0, NOVEMBER_20)

    assert await reached(database) is None


async def test_an_employee_that_reached_its_budget_gets_the_amount(database):
    await owner_sets(database, 20.0)
    await add_model_cost(database, "wf", 15.0, NOVEMBER_20)
    await add_tool_cost(database, "wf", 5.0, NOVEMBER_20)

    assert await reached(database) == 20.0


async def test_last_months_calls_do_not_count(database):
    await owner_sets(database, 20.0)
    october = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    await add_model_cost(database, "wf", 50.0, october)
    await add_tool_cost(database, "wf", 50.0, october)

    assert await reached(database) is None


async def test_another_employees_calls_do_not_count(database):
    await owner_sets(database, 20.0)
    await add_model_cost(database, "other", 50.0, NOVEMBER_20)
    await add_tool_cost(database, "other", 50.0, NOVEMBER_20)

    assert await reached(database) is None


async def test_the_month_starts_in_the_owners_time_zone(database):
    # 19:00 UTC on 31 October is already 1 November in Kolkata.
    await owner_sets(database, 20.0, profile_timezone="Asia/Kolkata")
    await add_model_cost(database, "wf", 25.0, datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc))

    assert await reached(database) == 20.0


async def test_the_environments_budget_applies_to_an_owner_who_set_none(database):
    await add_model_cost(database, "wf", 25.0, NOVEMBER_20)

    assert await reached(database, settings=SimpleNamespace(employee_monthly_budget_usd=20.0)) == 20.0


async def test_a_budget_that_cannot_be_read_is_not_reached():
    broken = SimpleNamespace(get_user_settings=unavailable, get_session=unavailable)

    assert await reached(broken) is None


# ---- the index the sum uses ----------------------------------------------------


@pytest.mark.skipif(bool(os.environ.get("TEST_POSTGRES_URL")), reason="SQLite schema check")
@pytest.mark.parametrize(
    "table, index",
    [
        ("token_usage_metrics", "ix_token_usage_metrics_workflow_created"),
        ("api_usage_metrics", "ix_api_usage_metrics_workflow_timestamp"),
    ],
)
async def test_an_older_database_gains_the_index(database, table, index):
    async with database.engine.begin() as conn:
        await conn.execute(text(f"DROP INDEX {index}"))

    await database._migrate_user_settings()

    async with database.engine.begin() as conn:
        result = await conn.execute(text(f"PRAGMA index_list({table})"))
        names = {row[1] for row in result.fetchall()}
    assert index in names
