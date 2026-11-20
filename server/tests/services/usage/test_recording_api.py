"""Paid API calls booked in ``api_usage_metrics`` (``record_api_usage``)."""

from types import SimpleNamespace

import structlog

from services.usage import record_api_usage
from tests.services.usage._helpers import api_rows, unavailable

NODE = "wf:twitterSend:1"


async def test_a_call_writes_one_row(database, prices):
    await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    assert len(await api_rows(database)) == 1


async def test_the_row_says_what_was_called_and_by_which_node(database, prices):
    await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    row = (await api_rows(database))[0]
    assert row.service == "twitter"
    assert row.endpoint == "tweet"
    assert row.node_id == NODE
    assert row.resource_count == 1


async def test_a_call_with_no_session_or_workflow_gets_the_defaults(database, prices):
    await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    row = (await api_rows(database))[0]
    assert row.session_id == "default"
    assert row.workflow_id is None


async def test_the_row_names_its_session_and_workflow(database, prices):
    await record_api_usage("twitter", "tweet", node_id=NODE, session_id="chat-1", workflow_id="wf", database=database)

    row = (await api_rows(database))[0]
    assert row.session_id == "chat-1"
    assert row.workflow_id == "wf"


async def test_the_price_service_is_asked_for_the_service_action_and_count(database, prices):
    await record_api_usage("twitter", "search", 20, node_id=NODE, database=database)

    prices.calculate_api_cost.assert_called_once_with("twitter", "search", 20)


async def test_the_row_takes_the_priced_operation_and_cost(database, prices):
    await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    row = (await api_rows(database))[0]
    assert row.operation == "content_create"
    assert row.cost == 0.01


async def test_the_cost_is_returned(database, prices):
    cost = await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    assert cost == 0.01


async def test_the_endpoint_can_say_more_than_the_action(database, prices):
    await record_api_usage("tikhub", "call", node_id=NODE, endpoint="/api/v1/douyin/web/fetch_one_video", database=database)

    assert (await api_rows(database))[0].endpoint == "/api/v1/douyin/web/fetch_one_video"


async def test_a_providers_own_charge_is_stored_and_returned(database, prices):
    cost = await record_api_usage("treg", "call", node_id=NODE, endpoint="serp.search", cost=0.0025, database=database)

    row = (await api_rows(database))[0]
    assert cost == 0.0025
    assert row.cost == 0.0025
    assert row.operation == "call"


async def test_a_providers_own_charge_is_never_looked_up(database, prices):
    await record_api_usage("treg", "call", node_id=NODE, cost=0.0025, database=database)

    prices.calculate_api_cost.assert_not_called()


async def test_a_call_made_for_a_chat_run_keeps_the_run(database, prices):
    # BaseNode.execute binds the chat run in the log context for a plugin
    # operation; the test conftest replaces core.logging.log_context, so the
    # run is bound through structlog here.
    with structlog.contextvars.bound_contextvars(chat_run_id="r_1"):
        await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    assert (await api_rows(database))[0].chat_run_id == "r_1"


async def test_a_call_outside_a_chat_run_keeps_none(database, prices):
    await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    assert (await api_rows(database))[0].chat_run_id is None


async def test_a_call_made_for_a_job_keeps_the_job(database, prices):
    with structlog.contextvars.bound_contextvars(employee_job_id="job-1"):
        await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    assert (await api_rows(database))[0].employee_job_id == "job-1"


async def test_a_call_outside_a_job_keeps_none(database, prices):
    await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    assert (await api_rows(database))[0].employee_job_id is None


async def test_a_fractional_count_is_priced_as_given(database, prices):
    await record_api_usage("deepgram", "speech_to_text", 12.5, node_id=NODE, database=database)

    prices.calculate_api_cost.assert_called_once_with("deepgram", "speech_to_text", 12.5)
    assert len(await api_rows(database)) == 1


async def test_a_fractional_count_is_stored_as_given(database, prices):
    await record_api_usage("deepgram", "speech_to_text", 12.5, node_id=NODE, database=database)

    assert (await api_rows(database))[0].resource_count == 12.5


async def test_a_real_price_comes_from_the_pricing_file(database, real_prices):
    expected = real_prices.calculate_api_cost("twitter", "tweet", 1)

    cost = await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    row = (await api_rows(database))[0]
    assert expected["total_cost"] > 0
    assert cost == expected["total_cost"]
    assert row.cost == expected["total_cost"]
    assert row.operation == expected["operation"]


async def test_a_failed_price_writes_nothing_and_costs_zero(database, prices):
    prices.calculate_api_cost.side_effect = RuntimeError("no price")

    cost = await record_api_usage("twitter", "tweet", node_id=NODE, database=database)

    assert cost == 0.0
    assert await api_rows(database) == []


async def test_a_failed_write_still_returns_the_cost(prices):
    broken = SimpleNamespace(get_session=unavailable)

    cost = await record_api_usage("twitter", "tweet", node_id=NODE, database=broken)

    assert cost == 0.01


async def test_the_containers_database_is_used_when_none_is_passed(database, prices, monkeypatch):
    import core.container

    monkeypatch.setattr(core.container.container, "database", lambda: database)

    await record_api_usage("twitter", "tweet", node_id=NODE)

    assert len(await api_rows(database)) == 1
