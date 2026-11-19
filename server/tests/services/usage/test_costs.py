"""What a chat run cost, and what each employee cost this month.

A cost is model calls plus paid tool calls: the rows of both usage tables.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services.usage import chat_run_costs, handlers, month_start, workflow_costs
from tests.services.usage._helpers import add_api_call, add_model_call, add_workflow, unavailable

NOVEMBER = datetime(2026, 11, 1, tzinfo=timezone.utc)
#: Model rows keep their time without a zone, API rows with one.
IN_OCTOBER = {"model": datetime(2026, 10, 31, 23, 0), "api": datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)}
IN_NOVEMBER = {"model": datetime(2026, 11, 10, 9, 0), "api": datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)}


async def add_model_cost(database, workflow_id, cost, *, when=IN_NOVEMBER, **fields):
    await add_model_call(database, workflow_id=workflow_id, total_cost=cost, created_at=when["model"], **fields)


async def add_tool_cost(database, workflow_id, cost, *, when=IN_NOVEMBER, **fields):
    await add_api_call(database, workflow_id=workflow_id, cost=cost, timestamp=when["api"], **fields)


# ---- a chat run's cost -------------------------------------------------------


async def test_a_runs_model_and_tool_calls_add_up(database):
    await add_model_cost(database, "wf", 0.5, chat_run_id="r_1")
    await add_model_cost(database, "wf", 0.25, chat_run_id="r_1")
    await add_tool_cost(database, "wf", 0.125, chat_run_id="r_1")

    assert await chat_run_costs(database, "wf") == {"r_1": 0.875}


async def test_each_run_of_the_workflow_has_its_own_cost(database):
    await add_model_cost(database, "wf", 0.5, chat_run_id="r_1")
    await add_tool_cost(database, "wf", 0.25, chat_run_id="r_2")

    assert await chat_run_costs(database, "wf") == {"r_1": 0.5, "r_2": 0.25}


async def test_another_workflows_runs_are_left_out(database):
    await add_model_cost(database, "wf", 0.5, chat_run_id="r_1")
    await add_model_cost(database, "other", 0.25, chat_run_id="r_9")
    await add_tool_cost(database, "other", 0.25, chat_run_id="r_9")

    assert await chat_run_costs(database, "wf") == {"r_1": 0.5}


async def test_calls_made_outside_a_chat_run_are_left_out(database):
    await add_model_cost(database, "wf", 0.5)
    await add_tool_cost(database, "wf", 0.25)

    assert await chat_run_costs(database, "wf") == {}


async def test_a_failed_read_of_run_costs_raises():
    with pytest.raises(RuntimeError):
        await chat_run_costs(SimpleNamespace(get_session=unavailable), "wf")


# ---- each employee's cost ----------------------------------------------------


async def test_an_employees_model_and_tool_calls_add_up(database):
    await add_workflow(database, "wf-1", "Ava")
    await add_model_cost(database, "wf-1", 0.5)
    await add_tool_cost(database, "wf-1", 0.25)

    assert await workflow_costs(database, NOVEMBER) == [{"workflow_id": "wf-1", "name": "Ava", "total_cost": 0.75}]


async def test_employees_come_highest_cost_first(database):
    await add_workflow(database, "wf-1", "Ava")
    await add_workflow(database, "wf-2", "Leo")
    await add_model_cost(database, "wf-1", 0.25)
    await add_model_cost(database, "wf-2", 0.5)

    costs = await workflow_costs(database, NOVEMBER)

    assert [row["name"] for row in costs] == ["Leo", "Ava"]


async def test_calls_with_no_employee_add_up_in_one_last_row(database):
    await add_workflow(database, "wf-1", "Ava")
    await add_model_cost(database, "wf-1", 0.25)
    # A hire screen's setup call has no workflow; "gone" was deleted since.
    await add_model_cost(database, None, 0.5)
    await add_tool_cost(database, "gone", 0.25)

    costs = await workflow_costs(database, NOVEMBER)

    assert costs == [
        {"workflow_id": "wf-1", "name": "Ava", "total_cost": 0.25},
        {"workflow_id": None, "name": None, "total_cost": 0.75},
    ]


async def test_calls_before_the_start_are_left_out(database):
    await add_workflow(database, "wf-1", "Ava")
    await add_model_cost(database, "wf-1", 0.5, when=IN_OCTOBER)
    await add_tool_cost(database, "wf-1", 0.5, when=IN_OCTOBER)
    await add_model_cost(database, "wf-1", 0.25)

    assert await workflow_costs(database, NOVEMBER) == [{"workflow_id": "wf-1", "name": "Ava", "total_cost": 0.25}]


async def test_no_calls_means_no_rows(database):
    await add_workflow(database, "wf-1", "Ava")

    assert await workflow_costs(database, NOVEMBER) == []


# ---- the month -------------------------------------------------------------


def test_the_month_starts_on_the_first_in_the_owners_zone():
    # 03:00 UTC on Nov 1 is still Oct 31 in New York.
    now = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)

    start = month_start(now, "America/New_York")

    assert start.date().isoformat() == "2026-10-01"
    assert start.astimezone(timezone.utc) == datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)


def test_an_unset_zone_counts_in_utc():
    now = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)

    assert month_start(now, None) == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_an_unknown_zone_counts_in_utc():
    now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

    assert month_start(now, "Mars/Olympus") == datetime(2026, 11, 1, tzinfo=timezone.utc)


# ---- the commands ------------------------------------------------------------


@pytest.fixture
def served(monkeypatch, database):
    """The commands reading the test database."""
    monkeypatch.setattr(handlers, "_database", lambda: database)
    return database


async def test_the_run_costs_command_answers_the_runs(served):
    await add_model_cost(served, "wf", 0.5, chat_run_id="r_1")

    reply = await handlers.handle_get_chat_run_costs({"workflow_id": "wf"}, None)

    assert reply == {"success": True, "runs": {"r_1": 0.5}}


async def test_the_run_costs_command_needs_a_workflow(served):
    reply = await handlers.handle_get_chat_run_costs({}, None)

    assert reply == {"success": False, "error": "workflow_id required"}


async def test_the_run_costs_command_says_when_the_read_failed(monkeypatch):
    monkeypatch.setattr(handlers, "_database", lambda: SimpleNamespace(get_session=unavailable))

    reply = await handlers.handle_get_chat_run_costs({"workflow_id": "wf"}, None)

    assert reply["success"] is False


async def test_the_month_command_answers_the_total_and_each_employee(served):
    now = datetime.now(timezone.utc)
    this_month = {"model": now.replace(tzinfo=None), "api": now}
    await add_workflow(served, "wf-1", "Ava")
    await add_model_cost(served, "wf-1", 0.5, when=this_month)
    await add_tool_cost(served, "wf-1", 0.25, when=this_month)
    await add_model_cost(served, None, 0.125, when=this_month)

    reply = await handlers.handle_get_workflow_costs({}, None)

    assert reply["success"] is True
    assert reply["since"] == month_start(now, None).date().isoformat()
    assert reply["total_cost"] == 0.875
    assert reply["workflows"] == [
        {"workflow_id": "wf-1", "name": "Ava", "total_cost": 0.75},
        {"workflow_id": None, "name": None, "total_cost": 0.125},
    ]


async def test_the_month_command_says_when_the_read_failed(monkeypatch):
    broken = SimpleNamespace(get_session=unavailable, get_user_settings=unavailable)
    monkeypatch.setattr(handlers, "_database", lambda: broken)

    reply = await handlers.handle_get_workflow_costs({}, None)

    assert reply["success"] is False
