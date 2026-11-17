"""Prompt caching for Claude: the request flag and the usage it reports.

Requests go through each SDK's real transport, because ``cache_control`` is a
top-level body field that the OpenAI SDK carries only through ``extra_body``.
"""

import json

import anthropic
import httpx
import openai
import pytest

from services.llm import config
from services.llm.protocol import Message
from services.llm.providers.anthropic import AnthropicProvider
from services.llm.providers.openrouter import OpenRouterProvider

EPHEMERAL = {"type": "ephemeral"}
HELLO = [Message(role="user", content="Hello")]


def _completion(model, usage):
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Done"},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


@pytest.fixture
async def openrouter(monkeypatch):
    requests = []
    usage = {
        "prompt_tokens": 1200,
        "completion_tokens": 50,
        "total_tokens": 1250,
        "prompt_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 100},
    }

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json=_completion(body["model"], usage))

    sdk_client = openai.AsyncOpenAI
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: sdk_client(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ))
    provider = OpenRouterProvider("sk-or-test", max_retries=0)
    try:
        yield provider, requests
    finally:
        await provider._client.close()


def _anthropic_stream(model):
    events = [
        {"type": "message_start", "message": {
            "id": "msg_cached", "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0,
                      "cache_creation_input_tokens": 100, "cache_read_input_tokens": 900},
        }},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Done"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    data = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=data)


@pytest.fixture
async def native_claude(monkeypatch):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append((request, body))
        return _anthropic_stream(body["model"])

    sdk_client = anthropic.AsyncAnthropic
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda **kwargs: sdk_client(
        **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    ))
    provider = AnthropicProvider("sk-test", max_retries=0)
    try:
        yield provider, requests
    finally:
        await provider._client.close()


async def test_openrouter_claude_request_asks_for_caching_and_books_both_counts(openrouter):
    provider, requests = openrouter
    result = await provider.chat(HELLO, model="anthropic/claude-sonnet-5.5", max_tokens=None)

    body = requests[0]
    assert body["cache_control"] == EPHEMERAL
    assert "max_tokens" not in body
    # input_tokens stays prompt_tokens (cache included), so context-pressure
    # projections and compaction thresholds read the same numbers as before.
    assert result.usage.input_tokens == 1200
    assert result.usage.cache_read_tokens == 900
    assert result.usage.cache_creation_tokens == 100
    assert result.usage.total_tokens == 1250


async def test_openrouter_other_vendors_get_no_cache_flag(openrouter):
    provider, requests = openrouter
    await provider.chat(HELLO, model="openai/gpt-6.1-terra", max_tokens=None)

    assert "cache_control" not in requests[0]
    assert "max_tokens" not in requests[0]


@pytest.mark.parametrize("details", [None, {}, {"cached_tokens": None, "cache_write_tokens": None}])
async def test_absent_cache_counts_are_zero(openrouter, details):
    from openai.types.chat import ChatCompletion

    provider, _ = openrouter
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    if details is not None:
        usage["prompt_tokens_details"] = details
    response = ChatCompletion.model_validate(_completion("openai/gpt-6.1-terra", usage))

    normalized = provider._normalize(response, "openai/gpt-6.1-terra").usage
    assert (normalized.cache_read_tokens, normalized.cache_creation_tokens) == (0, 0)


@pytest.mark.parametrize("model, beta_stream", [
    ("claude-sonnet-5-5", True),
    ("claude-sonnet-4-6", False),
])
async def test_native_claude_request_asks_for_caching(native_claude, model, beta_stream):
    provider, requests = native_claude
    result = await provider.chat(HELLO, model=model)

    request, body = requests[0]
    assert body["cache_control"] == EPHEMERAL
    assert ("anthropic-beta" in request.headers) is beta_stream
    assert result.usage.cache_read_tokens == 900
    assert result.usage.cache_creation_tokens == 100


async def test_the_flag_is_read_from_the_config(native_claude, monkeypatch):
    provider, requests = native_claude
    monkeypatch.setitem(config.LLM_DEFAULTS["providers"]["anthropic"], "cache_control_models", [])
    await provider.chat(HELLO, model="claude-sonnet-5-5")

    _, body = requests[0]
    assert "cache_control" not in body
