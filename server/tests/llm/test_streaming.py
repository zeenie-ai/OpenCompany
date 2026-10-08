"""Streaming a response never changes it.

A provider that declares ``streaming`` (llm_defaults.json) hands each delta
to the sink and still returns the response it would have returned without
one; ChatUnifier gives the sink to those providers only, and for every other
provider hands it the finished response. The chunks and events below are
built from the SDKs' own types, so the comparison is against what the
non-streaming call really returns.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.llm.protocol import LLMError, LLMResponse, Message, StreamEvent, ToolDef


class Sink:
    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    async def __call__(self, event: StreamEvent) -> None:
        self.events.append(event)

    def joined(self, kind: str = "text") -> str:
        return "".join(event.delta for event in self.events if event.kind == kind)


def comparable(response: LLMResponse):
    """Everything a caller reads from a response (``raw`` is the SDK object
    itself and differs by construction)."""
    return (
        response.content,
        response.thinking,
        response.tool_calls,
        response.usage,
        response.finish_reason,
        response.assistant_message,
    )


class AsyncIter:
    def __init__(self, items) -> None:
        self._items = list(items)

    def __aiter__(self):
        async def gen():
            for item in self._items:
                yield item

        return gen()


MESSAGES = [Message(role="system", content="Be brief."), Message(role="user", content="Any bookings?")]


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


def _anthropic_final():
    from anthropic.types import Message as AnthropicMessage

    return AnthropicMessage.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [
                {"type": "thinking", "thinking": "Check Saturday.", "signature": "sig"},
                {"type": "text", "text": "Saturday works."},
                {"type": "tool_use", "id": "tu_1", "name": "calendar", "input": {"day": "sat"}},
            ],
            "stop_reason": "tool_use",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )


def _anthropic_stream(final):
    from anthropic.lib.streaming import TextEvent
    from anthropic.lib.streaming._types import ThinkingEvent

    events = [
        ThinkingEvent(type="thinking", thinking="Check Saturday.", snapshot="Check Saturday."),
        # The raw delta a helper event comes from: never counted twice.
        SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text="Saturday ")),
        TextEvent(type="text", text="Saturday ", snapshot="Saturday "),
        TextEvent(type="text", text="works.", snapshot="Saturday works."),
    ]
    stream = AsyncIter(events)
    stream.get_final_message = AsyncMock(return_value=final)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=stream)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_anthropic_streams_each_delta_once_and_returns_the_same_response():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider("sk-test")
    final = _anthropic_final()
    provider._client.messages.stream = _anthropic_stream(final)

    sink = Sink()
    streamed = await provider.chat(MESSAGES, model="claude-test", on_event=sink)
    plain = await provider.chat(MESSAGES, model="claude-test")

    assert sink.joined("text") == "Saturday works."
    assert sink.joined("reasoning") == "Check Saturday."
    assert comparable(streamed) == comparable(plain)
    assert streamed.tool_calls[0].name == "calendar"


# ---------------------------------------------------------------------------
# OpenAI chat completions
# ---------------------------------------------------------------------------


def _chunk(choices, usage=None):
    from openai.types.chat import ChatCompletionChunk

    payload = {"id": "cc_1", "object": "chat.completion.chunk", "created": 1, "model": "gpt-test", "choices": choices}
    if usage is not None:
        payload["usage"] = usage
    return ChatCompletionChunk.model_validate(payload)


USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
ARGUMENTS = json.dumps({"day": "sat"})


def _openai_chunks(finish_reason: str):
    return [
        _chunk([{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]),
        _chunk([{"index": 0, "delta": {"content": "Two"}, "finish_reason": None}]),
        _chunk([{"index": 0, "delta": {"content": " bookings."}, "finish_reason": None}]),
        _chunk([{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "calendar", "arguments": ARGUMENTS[:5]}}]}, "finish_reason": None}]),
        _chunk([{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ARGUMENTS[5:]}}]}, "finish_reason": None}]),
        _chunk([{"index": 0, "delta": {}, "finish_reason": finish_reason}]),
        _chunk([], usage=USAGE),
    ]


def _openai_completion(finish_reason: str):
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "cc_1",
            "object": "chat.completion",
            "created": 1,
            "model": "gpt-test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": {
                        "role": "assistant",
                        "content": "Two bookings.",
                        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "calendar", "arguments": ARGUMENTS}}],
                    },
                }
            ],
            "usage": USAGE,
        }
    )


def _openai_provider():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        return OpenAIProvider("sk-test")


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["tool_calls", "length"])
async def test_openai_chat_streams_and_returns_what_create_returns(finish_reason):
    provider = _openai_provider()
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        if kwargs.get("stream"):
            return AsyncIter(_openai_chunks(finish_reason))
        return _openai_completion(finish_reason)

    provider._client.chat.completions.create = create
    tools = [ToolDef(name="calendar", description="Read the calendar", parameters={"type": "object", "properties": {"day": {"type": "string"}}})]

    sink = Sink()
    streamed = await provider.chat(MESSAGES, model="gpt-test", tools=tools, on_event=sink)
    plain = await provider.chat(MESSAGES, model="gpt-test", tools=tools)

    assert sink.joined() == "Two bookings."
    # A truncated (``length``) answer returns normally, as it does unstreamed.
    assert comparable(streamed) == comparable(plain)
    assert streamed.usage.total_tokens == 15
    streamed_call, plain_call = calls
    assert streamed_call.pop("stream") is True
    assert streamed_call.pop("stream_options") == {"include_usage": True}
    assert streamed_call == plain_call


# ---------------------------------------------------------------------------
# OpenAI Responses API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["response.completed", "response.incomplete"])
async def test_openai_responses_stream_returns_the_terminal_events_response(terminal):
    provider = _openai_provider()
    final = object()
    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Checking."),
        SimpleNamespace(type="response.output_text.delta", delta="Two"),
        SimpleNamespace(type="response.output_text.delta", delta=" bookings."),
        SimpleNamespace(type=terminal, response=final),
    ]
    provider._client.responses.create = AsyncMock(return_value=AsyncIter(events))
    normalized = LLMResponse(content="Two bookings.")
    with patch.object(provider, "_normalize_responses", return_value=normalized) as normalize:
        sink = Sink()
        response = await provider._chat_responses(
            messages=MESSAGES, model="gpt-test", max_tokens=100, thinking=None, tools=None, context_management=None, on_event=sink
        )
    assert response is normalized
    assert normalize.call_args.args[0] is final
    assert provider._client.responses.create.call_args.kwargs["stream"] is True
    assert sink.joined() == "Two bookings."
    assert sink.joined("reasoning") == "Checking."


@pytest.mark.asyncio
async def test_openai_responses_stream_that_never_finishes_is_a_retryable_error():
    provider = _openai_provider()
    provider._client.responses.create = AsyncMock(return_value=AsyncIter([SimpleNamespace(type="response.output_text.delta", delta="Two")]))
    with pytest.raises(LLMError) as caught:
        await provider._chat_responses(
            messages=MESSAGES, model="gpt-test", max_tokens=100, thinking=None, tools=None, context_management=None, on_event=Sink()
        )
    assert caught.value.retryable


# ---------------------------------------------------------------------------
# ChatUnifier
# ---------------------------------------------------------------------------


def _unifier_with(name: str, client, *, streaming: bool):
    from services.llm import registry
    from services.llm.registry import ProviderSpec
    from services.llm.unifier import ChatUnifier

    registry._REGISTRY[name] = ProviderSpec(name=name, factory=lambda **kwargs: client, sdk_exception_refs=("openai:OpenAIError",))
    auth = MagicMock()
    auth.resolve_api_key = AsyncMock(return_value=None)
    providers = {name: {"streaming": True}} if streaming else {}
    return ChatUnifier(defaults={"providers": providers}, auth_service=auth, client_cache_size=0)


@pytest.mark.asyncio
async def test_unifier_hands_the_sink_only_to_a_provider_that_declares_streaming():
    from services.llm import registry

    name = "streaming-test"
    client = MagicMock()
    client.chat = AsyncMock(return_value=LLMResponse(content="Two bookings."))
    unifier = _unifier_with(name, client, streaming=True)
    sink = Sink()
    try:
        await unifier.chat(provider=name, api_key="k", messages=MESSAGES, model="m", on_event=sink)
    finally:
        registry._REGISTRY.pop(name, None)
    assert client.chat.call_args.kwargs["on_event"] is sink
    # The provider streamed (here: did not call the sink); nothing is replayed.
    assert sink.events == []


@pytest.mark.asyncio
async def test_unifier_replays_the_finished_response_for_any_other_provider():
    from services.llm import registry

    name = "not-streaming-test"
    client = MagicMock()
    client.chat = AsyncMock(return_value=LLMResponse(content="Two bookings.", thinking="Checked."))
    unifier = _unifier_with(name, client, streaming=False)
    sink = Sink()
    try:
        response = await unifier.chat(provider=name, api_key="k", messages=MESSAGES, model="m", on_event=sink)
    finally:
        registry._REGISTRY.pop(name, None)
    assert "on_event" not in client.chat.call_args.kwargs
    assert [(event.kind, event.delta) for event in sink.events] == [("reasoning", "Checked."), ("text", "Two bookings.")]
    assert response.content == "Two bookings."


def test_only_providers_whose_streams_were_checked_declare_streaming():
    from services.llm.config import LLM_DEFAULTS

    declared = sorted(name for name, config in LLM_DEFAULTS["providers"].items() if config.get("streaming") is True)
    assert declared == ["anthropic", "openai"]
