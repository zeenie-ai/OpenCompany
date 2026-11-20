"""What each plugin's usage helper books.

Every helper makes one call to ``services.usage.record_api_usage``; these
tests pin that call. ``api_usage`` is the recorder's stand-in
(``tests/usage_fixtures.py``).
"""

from types import SimpleNamespace

import pytest

from nodes.google._base import track_google_usage
from nodes.location._service import _track_maps_usage
from nodes.microsoft._base import track_microsoft_usage
from nodes.proxy._usage import track_proxy_usage
from nodes.scraper.tikhub_action._sdk import track_tikhub_usage
from nodes.speech._base import track_usage as track_speech_usage
from nodes.translate._base import track_usage as track_translate_usage
from nodes.treg.treg_action import _record_cost as record_treg_cost
from nodes.twitter._base import track_twitter_usage

NODE = "wf:node:1"
#: The raw execution context a plugin operation receives.
CONTEXT = {"session_id": "chat-1", "workflow_id": "wf"}


def node_context(**fields):
    """A plugin's ``NodeContext``, as far as a usage helper reads it."""
    values = {"node_id": NODE, "session_id": "chat-1", "workflow_id": "wf", "raw": dict(CONTEXT)}
    values.update(fields)
    return SimpleNamespace(**values)


async def test_twitter_books_the_action_and_count(api_usage):
    await track_twitter_usage(NODE, "search", 20, CONTEXT)

    api_usage.assert_awaited_once_with("twitter", "search", 20, node_id=NODE, session_id="chat-1", workflow_id="wf")


async def test_twitter_without_a_session_books_the_default(api_usage):
    await track_twitter_usage(NODE, "tweet", 1, {})

    api_usage.assert_awaited_once_with("twitter", "tweet", 1, node_id=NODE, session_id="default", workflow_id=None)


async def test_google_books_under_the_service_it_is_given(api_usage):
    await track_google_usage("gmail", NODE, "send", 1, CONTEXT)

    api_usage.assert_awaited_once_with("gmail", "send", 1, node_id=NODE, session_id="chat-1", workflow_id="wf")


async def test_microsoft_books_under_microsoft_graph(api_usage):
    await track_microsoft_usage(NODE, "read", 5, CONTEXT)

    api_usage.assert_awaited_once_with("microsoft_graph", "read", 5, node_id=NODE, session_id="chat-1", workflow_id="wf")


async def test_maps_books_under_google_maps(api_usage):
    await _track_maps_usage(NODE, "geocode", 1, "wf", "chat-1")

    api_usage.assert_awaited_once_with("google_maps", "geocode", 1, node_id=NODE, session_id="chat-1", workflow_id="wf")


async def test_speech_books_the_units_under_its_provider(api_usage):
    await track_speech_usage(node_context(), provider="deepgram", operation="speech_to_text", units=12.5, unit="seconds")

    api_usage.assert_awaited_once_with("deepgram", "speech_to_text", 12.5, node_id=NODE, session_id="chat-1", workflow_id="wf")


@pytest.mark.parametrize("units", [None, 0])
async def test_speech_books_nothing_without_units(api_usage, units):
    await track_speech_usage(node_context(), provider="deepgram", operation="speech_to_text", units=units, unit="seconds")

    api_usage.assert_not_awaited()


async def test_translate_books_the_units_under_its_provider(api_usage):
    await track_translate_usage(node_context(), provider="deepl", operation="translate", units=120, unit="characters")

    api_usage.assert_awaited_once_with("deepl", "translate", 120, node_id=NODE, session_id="chat-1", workflow_id="wf")


async def test_translate_books_nothing_for_a_token_billed_provider(api_usage):
    # The LLM layer already costs those tokens.
    await track_translate_usage(node_context(), provider="openai", operation="translate", units=120, unit="tokens")

    api_usage.assert_not_awaited()


@pytest.mark.parametrize("units", [None, 0])
async def test_translate_books_nothing_without_units(api_usage, units):
    await track_translate_usage(node_context(), provider="deepl", operation="translate", units=units, unit="characters")

    api_usage.assert_not_awaited()


async def test_tikhub_books_the_route_it_called(api_usage):
    await track_tikhub_usage(node_context(), "call", "/api/v1/douyin/web/fetch_one_video")

    api_usage.assert_awaited_once_with(
        "tikhub", "call", node_id=NODE, session_id="chat-1", workflow_id="wf", endpoint="/api/v1/douyin/web/fetch_one_video"
    )


async def test_tikhub_without_a_route_books_the_action(api_usage):
    await track_tikhub_usage(node_context(), "account", None)

    assert api_usage.await_args.kwargs["endpoint"] == "account"


async def test_tikhub_returns_the_cost_for_its_output(api_usage):
    api_usage.return_value = 0.001

    cost = await track_tikhub_usage(node_context(), "call", "/api/v1/x")

    assert cost == 0.001


async def test_tikhub_reads_the_session_from_the_raw_context_when_the_context_has_none(api_usage):
    await track_tikhub_usage(node_context(session_id="", workflow_id=None), "call", "/api/v1/x")

    booked = api_usage.await_args.kwargs
    assert booked["session_id"] == "chat-1"
    assert booked["workflow_id"] == "wf"


async def test_treg_books_the_charge_treg_reported(api_usage):
    await record_treg_cost(node_context(), "serp.search", 2500, "call")

    api_usage.assert_awaited_once_with(
        "treg", "call", node_id=NODE, session_id="chat-1", workflow_id="wf", endpoint="serp.search", cost=0.0025
    )


async def test_treg_cuts_a_long_tool_id_to_the_column(api_usage):
    await record_treg_cost(node_context(), "t" * 150, 2500, "task")

    assert api_usage.await_args.kwargs["endpoint"] == "t" * 100


async def test_proxy_books_the_cost_it_is_given_under_its_provider(api_usage):
    await track_proxy_usage(NODE, "brightdata", 8.0, workflow_id="wf", session_id="chat-1")

    api_usage.assert_awaited_once_with(
        "proxy_brightdata", "proxy_request", node_id=NODE, session_id="chat-1", workflow_id="wf", endpoint="proxy", cost=8.0
    )


async def test_proxy_without_a_provider_books_under_proxy(api_usage):
    await track_proxy_usage(NODE, "", 0.0)

    assert api_usage.await_args.args[0] == "proxy"
