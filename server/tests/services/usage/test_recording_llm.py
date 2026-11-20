"""Model calls booked in ``token_usage_metrics`` (``record_llm_usage``)."""

from types import SimpleNamespace

import structlog

from services.usage import record_llm_usage
from services.usage.recording import NO_COST
from tests.services.usage._helpers import model_rows, unavailable

USAGE = {
    "input_tokens": 100,
    "output_tokens": 20,
    "total_tokens": 1120,
    "cache_creation_tokens": 50,
    "cache_read_tokens": 950,
    "reasoning_tokens": 5,
}


async def book(database, **overrides):
    """One recorded call; ``overrides`` replace the defaults."""
    call = {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "usage": USAGE,
        "session_id": "wf",
        "node_id": "wf:aiAgent:1",
        "database": database,
    }
    call.update(overrides)
    return await record_llm_usage(**call)


async def test_a_call_writes_one_row(database, prices):
    await book(database)

    assert len(await model_rows(database)) == 1


async def test_the_row_stores_every_token_kind(database, prices):
    await book(database)

    row = (await model_rows(database))[0]
    assert row.input_tokens == 100
    assert row.output_tokens == 20
    assert row.total_tokens == 1120
    assert row.cache_creation_tokens == 50
    assert row.cache_read_tokens == 950
    assert row.reasoning_tokens == 5


async def test_a_missing_token_kind_is_zero(database, prices):
    await book(database, usage={"input_tokens": 10, "output_tokens": 2})

    row = (await model_rows(database))[0]
    assert row.input_tokens == 10
    assert row.total_tokens == 0
    assert row.cache_read_tokens == 0


async def test_the_row_says_where_the_call_came_from(database, prices):
    await book(database, iteration=5, workflow_id="wf", execution_id="wf:execution:8")

    row = (await model_rows(database))[0]
    assert row.provider == "anthropic"
    assert row.model == "claude-sonnet-5-5"
    assert row.session_id == "wf"
    assert row.node_id == "wf:aiAgent:1"
    assert row.workflow_id == "wf"
    assert row.execution_id == "wf:execution:8"
    assert row.iteration == 5


async def test_a_call_is_iteration_one_unless_told(database, prices):
    await book(database)

    assert (await model_rows(database))[0].iteration == 1


async def test_the_row_keeps_the_chat_run_it_worked_for(database, prices):
    await book(database, chat_run_id="r_1")

    assert (await model_rows(database))[0].chat_run_id == "r_1"


async def test_a_call_outside_a_chat_run_keeps_none(database, prices):
    await book(database)

    assert (await model_rows(database))[0].chat_run_id is None


async def test_a_call_made_for_a_job_keeps_the_job(database, prices):
    # The model step binds the job in the log context; the test conftest
    # replaces core.logging.log_context, so it is bound through structlog here.
    with structlog.contextvars.bound_contextvars(employee_job_id="job-1"):
        await book(database)

    assert (await model_rows(database))[0].employee_job_id == "job-1"


async def test_a_call_outside_a_job_keeps_none(database, prices):
    await book(database)

    assert (await model_rows(database))[0].employee_job_id is None


async def test_the_row_is_stamped_when_it_is_booked(database, prices):
    await book(database)

    assert (await model_rows(database))[0].created_at is not None


async def test_the_price_service_is_asked_for_this_model_and_these_tokens(database, prices):
    await book(database)

    prices.calculate_cost.assert_called_once_with(provider="anthropic", model="claude-sonnet-5-5", **USAGE)


async def test_the_price_is_stored_on_the_row(database, prices):
    await book(database)

    row = (await model_rows(database))[0]
    assert row.input_cost == 0.01
    assert row.output_cost == 0.02
    assert row.cache_cost == 0.003
    assert row.total_cost == 0.033


async def test_the_price_is_returned(database, prices):
    cost = await book(database)

    assert cost == prices.calculate_cost.return_value


async def test_a_real_price_comes_from_the_pricing_file(database, real_prices):
    expected = real_prices.calculate_cost(provider="anthropic", model="claude-sonnet-5-5", **USAGE)

    cost = await book(database)

    assert expected["total_cost"] > 0
    assert cost == expected
    assert (await model_rows(database))[0].total_cost == expected["total_cost"]


async def test_a_failed_price_writes_nothing_and_costs_zero(database, prices):
    prices.calculate_cost.side_effect = RuntimeError("no price")

    cost = await book(database)

    assert cost == NO_COST
    assert await model_rows(database) == []


async def test_a_failed_write_still_returns_the_price(prices):
    broken = SimpleNamespace(get_session=unavailable)

    cost = await book(broken)

    assert cost == prices.calculate_cost.return_value


async def test_the_containers_database_is_used_when_none_is_passed(database, prices, monkeypatch):
    import core.container

    monkeypatch.setattr(core.container.container, "database", lambda: database)

    await book(None)

    assert len(await model_rows(database)) == 1
